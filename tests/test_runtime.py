import asyncio
import copy
import json
import math
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from deckyeq.graph import LEVEL, MUTED, SINK_NAME, PLAYBACK_NAME, controls, neutral, same_controls
from deckyeq.model import EQError, NodeGone, OutputGone, ToolError, flat_eq as factory_flat_eq, go_s_eq
from deckyeq.runtime import Runtime, UNITY_VOLUME, environment, normalize_sinks, sink_level
from deckyeq.service import Service
from deckyeq.storage import PresetFiles, Store


def flat_eq():
    return factory_flat_eq()


class Process:
    def __init__(self):
        self.returncode = None
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()
    def terminate(self): self.returncode = 0
    def kill(self): self.returncode = -9
    def send_signal(self, _): self.returncode = -15
    async def wait(self): return self.returncode


class Subscriber:
    def __init__(self):
        self.returncode = None
        self.stdout = asyncio.StreamReader()
    def kill(self):
        self.returncode = -9
        self.stdout.feed_eof()  # Like a real pipe: the killed writer's end closes.
    async def wait(self): return self.returncode


class AudioServer(Runtime):
    """Protocol fake exercises the real Runtime orchestration, not Service stubs."""
    def __init__(self, directory):
        super().__init__(directory)
        self.supported, self.reason = True, None
        self.tools = {name: "/usr/bin/" + name for name in ("pipewire", "pw-cli", "pw-dump", "pactl", "python3")}
        self.stage_library = Path(__file__)  # Any existing file: the fake never loads it.
        self.commands = []
        self.mock_default = "alsa_output.speaker"
        self.actual = controls(flat_eq())
        self.foreign = False
        self.drop_node = False
        self.reject_controls = False
        self.reject_keys = set()
        self.fail_hardware_volume_once = False
        self.sinks = [{"name": "alsa_output.speaker", "index": 1, "description": "Speakers", "active_port": "analog-output-speaker"},
                      {"name": "bluez_output.headset", "index": 2, "description": "Headphones", "active_port": "headset"}]
        self.streams = [{"index": 10, "sink": 1, "properties": {"object.serial": 100, "node.name": "game"}}]
        for sink in self.sinks:
            sink.update(mute=False, channel_map=["front-left", "front-right"],
                        volume={"front-left": {"value": 45864}, "front-right": {"value": 45864}})
        self.virtual = {"name": SINK_NAME, "index": 3, "mute": False, "channel_map": ["front-left", "front-right"],
                        "volume": {"front-left": {"value": UNITY_VOLUME}, "front-right": {"value": UNITY_VOLUME}}}
        self.routes = {}
        self.state = "running"
        self.unlinked = False  # True: WirePlumber never inserts the filter (pre-0.5).
        self.stale = None  # Props a suspended node keeps reporting.
        self.silent = set()  # Tools that time out, like a pw-dump while a game loads.
        self.serial = 200  # Our node's object.serial; another one under the same id is another node.
        self.rate = 48000  # The graph's sample rate, which PipeWire 1.6.8 clamps each written Freq to.

    async def _spawn(self, eq, boost, speakers_only=False):
        # Like PipeWire: the new graph starts with the controls its configuration names.
        graph = json.loads(self.config.read_text(encoding="utf-8"))["context.modules"][-1]["args"]["filter.graph"]
        self.actual = {f"{node['name']}:{key}": value for node in graph["nodes"] for key, value in node["control"].items()}
        await super()._spawn(eq, boost, speakers_only)

    def set_port(self, name, port):
        """Like WirePlumber: remember the current route's volume and restore the new route's."""
        sink = next(s for s in self.sinks if s["name"] == name)
        self.routes[(name, sink["active_port"])] = copy.deepcopy(sink["volume"])
        sink["active_port"] = port
        if (name, port) in self.routes:
            sink["volume"] = copy.deepcopy(self.routes[(name, port)])

    async def run(self, tool, *args, **_):
        self.commands.append((tool, *args))
        if tool in self.silent:
            raise ToolError("Could not communicate with the desktop audio service.")
        live = self.process is not None and self.process.returncode is None
        if tool == "pw-dump":
            reported = self.stale if self.state == "suspended" else self.actual
            node = {"id": 70, "type": "PipeWire:Interface:Node", "info": {
                "state": self.state,
                "props": {"node.name": SINK_NAME, "deckyeq.owner": self.owner, "object.serial": self.serial},
                "params": {"Props": [{"params": copy.deepcopy(reported)}]}}}
            if not live or self.drop_node:
                return "" if args else json.dumps([])
            if args:  # pw-dump <id>: just that object; nothing at all (not []) for a missing one, as in 1.6.8
                return json.dumps([node]) if args[0] == "70" else ""
            # WirePlumber links a smart filter's output to its target (unless it is too old to).
            playback = {"id": 71, "type": "PipeWire:Interface:Node",
                        "info": {"props": {"node.name": PLAYBACK_NAME, "deckyeq.owner": self.owner}}}
            target = {"id": 72, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": self.target}}}
            link = {"id": 73, "type": "PipeWire:Interface:Link", "info": {"output-node-id": 71, "input-node-id": 72}}
            return json.dumps([node, playback, target] + ([] if self.unlinked else [link]))
        if tool == "pw-cli":
            if not self.reject_controls:
                values = json.loads(args[3])["params"]
                self.actual.update({k: min(v, self.rate) if k.endswith(":Freq") else v
                                    for k, v in zip(values[::2], values[1::2]) if k not in self.reject_keys})
            return ""
        if args == ("--format=json", "list", "sinks"):
            return json.dumps(self.sinks + ([self.virtual] if live or self.foreign else []))
        if args == ("--format=json", "list", "sink-inputs"): return json.dumps(self.streams)
        if args == ("get-default-sink",): return self.mock_default + "\n"
        if args[0] in ("set-sink-volume", "set-sink-mute"):
            sink = next(s for s in [*self.sinks, self.virtual] if s["name"] == args[1])
            if args[0] == "set-sink-volume":
                if self.fail_hardware_volume_once and args[1] == "alsa_output.speaker":
                    self.fail_hardware_volume_once = False
                    sink["volume"]["front-left"]["value"] = UNITY_VOLUME
                    raise EQError("Hardware rejected volume")
                for key, value in zip(sink["channel_map"], args[2:]): sink["volume"][key]["value"] = int(value)
            else: sink["mute"] = args[2] == "1"
            return ""
        if args[0] == "set-default-sink":
            self.mock_default = args[1]
            return ""
        if args[0] == "move-sink-input":
            destination = {s["name"]: s["index"] for s in self.sinks}
            destination[SINK_NAME] = 3
            stream = next(s for s in self.streams if s["index"] == int(args[1]))
            stream["sink"] = destination[args[2]]
            return ""
        raise AssertionError((tool, args))


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = AudioServer(Path(self.temp.name))
        self.addAsyncCleanup(self.runtime.stop)
        self.spawn = patch("deckyeq.runtime.asyncio.create_subprocess_exec", new_callable=AsyncMock)
        self.mock_spawn = self.spawn.start()
        self.addCleanup(self.spawn.stop)
        self.mock_spawn.side_effect = lambda *a, **kw: Process()

    async def start(self):
        await self.runtime.start(flat_eq(), "alsa_output.speaker")

    def writes(self):
        """Commands that would change the audio session outside DeckyEQ's own filter."""
        return [c for c in self.runtime.commands if c[0] == "pactl" and c[1] in
                ("set-default-sink", "set-sink-volume", "set-sink-mute", "move-sink-input")]

    async def test_starts_a_smart_filter_without_touching_the_audio_session(self):
        before = (self.runtime.mock_default, copy.deepcopy(self.runtime.sinks), copy.deepcopy(self.runtime.streams))
        await self.start()
        self.assertTrue(self.runtime.running)
        self.assertEqual(self.writes(), [])
        self.assertEqual((self.runtime.mock_default, self.runtime.sinks, self.runtime.streams), before)
        args, kwargs = self.mock_spawn.call_args
        self.assertEqual(args[:2], ("/usr/bin/python3", "-I"))
        self.assertTrue(args[2].endswith("worker.py"))
        self.assertEqual(args[4:], ("/usr/bin/pipewire", "-c", "deckyeq.conf"))
        self.assertEqual(kwargs["env"]["PIPEWIRE_CONFIG_DIR"], str(self.runtime.directory))
        self.assertTrue(kwargs["start_new_session"])
        self.assertNotIn("shell", kwargs)
        capture = json.loads(self.runtime.config.read_text())["context.modules"][-1]["args"]["capture.props"]
        self.assertTrue(capture["filter.smart"])
        self.assertEqual(capture["filter.smart.target"], {"node.name": "alsa_output.speaker"})

    async def test_stop_only_ends_the_filter(self):
        await self.start()
        process = self.runtime.process
        await self.runtime.stop()
        self.assertIsNotNone(process.returncode)
        self.assertFalse(self.runtime.running)
        self.assertEqual(self.writes(), [])
        self.assertFalse(self.runtime.journal.exists())

    async def test_stop_hands_steams_output_back_to_the_filters_own_output(self):
        # Steam set to "DeckyEQ" itself: WirePlumber would otherwise fall back to any other device.
        await self.start()
        self.runtime.mock_default = SINK_NAME
        await self.runtime.stop()
        self.assertEqual(self.runtime.mock_default, "alsa_output.speaker")

    async def test_abandon_hands_steams_output_back_without_awaiting(self):
        await self.start()
        calls = []
        def pactl(command, **kwargs):
            calls.append((*command[1:], kwargs["timeout"]))
            return subprocess.CompletedProcess(command, 0, (default + "\n").encode(), b"")
        process = self.runtime.process
        process.send_signal = lambda _: calls.append("SIGTERM")
        with patch("deckyeq.runtime.subprocess.run", side_effect=pactl):
            default = "bluez_output.headset"  # Steam plays elsewhere: left alone.
            self.runtime.abandon()
            self.assertEqual(calls, [("get-default-sink", 1), "SIGTERM"])
            calls.clear()
            self.runtime.abandon()  # Decky 3.3 runs _unload, then _uninstall, which unloads again: once is enough.
            self.assertEqual(calls, ["SIGTERM"])
            calls.clear()
            self.runtime.target, default = "alsa_output.speaker", SINK_NAME  # A first abandon again.
            self.runtime.abandon()  # Synchronous, bounded calls: Decky 3.3 starves the loop during _unload.
        self.assertEqual(calls, [("get-default-sink", 1), ("set-default-sink", "alsa_output.speaker", 1), "SIGTERM"])
        process.returncode = 0

    async def test_abandon_ends_the_filter_even_when_pactl_hangs(self):
        await self.start()
        process, signals = self.runtime.process, []
        process.send_signal = signals.append
        with patch("deckyeq.runtime.subprocess.run", side_effect=subprocess.TimeoutExpired("pactl", 1)):
            self.runtime.abandon()
        self.assertEqual(signals, [signal.SIGTERM])
        process.returncode = 0

    async def test_failed_start_leaves_no_filter_running(self):
        self.runtime.drop_node = True
        with patch("deckyeq.runtime.START_SECONDS", 0.3):
            with self.assertRaises(EQError): await self.start()
        self.assertIsNone(self.runtime.process)
        self.assertEqual(self.writes(), [])

    async def test_disconnected_output_is_refused(self):
        with self.assertRaises(OutputGone): await self.runtime.start(flat_eq(), "alsa_output.gone")
        self.assertIsNone(self.runtime.process)

    async def test_live_controls_update_without_restarting_graph(self):
        await self.start()
        self.runtime.commands.clear()  # Not the fade-in after start.
        process = self.runtime.process
        eq = flat_eq()
        eq["bass"] = 9  # Above the 6 dB allowance: the level drops by the excess.
        await self.runtime.apply(eq)
        self.assertEqual(self.runtime.actual["bass_l:Gain"], 9)
        self.assertIs(self.runtime.process, process)
        self.mock_spawn.assert_called_once()
        commands = [c for c in self.runtime.commands if c[0] == "pw-cli"]
        first = json.loads(commands[0][4])["params"]
        self.assertEqual(first, [LEVEL, controls(eq)[LEVEL]])  # The level drops first (in dB).
        self.assertLess(first[1], 0)
        self.assertEqual(json.loads(commands[-1][4])["params"],
                         [item for pair in controls(eq).items() if pair[0] != LEVEL for item in pair])

    async def test_readback_failure_is_reported(self):
        await self.start()
        self.runtime.reject_controls = True
        eq = flat_eq(); eq["treble"] = 2
        with self.assertRaisesRegex(EQError, "confirm"): await self.runtime.apply(eq)
        self.assertIsNone(self.runtime.last_controls)  # Unknown now: the next apply reads back.

    async def test_type_changes_are_live_without_a_restart(self):
        # Also what switching between presets with different filter types does: never a rebuild,
        # which would let unequalized audio through for a moment.
        await self.start()
        process = self.runtime.process
        eq = flat_eq()
        for kind, suffix in (("peaking", ""), ("lowshelf", "_low"), ("highshelf", "_high")):
            eq["bands"][0].update(type=kind, gain=4, q=2)
            await self.runtime.apply(eq)
            for variant in ("", "_low", "_high"):
                self.assertEqual(self.runtime.actual[f"band0{variant}_l:Gain"], 4 if variant == suffix else 0)
            self.assertIs(self.runtime.process, process)
        self.mock_spawn.assert_called_once()

    async def test_partial_filter_failure_never_raises_input_level(self):
        await self.start()
        boosted = flat_eq(); boosted["bass"] = 6
        await self.runtime.apply(boosted)
        protected_level = self.runtime.actual[LEVEL]
        self.runtime.reject_keys.add("bass_l:Gain")
        self.runtime.commands.clear()
        with self.assertRaisesRegex(EQError, "confirm"):
            await self.runtime.apply(flat_eq())
        self.assertEqual(self.runtime.actual["bass_l:Gain"], 6)
        for command in self.runtime.commands:
            if command[0] == "pw-cli":
                params = json.loads(command[4])["params"]
                values = dict(zip(params[::2], params[1::2]))
                self.assertLessEqual(values.get(LEVEL, protected_level), protected_level)

    async def test_lost_node_not_hidden_by_cached_settings(self):
        await self.start()
        self.runtime.verified_at -= 20  # Past the cache window: the next apply reads back.
        self.runtime.drop_node = True
        # NodeGone, which rebuilds at once; not "invalid data", which paused EQ after every PipeWire restart.
        with self.assertRaises(NodeGone): await self.runtime.apply(flat_eq())

    async def test_polling_skips_readback_only_while_recent_and_sink_listed(self):
        await self.start()
        await self.runtime.scan()
        self.runtime.commands.clear()
        await self.runtime.apply(flat_eq(), max_age=15)
        self.assertEqual(self.runtime.commands, [])
        eq = flat_eq()
        eq["bass"] = 2
        await self.runtime.apply(eq, max_age=15)
        self.assertEqual(self.runtime.actual["bass_l:Gain"], 2)
        self.runtime.commands.clear()
        self.runtime.sink_present = False
        await self.runtime.apply(eq, max_age=15)
        self.assertIn("pw-dump", [c[0] for c in self.runtime.commands])
        self.runtime.commands.clear()
        self.runtime.sink_present = True
        self.runtime.verified_at -= 16
        await self.runtime.apply(eq, max_age=15)
        self.assertIn("pw-dump", [c[0] for c in self.runtime.commands])

    def test_malformed_sink_properties_are_ignored(self):
        outputs = normalize_sinks([{"name": "alsa_output.speaker", "properties": ["bad"], "active_port": "speaker"}])
        self.assertEqual(outputs[0]["name"], "alsa_output.speaker")

    def volume(self, sink):
        return [entry["value"] for entry in sink["volume"].values()]

    def set_volume(self, sink, value):
        for entry in sink["volume"].values():
            entry["value"] = value

    async def test_unreadable_legacy_journal_is_set_aside_instead_of_blocking_eq(self):
        self.runtime.journal.write_text('{"version": 3, "previous": []}', encoding="utf-8")
        with self.assertRaises(EQError): await self.runtime.recover()
        self.assertFalse(self.runtime.journal.exists())
        self.assertTrue(self.runtime.journal.with_name("routing.invalid.json").exists())
        await self.start()

    async def test_legacy_journal_for_an_absent_device_waits_for_it(self):
        headset = self.runtime.sinks.pop(1)
        level = {"values": [20000, 20000], "mute": False}
        self.runtime.journal.write_text(json.dumps({"version": 3, "pending": [
            {"target": "bluez_output.headset", "port": "", "force": False, "level": level}]}), encoding="utf-8")
        await self.start()  # Never blocked by a restore owed to another device.
        await self.runtime.stop()
        self.assertTrue(self.runtime.journal.exists())
        self.set_volume(headset, UNITY_VOLUME)
        self.runtime.sinks.append(headset)
        await self.runtime.recover()
        self.assertEqual(self.volume(headset), [20000, 20000])
        self.assertFalse(self.runtime.journal.exists())

    async def test_legacy_restore_the_device_does_not_confirm_is_dropped_not_retried(self):
        speaker = self.runtime.sinks[0]
        self.set_volume(speaker, UNITY_VOLUME)
        level = {"values": [20000, 20000], "mute": False}
        self.runtime.journal.write_text(json.dumps({"version": 3, "pending": [
            {"target": "alsa_output.speaker", "port": "", "force": True, "level": level}]}), encoding="utf-8")
        with patch.object(self.runtime, "_set_level", side_effect=EQError("did not confirm")) as set_level:
            await self.runtime.recover()
            await self.runtime.recover()
        self.assertEqual(set_level.call_count, 1)  # Tried once, then dropped.
        self.assertFalse(self.runtime.journal.exists())
        await self.start()  # Never blocked by it.

    async def test_a_tool_that_does_not_answer_is_reported_as_such(self):
        # The monitor only tolerates ToolError; JSON tools must not turn it into "invalid data".
        await self.start()
        self.runtime.silent.add("pw-dump")
        with self.assertRaises(ToolError): await self.runtime.dump_node(70)
        self.runtime.silent = {"pactl"}
        with self.assertRaises(ToolError): await self.runtime.scan()

    async def test_a_legacy_restore_that_met_a_hiccup_is_kept(self):
        speaker = self.runtime.sinks[0]
        self.set_volume(speaker, UNITY_VOLUME)
        level = {"values": [20000, 20000], "mute": False}
        self.runtime.journal.write_text(json.dumps({"version": 3, "pending": [
            {"target": "alsa_output.speaker", "port": "", "force": True, "level": level}]}), encoding="utf-8")
        with patch.object(self.runtime, "_set_level", side_effect=ToolError("no answer")):
            await self.runtime.recover()
        self.assertTrue(self.runtime.journal.exists())  # Owed still; tried again at the next chance.
        await self.runtime.recover()
        self.assertEqual(self.volume(speaker), [20000, 20000])
        self.assertFalse(self.runtime.journal.exists())

    async def test_odd_audio_objects_are_skipped(self):
        self.assertIsNone(self.runtime._own_node([{"type": "PipeWire:Interface:Node", "id": 1, "info": None}]))
        self.assertFalse(self.runtime._linked([{"type": "PipeWire:Interface:Link", "info": None}]))

    async def test_a_busy_event_stream_still_lets_the_monitor_act(self):
        subscriber = Subscriber()
        self.mock_spawn.side_effect = lambda *a, **kw: subscriber
        self.addAsyncCleanup(self.runtime.close_events)
        async def chatter():
            while True:  # Unrelated events every 50 ms, forever.
                subscriber.stdout.feed_data(b"Event 'change' on client #9\n")
                await asyncio.sleep(0.05)
        noise = asyncio.create_task(chatter())
        self.addCleanup(noise.cancel)
        subscriber.stdout.feed_data(b"Event 'change' on card #2\n")
        started = time.monotonic()
        self.assertTrue(await self.runtime.wait_for_change(5))
        self.assertLess(time.monotonic() - started, 2)

    async def test_version_2_journal_is_still_restored(self):
        speaker = self.runtime.sinks[0]
        self.set_volume(speaker, UNITY_VOLUME)
        level = {"values": [20000, 20000], "mute": False}
        self.runtime.journal.write_text(json.dumps({"version": 2, "previous": "alsa_output.speaker", "volume": {
            "target": "alsa_output.speaker", "serial": "7", "original": level, "last": level},
            "volume_committed": True}), encoding="utf-8")
        await self.runtime.recover()
        self.assertEqual(self.volume(speaker), [20000, 20000])
        self.assertFalse(self.runtime.journal.exists())

    async def test_stop_without_a_session_keeps_pending_restores(self):
        level = {"values": [20000, 20000], "mute": False}
        self.runtime.journal.write_text(json.dumps({"version": 3, "pending": [
            {"target": "bluez_output.gone", "port": "", "force": False, "level": level}]}), encoding="utf-8")
        await self.runtime.stop()
        self.assertTrue(self.runtime.journal.exists())

    async def test_edit_starts_from_a_recent_readback_but_still_verifies(self):
        await self.start()
        await self.runtime.scan()
        self.runtime.commands.clear()
        eq = flat_eq()
        eq["bass"] = 2
        await self.runtime.apply(eq)
        # One readback of just our node; a 2 dB boost moves no level: one write of the filters.
        self.assertEqual([c for c in self.runtime.commands if c[0] == "pw-dump"], [("pw-dump", "70")])
        self.assertEqual(sum(c[0] == "pw-cli" for c in self.runtime.commands), 1)
        self.assertEqual(self.runtime.actual["bass_l:Gain"], 2)
        self.runtime.reject_controls = True
        eq["bass"] = 4
        with self.assertRaises(EQError): await self.runtime.apply(eq)

    async def test_a_port_switch_wakes_the_monitor_without_waiting_for_the_burst(self):
        await self.start()  # Learns the target's index (1).
        subscriber = Subscriber()
        self.mock_spawn.side_effect = lambda *a, **kw: subscriber
        self.addAsyncCleanup(self.runtime.close_events)
        async def chatter():
            while True:  # The rest of the burst, which an unrelated event waits out (up to 1 s).
                subscriber.stdout.feed_data(b"Event 'change' on client #9\n")
                await asyncio.sleep(0.05)
        noise = asyncio.create_task(chatter())
        self.addCleanup(noise.cancel)
        for event in (b"Event 'change' on card #2\n", b"Event 'change' on sink #1\n"):
            subscriber.stdout.feed_data(event)
            started = time.monotonic()
            self.assertTrue(await self.runtime.wait_for_change(5))
            self.assertLess(time.monotonic() - started, 0.5)

    async def test_a_held_volume_key_wakes_the_monitor_once_not_per_step(self):
        await self.start()  # Learns the target's index (1).
        subscriber = Subscriber()
        self.mock_spawn.side_effect = lambda *a, **kw: subscriber
        self.addAsyncCleanup(self.runtime.close_events)
        for _ in range(10):  # Each step of the key changes the target's volume.
            subscriber.stdout.feed_data(b"Event 'change' on sink #1\n")
        self.assertTrue(await self.runtime.wait_for_change(5))  # The first step at once,
        self.assertTrue(await self.runtime.wait_for_change(5))  # the rest as one rescan.
        self.assertFalse(await self.runtime.wait_for_change(0.3))

    async def test_the_first_change_of_the_target_is_acted_on_at_once(self):
        # Headphones plugged in under "Speakers only": the speaker EQ is muted without waiting for more events.
        await self.start()  # Learns the target's index (1).
        subscriber = Subscriber()
        self.mock_spawn.side_effect = lambda *a, **kw: subscriber
        self.addAsyncCleanup(self.runtime.close_events)
        subscriber.stdout.feed_data(b"Event 'change' on sink #1\n")
        with patch("deckyeq.runtime.TARGET_QUIET", 2):
            started = time.monotonic()
            self.assertTrue(await self.runtime.wait_for_change(5))
            self.assertLess(time.monotonic() - started, 1)

    async def test_a_card_event_is_not_held_back_by_a_volume_burst(self):
        # Headphones plugged in while the volume key is held: the speaker EQ must go at once.
        await self.start()
        subscriber = Subscriber()
        self.mock_spawn.side_effect = lambda *a, **kw: subscriber
        self.addAsyncCleanup(self.runtime.close_events)
        async def held():
            while True:  # Faster than TARGET_QUIET, forever: alone, it waits out EVENT_DRAIN_SECONDS.
                subscriber.stdout.feed_data(b"Event 'change' on sink #1\n")
                await asyncio.sleep(0.03)
        key = asyncio.create_task(held())
        self.addCleanup(key.cancel)
        self.assertTrue(await self.runtime.wait_for_change(5))  # The key's first step, at once.
        asyncio.get_running_loop().call_later(0.2, subscriber.stdout.feed_data, b"Event 'change' on card #2\n")
        started = time.monotonic()
        self.assertTrue(await self.runtime.wait_for_change(5))
        self.assertLess(time.monotonic() - started, 0.6)

    async def test_monitor_wakes_on_output_events_and_ignores_stream_noise(self):
        subscriber = Subscriber()
        self.mock_spawn.side_effect = lambda *a, **kw: subscriber
        self.addAsyncCleanup(self.runtime.close_events)
        subscriber.stdout.feed_data(b"Event 'new' on sink-input #12\n")
        self.assertFalse(await self.runtime.wait_for_change(0.2))
        subscriber.stdout.feed_data(b"Event 'change' on server #4294967295\nEvent 'change' on sink #3\n")
        started = time.monotonic()
        self.assertTrue(await self.runtime.wait_for_change(5))
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(self.mock_spawn.call_args.args[-2:], ("/usr/bin/pactl", "subscribe"))
        self.assertTrue(str(self.mock_spawn.call_args.args[2]).endswith("worker.py"))  # Ends with the plugin.
        self.assertEqual(self.mock_spawn.call_count, 1)  # One long-lived subscription.
        subscriber.stdout.feed_eof()
        with patch("deckyeq.runtime.POLL_SECONDS", 0):
            self.assertTrue(await self.runtime.wait_for_change(5))
        self.assertIsNone(self.runtime.subscriber)

    async def test_own_sink_changes_are_ignored_but_its_removal_is_not(self):
        subscriber = Subscriber()
        self.mock_spawn.side_effect = lambda *a, **kw: subscriber
        self.addAsyncCleanup(self.runtime.close_events)
        self.runtime.sink_index = 3
        subscriber.stdout.feed_data(b"Event 'change' on sink #3\n")
        self.assertFalse(await self.runtime.wait_for_change(0.3))  # An edit or a suspend of our own.
        subscriber.stdout.feed_data(b"Event 'remove' on sink #3\n")
        self.assertTrue(await self.runtime.wait_for_change(5))  # The filter crashed.

    async def test_the_filters_own_sink_is_learned_when_it_starts(self):
        await self.start()
        subscriber = Subscriber()
        self.mock_spawn.side_effect = lambda *a, **kw: subscriber
        self.addAsyncCleanup(self.runtime.close_events)
        subscriber.stdout.feed_data(b"Event 'change' on sink #3\n")  # pactl lists our filter as #3.
        self.assertFalse(await self.runtime.wait_for_change(0.3))

    async def test_an_event_stream_that_ends_mid_burst_returns_at_once(self):
        subscriber = Subscriber()
        self.mock_spawn.side_effect = lambda *a, **kw: subscriber
        self.addAsyncCleanup(self.runtime.close_events)
        subscriber.stdout.feed_data(b"Event 'change' on sink #5\n")
        subscriber.stdout.feed_eof()
        with patch("deckyeq.runtime.EVENT_DRAIN_SECONDS", 3), patch("deckyeq.runtime.POLL_SECONDS", 0):
            started = time.monotonic()
            self.assertTrue(await self.runtime.wait_for_change(5))
            self.assertLess(time.monotonic() - started, 1)  # Not a busy loop reading the end until the drain ends.

    async def test_without_an_event_subscription_the_monitor_polls(self):
        self.mock_spawn.side_effect = OSError("pactl cannot start")
        with patch("deckyeq.runtime.POLL_SECONDS", 0):
            self.assertTrue(await self.runtime.wait_for_change(5))
        self.assertIsNone(self.runtime.subscriber)

    async def test_a_restart_waits_out_our_own_lingering_sink(self):
        await self.start()
        self.runtime.virtual["properties"] = {"deckyeq.owner": self.runtime.owner}
        self.runtime.foreign = True  # pipewire-pulse still lists our previous filter.
        with patch("deckyeq.runtime.STALE_SINK_SECONDS", 0.3):
            await self.start()  # E.g. EQ toggled quickly, or PipeWire restarted: not "another" equalizer.
            self.assertEqual(self.mock_spawn.call_count, 2)
            self.runtime.virtual["properties"] = {"deckyeq.owner": "someone-else"}
            await self.runtime.stop()
            with self.assertRaisesRegex(EQError, "already active"):
                await self.start()

    async def test_an_older_copy_being_unloaded_is_waited_out(self):
        # Decky reloading or updating the plugin: the old copy's filter lasts until Decky kills it (~5 s).
        self.runtime.foreign = True
        asyncio.get_running_loop().call_later(2, setattr, self.runtime, "foreign", False)
        await self.start()
        self.assertTrue(self.runtime.running)

    async def test_unloading_does_not_wait_for_an_old_sink_to_go(self):
        self.runtime.foreign = True  # Never goes away.
        service = Service(Store(self.runtime.directory / "settings"), self.runtime, PresetFiles(self.runtime.directory))
        service.data.update(enabled=True, target="alsa_output.speaker")
        start = asyncio.create_task(service.start())
        await asyncio.sleep(0.3)
        began = time.monotonic()
        await service.close()  # Decky's stop arrives while the start waits.
        await start
        self.assertLess(time.monotonic() - began, 1)
        self.mock_spawn.assert_not_called()
        self.assertIsNone(service.monitor)

    async def test_unloading_stops_a_filter_that_is_still_starting(self):
        self.runtime.unlinked = True  # The filter never becomes ready: the start would wait START_SECONDS.
        service = Service(Store(self.runtime.directory / "settings"), self.runtime, PresetFiles(self.runtime.directory))
        service.data.update(enabled=True, target="alsa_output.speaker")
        start = asyncio.create_task(service.start())
        await asyncio.sleep(0.3)
        self.mock_spawn.assert_called_once()
        process = self.runtime.process
        began = time.monotonic()
        await service.close()  # Decky's stop arrives while the filter is starting.
        await start
        self.assertLess(time.monotonic() - began, 1)
        self.assertIsNotNone(process.returncode)  # Ended, not left running.
        self.assertIsNone(self.runtime.process)

    async def test_unloading_starts_no_filter_once_the_old_sink_is_gone(self):
        self.runtime.closing = True
        with self.assertRaisesRegex(EQError, "unloading"):
            await self.start()
        self.mock_spawn.assert_not_called()

    async def test_suspended_filter_takes_writes_and_is_verified_once_audio_flows(self):
        await self.start()
        await self.runtime.scan()
        self.runtime.state, self.runtime.stale = "suspended", copy.deepcopy(self.runtime.actual)
        self.runtime.verified_at -= 20  # Read the node afresh: it reports "suspended" now.
        eq = flat_eq()
        eq["bass"] = 6
        await self.runtime.apply(eq)  # Stale readback while suspended is not an error.
        self.assertEqual(self.runtime.actual["bass_l:Gain"], 6)
        self.assertEqual(self.runtime.verified_at, -math.inf)
        self.runtime.commands.clear()
        await self.runtime.apply(eq, max_age=15)  # Already written: no second write while suspended.
        self.assertNotIn("pw-cli", [command[0] for command in self.runtime.commands])
        self.runtime.state = "running"
        await self.runtime.apply(eq, max_age=15)
        self.assertGreater(self.runtime.verified_at, 0.0)

    async def test_large_filter_changes_are_one_write(self):
        # PipeWire 1.6 clears a biquad's history whenever its Freq, Q or Gain changes: each step would click.
        await self.start()
        await self.runtime.scan()
        eq = flat_eq()
        eq["bands"][4].update(gain=6)
        eq["bass"] = 3
        await self.runtime.apply(eq)
        self.runtime.commands.clear()
        eq["bands"][4].update(frequency=4000.0, gain=-6)  # 500 Hz -> 4 kHz, +6 -> -6 dB.
        eq["bass"] = 9  # Above the allowance: the level drops first.
        await self.runtime.apply(eq)
        writes = [dict(zip(p[::2], p[1::2])) for p in
                  (json.loads(c[4])["params"] for c in self.runtime.commands if c[0] == "pw-cli")]
        self.assertEqual(writes, [{LEVEL: controls(eq)[LEVEL]}, {k: v for k, v in controls(eq).items() if k != LEVEL}])
        self.assertEqual(self.runtime.actual["band4_l:Freq"], 4000.0)

    async def test_rollback_after_a_failed_write_really_restores_the_sound(self):
        await self.start()
        await self.runtime.scan()
        eq = flat_eq()
        eq["bass"] = 6
        self.runtime.reject_controls = True
        with self.assertRaises(EQError): await self.runtime.apply(eq)
        self.runtime.reject_controls = False
        self.runtime.actual["bass_l:Gain"] = 3.6  # Half-written before the failure.
        await self.runtime.apply(flat_eq())  # The rollback must not trust the old cache.
        self.assertEqual(self.runtime.actual["bass_l:Gain"], 0)

    async def test_unverified_state_is_not_fresh_right_after_boot(self):
        await self.start()
        await self.runtime.scan()
        self.runtime.state, self.runtime.stale = "suspended", copy.deepcopy(self.runtime.actual)
        eq = flat_eq()
        eq["bass"] = 6
        await self.runtime.apply(eq)
        self.runtime.state = "running"
        self.runtime.commands.clear()
        with patch("deckyeq.runtime.time.monotonic", return_value=5.0):  # 5 s after boot
            await self.runtime.apply(eq, max_age=15)
        self.assertIn("pw-dump", [c[0] for c in self.runtime.commands])

    async def test_level_rises_only_after_the_filters_are_confirmed(self):
        await self.start()
        await self.runtime.scan()
        boosted = flat_eq()
        boosted["bass"] = 9  # Auto headroom lowers the level by ~9.5 dB.
        await self.runtime.apply(boosted)
        self.runtime.commands.clear()
        await self.runtime.apply(flat_eq())  # Level back up by ~9.5 dB; the stage glides it.
        tools = [c[0] for c in self.runtime.commands]
        writes = [json.loads(c[4])["params"] for c in self.runtime.commands if c[0] == "pw-cli"]
        self.assertEqual(writes[-1], [LEVEL, 0.0])
        self.assertTrue(all(LEVEL not in w for w in writes[:-1]))
        self.assertEqual(tools[-3:], ["pw-dump", "pw-cli", "pw-dump"])  # Confirmed, raised, confirmed.

    async def test_start_and_stop_pass_through_a_neutral_filter(self):
        # Linked in neutral and moved to the settings; moved back before it ends. So the moment
        # WirePlumber links it in or out of the playback path makes no audible jump.
        graph = lambda: json.loads(self.runtime.config.read_text(encoding="utf-8"))["context.modules"][-1]["args"]["filter.graph"]
        eq = flat_eq()
        eq.update(bass=6, preamp=-3)
        await self.runtime.start(eq, "alsa_output.speaker", 3.0)
        self.assertEqual(next(n for n in graph()["nodes"] if n["name"] == "bass_l")["control"]["Gain"], 0.0)
        self.assertEqual(self.runtime.actual["bass_l:Gain"], 6)
        self.assertEqual(self.runtime.actual["stage:gain"], 3.0)
        self.runtime.commands.clear()
        await self.runtime.stop()
        writes = [dict(zip(p[::2], p[1::2])) for p in
                  (json.loads(c[4])["params"] for c in self.runtime.commands if c[0] == "pw-cli")]
        self.assertEqual(writes[0], {k: v for k, v in neutral().items() if k != LEVEL})  # The filters, then
        self.assertEqual(writes[1:], [{LEVEL: 0.0}])  # the level, once they are confirmed.
        self.assertEqual({k: self.runtime.actual[k] for k in neutral()}, neutral())

    async def test_headphones_get_a_muted_filter_ended_at_once(self):
        # Plugged in under "Speakers only": no neutral ramp or settle wait while the speaker EQ plays on.
        await self.runtime.start(flat_eq(), "alsa_output.speaker", 6.0)
        self.runtime.commands.clear()
        process = self.runtime.process
        terminate = process.terminate
        process.terminate = lambda: (self.runtime.commands.append(("terminate",)), terminate())
        with patch("deckyeq.runtime.asyncio.sleep", side_effect=AssertionError("waited before ending")):
            await self.runtime.stop(mute=True)
        writes = [c for c in self.runtime.commands if c[0] == "pw-cli"]
        self.assertEqual([dict(zip(*[iter(json.loads(c[4])["params"])] * 2)) for c in writes],
                         [{LEVEL: MUTED, "stage:gain": 0.0}])
        self.assertLess(self.runtime.commands.index(writes[0]), self.runtime.commands.index(("terminate",)))
        self.assertNotIn("pw-dump", [c[0] for c in self.runtime.commands])  # No readback for a ramp.
        self.assertEqual(self.writes(), [])  # Default output, volumes and streams untouched.
        self.assertFalse(self.runtime.running)

    async def test_stop_ends_the_filter_whatever_a_tool_step_raises(self):
        # E.g. odd pw-dump output: close() must not abort with the filter process still alive.
        real_run = self.runtime.run
        for mute, tool in ((True, "pw-cli"), (False, "pactl"), (False, "pw-dump")):
            with self.subTest(mute=mute, tool=tool):
                await self.start()
                process = self.runtime.process
                async def run(name, *args, **kw):
                    if name == tool:
                        raise KeyError("odd tool output")
                    return await real_run(name, *args, **kw)
                with patch.object(self.runtime, "run", side_effect=run):
                    await self.runtime.stop(mute=mute)
                self.assertIsNotNone(process.returncode)
                self.assertIsNone(self.runtime.process)

    async def test_an_output_unplugged_during_start_is_reported_as_such(self):
        self.runtime.unlinked = True  # WirePlumber cannot link to an output that went away.
        asyncio.get_running_loop().call_later(0.1, self.runtime.sinks.pop, 0)
        with patch("deckyeq.runtime.START_SECONDS", 0.3):
            with self.assertRaisesRegex(OutputGone, "^Selected output is not connected.$"): await self.start()
        self.assertIsNone(self.runtime.process)

    async def test_headphones_plugged_in_while_starting_never_get_the_speaker_eq(self):
        self.runtime.unlinked = True  # Still being linked when the port switches.
        def plug():
            self.runtime.set_port("alsa_output.speaker", "analog-output-headphones")
            self.runtime.unlinked = False
        asyncio.get_running_loop().call_later(0.2, plug)
        eq = flat_eq()
        eq["bass"] = 6
        with self.assertRaisesRegex(OutputGone, "Speakers only"):
            await self.runtime.start(eq, "alsa_output.speaker", 6.0, speakers_only=True)
        self.assertEqual([c for c in self.runtime.commands if c[0] == "pw-cli"], [])  # Never left neutral.
        self.assertIsNone(self.runtime.process)

    async def test_start_refuses_without_the_audio_stage(self):
        self.runtime.stage_library = Path(self.runtime.directory) / "missing.so"
        with self.assertRaisesRegex(EQError, "audio stage"):
            await self.runtime.start(flat_eq(), "alsa_output.speaker")
        self.mock_spawn.assert_not_called()

    async def test_a_just_stopped_filter_still_listed_does_not_block_start(self):
        self.runtime.foreign = True
        loop = asyncio.get_running_loop()
        loop.call_later(0.3, setattr, self.runtime, "foreign", False)
        await self.start()
        self.assertTrue(self.runtime.running)

    async def test_start_explains_when_wireplumber_does_not_insert_the_filter(self):
        self.runtime.unlinked = True
        with patch("deckyeq.runtime.START_SECONDS", 0.3):
            with self.assertRaisesRegex(EQError, "WirePlumber 0.5"): await self.start()
        self.assertIsNone(self.runtime.process)

    async def test_a_wireplumber_that_linked_the_filter_before_is_waited_for_quietly(self):
        # E.g. PipeWire restarted and WirePlumber takes longer than START_SECONDS to come back: not "too old".
        await self.start()
        await self.runtime.stop()
        self.runtime.unlinked = True
        with patch("deckyeq.runtime.START_SECONDS", 0.3):
            with self.assertRaises(ToolError): await self.start()
        self.assertIsNone(self.runtime.process)

    async def test_a_node_replaced_under_the_same_id_is_not_confirmed(self):
        await self.start()
        await self.runtime.scan()
        self.runtime.serial = 201  # PipeWire restarted, and another node got the id.
        eq = flat_eq()
        eq["bass"] = 2
        with self.assertRaisesRegex(EQError, "confirm"): await self.runtime.apply(eq)

    async def test_a_graph_at_44_1_khz_takes_the_go_s_preset(self):
        # PipeWire clamps each Freq to the graph's rate: the Go S 48 kHz shelf reads back as 44100 Hz.
        self.runtime.rate = 44100
        await self.runtime.start(go_s_eq(), "alsa_output.speaker")
        self.assertEqual(self.runtime.actual["band8_l:Freq"], 44100)
        eq = go_s_eq()
        eq["bands"][2]["gain"] = 3
        await self.runtime.apply(eq)
        self.runtime.reject_keys.add("band9_l:Freq")  # A write that did not happen is still no clamp.
        eq["bands"][9]["frequency"] = 18000.0
        with self.assertRaisesRegex(EQError, "confirm"): await self.runtime.apply(eq)

    def test_an_old_frequency_left_by_a_failed_write_is_no_clamp(self):
        expected = {"band8_l:Freq": 48000.0, "band9_l:Freq": 16000.0}
        self.assertTrue(same_controls({"band8_l:Freq": 44100.0, "band9_l:Freq": 16000.0}, expected))
        self.assertFalse(same_controls({"band8_l:Freq": 10000.0, "band9_l:Freq": 10000.0}, expected))  # Not a graph rate.

    async def test_external_control_drift_repaired(self):
        await self.start()
        self.runtime.verified_at -= 20  # Past the cache window, as for the monitor's heartbeat.
        self.runtime.actual["band3_l:Gain"] = 3
        await self.runtime.apply(flat_eq())
        self.assertEqual(self.runtime.actual["band3_l:Gain"], 0)

    async def test_foreign_equalizer_is_never_reused_or_killed(self):
        self.runtime.foreign = True
        with patch("deckyeq.runtime.STALE_SINK_SECONDS", 0.3):
            with self.assertRaisesRegex(EQError, "already active"): await self.start()
        self.mock_spawn.assert_not_called()
        self.assertEqual(self.runtime.mock_default, "alsa_output.speaker")


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    """The real Runtime.run and stop() on real processes; python stands in for the audio tools."""
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.runtime = Runtime(Path(temp.name))
        self.runtime.tools["python3"] = sys.executable

    async def test_a_tool_that_hangs_is_killed_and_reported_as_silent(self):
        spawned, spawn = [], asyncio.create_subprocess_exec
        async def record(*args, **kwargs):
            spawned.append(await spawn(*args, **kwargs))
            return spawned[-1]
        began = time.monotonic()
        with patch("deckyeq.runtime.asyncio.create_subprocess_exec", new=record):
            with self.assertRaises(ToolError):  # The monitor tolerates these as hiccups.
                await self.runtime.run("python3", "-c", "import time; time.sleep(30)", timeout=0.5)
        self.assertLess(time.monotonic() - began, 5)
        self.assertIsNotNone(spawned[0].returncode)  # Killed and reaped, not left running.

    async def test_abandon_ends_the_filter_and_the_subscriber_without_awaiting(self):
        sleep = [sys.executable, "-c", "import time; time.sleep(30)"]
        self.runtime.process = await asyncio.create_subprocess_exec(*sleep)
        self.runtime.subscriber = await asyncio.create_subprocess_exec(*sleep)
        self.runtime.abandon()  # Synchronous: Decky 3.3 starves the loop during _unload.
        for proc in (self.runtime.process, self.runtime.subscriber):
            self.assertIsNotNone(await asyncio.wait_for(proc.wait(), 5))
        self.assertTrue(self.runtime.closing)

    async def test_a_tool_still_writing_at_its_timeout_is_reaped_without_hanging(self):
        # Killed with a full pipe that asyncio has paused: unread, it never closes and wait() never returns.
        writer = "import sys\nwhile True: sys.stdout.buffer.write(b'x' * 65536)"
        with self.assertRaises(ToolError):
            await asyncio.wait_for(self.runtime.run("python3", "-c", writer, timeout=0.5), 10)

    async def test_closing_events_with_a_full_pipe_returns(self):
        writer = [sys.executable, "-c", "import sys\nwhile True: sys.stdout.buffer.write(b'Event x\\n' * 8192)"]
        self.runtime.subscriber = await asyncio.create_subprocess_exec(*writer, stdout=asyncio.subprocess.PIPE)
        await asyncio.sleep(0.5)  # Unread events pile up past the reader's limit.
        await asyncio.wait_for(self.runtime.close_events(), 10)
        self.assertIsNone(self.runtime.subscriber)

    async def test_pw_cli_errors_are_read_from_its_messages(self):
        # pw-cli exits 0 even when a write fails; only its notes about an object removed meanwhile are harmless.
        self.runtime.tools["pw-cli"] = sys.executable
        say = lambda text: ("-c", f"import sys; sys.stderr.write({text!r})")
        miss = "remote 0: error id:2 seq:7 res:-2 (No such file or directory): no global 55\n"
        self.assertEqual(await self.runtime.run("pw-cli", *say(miss)), "")
        with self.assertRaisesRegex(ToolError, "unknown global"):
            await self.runtime.run("pw-cli", *say("Error: unknown global 55\n"))

    async def test_an_oversized_answer_is_refused(self):
        with self.assertRaisesRegex(EQError, "size limit"):  # And returns: an unread pipe once hung it for good.
            await asyncio.wait_for(self.runtime.run(
                "python3", "-c", "import sys; sys.stdout.write('x' * (9 * 1024 * 1024))"), 10)

    @unittest.skipUnless(sys.platform.startswith("linux"), "process groups and /proc are Linux")
    async def test_stop_kills_a_filter_that_ignores_sigterm_with_its_whole_group(self):
        # Ignored signals stay ignored in children, so only the group SIGKILL ends the grandchild.
        script = ("import signal, subprocess, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                  "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                  "print(child.pid, flush=True); time.sleep(60)")
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", script, stdout=asyncio.subprocess.PIPE,
                                                       start_new_session=True)
        def cleanup():
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.addCleanup(cleanup)
        child = int(await process.stdout.readline())
        def gone():
            try:
                return Path(f"/proc/{child}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"
            except FileNotFoundError:
                return True
        self.runtime.process = process
        with patch("deckyeq.runtime.TERMINATE_SECONDS", 0.3):
            await asyncio.wait_for(self.runtime.stop(), 10)
        self.assertEqual(process.returncode, -signal.SIGKILL)
        deadline = time.monotonic() + 5
        while not gone():
            self.assertLess(time.monotonic(), deadline, "the filter's child outlived it")
            await asyncio.sleep(0.05)
        self.assertIsNone(self.runtime.process)

    @unittest.skipUnless(sys.platform.startswith("linux"), "process groups and sessions are Linux")
    async def test_stop_returns_although_a_child_that_left_the_group_holds_the_pipes(self):
        script = ("import os, signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                  "if os.fork() == 0:\n    os.setsid()\n    print(os.getpid(), flush=True)\n"
                  "time.sleep(60)")
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", script, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.PIPE, start_new_session=True)
        child = int(await process.stdout.readline())
        def cleanup():
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.addCleanup(cleanup)
        self.runtime.process = process
        with patch("deckyeq.runtime.TERMINATE_SECONDS", 0.3):
            await asyncio.wait_for(self.runtime.stop(), 10)  # Not until the child ends its sleep.
        self.assertEqual(process.returncode, -signal.SIGKILL)
        self.assertIsNone(self.runtime.process)


class OutputTests(unittest.TestCase):
    def test_pactl_channel_map_string_preserves_channel_order(self):
        sink = {"channel_map": "front-left,front-right", "mute": False,
                "volume": {"front-right": {"value": 22000}, "front-left": {"value": 18000}}}
        self.assertEqual(sink_level(sink), {"values": [18000, 22000], "mute": False})

    def test_system_tools_do_not_inherit_frozen_loader_libraries(self):
        with patch.dict(os.environ, {"LD_LIBRARY_PATH": "/frozen-libraries"}, clear=True):
            self.assertNotIn("LD_LIBRARY_PATH", environment())
        with patch.dict(os.environ, {"LD_LIBRARY_PATH": "/frozen-libraries", "LD_LIBRARY_PATH_ORIG": "/system-libraries"}, clear=True):
            clean = environment()
            self.assertEqual(clean["LD_LIBRARY_PATH"], "/system-libraries")
            self.assertNotIn("LD_LIBRARY_PATH_ORIG", clean)

    @unittest.skipUnless(sys.platform.startswith("linux"), "pwd and /run/user are Linux")
    def test_another_users_runtime_directory_is_not_used(self):
        uid, real_stat = os.geteuid(), os.stat
        for owner, used in ((uid, True), (uid + 1, False)):
            def fake(path, *args, **kwargs):
                if path == f"/run/user/{uid}":
                    return os.stat_result((stat.S_IFDIR | 0o700, 0, 0, 0, owner, 0, 0, 0, 0, 0))
                return real_stat(path, *args, **kwargs)
            with self.subTest(owner=owner), patch.dict(os.environ, {}, clear=True), \
                    patch("deckyeq.runtime.os.stat", side_effect=fake):
                self.assertEqual("XDG_RUNTIME_DIR" in environment(), used)

    def test_audio_is_unsupported_as_root(self):
        # Settings and the filter's files written as root would lock the user out of them.
        with tempfile.TemporaryDirectory() as temp, patch("deckyeq.runtime.environment", return_value={}), \
                patch("deckyeq.runtime.platform.system", return_value="Linux"), \
                patch("deckyeq.runtime.shutil.which", return_value="/usr/bin/tool"):
            for euid, supported in ((1000, True), (0, False)):
                with self.subTest(euid=euid), patch("deckyeq.runtime.os.geteuid", return_value=euid, create=True):
                    self.assertEqual(Runtime(Path(temp) / str(euid)).supported, supported)

    def test_tools_run_untranslated(self):
        # pactl translates its event lines ("Event 'change' on sink #1"), which the monitor matches.
        with patch.dict(os.environ, {"LANG": "pl_PL.UTF-8", "LC_ALL": "pl_PL.UTF-8"}, clear=True):
            self.assertEqual(environment()["LC_ALL"], "C")

    def test_deckyeq_outputs_are_never_offered_as_targets(self):
        sinks = [{"name": name, "active_port": "speaker"} for name in (SINK_NAME, "deckyeq.sink", "alsa_output.x")]
        self.assertEqual([o["name"] for o in normalize_sinks(sinks)], ["alsa_output.x"])

    def test_speaker_detection_is_based_on_active_port(self):
        outputs = normalize_sinks([
            {"name": "alsa_output.internal", "index": 1, "active_port": "analog-output-headphones"},
            {"name": "alsa_output.second", "index": 2, "active_port": {"name": "analog-output-speaker"}},
            {"name": "easyeffects_sink", "index": 3},
            {"name": SINK_NAME, "index": 4},
            {"name": "alsa_output.unknown", "index": 5}])
        self.assertEqual(len(outputs), 3)
        self.assertFalse(outputs[0]["speaker"])
        self.assertTrue(outputs[1]["speaker"])
        self.assertFalse(outputs[2]["speaker"])


if __name__ == "__main__": unittest.main()
