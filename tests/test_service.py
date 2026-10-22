import asyncio
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from deckyeq.graph import SINK_NAME
from deckyeq.model import EQError, flat_eq, export_document, initial_state
from deckyeq.service import Service
from deckyeq.storage import Store, PresetFiles


class FakeRuntime:
    def __init__(self):
        self.supported, self.reason = True, None
        self.outputs = [{"name": "alsa_output.speakers", "description": "Speakers", "speaker": True, "port": "analog-output-speaker"},
                        {"name": "bluez_output.headset", "description": "Bluetooth", "speaker": False, "port": ""}]
        self.default = "alsa_output.speakers"
        self.process = self.target = self.last_controls = None
        self.eq = None
        self.events = []
        self.fail_next = False
        self.gate = None

    @property
    def running(self): return self.process is not None
    async def recover(self): self.events.append("recover")
    async def wait_for_change(self, timeout):
        await asyncio.sleep(1.5)
        return True
    async def close_events(self): self.events.append("close_events")
    async def scan(self): return self.outputs, self.default
    async def start(self, eq, target):
        self.events.append("start")
        self.process, self.target = object(), target
        self.default = SINK_NAME
        self.eq = copy.deepcopy(eq)
    async def apply(self, eq, max_age=0.0):
        self.events.append("apply")
        self.eq = copy.deepcopy(eq)
        if self.gate: await self.gate.wait()
        if self.fail_next:
            self.fail_next = False
            raise EQError("Simulated readback mismatch")
    async def stop(self):
        self.events.append("stop")
        self.process = self.target = None
        if self.default == SINK_NAME: self.default = "alsa_output.speakers"


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.store, self.runtime = Store(self.home / "settings"), FakeRuntime()
        self.service = Service(self.store, self.runtime, PresetFiles(self.home))
        await self.service.start()
        self.addAsyncCleanup(self.service.close)
        self.addCleanup(self.temp.cleanup)

    async def act(self, action, **payload):
        return await self.service.action(action, payload, self.service.revision)

    async def test_enable_live_edit_and_disable_preserves_library(self):
        self.assertTrue((await self.act("enable", enabled=True))["ok"])
        process = self.runtime.process
        eq = flat_eq()
        eq["bass"] = 4
        self.assertTrue((await self.act("edit", eq=eq))["ok"])
        self.assertIs(self.runtime.process, process)
        self.assertEqual(self.runtime.eq["bass"], 4)
        await self.act("save_as", name="Warm")
        await self.act("enable", enabled=False)
        self.assertEqual(self.store.load()["presets"][0]["eq"]["bass"], 4)
        self.assertIsNone(self.runtime.process)

    async def test_disk_error_rolls_back_live_audio_and_settings(self):
        await self.act("enable", enabled=True)
        old = copy.deepcopy(self.service.data)
        eq = flat_eq()
        eq["treble"] = 5
        with patch.object(self.store, "save", side_effect=OSError("Disk full")):
            result = await self.act("edit", eq=eq)
        self.assertFalse(result["ok"])
        self.assertEqual(self.service.data, old)
        self.assertEqual(self.runtime.eq, old["working"])

    async def test_partial_audio_failure_rolls_back_without_save(self):
        await self.act("enable", enabled=True)
        old = copy.deepcopy(self.service.data)
        self.runtime.fail_next = True
        eq = flat_eq()
        eq["bass"] = 6
        result = await self.act("edit", eq=eq)
        self.assertFalse(result["ok"])
        self.assertEqual(self.service.data, old)
        self.assertEqual(self.store.load(), old)
        self.assertEqual(self.runtime.eq, old["working"])

    async def test_unexpected_audio_error_still_rolls_back(self):
        await self.act("enable", enabled=True)
        old = copy.deepcopy(self.service.data)
        original, calls = self.runtime.apply, []
        async def flaky(eq, max_age=0.0):
            calls.append(eq)
            if len(calls) == 1: raise KeyError("odd node data")
            await original(eq, max_age)
        eq = flat_eq()
        eq["bass"] = 6
        with patch.object(self.runtime, "apply", side_effect=flaky):
            result = await self.act("edit", eq=eq)
        self.assertFalse(result["ok"])
        self.assertEqual(self.service.data, old)
        self.assertEqual(self.runtime.eq, old["working"])

    async def test_stale_revision_cannot_overwrite_newer_draft(self):
        await self.act("save_as", name="New")
        result = await self.service.action("edit", {"eq": flat_eq()}, 0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"]["presets"][-1]["name"], "New")
        self.assertNotIn("presets", result["state"]["state"])

    async def test_headphone_port_bypasses_then_speakers_resume(self):
        await self.act("enable", enabled=True)
        self.runtime.outputs[0]["speaker"] = False
        await self.service._sync(self.service.data)
        self.assertEqual(self.service.phase, "bypassed")
        self.assertIsNone(self.runtime.process)
        self.assertTrue(self.service.data["enabled"])
        self.runtime.outputs[0]["speaker"] = True
        await self.service._sync(self.service.data)
        self.assertTrue(self.runtime.running)

    async def test_manual_output_switch_not_stolen_by_edits(self):
        await self.act("enable", enabled=True)
        self.runtime.default = "bluez_output.headset"
        eq = flat_eq()
        eq["treble"] = 3
        await self.act("edit", eq=eq)
        self.assertEqual(self.runtime.default, "bluez_output.headset")
        await self.service._sync(self.service.data)  # The monitor handles the output-change event.
        self.assertFalse(self.runtime.running)
        self.assertEqual(self.runtime.default, "bluez_output.headset")
        self.assertEqual(self.service.phase, "bypassed")

    async def test_selecting_hardware_in_steam_bypasses_instead_of_playing_at_full_volume(self):
        await self.act("enable", enabled=True)
        self.runtime.default = "alsa_output.speakers"
        await self.service._sync(self.service.data)
        self.assertEqual(self.service.phase, "bypassed")
        self.assertIsNone(self.runtime.process)
        await self.service._sync(self.service.data)
        self.assertEqual(self.runtime.events.count("start"), 1)
        self.runtime.default = "bluez_output.headset"
        await self.service._sync(self.service.data)
        self.runtime.default = "alsa_output.speakers"
        await self.service._sync(self.service.data)
        self.assertEqual(self.service.phase, "active")
        self.assertEqual(self.runtime.default, SINK_NAME)

    async def test_refresh_reroutes_after_hardware_was_selected_in_steam(self):
        await self.act("enable", enabled=True)
        self.runtime.default = "alsa_output.speakers"
        self.assertTrue((await self.act("refresh"))["ok"])
        self.assertEqual(self.runtime.events.count("start"), 2)
        self.assertEqual(self.runtime.default, SINK_NAME)
        self.assertEqual(self.service.phase, "active")

    async def test_enabling_while_bluetooth_is_selected_waits_instead_of_taking_playback(self):
        self.runtime.default = "bluez_output.headset"
        self.assertTrue((await self.act("enable", enabled=True))["ok"])
        self.assertNotIn("start", self.runtime.events)
        self.assertEqual(self.runtime.default, "bluez_output.headset")
        self.assertEqual(self.service.phase, "bypassed")
        self.assertTrue((await self.act("refresh"))["ok"])
        self.assertEqual(self.runtime.default, SINK_NAME)

    async def test_disabled_monitor_does_not_poll_audio_tools(self):
        with patch.object(self.runtime, "scan", side_effect=AssertionError("scanned while off")):
            await self.service._sync(self.service.data)
        self.assertEqual(self.service.phase, "off")
        with patch.object(self.runtime, "scan", wraps=self.runtime.scan) as scan:
            await self.service.status()
        scan.assert_not_awaited()  # Audio events refresh the output list instead.

    async def test_slider_edit_skips_output_rescan(self):
        await self.act("enable", enabled=True)
        eq = flat_eq()
        eq["bass"] = 2
        with patch.object(self.runtime, "scan", side_effect=AssertionError("rescanned")), \
                patch.object(self.store, "save", wraps=self.store.save) as save:
            self.assertTrue((await self.act("edit", eq=eq))["ok"])
        self.assertEqual(self.runtime.eq["bass"], 2)
        self.assertFalse(save.call_args.kwargs["backup"])

    async def test_repeated_failures_back_off_up_to_a_minute(self):
        delays = []
        for _ in range(6):
            self.service._failed(EQError("no audio"))
            delays.append(round(self.service.retry_at - time.monotonic()))
        self.assertEqual(delays, [8, 16, 32, 60, 60, 60])
        await self.act("enable", enabled=True)
        self.assertEqual(self.service.failures, 0)

    async def test_unexpected_monitor_error_pauses_audio_without_stopping_monitoring(self):
        await self.act("enable", enabled=True)
        real_sleep = asyncio.sleep
        async def tick(_): await real_sleep(0)
        with patch.object(self.runtime, "scan", side_effect=KeyError("index")),                 patch.object(self.runtime, "stop", side_effect=AttributeError("stop failed too")),                 patch("deckyeq.service.asyncio.sleep", side_effect=tick):
            self.service.monitor.cancel()
            self.service.monitor = asyncio.create_task(self.service._watch())
            for _ in range(50):
                await real_sleep(0)
                if self.service.phase == "error": break
        self.assertEqual(self.service.phase, "error")
        self.assertFalse(self.service.monitor.done())

    async def test_close_during_startup_leaves_no_monitor_or_routing(self):
        service = Service(self.store, FakeRuntime(), PresetFiles(self.home))
        service.data["enabled"] = True
        service.data["target"] = "alsa_output.speakers"
        gate = asyncio.Event()
        service.runtime.recover = gate.wait
        start = asyncio.create_task(service.start())
        await asyncio.sleep(0)
        close = asyncio.create_task(service.close())
        await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(start, close)
        self.assertIsNone(service.monitor)
        self.assertIsNone(service.runtime.process)

    async def test_output_disconnection_waits_without_retargeting(self):
        await self.act("enable", enabled=True)
        self.runtime.outputs = self.runtime.outputs[1:]
        await self.service._sync(self.service.data)
        self.assertEqual(self.service.phase, "waiting")
        self.assertEqual(self.service.data["target"], "alsa_output.speakers")

    async def test_cancelled_rpc_finishes_transaction_before_unlock(self):
        await self.act("enable", enabled=True)
        self.runtime.gate = asyncio.Event()
        eq = flat_eq()
        eq["bass"] = 2
        task = asyncio.create_task(self.act("edit", eq=eq))
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        self.assertTrue(self.service.lock.locked())
        self.runtime.gate.set()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(self.store.load()["working"]["bass"], 2)
        self.assertFalse(self.service.lock.locked())

    async def test_repeated_cancellation_still_finishes_the_transaction(self):
        await self.act("enable", enabled=True)
        self.runtime.gate = asyncio.Event()
        eq = flat_eq()
        eq["bass"] = 3
        task = asyncio.create_task(self.act("edit", eq=eq))
        for _ in range(3):
            await asyncio.sleep(0.01)
            task.cancel()
        self.runtime.gate.set()
        with self.assertRaises(asyncio.CancelledError): await task
        await asyncio.sleep(0.01)
        self.assertEqual(self.service.data["working"]["bass"], 3)
        self.assertEqual(self.store.load()["working"]["bass"], 3)

    async def test_status_answers_while_a_transaction_holds_the_lock(self):
        async with self.service.lock:
            self.assertEqual((await asyncio.wait_for(self.service.status(), 0.5))["revision"], self.service.revision)

    async def test_monitor_pauses_after_unexpected_errors(self):
        real_sleep, pauses = asyncio.sleep, []
        async def tick(seconds):
            pauses.append(seconds)
            await real_sleep(0)
        with patch.object(self.runtime, "wait_for_change", side_effect=RuntimeError("event stream broke")), \
                patch("deckyeq.service.asyncio.sleep", side_effect=tick):
            self.service.monitor.cancel()
            self.service.monitor = asyncio.create_task(self.service._watch())
            for _ in range(20):
                await real_sleep(0)
        self.assertIn(2, pauses)

    async def test_import_is_atomic_and_does_not_switch_playback(self):
        document = {"format": "deckyeq-bank", "version": 2, "presets": [
            {"name": "Good", "eq": flat_eq()}, {"name": "Bad", "eq": {}}]}
        path = self.home / "bank.json"
        path.write_text(json.dumps(document))
        result = await self.act("import", path=str(path))
        self.assertFalse(result["ok"])
        self.assertEqual(self.service.data["presets"], [])
        path.write_text(json.dumps(export_document(initial_state())))
        result = await self.act("import", path=str(path))
        self.assertTrue(result["ok"])
        self.assertEqual(self.service.data["selected"], "flat")
        self.assertEqual(len(self.service.data["presets"]), 1)

    async def test_preset_save_revert_rename_delete(self):
        await self.act("save_as", name="Test")
        eq = flat_eq()
        eq["bands"][2]["gain"] = -2
        await self.act("edit", eq=eq)
        self.assertTrue(self.service.snapshot()["dirty"])
        await self.act("save")
        self.assertFalse(self.service.snapshot()["dirty"])
        await self.act("reset")
        await self.act("revert")
        self.assertEqual(self.service.data["working"], eq)
        await self.act("rename", name="Renamed")
        self.assertEqual(self.service.data["presets"][0]["name"], "Renamed")
        await self.act("delete")
        self.assertEqual(self.service.data["selected"], "flat")
        self.assertEqual(self.service.data["working"], flat_eq())

    async def test_confirmation_for_another_preset_is_rejected(self):
        await self.act("save_as", name="Keep me")
        kept = self.service.data["selected"]
        result = await self.act("delete", id="flat")
        self.assertFalse(result["ok"])
        self.assertEqual([p["id"] for p in self.service.data["presets"]], [kept])
        self.assertTrue((await self.act("delete", id=kept))["ok"])

    async def test_flat_cannot_be_overwritten_renamed_or_deleted(self):
        for action in ("save", "rename", "delete"):
            self.assertFalse((await self.act(action, name="Anything"))["ok"])

    async def test_go_s_factory_can_be_copied_but_not_replaced(self):
        from deckyeq.model import go_s_eq
        await self.act("select", id="go-s-original")
        self.assertEqual(self.service.data["working"], go_s_eq())
        for action in ("save", "rename", "delete"):
            self.assertFalse((await self.act(action, name="Anything"))["ok"])
        await self.act("enable", enabled=True)
        process = self.runtime.process
        draft = go_s_eq()
        draft["bands"][5].update(type="lowshelf", gain=-2)
        self.assertTrue((await self.act("edit", eq=draft))["ok"])
        self.assertIs(self.runtime.process, process)
        await self.act("save_as", name="My Go S")
        self.assertEqual(self.service.data["presets"][0]["eq"], draft)
        await self.act("select", id="go-s-original")
        self.assertEqual(self.service.data["working"], go_s_eq())

    async def test_offline_editing_presets_remains_available(self):
        self.runtime.supported, self.runtime.reason = False, "No PipeWire"
        eq = flat_eq()
        eq["treble"] = 2
        self.assertTrue((await self.act("edit", eq=eq))["ok"])
        self.assertTrue((await self.act("save_as", name="Offline"))["ok"])
        self.assertFalse((await self.act("enable", enabled=True))["ok"])

    async def test_export_includes_latest_draft(self):
        eq = flat_eq()
        eq["bass"] = 3
        await self.act("edit", eq=eq)
        result = await self.act("export", all=False)
        self.assertTrue(result["ok"])
        self.assertEqual(json.loads(Path(result["path"]).read_text())["preset"]["eq"]["bass"], 3)


if __name__ == "__main__": unittest.main()
