import asyncio
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from deckyeq.graph import SINK_NAME, PLAYBACK_NAME, controls
from deckyeq.model import EQError, flat_eq
from deckyeq.runtime import Runtime, UNITY_VOLUME, environment, normalize_sinks, sink_level


class Process:
    def __init__(self):
        self.returncode = None
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()
    def terminate(self): self.returncode = 0
    def kill(self): self.returncode = -9
    async def wait(self): return self.returncode


class AudioServer(Runtime):
    """Protocol fake exercises the real Runtime orchestration, not Service stubs."""
    def __init__(self, directory):
        super().__init__(directory)
        self.supported, self.reason = True, None
        self.tools = {name: "/usr/bin/" + name for name in ("pipewire", "pw-cli", "pw-dump", "pactl", "python3")}
        self.commands = []
        self.mock_default = "alsa_output.speaker"
        self.actual = controls(flat_eq())
        self.foreign = False
        self.drop_node = False
        self.reject_controls = False
        self.reject_keys = set()
        self.fail_default = False
        self.fail_hardware_volume_once = False
        self.sinks = [{"name": "alsa_output.speaker", "index": 1, "description": "Speakers", "active_port": "analog-output-speaker"},
                      {"name": "bluez_output.headset", "index": 2, "description": "Headphones", "active_port": "headset"}]
        self.streams = [{"index": 10, "sink": 1, "properties": {"object.serial": 100, "node.name": "game"}}]
        for sink in self.sinks:
            sink.update(mute=False, channel_map=["front-left", "front-right"],
                        volume={"front-left": {"value": 45864}, "front-right": {"value": 45864}})
        self.virtual = {"name": SINK_NAME, "index": 3, "mute": False, "channel_map": ["front-left", "front-right"],
                        "volume": {"front-left": {"value": UNITY_VOLUME}, "front-right": {"value": UNITY_VOLUME}}}

    async def run(self, tool, *args, **_):
        self.commands.append((tool, *args))
        live = self.process is not None and self.process.returncode is None
        if tool == "pw-dump":
            node = {"id": 70, "type": "PipeWire:Interface:Node", "info": {
                "props": {"node.name": SINK_NAME, "deckyeq.owner": self.owner, "object.serial": 200},
                "params": {"Props": [{"params": copy.deepcopy(self.actual)}]}}}
            return json.dumps([node] if live and not self.drop_node else [])
        if tool == "pw-cli":
            if not self.reject_controls:
                values = json.loads(args[3])["params"]
                self.actual.update({k: v for k, v in zip(values[::2], values[1::2]) if k not in self.reject_keys})
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
            if self.fail_default and args[1] == SINK_NAME: raise EQError("Cannot set default")
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

    async def test_starts_private_process_and_routes_existing_streams(self):
        await self.start()
        self.assertTrue(self.runtime.running)
        self.assertEqual(self.runtime.mock_default, SINK_NAME)
        self.assertEqual(self.runtime.streams[0]["sink"], 3)
        self.assertTrue(self.runtime.journal.exists())
        args, kwargs = self.mock_spawn.call_args
        self.assertEqual(args[:2], ("/usr/bin/python3", "-I"))
        self.assertTrue(args[2].endswith("worker.py"))
        self.assertEqual(args[5], "deckyeq.conf")
        self.assertEqual(kwargs["env"]["PIPEWIRE_CONFIG_DIR"], str(self.runtime.directory))
        self.assertTrue(kwargs["start_new_session"])
        self.assertNotIn("shell", kwargs)

    async def test_live_controls_update_without_restarting_graph(self):
        await self.start()
        process = self.runtime.process
        eq = flat_eq()
        eq["bass"] = 6
        await self.runtime.apply(eq)
        self.assertEqual(self.runtime.actual["bass:Gain"], 6)
        self.assertIs(self.runtime.process, process)
        self.mock_spawn.assert_called_once()
        commands = [c for c in self.runtime.commands if c[0] == "pw-cli"]
        first = json.loads(commands[0][4])["params"]
        self.assertEqual(first[0], "level:Mult")
        self.assertLess(first[1], 1)
        self.assertEqual(json.loads(commands[-1][4])["params"],
                         [item for pair in controls(eq).items() for item in pair])

    async def test_readback_failure_is_reported(self):
        await self.start()
        self.runtime.reject_controls = True
        eq = flat_eq(); eq["treble"] = 2
        with self.assertRaisesRegex(EQError, "confirm"): await self.runtime.apply(eq)
        self.assertEqual(self.runtime.last_controls["treble:Gain"], 0)

    async def test_type_changes_disable_previous_filter_without_restart(self):
        await self.start()
        process = self.runtime.process
        eq = flat_eq()
        for kind, suffix in (("peaking", ""), ("lowshelf", "_low"), ("highshelf", "_high")):
            eq["bands"][0].update(type=kind, gain=4, q=2)
            await self.runtime.apply(eq)
            for variant in ("", "_low", "_high"):
                self.assertEqual(self.runtime.actual[f"band0{variant}:Gain"], 4 if variant == suffix else 0)
            self.assertIs(self.runtime.process, process)
        self.mock_spawn.assert_called_once()

    async def test_partial_filter_failure_never_raises_input_level(self):
        await self.start()
        boosted = flat_eq(); boosted["bass"] = 6
        await self.runtime.apply(boosted)
        protected_level = self.runtime.actual["level:Mult"]
        self.runtime.reject_keys.add("bass:Gain")
        self.runtime.commands.clear()
        with self.assertRaisesRegex(EQError, "confirm"):
            await self.runtime.apply(flat_eq())
        self.assertEqual(self.runtime.actual["bass:Gain"], 6)
        for command in self.runtime.commands:
            if command[0] == "pw-cli":
                params = json.loads(command[4])["params"]
                values = dict(zip(params[::2], params[1::2]))
                self.assertLessEqual(values.get("level:Mult", protected_level), protected_level)

    async def test_lost_node_not_hidden_by_cached_settings(self):
        await self.start()
        self.runtime.drop_node = True
        with self.assertRaises(EQError): await self.runtime.apply(flat_eq())

    async def test_external_control_drift_repaired(self):
        await self.start()
        self.runtime.actual["band3:Gain"] = 3
        await self.runtime.apply(flat_eq())
        self.assertEqual(self.runtime.actual["band3:Gain"], 0)

    async def test_stop_restores_original_and_new_streams(self):
        await self.start()
        self.runtime.streams.append({"index": 11, "sink": 3, "properties": {"object.serial": 101}})
        await self.runtime.stop()
        self.assertEqual(self.runtime.mock_default, "alsa_output.speaker")
        self.assertEqual([s["sink"] for s in self.runtime.streams], [1, 1])
        self.assertFalse(self.runtime.journal.exists())
        self.assertEqual(sink_level(self.runtime.sinks[0]), {"values": [45864, 45864], "mute": False})

    async def test_manual_default_change_survives_stop(self):
        await self.start()
        self.runtime.mock_default = "bluez_output.headset"
        await self.runtime.stop()
        self.assertEqual(self.runtime.mock_default, "bluez_output.headset")
        self.assertEqual(self.runtime.streams[0]["sink"], 2)

    async def test_failed_activation_stops_child_and_restores_default(self):
        self.runtime.fail_default = True
        with self.assertRaises(EQError): await self.start()
        self.assertIsNone(self.runtime.process)
        self.assertEqual(self.runtime.mock_default, "alsa_output.speaker")
        self.assertFalse(self.runtime.journal.exists())
        self.assertEqual(sink_level(self.runtime.sinks[0]), {"values": [45864, 45864], "mute": False})

    async def test_full_hardware_volume_is_verified_before_switching_output(self):
        await self.start()
        self.assertEqual(sink_level(self.runtime.sinks[0])["values"], [UNITY_VOLUME, UNITY_VOLUME])
        self.assertEqual(sink_level(self.runtime.virtual)["values"], [45864, 45864])
        commands = self.runtime.commands
        gain_index = commands.index(("pactl", "set-sink-volume", "alsa_output.speaker", "65536", "65536"))
        switch_index = commands.index(("pactl", "set-default-sink", SINK_NAME))
        self.assertLess(gain_index, switch_index)
        self.assertIn(("pactl", "--format=json", "list", "sinks"), commands[gain_index:switch_index])

    async def test_current_eq_volume_and_mute_return_to_hardware_on_disable(self):
        await self.start()
        self.runtime.virtual["volume"]["front-left"]["value"] = 20000
        self.runtime.virtual["volume"]["front-right"]["value"] = 25000
        self.runtime.virtual["mute"] = True
        await self.runtime.stop()
        self.assertEqual(sink_level(self.runtime.sinks[0]), {"values": [20000, 25000], "mute": True})

    async def test_partial_hardware_volume_failure_restores_before_activation(self):
        self.runtime.fail_hardware_volume_once = True
        with self.assertRaises(EQError): await self.start()
        self.assertEqual(sink_level(self.runtime.sinks[0]), {"values": [45864, 45864], "mute": False})
        self.assertNotIn(("pactl", "set-default-sink", SINK_NAME), self.runtime.commands)
        self.assertIsNone(self.runtime.process)

    async def test_crash_recovery_uses_last_observed_eq_volume(self):
        await self.start()
        for v in self.runtime.virtual["volume"].values(): v["value"] = 22000
        await self.runtime.scan()
        self.runtime.process.terminate()
        self.runtime.process = self.runtime.node = None
        self.runtime.volume = None
        self.runtime.volume_committed = False
        await self.runtime.recover()
        self.assertEqual(sink_level(self.runtime.sinks[0])["values"], [22000, 22000])
        self.assertFalse(self.runtime.journal.exists())

    async def test_external_hardware_volume_change_is_preserved(self):
        await self.start()
        for v in self.runtime.sinks[0]["volume"].values(): v["value"] = 12000
        await self.runtime.stop()
        self.assertEqual(sink_level(self.runtime.sinks[0])["values"], [12000, 12000])

    async def test_foreign_equalizer_is_never_reused_or_killed(self):
        self.runtime.foreign = True
        with self.assertRaisesRegex(EQError, "already active"): await self.start()
        self.mock_spawn.assert_not_called()
        self.assertEqual(self.runtime.mock_default, "alsa_output.speaker")


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
