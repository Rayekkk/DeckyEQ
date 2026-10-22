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
        self._ready = asyncio.Event()
        self._service = None
        self._startup_error = None
        try:
            previous_updates = getattr(self, "_updates", None)
            if previous_updates:
                previous_updates.close()
            self._updates = PluginUpdates.for_plugin(PLUGIN_DIR)
            settings = Path(decky.DECKY_PLUGIN_SETTINGS_DIR)
            runtime_dir = Path(getattr(decky, "DECKY_PLUGIN_RUNTIME_DIR", None) or str(settings / "runtime"))
            runtime = Runtime(runtime_dir)
            try:
                # Restore a volume left at 100% by a crash even if the settings cannot be loaded.
                await runtime.recover()
            except Exception:
                decky.logger.exception("DeckyEQ audio recovery failed")
            files = PresetFiles(environment().get("HOME") or str(Path.home()))
            self._service = Service(Store(settings), runtime, files)
            await self._service.start()
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
                await asyncio.wait_for(self._ready.wait(), 15)
                if self._startup_error:
                    raise RuntimeError(self._startup_error)
                return self._service
            await asyncio.sleep(0.05)
        raise RuntimeError("DeckyEQ is still starting. Try again.")

    async def get_status(self):
        service = await self._wait()
        return await service.status()

    async def perform(self, action, payload, revision):
        service = await self._wait()
        return await service.action(action, payload, revision)

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
        updates = getattr(self, "_updates", None)
        if updates:
            updates.close()
        service = getattr(self, "_service", None)
        if service:
            await service.close()

    async def _uninstall(self):
        await self._unload()
