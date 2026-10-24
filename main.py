"""Decky entry point. The reusable EQ engine lives entirely in deckyeq/."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import decky

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from deckyeq.runtime import Runtime, environment
from deckyeq.service import VERSION, Service
from deckyeq.storage import PresetFiles, Store
from deckyeq.updates import PluginUpdates


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
            self._updates = PluginUpdates.for_plugin(PLUGIN_DIR)
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
            files = PresetFiles(environment().get("HOME") or str(Path.home()))
            if getattr(self, "_unloading", False):
                self._startup_error = "DeckyEQ is unloading."  # RPCs say so instead of failing on no service.
                return  # Decky unloaded us during recovery: start no audio.
            self._service = Service(Store(settings), runtime, files)
            await self._service.start()
            if getattr(self, "_unloading", False):
                await self._service.close()  # Decky unloaded us while we were starting.
                return
            decky.logger.info("DeckyEQ %s loaded pid=%s uid=%s audio=%s", VERSION, os.getpid(),
                              getattr(os, "getuid", lambda: -1)(), self._service.phase)
        except Exception as exc:
            self._startup_error = str(exc)
            decky.logger.exception("DeckyEQ startup failed")
        finally:
            self._ready.set()

    async def _wait(self):
        # Decky can receive a frontend RPC before scheduling _main.
        for _ in range(100):
            if hasattr(self, "_ready"):
                try:
                    await asyncio.wait_for(self._ready.wait(), 30)
                except asyncio.TimeoutError:
                    raise RuntimeError("DeckyEQ is still starting. Try again.") from None
                if self._startup_error:
                    raise RuntimeError(self._startup_error)
                if self._service is None:  # A second _main began meanwhile and is starting a new one.
                    break
                return self._service
            await asyncio.sleep(0.05)
        raise RuntimeError("DeckyEQ is still starting. Try again.")

    async def get_status(self):
        service = await self._wait()
        return await service.status()

    async def perform(self, action, payload, revision):
        service = await self._wait()
        return await service.action(action, payload, revision)

    async def set_game(self, appid, name):
        try:
            service = await self._wait()
        except RuntimeError as exc:
            if not self._startup_error:
                raise  # Still starting: the panel tries again on its next tick.
            return {"ok": False, "error": str(exc)}  # Failed for good: the panel backs off.
        return await service.set_game(appid, name)

    async def updates_check(self):
        updates = getattr(self, "_updates", None)
        if updates is None:
            return {"success": False, "current_version": "", "update_available": False,
                    "download_available": False, "error": "DeckyEQ is still starting. Try again."}
        return await asyncio.to_thread(updates.check)

    async def updates_download(self, expected_version):
        updates = getattr(self, "_updates", None)
        if updates is None:
            return {"success": False, "error": "DeckyEQ is still starting. Try again."}
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
        await self._unload()
