"""Decky entry point. The reusable EQ engine lives entirely in deckyeq/.

Every RPC answers {ok, error?, state?, ...} (DeckyKit D2, dk_rpc): `state` is Service.snapshot()."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import decky

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import dk_rpc  # noqa: E402
import dk_update  # noqa: E402
from deckyeq.model import EQError  # noqa: E402
from deckyeq.runtime import Runtime  # noqa: E402
from deckyeq.service import VERSION, Service  # noqa: E402
from deckyeq.storage import PresetFiles, Store  # noqa: E402

REPOSITORY = "Rayekkk/DeckyEQ"
STARTING = "DeckyEQ is still starting. Try again."
# No ready event here: Decky may start _main again on the same instance, so each start has its own gate (_wait).
rpc = dk_rpc.boundary(decky.logger)


class Plugin:
    async def _main(self):
        if getattr(self, "_main_lock", None) is None:
            self._main_lock = asyncio.Lock()
        # A second start waits for the first, then closes its service, so two never run side by side.
        async with self._main_lock:
            await self._start()

    async def _start(self):
        self._ready = asyncio.Event()
        previous, self._service = getattr(self, "_service", None), None
        self._previous = previous  # Until it has closed, an unload must still end it.
        self._startup_error = None
        try:
            previous_updates = getattr(self, "_updates", None)
            if previous_updates:
                previous_updates.close()
            home = getattr(decky, "DECKY_USER_HOME", None) or str(Path.home())
            # Installed 1.0 and 1.1 updaters accept only what this one also requires: the release workflow's
            # asset of a plugin that never asks for root.
            self._updates = dk_update.Updates(
                repo=REPOSITORY, folder="DeckyEQ", plugin_name="DeckyEQ", package_name="deckyeq",
                current=os.environ.get("DECKY_PLUGIN_VERSION") or VERSION, home=home, logger=decky.logger,
                required=(*dk_update.REQUIRED, "bin/deckyeq_dsp.so"), flags=[], uploader="github-actions[bot]")
            if previous:
                await previous.close()  # Started again on this instance: the old filter and monitor go first.
            self._previous = None
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                # Settings and presets written as root would lock the user out of them later.
                raise RuntimeError("DeckyEQ must not run as root. Reinstall it so Decky starts it as your user.")
            settings = Path(decky.DECKY_PLUGIN_SETTINGS_DIR)
            runtime_dir = Path(getattr(decky, "DECKY_PLUGIN_RUNTIME_DIR", None) or str(settings / "runtime"))
            runtime = Runtime(runtime_dir)
            try:
                # Settle a DeckyEQ 1.0 crash journal even if the settings cannot be loaded.
                await runtime.recover()
            except Exception:
                decky.logger.exception("DeckyEQ audio recovery failed")
            files = PresetFiles(home)
            if getattr(self, "_unloading", False):
                self._startup_error = "DeckyEQ is unloading."  # RPCs say so instead of failing on no service.
                return  # Decky unloaded us during recovery: start no audio.
            self._service = Service(Store(settings), runtime, files, decky.logger)
            await self._service.start()
            if getattr(self, "_unloading", False):
                await self._service.close()  # Decky unloaded us while we were starting.
                return
            decky.logger.info("DeckyEQ %s loaded pid=%s uid=%s audio=%s", VERSION, os.getpid(),
                              getattr(os, "getuid", lambda: -1)(), self._service.phase)
        except Exception as exc:
            # Shown in the panel: DeckyEQ's own messages name no path; any other error (an OSError holds one) its type.
            known = isinstance(exc, (EQError, RuntimeError))
            self._startup_error = str(exc) if known else f"DeckyEQ could not start ({type(exc).__name__}). Details are in the Decky log."
            decky.logger.exception("DeckyEQ startup failed")
        finally:
            self._ready.set()

    async def _wait(self):
        """(service, None) once started, else (None, refusal); a refusal with `starting` asks to try again soon."""
        # Decky can receive a frontend RPC before scheduling _main.
        for _ in range(100):
            if hasattr(self, "_ready"):
                try:
                    await asyncio.wait_for(self._ready.wait(), 30)
                except asyncio.TimeoutError:
                    break
                if self._startup_error:
                    return None, dk_rpc.fail(self._startup_error)
                if self._service is None:  # A second _main began meanwhile and is starting a new one.
                    break
                return self._service, None
            await asyncio.sleep(0.05)
        return None, dk_rpc.fail(STARTING, starting=True)

    @rpc
    async def get_status(self):
        service, refusal = await self._wait()
        return refusal or dk_rpc.ok(await service.status())

    @rpc
    async def perform(self, action, payload, revision):
        service, refusal = await self._wait()
        return refusal or await service.action(action, payload, revision)

    @rpc
    async def set_game(self, appid, name):
        service, refusal = await self._wait()
        return refusal or await service.set_game(appid, name)

    @rpc
    async def updates_check(self):
        updates = getattr(self, "_updates", None)
        if updates is None:
            return dk_rpc.fail(STARTING, current_version="", update_available=False, download_available=False)
        return await asyncio.to_thread(updates.check, True)  # The panel checks only when asked to.

    @rpc
    async def updates_download(self, expected_version):
        updates = getattr(self, "_updates", None)
        if updates is None:
            return dk_rpc.fail(STARTING)
        return await asyncio.to_thread(updates.download, expected_version)

    async def _unload(self):
        self._unloading = True
        updates = getattr(self, "_updates", None)
        if updates:
            updates.close()
        for service in (getattr(self, "_service", None), getattr(self, "_previous", None)):
            if service:
                service.abandon()  # Never await here: Decky 3.3 starves the loop once it closed our socket.

    async def _uninstall(self):
        # Decky runs this on every update too: end the audio like _unload; settings and presets stay.
        await self._unload()
