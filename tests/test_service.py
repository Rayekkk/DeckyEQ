import asyncio
import copy
import json
import tempfile
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
    async def scan(self): return self.outputs, self.default
    async def start(self, eq, target):
        self.events.append("start")
        self.process, self.target = object(), target
        self.default = SINK_NAME
        self.eq = copy.deepcopy(eq)
    async def apply(self, eq):
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

    async def test_stale_revision_cannot_overwrite_newer_draft(self):
        await self.act("save_as", name="New")
        result = await self.service.action("edit", {"eq": flat_eq()}, 0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"]["presets"][-1]["name"], "New")

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
        self.assertFalse(self.runtime.running)
        self.assertEqual(self.runtime.default, "bluez_output.headset")
        self.assertEqual(self.service.phase, "bypassed")

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
