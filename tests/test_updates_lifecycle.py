"""Exercise the real Decky RPC entry points without an audio session or network."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parent.parent


class UpdateLifecycle(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        stub = SimpleNamespace(DECKY_PLUGIN_SETTINGS_DIR=str(ROOT / "unused-test-settings"), logger=Mock())
        spec = importlib.util.spec_from_file_location("deckyeq_test_entry", ROOT / "main.py")
        self.entry = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, decky=stub):
            spec.loader.exec_module(self.entry)
        self.plugin = self.entry.Plugin()

    async def test_requests_before_start_are_retryable(self):
        self.assertFalse((await self.plugin.updates_check())["success"])
        self.assertFalse((await self.plugin.updates_download("1.0.1"))["success"])
        await self.plugin._unload()

    async def test_unload_during_startup_still_closes_the_audio_service(self):
        gate = asyncio.Event()
        async def slow_start(): await gate.wait()
        service = Mock(start=AsyncMock(side_effect=slow_start), close=AsyncMock(), phase="off")
        with patch.object(self.entry, "Store"), patch.object(self.entry, "Runtime", return_value=Mock(recover=AsyncMock())),                 patch.object(self.entry, "PresetFiles"), patch.object(self.entry, "Service", return_value=service):
            main = asyncio.create_task(self.plugin._main())
            await asyncio.sleep(0.01)
            await self.plugin._unload()  # Arrives while _main is still starting the service.
            gate.set()
            await main
        service.close.assert_awaited()

    async def test_checks_remain_available_after_audio_startup_failure(self):
        updater = self.entry.PluginUpdates.for_plugin(ROOT)
        service = Mock(start=AsyncMock(side_effect=RuntimeError("audio unavailable")), close=AsyncMock())
        with patch.object(self.entry.PluginUpdates, "for_plugin", return_value=updater), \
                patch.object(self.entry, "Store"), patch.object(self.entry, "Runtime"), \
                patch.object(self.entry, "PresetFiles"), patch.object(self.entry, "Service", return_value=service), \
                patch.object(updater, "_metadata") as metadata:
            await self.plugin._main()
            metadata.assert_not_called()
            self.assertEqual(self.plugin._startup_error, "audio unavailable")
            metadata.return_value = {"tag_name": "v1.0.0", "draft": False, "prerelease": False,
                                     "html_url": "https://github.com/Rayekkk/DeckyEQ/releases/tag/v1.0.0", "assets": []}
            with patch.dict("os.environ", DECKY_PLUGIN_VERSION=""):
                result = await self.plugin.updates_check()
            self.assertTrue(result["success"])
            self.assertEqual(result["current_version"], json.loads((ROOT / "package.json").read_text())["version"])
            self.assertFalse(result["download_available"])
        await self.plugin._unload()
        self.assertTrue(updater._closed.is_set())

    async def test_slow_network_does_not_block_audio_rpc_or_unload(self):
        started, release = threading.Event(), threading.Event()
        def check():
            started.set()
            release.wait(3)
            return {"success": True}
        updater = Mock(check=check)
        self.plugin._updates = updater
        self.plugin._service = Mock(status=AsyncMock(return_value={"audio": "ready"}), close=AsyncMock())
        self.plugin._ready = asyncio.Event()
        self.plugin._ready.set()
        self.plugin._startup_error = None
        task = asyncio.create_task(self.plugin.updates_check())
        try:
            await asyncio.wait_for(asyncio.to_thread(started.wait, 1), 2)
            self.assertTrue(started.is_set())
            self.assertEqual(await asyncio.wait_for(self.plugin.get_status(), 1), {"audio": "ready"})
            await asyncio.wait_for(self.plugin._unload(), 1)
            updater.close.assert_called_once()
        finally:
            release.set()
            await task

    async def test_download_rpc_forwards_only_the_expected_version(self):
        self.plugin._updates = Mock(download=Mock(return_value={"success": True, "version": "1.0.1"}))
        result = await self.plugin.updates_download("1.0.1")
        self.assertEqual(result["version"], "1.0.1")
        self.plugin._updates.download.assert_called_once_with("1.0.1")

    def test_release_identity_and_license_match_public_metadata(self):
        manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], package["version"])
        self.assertEqual(package["license"], "BSD-3-Clause")
        self.assertEqual(manifest["flags"], [])
        self.assertIn("Copyright (c) 2026, Rayekkk", (ROOT / "LICENSE").read_text())
