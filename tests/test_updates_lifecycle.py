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
        self.assertFalse((await self.plugin.updates_check())["ok"])
        self.assertFalse((await self.plugin.updates_download("1.0.1"))["ok"])
        await self.plugin._unload()

    async def test_a_request_while_a_second_start_replaces_the_service_is_still_starting(self):
        self.plugin._ready = asyncio.Event()
        self.plugin._ready.set()  # The first start's, already set; the second has not made its service yet.
        self.plugin._startup_error, self.plugin._service = None, None
        with patch.object(self.entry.asyncio, "sleep", AsyncMock()):  # The 100 short waits for the new service.
            for answer in (await self.plugin.get_status(), await self.plugin.set_game("7", "Celeste")):
                # An answer, never an exception; `starting` asks the panel to try again on its next tick.
                self.assertEqual(answer, {"ok": False, "error": "DeckyEQ is still starting. Try again.", "starting": True})

    async def test_unload_finishes_without_ever_yielding_to_the_event_loop(self):
        # Decky 3.3 busy-loops once it closed the plugin's socket: an _unload that awaits is SIGKILLed.
        async def slow(): await asyncio.sleep(0.01)  # A real close waits for its tools.
        self.plugin._service = service = Mock(close=AsyncMock(side_effect=slow))
        with self.assertRaises(StopIteration):
            self.plugin._unload().send(None)  # Runs to the end on the first step, never suspended.
        service.abandon.assert_called_once()
        service.close.assert_not_awaited()

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

    async def test_unload_during_recovery_starts_no_audio(self):
        gate = asyncio.Event()
        with patch.object(self.entry, "Store"), patch.object(self.entry, "PresetFiles"), \
                patch.object(self.entry, "Runtime", return_value=Mock(recover=AsyncMock(side_effect=gate.wait))), \
                patch.object(self.entry, "Service") as service:
            main = asyncio.create_task(self.plugin._main())
            await asyncio.sleep(0.01)
            await self.plugin._unload()  # Arrives while a DeckyEQ 1.0 journal is being settled.
            gate.set()
            await main
        service.assert_not_called()  # Not started and then ramped off again.
        # A clear reason, not an AttributeError on a missing service.
        self.assertEqual(await self.plugin.get_status(), {"ok": False, "error": "DeckyEQ is unloading."})
        self.assertEqual(await self.plugin.set_game("7", "Celeste"), {"ok": False, "error": "DeckyEQ is unloading."})

    async def test_starting_again_closes_the_previous_audio_service_first(self):
        order = []
        def service(name):
            return Mock(start=AsyncMock(side_effect=lambda: order.append(name + " start")),
                        close=AsyncMock(side_effect=lambda: order.append(name + " close")), phase="off")
        with patch.object(self.entry, "Store"), patch.object(self.entry, "PresetFiles"), \
                patch.object(self.entry, "Runtime", return_value=Mock(recover=AsyncMock())), \
                patch.object(self.entry, "Service", side_effect=[service("old"), service("new")]):
            await self.plugin._main()
            await self.plugin._main()  # Its filter and monitor would otherwise outlive it.
        self.assertEqual(order, ["old start", "old close", "new start"])
        await self.plugin._unload()

    async def test_unload_while_a_restart_closes_the_old_service_still_ends_it(self):
        gate = asyncio.Event()
        old = Mock(start=AsyncMock(), close=AsyncMock(side_effect=gate.wait), phase="off")
        with patch.object(self.entry, "Store"), patch.object(self.entry, "PresetFiles"), \
                patch.object(self.entry, "Runtime", return_value=Mock(recover=AsyncMock())), \
                patch.object(self.entry, "Service", side_effect=[old, Mock()]) as service:
            await self.plugin._main()
            second = asyncio.create_task(self.plugin._main())
            await asyncio.sleep(0.01)  # Closing the old service; Decky 3.3 would never let that finish.
            await self.plugin._unload()
            old.abandon.assert_called_once()
            gate.set()
            await second
        self.assertEqual(service.call_count, 1)  # And nothing new starts.

    async def test_a_start_during_a_start_waits_and_then_replaces_it(self):
        order, gate = [], asyncio.Event()
        def service(name):
            return Mock(start=AsyncMock(side_effect=lambda: order.append(name + " start")),
                        close=AsyncMock(side_effect=lambda: order.append(name + " close")), phase="off")
        with patch.object(self.entry, "Store"), patch.object(self.entry, "PresetFiles"),                 patch.object(self.entry, "Runtime", return_value=Mock(recover=AsyncMock(side_effect=gate.wait))),                 patch.object(self.entry, "Service", side_effect=[service("old"), service("new")]):
            first = asyncio.create_task(self.plugin._main())
            await asyncio.sleep(0.01)
            second = asyncio.create_task(self.plugin._main())  # Arrives while the first settles a journal.
            await asyncio.sleep(0.01)
            gate.set()
            await asyncio.gather(first, second)
        self.assertEqual(order, ["old start", "old close", "new start"])  # Never two filters and monitors.
        await self.plugin._unload()

    async def test_refuses_to_run_as_root(self):
        # Settings and presets written by root would later be unreadable for the user.
        with patch.object(self.entry.os, "geteuid", create=True, return_value=0),                 patch.object(self.entry, "Store") as store, patch.object(self.entry, "PresetFiles"),                 patch.object(self.entry, "Runtime") as runtime, patch.object(self.entry, "Service") as service:
            await self.plugin._main()
        store.assert_not_called()
        runtime.assert_not_called()
        service.assert_not_called()
        self.assertIn("must not run as root", (await self.plugin.get_status())["error"])
        self.assertFalse((await self.plugin.set_game("7", "Celeste"))["ok"])
        await self.plugin._unload()

    async def test_a_startup_error_shown_in_the_panel_names_no_path(self):
        with patch.object(self.entry, "Runtime", side_effect=PermissionError(13, "Permission denied", "/home/deck/x")), \
                patch.object(self.entry, "Store"), patch.object(self.entry, "PresetFiles"):
            await self.plugin._main()
        error = (await self.plugin.get_status())["error"]
        self.assertEqual(error, "DeckyEQ could not start (PermissionError). Details are in the Decky log.")
        await self.plugin._unload()

    async def test_checks_remain_available_after_audio_startup_failure(self):
        service = Mock(start=AsyncMock(side_effect=RuntimeError("audio unavailable")), close=AsyncMock())
        with patch.object(self.entry, "Store"), patch.object(self.entry, "Runtime"), \
                patch.object(self.entry, "PresetFiles"), patch.object(self.entry, "Service", return_value=service), \
                patch.dict("os.environ", DECKY_PLUGIN_VERSION=""), patch("dk_net.get") as get:
            await self.plugin._main()
            get.assert_not_called()  # Starting asks GitHub nothing.
        self.assertEqual(self.plugin._startup_error, "audio unavailable")
        updater = self.plugin._updates
        latest = {"tag_name": "1.0.0", "draft": False, "prerelease": False,
                  "html_url": "https://github.com/Rayekkk/DeckyEQ/releases/tag/1.0.0", "assets": []}
        with patch.object(updater, "_fetch_release", return_value=latest):
            result = await self.plugin.updates_check()
        self.assertTrue(result["ok"])
        self.assertEqual(result["current_version"], json.loads((ROOT / "package.json").read_text())["version"])
        self.assertFalse(result["download_available"])
        await self.plugin._unload()
        self.assertTrue(updater._closed.is_set())

    def test_the_updater_accepts_only_what_installed_updaters_accept(self):
        # DeckyEQ 1.0 and 1.1 refuse a release with flags or one uploaded by hand: so does this one.
        stub = self.entry.decky
        with patch.object(self.entry, "Store"), patch.object(self.entry, "Runtime"), \
                patch.object(self.entry, "PresetFiles"), patch.object(self.entry, "Service"):
            asyncio.run(self.plugin._main())
        updater = self.plugin._updates
        self.assertEqual((updater.repo, updater.folder, updater.plugin_name, updater.package_name),
                         ("Rayekkk/DeckyEQ", "DeckyEQ", "DeckyEQ", "deckyeq"))
        self.assertEqual((updater.flags, updater.uploader), ([], "github-actions[bot]"))
        self.assertIn("bin/deckyeq_dsp.so", updater.required)
        self.assertIs(stub.logger, updater.logger)

    async def test_slow_network_does_not_block_audio_rpc_or_unload(self):
        started, release = threading.Event(), threading.Event()
        def check(click=False):
            started.set()
            release.wait(3)
            return {"ok": True}
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
            self.assertEqual(await asyncio.wait_for(self.plugin.get_status(), 1), {"ok": True, "state": {"audio": "ready"}})
            await asyncio.wait_for(self.plugin._unload(), 1)
            updater.close.assert_called_once()
        finally:
            release.set()
            await task

    async def test_download_rpc_forwards_only_the_expected_version(self):
        self.plugin._updates = Mock(download=Mock(return_value={"ok": True, "version": "1.0.1"}))
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
