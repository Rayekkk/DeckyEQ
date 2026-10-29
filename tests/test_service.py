import asyncio
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from deckyeq.graph import SINK_NAME
from deckyeq.model import EQError, NodeGone, OutputGone, ToolError, create_preset, flat_eq, export_document, initial_state, selected_preset
from deckyeq.runtime import private
from deckyeq.service import TOOL_RETRIES, Service
from deckyeq.storage import Store, PresetFiles

REAL_SLEEP = asyncio.sleep  # Tests patch asyncio.sleep (through deckyeq.service) to run the monitor fast.


async def quiet(timeout):
    """An audio session without events: only a short (owed) wait ends."""
    await REAL_SLEEP(timeout if timeout < 1 else 3600)
    return False


class FakeRuntime:
    def __init__(self):
        self.supported, self.reason = True, None
        self.outputs = [{"name": "alsa_output.speakers", "description": "Speakers", "speaker": True, "port": "analog-output-speaker"},
                        {"name": "bluez_output.headset", "description": "Bluetooth", "speaker": False, "port": ""}]
        self.default = "alsa_output.speakers"
        self.process = self.target = self.last_controls = None
        self.log = ""  # The filter's stderr.
        self.eq = None
        self.events = []
        self.fail_next = False
        self.gate = None

    @property
    def running(self): return self.process is not None and self.process.returncode is None
    async def recover(self): self.events.append("recover")
    async def wait_for_change(self, timeout):
        await asyncio.sleep(1.5)
        return True
    async def close_events(self): self.events.append("close_events")
    def abandon(self): self.events.append("abandon")
    async def scan(self): return self.outputs, self.default
    async def start(self, eq, target, boost=0.0, speakers_only=False):
        self.events.append("start")
        self.process, self.target = SimpleNamespace(returncode=None), target
        self.eq, self.boost, self.started_at = copy.deepcopy(eq), boost, time.monotonic()
    async def apply(self, eq, max_age=0.0, boost=0.0):
        self.events.append("apply")
        self.eq, self.boost = copy.deepcopy(eq), boost
        if self.gate: await self.gate.wait()
        if self.fail_next:
            failure, self.fail_next = self.fail_next, False
            raise failure if isinstance(failure, Exception) else EQError("Simulated readback mismatch")
    async def stop(self, mute=False):
        self.events.append("stop")
        self.process = self.target = None
        self.muted = mute


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
        with patch.object(self.store, "save", side_effect=OSError("Disk full")):
            result = await self.act("select", id="go-s-original")  # Saved at once, unlike drafts.
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
        self.quiet_monitor()
        old = copy.deepcopy(self.service.data)
        original, calls = self.runtime.apply, []
        async def flaky(eq, max_age=0.0, boost=0.0):
            calls.append(eq)
            await original(eq, max_age, boost)  # The node took the change, then its readback was odd.
            if len(calls) == 1: raise KeyError("odd node data")
        eq = flat_eq()
        eq["bass"] = 6
        with patch.object(self.runtime, "apply", side_effect=flaky):
            result = await self.act("edit", eq=eq)
        self.assertFalse(result["ok"])
        self.assertEqual(calls, [eq, old["working"]])  # Rolled back at once, through the running filter.
        self.assertEqual(self.service.data, old)
        self.assertEqual(self.runtime.eq, old["working"])
        self.assertEqual((self.service.phase, self.service.error), ("active", None))
        self.assertNotIn("stop", self.runtime.events)

    def quiet_monitor(self):
        """Restarts the monitor without audio events: it acts only when a retry is owed."""
        patcher = patch.object(self.runtime, "wait_for_change", side_effect=quiet)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.service.monitor.cancel()
        self.service.monitor = asyncio.create_task(self.service._watch())

    async def test_a_tool_timeout_in_a_game_switch_keeps_the_eq_and_the_monitor_heals_it(self):
        # pw-dump timing out while a game loads: the switch is refused, the everyday EQ keeps playing.
        await self.act("enable", enabled=True)
        await self.service.set_game("1145360", "Hades")
        game = flat_eq()
        game["treble"] = 6
        await self.act("own_sound", enabled=True, appid="1145360")
        await self.act("edit", eq=game)
        await self.service.set_game(None, "")
        everyday, process = copy.deepcopy(self.service.data), self.runtime.process
        self.quiet_monitor()
        original, calls = self.runtime.apply, []
        async def timing_out(eq, max_age=0.0, boost=0.0):
            calls.append(eq)
            await original(eq, max_age, boost)  # The node may take part of the change before the readback.
            if len(calls) <= 2:  # Still timing out right after.
                raise ToolError("pw-dump: timed out")
        with patch.object(self.runtime, "apply", side_effect=timing_out),                 patch("deckyeq.service.ERROR_PAUSE_SECONDS", 0):
            result = await self.service.set_game("1145360", "Hades")
            self.assertEqual((result["ok"], result["error"]), (False, "pw-dump: timed out"))
            self.assertEqual(len(calls), 1)  # No second timeout under the lock for a rollback.
            self.assertEqual(self.service.data, everyday)
            self.assertEqual((self.service.phase, self.service.error, self.service.retry_at), ("active", None, 0))
            self.assertIs(self.runtime.process, process)
            deadline = time.monotonic() + 5
            while self.runtime.eq != everyday["working"] or self.service.owed:  # The monitor retries.
                self.assertLess(time.monotonic(), deadline, "the monitor never restored the sound")
                await REAL_SLEEP(0.05)
        self.assertEqual(len(calls), 3)
        self.assertEqual((self.service.phase, self.service.error), ("active", None))
        self.assertNotIn("stop", self.runtime.events)
        self.assertTrue((await self.service.set_game("1145360", "Hades"))["ok"])  # The panel's retry.
        self.assertEqual(self.runtime.eq, game)

    async def test_a_rollback_that_times_out_keeps_the_eq_for_the_monitor(self):
        await self.act("enable", enabled=True)
        process = self.runtime.process
        self.quiet_monitor()
        failures = iter([EQError("Simulated readback mismatch"), ToolError("pw-dump: timed out")])
        original = self.runtime.apply
        async def apply(eq, max_age=0.0, boost=0.0):
            await original(eq, max_age, boost)
            failure = next(failures, None)
            if failure: raise failure
        eq = flat_eq()
        eq["bass"] = 6
        with patch.object(self.runtime, "apply", side_effect=apply):
            self.assertFalse((await self.act("edit", eq=eq))["ok"])
        self.assertIs(self.runtime.process, process)
        self.assertEqual((self.service.phase, self.service.retry_at), ("active", 0))
        self.assertTrue(self.service.owed)

    async def test_messages_name_the_panel_controls(self):
        self.assertEqual((await self.act("save"))["error"],
                         "Built-in presets are read-only. Use Save as new… to keep your changes.")
        self.assertEqual((await self.act("select", id="0" * 32))["error"], "Preset no longer exists. Try again.")
        self.runtime.outputs = []
        self.assertEqual((await self.act("enable", enabled=True))["error"],
                         "Choose a connected audio output in Output first.")
        self.service._failed(EQError("no audio"))
        self.assertEqual(self.service.message, "Audio changes are paused. Check your output, then use Reconnect EQ.")

    async def test_a_tool_timeout_in_a_reconnect_keeps_the_eq_running(self):
        await self.act("enable", enabled=True)
        process = self.runtime.process
        self.quiet_monitor()
        with patch.object(self.runtime, "scan", side_effect=ToolError("pactl: timed out")):
            result = await self.act("refresh")
        self.assertEqual((result["ok"], result["error"]), (False, "pactl: timed out"))
        self.assertIs(self.runtime.process, process)
        self.assertEqual((self.service.phase, self.service.retry_at), ("active", 0))
        self.assertTrue(self.service.owed)  # The monitor looks again shortly.

    async def test_stale_revision_cannot_overwrite_newer_draft(self):
        await self.act("save_as", name="New")
        result = await self.service.action("edit", {"eq": flat_eq()}, 0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "Settings changed meanwhile. Check them and try again.")
        self.assertEqual(result["state"]["presets"][-1]["name"], "New")
        self.assertNotIn("presets", result["state"]["state"])

    async def test_status_message_says_why_and_does_not_repeat_the_phase(self):
        self.assertEqual(self.service.message, "EQ is off, so changes are not heard.")
        await self.act("enable", enabled=True)
        self.assertEqual(self.service.message, "EQ on Speakers")
        self.runtime.default = "bluez_output.headset"
        await self.service._sync(self.service.data)
        self.assertEqual(self.service.message, "Steam is playing to another output. EQ applies to Speakers.")
        self.runtime.default = "alsa_output.speakers"
        self.runtime.outputs[0]["speaker"] = False
        await self.service._sync(self.service.data)
        self.assertEqual(self.service.message, "Speakers only is on, so EQ is off on this output.")
        self.runtime.outputs = self.runtime.outputs[1:]
        await self.service._sync(self.service.data)
        self.assertEqual(self.service.message, "The selected output is not connected. Choose another under Output.")
        await self.act("enable", enabled=False)
        self.assertEqual(self.service.message, "EQ is off, so changes are not heard.")

    async def test_one_wording_for_a_game_that_changed_and_for_a_preset_that_changed(self):
        changed = "The running game changed. Check the sound and try again."
        await self.service.set_game("1145360", "Hades")
        self.assertEqual((await self.act("reset", scope="somewhere else"))["error"], changed)  # A dialog opened on another sound.
        self.assertEqual((await self.act("own_sound", enabled=True, appid="42"))["error"], changed)  # Another game's switch.
        self.assertEqual((await self.act("revert", id="other"))["error"], "The selected preset changed. Check it and try again.")

    async def test_a_full_library_refuses_an_import_in_the_same_words_as_a_save(self):
        for i in range(64): create_preset(self.service.data, f"P{i}", flat_eq())
        path = self.home / "one.json"
        path.write_text(json.dumps(export_document(initial_state())))
        full = "The preset library is full. Delete some presets first."
        self.assertEqual((await self.act("import", path=str(path)))["error"], full)
        self.assertEqual((await self.act("save_as", name="One more"))["error"], full)

    async def test_an_import_that_does_not_fit_says_how_many_do(self):
        for i in range(60): create_preset(self.service.data, f"P{i}", flat_eq())
        path = self.home / "bank.json"
        path.write_text(json.dumps({"format": "deckyeq-bank", "version": 2,
                                    "presets": [{"name": f"B{i}", "eq": flat_eq()} for i in range(10)]}))
        self.assertEqual((await self.act("import", path=str(path)))["error"], "This file has 10 presets; there is room for 4.")

    async def test_file_exchange_does_not_hold_up_the_plugin(self):
        # A folder on a stalled network mount: status and the monitor keep answering meanwhile.
        path = self.home / "one.json"
        path.write_text(json.dumps(export_document(initial_state())))
        def slow(real):
            def call(*args):
                time.sleep(0.5)
                return real(*args)
            return call
        files = self.service.files
        for action, payload, method in (("import", {"path": str(path)}, "read"), ("export", {"all": False}, "write")):
            with self.subTest(action=action), patch.object(files, method, side_effect=slow(getattr(files, method))):
                task = asyncio.create_task(self.act(action, **payload))
                began = time.monotonic()
                await asyncio.sleep(0.05)
                self.assertLess(time.monotonic() - began, 0.3)
                self.assertTrue((await task)["ok"])

    async def test_a_settings_save_failure_names_no_path(self):
        # Toasts can be seen on a stream: not "[Errno 28] No space left on device: '/home/deck/...'".
        error = OSError(28, "No space left on device", str(self.home / "settings" / ".settings.json"))
        await self.act("edit", eq=flat_eq() | {"bass": 2.0})
        with patch("dk_fs.write_atomic", side_effect=error):
            result = await self.act("select", id="go-s-original")
            self.service._save_draft()
        self.assertEqual(result["error"], "Settings could not be saved. Check free space.")
        self.assertNotIn(str(self.home), self.service.snapshot()["warning"])

    async def test_an_audio_os_error_shows_only_its_description(self):
        # E.g. the filter's config in the plugin's folder: the path names the user; the log keeps it.
        error = OSError(13, "Permission denied", str(self.home / "runtime" / "deckyeq.conf"))
        for action, method in (("enable", "start"), ("refresh", "apply")):
            if action == "refresh":
                self.assertTrue((await self.act("enable", enabled=True))["ok"])
            with self.subTest(action), patch.object(self.runtime, method, side_effect=error), \
                    self.assertLogs("deckyeq.service", "ERROR") as logs:
                result = await self.act(action, **({"enabled": True} if action == "enable" else {}))
            self.assertEqual(result["error"], "Permission denied")
            self.assertNotIn("deckyeq.conf", json.dumps(self.service.snapshot()["audio"]))
            self.assertIn("deckyeq.conf", "\n".join(logs.output))
        self.assertEqual(self.service.error, "Permission denied")

    async def test_headphone_port_bypasses_then_speakers_resume(self):
        await self.act("enable", enabled=True)
        self.runtime.outputs[0]["speaker"] = False
        await self.service._sync(self.service.data)
        self.assertEqual(self.service.phase, "bypassed")
        self.assertIsNone(self.runtime.process)
        self.assertTrue(self.runtime.muted)  # Silenced at once: the speaker EQ must not reach the headphones.
        self.assertTrue(self.service.data["enabled"])
        self.runtime.outputs[0]["speaker"] = True
        await self.service._sync(self.service.data)
        self.assertTrue(self.runtime.running)

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
            save.assert_not_called()  # Saved after the drag settles, not on every step.
            await asyncio.wait_for(self.service.draft_task, 3)
        self.assertEqual(self.runtime.eq["bass"], 2)
        self.assertEqual(self.store.load()["working"]["bass"], 2)

    async def test_volume_boost_is_a_live_global_setting(self):
        await self.act("enable", enabled=True)
        starts = self.runtime.events.count("start")
        eq = copy.deepcopy(self.service.data["working"])
        self.assertTrue((await self.act("edit", eq=eq, boost=6))["ok"])
        self.assertEqual((self.runtime.boost, self.runtime.events.count("start")), (6.0, starts))
        snapshot = self.service.snapshot()
        self.assertEqual(snapshot["state"]["boost"], 6.0)
        self.assertFalse(snapshot["dirty"])  # Not part of the preset.
        await self.act("select", id="go-s-original")
        self.assertEqual(self.runtime.boost, 6.0)  # Survives a preset change.
        self.assertFalse((await self.act("edit", eq=eq, boost=13))["ok"])
        self.service._save_draft()
        self.assertEqual(self.store.load()["boost"], 6.0)
        self.runtime.fail_next = True  # A failed write puts the previous boost back.
        self.assertFalse((await self.act("edit", eq=self.service.data["working"], boost=3))["ok"])
        self.assertEqual((self.runtime.boost, self.service.data["boost"]), (6.0, 6.0))

    async def test_failed_draft_save_is_reported_and_keeps_the_sound(self):
        await self.act("enable", enabled=True)
        eq = flat_eq()
        eq["bass"] = 4
        await self.act("edit", eq=eq)
        with patch.object(self.store, "save", side_effect=OSError("Disk full")):
            self.service._save_draft()
        self.assertIn("could not be saved", self.service.snapshot()["warning"])
        self.assertEqual(self.runtime.eq["bass"], 4)
        await self.service.close()  # Unloading tries again, although no save was pending any more.
        self.assertEqual(self.store.load()["working"]["bass"], 4)
        self.service._save_draft()
        self.assertIsNone(self.service.snapshot()["warning"])

    async def test_slider_edits_while_paused_do_not_restart_the_filter(self):
        await self.act("enable", enabled=True)
        await self.runtime.stop()
        self.service._failed(EQError("output vanished"))
        eq = flat_eq()
        eq["bass"] = 5
        self.assertTrue((await self.act("edit", eq=eq))["ok"])
        self.assertEqual(self.runtime.events.count("start"), 1)
        self.assertEqual(self.service.data["working"]["bass"], 5)

    async def test_repeated_failures_back_off_up_to_a_minute(self):
        delays = []
        for _ in range(6):
            self.service._failed(EQError("no audio"))
            delays.append(round(self.service.retry_at - time.monotonic()))
        self.assertEqual(delays, [8, 16, 32, 60, 60, 60])
        await self.act("enable", enabled=True)
        # Running again, but a filter that keeps dying must keep its backoff for a while.
        self.assertEqual((self.service.phase, self.service.retry_at, self.service.failures), ("active", 0, 6))
        self.service.failed_at -= 61
        await self.act("refresh")
        self.assertEqual(self.service.failures, 0)

    async def run_monitor(self, until, change=lambda: False):
        """Runs the real monitor with instant sleeps until the condition holds. change() is what each
        wait for an audio event returns (False: a heartbeat). self.pauses: the sleeps the monitor asked for;
        self.waits: how long it asked to wait for an event."""
        self.pauses, self.waits = [], []
        async def tick(seconds, *_):
            self.pauses.append(seconds)
            await REAL_SLEEP(0)
        async def wait(timeout):
            self.waits.append(timeout)
            await REAL_SLEEP(0)
            return change()
        with patch.object(self.runtime, "wait_for_change", side_effect=wait), \
                patch("deckyeq.service.asyncio.sleep", side_effect=tick):
            self.service.monitor.cancel()
            self.service.monitor = asyncio.create_task(self.service._watch())
            try:
                deadline = time.monotonic() + 5
                while not until():
                    self.assertLess(time.monotonic(), deadline, "the monitor never got there")
                    await REAL_SLEEP(0)
            finally:
                self.service.monitor.cancel()

    async def test_an_output_that_vanishes_while_starting_is_waited_for_not_an_error(self):
        # E.g. a Bluetooth headset changing profile just as its event starts the filter.
        await self.act("enable", enabled=True)
        await self.runtime.stop()
        with patch.object(self.runtime, "start", side_effect=OutputGone("Selected output is not connected.")):
            await self.service._sync(self.service.data)
        self.assertEqual((self.service.phase, self.service.message, self.service.retry_at, self.service.failures),
                         ("waiting", "The selected output is not connected. Choose another under Output.", 0, 0))
        await self.run_monitor(until=lambda: self.service.phase == "active", change=lambda: True)  # It is back.
        self.assertEqual((self.service.error, self.service.failures), (None, 0))

    async def test_headphones_plugged_in_while_starting_bypass_the_eq(self):
        await self.act("enable", enabled=True)
        await self.runtime.stop()
        async def start(*_, speakers_only=False):
            self.assertTrue(speakers_only)  # So the runtime never raises the level into headphones.
            self.runtime.outputs[0] = {**self.runtime.outputs[0], "speaker": False, "port": "analog-output-headphones"}
            raise OutputGone("Speakers only is on, so EQ is off on this output.")
        with patch.object(self.runtime, "start", side_effect=start):
            await self.service._sync(self.service.data)
        self.assertEqual((self.service.phase, self.service.retry_at, self.service.failures), ("bypassed", 0, 0))

    async def test_a_failure_after_an_event_keeps_its_backoff(self):
        # The event's pass failed: the monitor must sleep until the retry, not wake every half second.
        await self.act("enable", enabled=True)
        self.runtime.fail_next = True
        events = iter([True])
        await self.run_monitor(until=lambda: self.service.phase == "error" and len(self.waits) > 3,
                               change=lambda: next(events, False))
        self.assertTrue(all(wait > 5 for wait in self.waits[1:]), self.waits)

    async def test_an_event_during_a_backoff_waits_for_the_retry(self):
        await self.act("enable", enabled=True)
        await self.runtime.stop()
        self.service._failed(EQError("no audio"))
        events = iter([True])  # E.g. the output changing while audio is paused.
        await self.run_monitor(until=lambda: len(self.waits) > 3, change=lambda: next(events, False))
        self.assertTrue(all(wait > 5 for wait in self.waits), self.waits)

    async def test_the_log_hears_actions_and_audio_changes_not_heartbeats_or_slider_steps(self):
        with self.assertLogs("deckyeq.service", "INFO") as logs:
            await self.act("enable", enabled=True)
            for bass in (1, 2, 3):
                await self.act("edit", eq=flat_eq() | {"bass": float(bass)})
            await self.run_monitor(until=lambda: len(self.waits) > 5)  # Heartbeats only: nothing changes.
            await self.service.action("edit", {"eq": flat_eq()}, 10 ** 6)  # A wrong revision: refused.
        lines = [line.split(":", 2)[2] for line in logs.output]
        self.assertEqual(lines[0], "Action enable done")
        self.assertTrue(lines[1].startswith("Audio active: EQ on "), lines)
        self.assertEqual(lines[2:], ["Action edit refused: Settings changed meanwhile. Check them and try again."])
        self.assertFalse([line for line in logs.output if str(self.home) in line])

    async def test_an_unexpected_error_is_logged_and_the_panel_gets_no_detail(self):
        boom = RuntimeError(f"could not open {self.home}/secret")
        with patch.dict("deckyeq.service.HANDLERS", reset=Mock(side_effect=boom)), self.assertLogs("deckyeq.service", "ERROR") as logs:
            result = await self.act("reset")
        self.assertEqual(result["error"], "Unexpected error (RuntimeError). Details are in the Decky log.")
        self.assertIn("state", result)
        self.assertIn("Traceback", logs.output[0])
        self.assertEqual((await self.act("reset"))["ok"], True, "nothing is left half-done")

    async def test_a_filter_that_keeps_ending_is_paused_not_restarted_on_every_event(self):
        await self.act("enable", enabled=True)
        self.runtime.process.returncode, self.runtime.log = 1, "spa.audioconvert: assertion failed\n"
        with self.assertLogs("deckyeq.service", "WARNING"):
            await self.run_monitor(until=lambda: self.service.phase == "error", change=lambda: True)
        self.assertEqual(self.runtime.events.count("start"), 1)
        self.assertEqual(self.service.error, "The equalizer stopped: spa.audioconvert: assertion failed")
        self.assertGreater(self.service.retry_at, time.monotonic())  # The backoff decides when to start again.
        self.assertIsNone(self.runtime.process)

    async def test_pipewire_errors_name_the_home_folder_as_a_tilde(self):
        # PipeWire's stderr can name the plugin's files, under /home/<user>: the panel gets ~ instead; the log keeps it.
        await self.act("enable", enabled=True)
        self.runtime.process.returncode = 1
        self.runtime.log = "mod.filter-chain: can't load /home/deck/homebrew/plugins/DeckyEQ/bin/deckyeq_dsp.so\n"
        with patch("dk_process.session_env", return_value={"HOME": "/home/deck"}), \
                self.assertLogs("deckyeq.service", "WARNING") as logs:
            await self.run_monitor(until=lambda: self.service.phase == "error", change=lambda: True)
        self.assertEqual(self.service.error, "The equalizer stopped: mod.filter-chain: can't load "
                                             "~/homebrew/plugins/DeckyEQ/bin/deckyeq_dsp.so")
        self.assertIn("/home/deck/homebrew", "\n".join(logs.output))
        with patch("dk_process.session_env", return_value={"HOME": "/home/deck/"}):
            self.assertEqual(private("open /home/deck/x and /home/deck"), "open ~/x and ~")
        with patch("dk_process.session_env", return_value={"HOME": "/"}):
            self.assertEqual(private("/etc/x"), "/etc/x")  # A root home must not turn every path into ~.

    async def test_a_filter_that_ended_after_playing_a_while_is_rebuilt_quietly(self):
        # As when PipeWire restarts: the filter process ends with the daemon, long after its start.
        await self.act("enable", enabled=True)
        self.runtime.started_at -= 120
        self.runtime.process.returncode = 0
        await self.run_monitor(until=lambda: self.runtime.events.count("start") == 2, change=lambda: True)
        self.assertEqual(self.service.phase, "active")
        self.assertIsNone(self.service.error)

    async def test_slider_edits_while_connecting_are_kept_and_applied_once_audio_answers(self):
        store, runtime = Store(self.home / "boot"), FakeRuntime()
        store.save({**initial_state(), "enabled": True, "target": "alsa_output.speakers"})
        service = Service(store, runtime, PresetFiles(self.home))
        self.addAsyncCleanup(service.close)
        eq = flat_eq()
        eq["bass"] = 4
        with patch.object(runtime, "wait_for_change", side_effect=quiet), patch("deckyeq.service.ERROR_PAUSE_SECONDS", 0):
            with patch.object(runtime, "scan", side_effect=ToolError("pactl: Connection refused")):
                await service.start()
                self.assertEqual(service.phase, "connecting")
                result = await service.action("edit", {"eq": eq}, service.revision)
                self.assertTrue(result["ok"], result)  # Kept, not refused after another tool timeout.
            deadline = time.monotonic() + 5
            while runtime.eq != eq:
                self.assertLess(time.monotonic(), deadline, "the monitor never applied the edit")
                await REAL_SLEEP(0.05)
        self.assertEqual((service.phase, runtime.events.count("start")), ("active", 1))

    async def test_audio_that_answers_only_after_a_long_boot_is_connecting_not_paused(self):
        # EQ on at boot; the user's audio session needs well over TOOL_RETRIES tries to answer.
        store, self.runtime = Store(self.home / "boot"), FakeRuntime()
        store.save({**initial_state(), "enabled": True, "target": "alsa_output.speakers"})
        self.service = Service(store, self.runtime, PresetFiles(self.home))
        self.addAsyncCleanup(self.service.close)
        real_scan, scans = self.runtime.scan, []
        async def scan():
            scans.append(1)
            if len(scans) <= 3 * TOOL_RETRIES:
                raise ToolError("pactl: Connection refused")
            return await real_scan()
        with patch.object(self.runtime, "scan", side_effect=scan):
            await self.service.start()
            await self.run_monitor(until=lambda: self.service.phase in ("active", "error"))
        self.assertEqual((self.service.phase, self.service.failures, self.runtime.events.count("start")), ("active", 0, 1))

    async def test_a_tool_hiccup_keeps_the_filter_running(self):
        await self.act("enable", enabled=True)
        applies = self.runtime.events.count("apply")
        self.runtime.fail_next = ToolError("pw-dump: timed out")
        await self.run_monitor(until=lambda: self.runtime.events.count("apply") >= applies + 2
                               and self.service.tool_errors == 0)
        self.assertNotIn("stop", self.runtime.events)
        self.assertEqual((self.service.phase, self.service.error), ("active", None))

    async def test_a_restart_found_by_the_heartbeat_rebuilds_the_filter(self):
        await self.act("enable", enabled=True)
        starts = self.runtime.events.count("start")
        self.runtime.fail_next = NodeGone("DeckyEQ's live audio node is unavailable.")
        await self.run_monitor(until=lambda: self.runtime.events.count("start") > starts)
        self.assertEqual(self.runtime.events.count("start"), starts + 1)
        self.assertEqual((self.service.phase, self.service.error), ("active", None))

    async def test_the_heartbeat_keeps_the_volume_boost(self):
        await self.act("enable", enabled=True)
        await self.act("edit", eq=self.service.data["working"], boost=6)
        self.runtime.boost = None
        await self.run_monitor(until=lambda: self.runtime.boost is not None)
        self.assertEqual(self.runtime.boost, 6.0)

    async def test_status_reports_the_applied_headroom(self):
        from deckyeq.graph import headroom
        eq = flat_eq()
        eq.update(bass=9)
        await self.act("edit", eq=eq, boost=6)
        audio = self.service.snapshot()["audio"]
        self.assertEqual(audio["compensation"], headroom(eq)["compensation"])
        self.assertLess(audio["compensation"], 0)

    async def test_an_output_change_met_by_a_hiccup_is_not_lost(self):
        # Headphones plugged in, then the rescan times out once: the bypass must still follow.
        await self.act("enable", enabled=True)
        self.runtime.outputs[0] = {**self.runtime.outputs[0], "speaker": False, "port": "analog-output-headphones"}
        events = iter([True])  # One event, then heartbeats only.
        real_scan = self.runtime.scan
        scans = []
        async def scan():
            scans.append(1)
            if len(scans) == 1:
                raise ToolError("pactl: timed out")
            return await real_scan()
        with patch.object(self.runtime, "scan", side_effect=scan):
            await self.run_monitor(until=lambda: self.service.phase != "active", change=lambda: next(events, False))
        self.assertEqual(self.service.phase, "bypassed")

    async def test_a_start_that_meets_a_hiccup_says_it_is_connecting(self):
        # E.g. PipeWire restarted under the filter and pw-dump times out while it comes back.
        await self.act("enable", enabled=True)
        self.runtime.fail_next = NodeGone("DeckyEQ's live audio node is unavailable.")
        async def start(*_, **__):
            self.runtime.process = None  # Like Runtime.start: a failed start leaves nothing running.
            raise ToolError("pw-dump: timed out")
        with patch.object(self.runtime, "start", side_effect=start):
            await self.run_monitor(until=lambda: self.service.tool_errors > 0)
        self.assertEqual((self.service.phase, self.service.message), ("connecting", "Connecting to Speakers…"))

    async def test_a_lasting_tool_outage_while_eq_is_off_shows_no_error(self):
        # E.g. at boot, before the user's audio session answers. Nothing plays through DeckyEQ.
        with patch.object(self.runtime, "scan", side_effect=ToolError("pactl: timed out")) as scan:
            await self.run_monitor(until=lambda: scan.call_count > 2 * TOOL_RETRIES or self.service.phase == "error",
                                   change=lambda: True)
        self.assertEqual((self.service.phase, self.service.error), ("off", None))

    async def test_a_lasting_tool_outage_after_eq_was_turned_off_shows_no_error(self):
        await self.act("enable", enabled=True)  # The audio session has answered.
        await self.act("enable", enabled=False)
        with patch.object(self.runtime, "scan", side_effect=ToolError("pactl: timed out")) as scan:
            await self.run_monitor(until=lambda: scan.call_count > 2 * TOOL_RETRIES or self.service.phase == "error",
                                   change=lambda: True)
        self.assertEqual((self.service.phase, self.service.error), ("off", None))

    async def test_an_output_gone_while_rebuilding_after_a_restart_is_waited_for(self):
        await self.act("enable", enabled=True)
        self.runtime.fail_next = NodeGone("DeckyEQ's live audio node is unavailable.")
        async def start(*_, **__):
            self.runtime.process = None  # Like Runtime.start: nothing runs after it.
            raise OutputGone("Selected output is not connected.")
        with patch.object(self.runtime, "start", side_effect=start):
            await self.run_monitor(until=lambda: self.service.phase != "active")
        self.assertEqual((self.service.phase, self.service.failures), ("waiting", 0))

    async def test_a_slider_edit_after_a_pipewire_restart_is_applied(self):
        await self.act("enable", enabled=True)
        starts = self.runtime.events.count("start")
        self.runtime.fail_next = NodeGone("DeckyEQ's live audio node is unavailable.")
        eq = flat_eq()
        eq["bass"] = 3
        self.assertTrue((await self.act("edit", eq=eq))["ok"])
        self.assertEqual(self.runtime.events.count("start"), starts + 1)
        self.assertEqual(self.runtime.eq["bass"], 3)

    async def test_a_full_save_clears_an_old_draft_warning(self):
        eq = flat_eq()
        eq["bass"] = 2
        await self.act("edit", eq=eq)
        with patch.object(self.store, "save", side_effect=OSError("Disk full")):
            self.service._save_draft()
        self.assertIsNotNone(self.service.snapshot()["warning"])
        await self.act("select", id="go-s-original")  # Saved at once, with everything.
        self.assertIsNone(self.service.snapshot()["warning"])

    async def test_without_audio_support_settings_still_change(self):
        self.runtime.supported, self.runtime.reason = False, "No PipeWire here."
        self.service.data["enabled"] = True
        eq = flat_eq()
        eq["bass"] = 4
        self.assertTrue((await self.act("edit", eq=eq))["ok"])
        self.assertEqual(self.service.data["working"]["bass"], 4)

    async def test_lasting_tool_errors_still_pause_audio(self):
        await self.act("enable", enabled=True)
        with patch.object(self.runtime, "apply", side_effect=ToolError("pw-dump: timed out")):
            await self.run_monitor(until=lambda: self.service.phase == "error")
        self.assertIn("stop", self.runtime.events)
        self.assertEqual(self.service.phase, "error")

    async def test_a_vanished_node_restarts_the_filter_without_an_error(self):
        await self.act("enable", enabled=True)
        starts = self.runtime.events.count("start")
        self.runtime.fail_next = NodeGone("DeckyEQ's live audio node is unavailable.")
        self.assertTrue((await self.act("refresh"))["ok"])
        self.assertEqual(self.runtime.events.count("start"), starts + 1)
        self.assertEqual((self.service.phase, self.service.failures), ("active", 0))

    async def test_audio_that_answers_late_at_boot_says_connecting_and_is_retried(self):
        store, runtime = Store(self.home / "boot"), FakeRuntime()
        store.save({**initial_state(), "enabled": True, "target": "alsa_output.speakers"})
        service = Service(store, runtime, PresetFiles(self.home))
        self.addAsyncCleanup(service.close)
        real_scan, scans = runtime.scan, []
        async def scan():
            scans.append(1)
            if len(scans) == 1:
                raise ToolError("pactl: timed out")  # PipeWire is not up yet.
            return await real_scan()
        with patch.object(runtime, "scan", side_effect=scan), patch.object(runtime, "wait_for_change", side_effect=quiet):
            await service.start()
            self.assertEqual((service.phase, service.message, service.error, service.retry_at),
                             ("connecting", "Connecting to Speakers…", None, 0))
            deadline = time.monotonic() + 5
            while service.phase != "active":
                self.assertLess(time.monotonic(), deadline, "the monitor never connected")
                await REAL_SLEEP(0.05)
        self.assertEqual(runtime.events.count("start"), 1)

    async def test_an_unexpected_startup_error_still_starts_the_monitor(self):
        runtime = FakeRuntime()
        service = Service(Store(self.home / "other"), runtime, PresetFiles(self.home))
        with patch.object(runtime, "recover", side_effect=AttributeError("odd tool output")):
            await service.start()
        self.addAsyncCleanup(service.close)
        self.assertEqual(service.phase, "error")
        self.assertIsNotNone(service.monitor)
        self.assertTrue((await service.action("select", {"id": "go-s-original"}, service.revision))["ok"])

    async def test_a_failed_reconnect_is_reported(self):
        await self.act("enable", enabled=True)
        self.runtime.fail_next = True
        result = await self.act("refresh")
        self.assertFalse(result["ok"])
        self.assertEqual(self.service.phase, "error")
        self.assertEqual(self.runtime.events[-1], "stop")  # No half-applied filter keeps playing.

    async def test_bypass_clears_an_old_backoff(self):
        await self.act("enable", enabled=True)
        self.service._failed(EQError("no audio"))
        await self.act("output", target="bluez_output.headset", speakers_only=True)
        self.assertEqual((self.service.phase, self.service.retry_at, self.service.failures), ("bypassed", 0, 0))
        # The speakers still play: their EQ ramps out, it is not cut as for headphones on its own output.
        self.assertFalse(self.runtime.muted)

    async def test_unexpected_monitor_error_pauses_audio_without_stopping_monitoring(self):
        await self.act("enable", enabled=True)
        with patch.object(self.runtime, "scan", side_effect=KeyError("index")), \
                patch.object(self.runtime, "stop", side_effect=AttributeError("stop failed too")):
            # Still monitoring: the task is alive once the error shows.
            await self.run_monitor(until=lambda: self.service.phase == "error" and not self.service.monitor.done(),
                                   change=lambda: True)

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

    async def test_other_output_in_steam_puts_eq_on_standby_without_switching(self):
        await self.act("enable", enabled=True)
        self.assertEqual(self.service.phase, "active")
        self.runtime.default = "bluez_output.headset"
        await self.service._sync(self.service.data)
        self.assertEqual(self.service.phase, "standby")
        self.assertTrue(self.runtime.running)
        self.assertEqual(self.runtime.default, "bluez_output.headset")
        self.runtime.default = "alsa_output.speakers"
        await self.service._sync(self.service.data)
        self.assertEqual(self.service.phase, "active")
        self.assertEqual(self.runtime.events.count("start"), 1)

    async def test_enabling_while_bluetooth_plays_leaves_playback_where_it_is(self):
        self.runtime.default = "bluez_output.headset"
        self.assertTrue((await self.act("enable", enabled=True))["ok"])
        self.assertEqual(self.service.data["target"], "alsa_output.speakers")
        self.assertEqual(self.runtime.default, "bluez_output.headset")
        self.assertEqual(self.service.phase, "standby")

    async def test_edits_never_change_the_output(self):
        await self.act("enable", enabled=True)
        self.runtime.default = "bluez_output.headset"
        eq = flat_eq()
        eq["treble"] = 3
        self.assertTrue((await self.act("edit", eq=eq))["ok"])
        self.assertEqual(self.runtime.eq["treble"], 3)
        self.assertEqual(self.runtime.default, "bluez_output.headset")

    async def test_output_disconnection_waits_without_retargeting(self):
        await self.act("enable", enabled=True)
        self.runtime.outputs = self.runtime.outputs[1:]
        await self.service._sync(self.service.data)
        self.assertEqual(self.service.phase, "waiting")
        self.assertFalse(self.runtime.muted)  # Nothing to protect: the usual ramp.
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
        self.service._save_draft()  # Drafts reach the disk DRAFT_SAVE_SECONDS later.
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
        self.service._save_draft()
        self.assertEqual(self.store.load()["working"]["bass"], 3)

    async def test_status_answers_while_a_transaction_holds_the_lock(self):
        async with self.service.lock:
            self.assertEqual((await asyncio.wait_for(self.service.status(), 0.5))["revision"], self.service.revision)

    async def test_monitor_pauses_after_unexpected_errors(self):
        def broken(): raise RuntimeError("event stream broke")
        await self.run_monitor(until=lambda: self.pauses, change=broken)
        self.assertEqual(self.pauses[0], 2)

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

    async def test_a_game_gets_its_own_sound_and_gives_the_everyday_one_back(self):
        await self.act("enable", enabled=True)
        everyday = flat_eq()
        everyday["bass"] = 2
        await self.act("edit", eq=everyday)
        self.assertTrue((await self.service.set_game(1145360, "Hades"))["ok"])
        self.assertEqual(self.service.snapshot()["game"], {"appid": "1145360", "name": "Hades"})
        self.assertFalse((await self.act("own_sound", enabled="yes", appid="1145360"))["ok"])
        self.assertTrue((await self.act("own_sound", enabled=True, appid="1145360"))["ok"])
        self.assertEqual(self.service.data["working"], everyday)  # Starts as what was playing.
        game = flat_eq()
        game["treble"] = 5
        await self.act("edit", eq=game)
        self.assertEqual(self.runtime.eq["treble"], 5)
        await self.service.set_game(None, "")
        self.assertEqual((self.service.data["scope"], self.service.data["working"]), ("", everyday))
        self.assertEqual(self.runtime.eq, everyday)
        await self.service.set_game("730", "Counter-Strike 2")  # A game without its own sound changes nothing.
        self.assertEqual(self.service.data["working"], everyday)
        await self.service.set_game("1145360", "Hades")
        self.assertEqual(self.service.data["working"], game)
        self.assertEqual(self.runtime.eq["treble"], 5)
        saved = self.store.load()  # On disk, so a restart in the game keeps both sounds.
        self.assertEqual((saved["scope"], saved["outside"]["working"]), ("1145360", everyday))
        self.assertTrue((await self.act("own_sound", enabled=False, appid="1145360"))["ok"])
        self.assertEqual((self.service.data["working"], self.service.data["games"]), (everyday, {}))

    async def test_a_game_sound_can_be_forgotten_from_outside_the_game(self):
        await self.service.set_game("1145360", "Hades")
        await self.act("save_as", name="Loud")
        await self.act("own_sound", enabled=True, appid="1145360")
        preset = self.service.data["selected"]
        await self.service.set_game(None, "")
        self.assertEqual(self.service.data["games"]["1145360"]["selected"], preset)
        await self.act("select", id=preset)
        await self.act("delete", id=preset)  # The game keeps its sound, as an edited Flat.
        self.assertEqual(self.service.data["games"]["1145360"]["selected"], "flat")
        self.assertFalse((await self.act("forget_game", appid="730"))["ok"])
        self.assertTrue((await self.act("forget_game", appid="1145360"))["ok"])
        self.assertEqual(self.service.data["games"], {})
        self.assertFalse((await self.act("own_sound", enabled=True, appid="1145360"))["ok"])  # No game is running.

    async def test_a_failed_game_switch_never_costs_the_game_its_sound(self):
        await self.act("enable", enabled=True)
        await self.service.set_game("1145360", "Hades")
        game = flat_eq()
        game["treble"] = 6
        await self.act("own_sound", enabled=True, appid="1145360")
        await self.act("edit", eq=game)
        await self.service.set_game(None, "")
        self.runtime.fail_next = True  # e.g. pw-dump timing out while the game loads.
        self.assertFalse((await self.service.set_game("1145360", "Hades"))["ok"])
        self.assertEqual(self.service.data["scope"], "")
        self.assertTrue((await self.act("own_sound", enabled=True, appid="1145360"))["ok"])  # The panel's retry.
        self.assertEqual(self.service.data["working"], game)
        self.assertEqual(self.runtime.eq["treble"], 6)

    async def test_a_failed_switch_back_keeps_showing_the_game_whose_sound_plays(self):
        await self.act("enable", enabled=True)
        await self.service.set_game("1145360", "Hades")
        await self.act("own_sound", enabled=True, appid="1145360")
        game = flat_eq()
        game["treble"] = 6
        await self.act("edit", eq=game)
        self.runtime.fail_next = True  # The game ended, but its sound could not be swapped back.
        self.assertFalse((await self.service.set_game(None, ""))["ok"])
        snapshot = self.service.snapshot()  # Not "no game" over the game's sound: the panel stays coherent.
        self.assertEqual((snapshot["state"]["scope"], snapshot["game"]["appid"]), ("1145360", "1145360"))
        self.assertEqual(self.runtime.eq["treble"], 6)
        self.assertTrue(snapshot["audio"]["game_error"])  # The details behind the short toast.
        self.assertTrue((await self.service.set_game(None, ""))["ok"])  # The panel's retry.
        self.assertEqual((self.service.data["scope"], self.service.game), ("", None))
        self.assertIsNone(self.service.snapshot()["audio"]["game_error"])

    async def test_abandon_keeps_slider_edits_and_ends_the_audio_without_awaiting(self):
        await self.act("enable", enabled=True)
        eq = flat_eq()
        eq["bass"] = 4
        await self.act("edit", eq=eq)
        monitor = self.service.monitor
        self.service.abandon()  # Synchronous: Decky 3.3 starves the loop during _unload.
        self.assertEqual(self.store.load()["working"]["bass"], 4)
        self.assertIn("abandon", self.runtime.events)
        self.assertTrue(monitor is None or monitor.cancelling())
        unloading = "DeckyEQ is unloading."  # Nothing starts audio again before Decky ends the process.
        self.assertEqual((await self.act("enable", enabled=True))["error"], unloading)
        self.assertEqual((await self.service.set_game("1145360", "Hades"))["error"], unloading)

    async def test_abandon_ends_the_audio_whatever_saving_the_edits_raises(self):
        eq = flat_eq()
        eq["bass"] = 4
        await self.act("edit", eq=eq)
        with patch.object(self.store, "save", side_effect=TypeError("odd value")):
            with self.assertRaises(TypeError): self.service.abandon()
        self.assertIn("abandon", self.runtime.events)

    async def test_close_ends_the_event_subscription(self):
        await self.service.close()  # E.g. Decky starting the plugin again: no pactl subscribe left behind.
        self.assertIn("close_events", self.runtime.events)

    async def test_unload_saves_slider_edits_before_waiting_for_the_audio(self):
        eq = flat_eq()
        eq["bass"] = 4
        await self.act("edit", eq=eq)
        async with self.service.lock:  # A long filter start; Decky kills the plugin ~5 s after unloading.
            close = asyncio.create_task(self.service.close())
            await asyncio.sleep(0)
            self.assertEqual(self.store.load()["working"]["bass"], 4)
        await close

    async def test_a_restart_starts_with_the_everyday_sound(self):
        everyday = flat_eq()
        everyday["bass"] = 3
        await self.act("edit", eq=everyday)
        await self.service.set_game("1145360", "Hades")
        await self.act("own_sound", enabled=True, appid="1145360")
        game = flat_eq()
        game["preamp"] = -6
        await self.act("edit", eq=game)
        await self.service.close()  # A reboot while the game runs.
        restarted = Service(self.store, FakeRuntime(), PresetFiles(self.home))
        await restarted.start()
        self.addAsyncCleanup(restarted.close)
        self.assertEqual((restarted.data["scope"], restarted.data["working"]), ("", everyday))
        self.assertEqual(restarted.data["games"]["1145360"]["working"], game)
        self.assertEqual(self.store.load()["scope"], "")
        await restarted.set_game("1145360", "Hades")  # The panel reports the game again.
        self.assertEqual(restarted.data["working"], game)

    async def test_a_dialog_confirmed_after_a_game_switch_is_refused(self):
        await self.service.set_game("1145360", "Hades")
        await self.act("own_sound", enabled=True, appid="1145360")
        await self.service.set_game(None, "")
        tuned = flat_eq()
        tuned["bass"] = 6
        await self.service.set_game("1145360", "Hades")
        await self.act("edit", eq=tuned)
        result = await self.act("reset", scope="")  # "Reset to flat?" opened on the everyday sound.
        self.assertFalse(result["ok"])
        self.assertEqual(self.service.data["working"], tuned)
        self.assertTrue((await self.act("reset", scope="1145360"))["ok"])

    async def test_an_edit_overtaken_by_a_game_switch_says_so(self):
        await self.service.set_game("1145360", "Hades")
        await self.act("own_sound", enabled=True, appid="1145360")
        before = self.service.revision  # The panel's view, e.g. mid-drag, when the game ends.
        await self.service.set_game(None, "")
        result = await self.service.action("edit", {"eq": flat_eq()}, before)
        self.assertEqual(result["error"], "The game's sound was switched. Your change was not applied; try again.")
        self.assertTrue((await self.act("edit", eq=flat_eq()))["ok"])

    async def test_own_sound_applies_only_to_the_game_the_panel_showed(self):
        await self.service.set_game("1145360", "Hades")
        await self.service.set_game("730", "Counter-Strike 2")  # No own sound: the revision stays.
        self.assertFalse((await self.act("own_sound", enabled=True, appid="1145360"))["ok"])
        self.assertFalse((await self.act("own_sound", enabled=True))["ok"])
        self.assertEqual(self.service.data["games"], {})

    async def test_status_carries_game_names_not_their_sounds(self):
        await self.service.set_game("1145360", "Hades")
        await self.act("own_sound", enabled=True, appid="1145360")
        state = self.service.snapshot()["state"]
        self.assertEqual(state["games"], {"1145360": {"name": "Hades"}})
        self.assertNotIn("outside", state)

    async def test_bad_game_reports_are_rejected(self):
        for appid in ("12a", "1" * 21, "-5", 7.5):
            self.assertFalse((await self.service.set_game(appid, "x"))["ok"])
        self.assertIsNone(self.service.game)
        self.assertTrue((await self.service.set_game(7, "\x00"))["ok"])  # An odd title gets a plain name.
        self.assertEqual(self.service.game["name"], "App 7")
        await self.service.set_game(8, "\u202eHades")  # Bidi controls that would disguise it are dropped.
        self.assertEqual(self.service.game["name"], "Hades")

    async def test_confirmation_for_another_preset_is_rejected(self):
        await self.act("save_as", name="Keep me")
        kept = self.service.data["selected"]
        result = await self.act("delete", id="flat")
        self.assertFalse(result["ok"])
        self.assertEqual([p["id"] for p in self.service.data["presets"]], [kept])
        await self.act("save_as", name="Other")
        result = await self.act("rename", name="Renamed", id=kept)  # A rename dialog opened for "Keep me".
        self.assertFalse(result["ok"])
        self.assertEqual([p["name"] for p in self.service.data["presets"]], ["Keep me", "Other"])
        await self.act("select", id=kept)
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

    async def test_legion_go_2_sets_its_sound_and_its_volume_boost(self):
        await self.act("select", id="legion-go-2")
        data = self.service.data
        self.assertEqual(data["working"], selected_preset(data)["eq"])
        self.assertEqual((data["working"]["bass"], data["working"]["treble"], data["working"]["width"]), (2.5, 1.5, 1.6))
        self.assertEqual(data["boost"], 9.0)
        await self.act("select", id="flat")
        self.assertEqual(self.service.data["boost"], 9.0)  # Other presets leave the global boost as it is.

    async def test_clear_voices_and_more_bass_are_read_only_but_can_be_copied(self):
        self.assertEqual([p["id"] for p in self.service.snapshot()["presets"]],
                         ["flat", "clear-voices", "more-bass", "go-s-original", "legion-go-2"])
        for preset_id, title in (("clear-voices", "Clear voices"), ("more-bass", "More bass")):
            with self.subTest(preset=preset_id):
                await self.act("select", id=preset_id)
                factory = self.service.data["working"]
                self.assertEqual(factory, selected_preset(self.service.data)["eq"])
                for action in ("save", "rename", "delete"):
                    self.assertFalse((await self.act(action, name="Anything"))["ok"])
                self.assertTrue((await self.act("save_as", name=title))["ok"])  # Same name: made unique, not clobbered.
                self.assertEqual(self.service.data["presets"][-1]["name"], f"{title} (2)")
                self.assertEqual(self.service.data["presets"][-1]["eq"], factory)
                self.assertFalse((await self.act("rename", name=title))["ok"])  # A built-in name is taken.
        self.assertEqual(len(self.service.data["presets"]), 2)

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
