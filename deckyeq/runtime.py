"""Unprivileged PipeWire filter lifecycle and live control readback.

The equalizer is a WirePlumber smart filter placed in front of one output. WirePlumber
links whatever plays to that output through it, so DeckyEQ never changes the default
output, moves streams or touches volumes; if the filter stops, playback continues on
the output directly at the user's volume."""
from __future__ import annotations

import asyncio
import json
import math
import os
import platform
import re
import shutil
import signal
import stat
import time
import uuid
from pathlib import Path

from .graph import (LEGACY_SINK_NAME, LEVEL, PLAYBACK_NAME, SINK_NAME, STAGE_LIBRARY, controls, control_payload,
                    filter_config, neutral, read_controls, same_controls)
from .model import EQError, NodeGone, ToolError, flat_eq, frequency
from .storage import atomic_json, read_json, safe_directory

TOOLS = ("pipewire", "pw-cli", "pw-dump", "pactl", "python3")
UNITY_VOLUME = 65536  # PulseAudio's native 100% volume, independent of locale.
POLL_SECONDS = 1.5  # Fallback monitor interval when pactl subscribe is unavailable.
EVENT_QUIET = 0.15  # One output switch emits a burst of events; act once it settles,
EVENT_DRAIN_SECONDS = 1.0  # or after this long at most, however busy the event stream is.
AUDIO_EVENT = re.compile(rb"Event '([a-z]+)' on (sink|server|card) #(\d+)")
START_SECONDS = 6  # PipeWire must expose the filter's controls within this time.
START_POLL_SECONDS = 0.12
TERMINATE_SECONDS = 3
STALE_SINK_SECONDS = 1.5  # A just-stopped filter can stay listed by pipewire-pulse for a moment.
OWN_STALE_SECONDS = 4  # Our own previous filter (a rebuild) may linger longer; it is never "another" one.
STEP_DB = 1.5  # Larger filter gain changes are written in steps of about this size to avoid zipper
MAX_STEPS = 5  # noise, in at most this many pw-cli writes. The stage glides level, width and boost itself.
STEP_OCTAVES = 0.5  # Filter frequency changes move in steps of at most this many octaves.
SETTLE_SECONDS = 0.05  # Lets the last neutral write reach the audio before the filter ends.
BASELINE_SECONDS = 15  # An edit may start from a readback this recent instead of a new pw-dump.
MAX_PENDING = 8  # Legacy volume restores kept for absent devices/ports; the oldest are dropped.
REGISTRY_MISS = re.compile(r'remote \d+: error id:\d+ seq:\d+ res:-2 \(No such file or directory\): (?:no global \d+|unknown resource \d+ op:\d+)')


def environment():
    env = os.environ.copy()
    # Decky is a frozen application. Do not load its bundled shared libraries
    # into system tools or accidentally start another copy of PluginLoader.
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env.pop("LD_LIBRARY_PATH_ORIG")
    else:
        env.pop("LD_LIBRARY_PATH", None)
    if platform.system() == "Linux":
        import pwd
        uid = os.geteuid()
        env["HOME"] = pwd.getpwuid(uid).pw_dir
        runtime = f"/run/user/{uid}"
        try:
            info = os.stat(runtime)
            if stat.S_ISDIR(info.st_mode) and info.st_uid == uid:
                env["XDG_RUNTIME_DIR"] = runtime
        except OSError:
            pass
    return env


def active_port(sink):
    port = sink.get("active_port") or ""
    if isinstance(port, dict):
        port = port.get("name", "")
    return port if isinstance(port, str) else ""


def normalize_sinks(items):
    if not isinstance(items, list) or len(items) > 512:
        raise EQError("Invalid audio output list.")
    result = []
    for sink in items:
        if not isinstance(sink, dict):
            continue
        name = sink.get("name", "")
        if not isinstance(name, str) or name in (SINK_NAME, LEGACY_SINK_NAME):
            continue
        # Exclude EasyEffects/other virtual processors to avoid accidental double EQ.
        if not name.startswith(("alsa_output.", "bluez_output.", "bluez_sink.")):
            continue
        port = active_port(sink)
        speaker = "speaker" in port.lower() and not name.startswith("bluez")
        result.append({"name": name, "description": str(sink.get("description") or name),
                       "port": port, "speaker": speaker})
    return result


def suspended(node):
    # A suspended filter keeps reporting the Props it last processed with, but it keeps
    # later writes and applies them once audio flows (verified on PipeWire 1.6.8).
    return node.get("info", {}).get("state") == "suspended"


def checked_level(value):
    if not isinstance(value, dict) or type(value.get("mute")) is not bool:
        raise EQError("The audio output returned an invalid volume.")
    values = value.get("values")
    if not isinstance(values, list) or not 1 <= len(values) <= 32 or any(type(v) is not int or not 0 <= v <= 10 * UNITY_VOLUME for v in values):
        raise EQError("The audio output returned invalid channel volumes.")
    return {"values": list(values), "mute": value["mute"]}


def sink_level(sink):
    volume = sink.get("volume", {})
    if not isinstance(volume, dict):
        raise EQError("The audio output returned invalid channel volumes.")
    channels = sink.get("channel_map", list(volume))
    if isinstance(channels, str):
        channels = [part.strip() for part in channels.split(",")]
    if not isinstance(channels, list) or any(not isinstance(c, str) for c in channels) or set(channels) != set(volume) or len(channels) != len(volume):
        raise EQError("The audio output returned an invalid channel map.")
    if any(not isinstance(entry, dict) for entry in volume.values()):
        raise EQError("The audio output returned invalid channel volumes.")
    return checked_level({"values": [volume[c].get("value") for c in channels], "mute": sink.get("mute")})


class Runtime:
    def __init__(self, directory):
        self.directory = safe_directory(Path(directory))
        self.journal = self.directory / "routing.json"
        self.config = self.directory / "deckyeq.conf"
        self.owner = uuid.uuid4().hex
        self.env = environment()
        self.tools = {tool: shutil.which(tool) for tool in TOOLS}
        self.stage_library = STAGE_LIBRARY
        self.sink_owner = None  # deckyeq.owner of the listed DeckyEQ sink, when pactl reports it.
        self.sink_index = None  # Our sink's pactl index: its own events need no reaction.
        self.supported = platform.system() == "Linux" and getattr(os, "geteuid", lambda: 0)() != 0 and all(self.tools.values())
        self.reason = None if self.supported else "Requires a non-root Decky session on Linux with system PipeWire and PulseAudio compatibility tools."
        self.process = None
        self.reader = None
        self.log = ""
        self.node = None
        self.target = None
        self.last_controls = None
        self.verified_at = -math.inf  # Not 0: the monotonic clock starts near 0 at boot.
        self.outputs = []
        self.default = ""
        self.sink_present = False
        self.pending = []  # Legacy restores whose device or port is absent (see recover()).
        self.subscriber = None

    @property
    def running(self):
        return self.process is not None and self.process.returncode is None and self.node is not None

    async def run(self, tool, *args, timeout=4):
        executable = self.tools.get(tool)
        if not executable:
            raise EQError(f"System tool {tool} is unavailable.")
        if "XDG_RUNTIME_DIR" not in self.env:  # The user session may come up after the plugin.
            self.env = environment()
        proc = None
        async def bounded(stream, limit):
            chunks, size = [], 0
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    return b"".join(chunks)
                size += len(chunk)
                if size > limit:
                    raise EQError("Audio service response exceeded its size limit.")
                chunks.append(chunk)
        try:
            proc = await asyncio.create_subprocess_exec(executable, *args, env=self.env,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err, code = await asyncio.wait_for(asyncio.gather(
                bounded(proc.stdout, 8 * 1024 * 1024), bounded(proc.stderr, 65536), proc.wait()), timeout)
            errors = "\n".join(line for line in err.decode("utf-8", "replace").splitlines()
                               if not REGISTRY_MISS.fullmatch(line.strip()))
            if code or (tool == "pw-cli" and re.search(r"error|failed|unknown global", errors, re.I)):
                raise ToolError(f"{tool}: {(errors.strip() or 'audio command failed')[:240]}")
            return out.decode("utf-8", "strict")
        except (OSError, UnicodeError, asyncio.TimeoutError):
            raise ToolError("Could not communicate with the desktop audio service.") from None
        finally:
            if proc is not None and proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()

    async def wait_for_change(self, timeout):
        """Sleep until pactl reports a sink, card or server change, or the timeout ends.
        Returns False on timeout. Without a working subscription, polls every POLL_SECONDS."""
        subscriber = self.subscriber
        if subscriber is None or subscriber.returncode is not None:
            subscriber = self.subscriber = None
            if self.supported:
                try:
                    # Through the worker, like the filter: it ends with the plugin even if Decky is killed.
                    subscriber = self.subscriber = await asyncio.create_subprocess_exec(
                        self.tools["python3"], "-I", str(Path(__file__).with_name("worker.py")), str(os.getpid()),
                        self.tools["pactl"], "subscribe", env=self.env, stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                except OSError:
                    pass
        if subscriber is None:
            await asyncio.sleep(POLL_SECONDS if self.supported else timeout)
            return True
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    line = await asyncio.wait_for(subscriber.stdout.readline(), max(0.0, deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    return False
                if not line:  # The subscription ended; poll until the next call resubscribes.
                    await self.close_events()
                    await asyncio.sleep(POLL_SECONDS)
                    return True
                event = AUDIO_EVENT.match(line)
                # Our own sink changes with every edit and suspend; those need no rescan. Its removal
                # (a crashed filter) does.
                if event and not (event[1] == b"change" and event[2] == b"sink" and self.sink_index is not None
                                  and int(event[3]) == self.sink_index):
                    break
            drained = time.monotonic() + EVENT_DRAIN_SECONDS
            try:
                while time.monotonic() < drained and await asyncio.wait_for(
                        subscriber.stdout.readline(), min(EVENT_QUIET, max(0.0, drained - time.monotonic()))):
                    pass
            except asyncio.TimeoutError:
                pass
            return True
        except (OSError, ValueError):  # ValueError: an over-long line from the subscriber.
            await self.close_events()
            return True

    async def close_events(self):
        subscriber, self.subscriber = self.subscriber, None
        if subscriber is not None and subscriber.returncode is None:
            try:
                subscriber.kill()
            except ProcessLookupError:
                pass
            await subscriber.wait()

    async def json_run(self, tool, *args):
        text = await self.run(tool, *args)  # A ToolError (no answer) passes through unchanged.
        try:
            return json.loads(text)
        except (ValueError, RecursionError):
            raise EQError("The audio service returned invalid data.") from None

    async def scan(self):
        sinks, default = await asyncio.gather(self.json_run("pactl", "--format=json", "list", "sinks"),
                                              self.run("pactl", "get-default-sink"))
        self.outputs, self.default = normalize_sinks(sinks), default.strip()
        self.sink_present = any(s.get("name") in (SINK_NAME, LEGACY_SINK_NAME) for s in sinks if isinstance(s, dict))
        ours = next((s for s in sinks if isinstance(s, dict) and s.get("name") == SINK_NAME), None)
        self.sink_index = ours.get("index") if ours and type(ours.get("index")) is int else None
        props = ours.get("properties") if ours else None
        self.sink_owner = props.get("deckyeq.owner") if isinstance(props, dict) else None
        return self.outputs, self.default

    async def _set_level(self, target, level):
        level = checked_level(level)
        if level["mute"]:
            await self.run("pactl", "set-sink-mute", target, "1")
        await self.run("pactl", "set-sink-volume", target, *(str(v) for v in level["values"]))
        if not level["mute"]:
            await self.run("pactl", "set-sink-mute", target, "0")
        sinks = await self.json_run("pactl", "--format=json", "list", "sinks")
        sink = next((s for s in sinks if s.get("name") == target), None)
        if sink is None or sink_level(sink) != level:
            raise EQError("The audio output did not confirm its new volume.")

    async def _restore_level(self, entry, sinks):
        """True once handled: the volume was restored, or the user has changed it since.
        False keeps the entry until its device and port return: WirePlumber restores
        route volumes per port, so a route held at 100% comes back at 100%."""
        sink = next((s for s in sinks if isinstance(s, dict) and s.get("name") == entry["target"]), None)
        if sink is None or (entry["port"] and active_port(sink) != entry["port"]):
            return False
        actual = sink_level(sink)
        if not entry["force"] and actual["values"] != [UNITY_VOLUME] * len(actual["values"]):
            return True
        await self._set_level(entry["target"], entry["level"])
        return True

    async def dump_node(self, node_id=None):
        """Our filter node, from the whole graph or, once its id is known, from just that object."""
        node = self._own_node(await self.json_run("pw-dump", *([str(node_id)] if node_id is not None else [])))
        if node is None:
            raise NodeGone("DeckyEQ's live audio node is unavailable.")
        return node

    async def _log_reader(self):
        try:
            while self.process and self.process.stderr:
                data = await self.process.stderr.read(4096)
                if not data:
                    break
                self.log = (self.log + data.decode("utf-8", "replace"))[-4096:]
        except asyncio.CancelledError:
            pass

    # DeckyEQ 1.0 routed playback through a virtual sink with the hardware held at 100% and
    # journalled what it owed. That model is gone; recover() only settles a journal left by it.

    def _save_pending(self):
        atomic_json(self.journal, {"version": 3, "pending": self.pending})

    @staticmethod
    def _entry(value):
        if (not isinstance(value, dict) or not isinstance(value.get("target"), str) or
                not isinstance(value.get("port"), str) or type(value.get("force")) is not bool):
            raise EQError("Invalid saved audio volume.")
        return {"target": value["target"], "port": value["port"], "force": value["force"],
                "level": checked_level(value.get("level"))}

    def _read_journal(self):
        """Return (previous default, restore entries). Version 1/2 journals have no port."""
        saved = read_json(self.journal)
        if not isinstance(saved, dict) or saved.get("version") not in (1, 2, 3):
            raise EQError("Invalid audio restoration journal.")
        previous = saved.get("previous")
        if previous is not None and not isinstance(previous, str):
            raise EQError("Invalid saved default output.")
        pending = saved.get("pending", [])
        if not isinstance(pending, list) or len(pending) > MAX_PENDING:
            raise EQError("Invalid saved audio volumes.")
        entries = [self._entry(item) for item in pending]
        volume, committed = saved.get("volume"), saved.get("volume_committed", False)
        if volume:
            if not isinstance(volume, dict) or type(committed) is not bool:
                raise EQError("Invalid saved audio volume.")
            entries.append(self._entry({"target": volume.get("target"), "port": volume.get("port", ""),
                                        "force": not committed,
                                        "level": volume.get("last" if committed else "original")}))
        return previous, entries[-MAX_PENDING:]

    async def recover(self):
        if not self.supported or not self.journal.exists():
            return
        try:
            previous, entries = self._read_journal()
        except (OSError, ValueError, TypeError, RecursionError):
            # An unreadable journal must not block EQ forever; keep it for diagnosis.
            os.replace(self.journal, self.journal.with_name("routing.invalid.json"))
            raise EQError("The previous audio restore record was unreadable and was set aside.") from None
        try:
            await self.scan()
            names = {o["name"] for o in self.outputs}
            sinks = await self.json_run("pactl", "--format=json", "list", "sinks")
            remaining = []
            for entry in entries:
                try:
                    if not await self._restore_level(entry, sinks):
                        remaining.append(entry)
                except ToolError:
                    remaining.append(entry)  # The tool did not answer: try again later.
                except EQError:
                    pass  # Not confirmed (e.g. a device that rounds volumes): drop it, do not retry forever.
            if self.default == LEGACY_SINK_NAME and previous in names:
                await self.run("pactl", "set-default-sink", previous)
            # A parent-death signal removes the old virtual sink. WirePlumber moves
            # its streams to a real output; do not claim or kill an unknown process.
            changed = remaining != self.pending
            self.pending = remaining
            if remaining:
                if changed or previous is not None:  # Do not rewrite an unchanged journal on every event.
                    self._save_pending()
            else:
                self.journal.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError, KeyError):
            raise EQError("Could not restore audio routing from the previous session.") from None

    async def start(self, eq, target, boost=0.0):
        """Start the filter in front of target. Nothing else in the audio session changes."""
        if not self.supported:
            raise EQError(self.reason)
        if self.process is not None:
            await self.stop()
        try:
            await self.recover()
        except EQError:
            pass  # A DeckyEQ 1.0 journal must never block EQ; recover() keeps what it could not settle.
        start = time.monotonic()
        while True:
            await self.scan()
            mine = self.sink_owner == self.owner  # Our previous filter, e.g. during a rebuild.
            if not self.sink_present or time.monotonic() - start >= (OWN_STALE_SECONDS if mine else STALE_SINK_SECONDS):
                break
            await asyncio.sleep(START_POLL_SECONDS)
        if target not in {o["name"] for o in self.outputs}:
            raise EQError("Selected output is not connected.")
        if self.sink_present and self.sink_owner != self.owner:
            raise EQError("Another DeckyEQ audio node is already active. Reload Decky before enabling EQ.")
        if not self.stage_library.is_file():
            raise EQError("DeckyEQ's audio stage (bin/deckyeq_dsp.so) is missing. Reinstall the plugin.")
        self.target = target
        # Started neutral, then moved to the settings like any edit: no cut when WirePlumber links it in.
        atomic_json(self.config, filter_config(flat_eq(), target, self.owner, self.stage_library))
        try:
            await self._spawn(eq, boost)
            await self.scan()  # Learn our new sink's index, so its own events need no rescan.
        except BaseException:
            await self.stop()
            raise

    async def _spawn(self, eq, boost):
        self.log = ""
        child_env = self.env.copy()
        # Do not inherit user filter fragments into our private client graph.
        child_env["PIPEWIRE_CONFIG_DIR"] = str(self.directory)
        child_env["PIPEWIRE_CONFIG_PREFIX"] = ""
        child_env.pop("PIPEWIRE_CONFIG_NAME", None)
        self.process = await asyncio.create_subprocess_exec(
            self.tools["python3"], "-I", str(Path(__file__).with_name("worker.py")), str(os.getpid()),
            self.tools["pipewire"], "-c", self.config.name, env=child_env,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE, start_new_session=True)
        self.reader = asyncio.create_task(self._log_reader())
        expected = controls(eq, boost)
        start = neutral()
        deadline = time.monotonic() + START_SECONDS
        while True:
            if self.process.returncode is not None:
                raise EQError("PipeWire could not start the equalizer: " + self.log[-240:])
            if time.monotonic() >= deadline:
                raise EQError("WirePlumber did not insert the equalizer in front of the output. "
                              "DeckyEQ needs WirePlumber 0.5 or newer (SteamOS 3.7+).")
            objects = await self.json_run("pw-dump")
            node = self._own_node(objects)
            # Ready once the controls are live and WirePlumber has linked the filter to its output.
            if node is not None and same_controls(read_controls(node), start) and self._linked(objects):
                self.node = node
                break
            await asyncio.sleep(START_POLL_SECONDS)
        self.last_controls, self.verified_at = start, time.monotonic()
        await self._write(expected, use_cache=True)

    def _own_node(self, objects):
        if not isinstance(objects, list):
            return None
        props = lambda o: ((o.get("info") or {}).get("props") or {}) if isinstance(o.get("info"), dict) else {}
        nodes = [o for o in objects if isinstance(o, dict) and o.get("type") == "PipeWire:Interface:Node"
                 and props(o).get("node.name") == SINK_NAME and props(o).get("deckyeq.owner") == self.owner
                 and type(o.get("id")) is int]
        return nodes[0] if len(nodes) == 1 else None

    def _linked(self, objects):
        """True when WirePlumber has linked our filter's output to the target (smart filter active)."""
        info = lambda o: o.get("info") if isinstance(o.get("info"), dict) else {}
        names = {o.get("id"): info(o).get("props") or {} for o in objects
                 if isinstance(o, dict) and o.get("type") == "PipeWire:Interface:Node"}
        for link in objects:
            if isinstance(link, dict) and link.get("type") == "PipeWire:Interface:Link":
                source = names.get(info(link).get("output-node-id"), {})
                sink = names.get(info(link).get("input-node-id"), {})
                if (source.get("node.name") == PLAYBACK_NAME and source.get("deckyeq.owner") == self.owner
                        and sink.get("node.name") == self.target):
                    return True
        return False

    def _confirmed(self, node, serial, expected):
        """True when the readback matches; False while suspended, when it cannot be read back yet."""
        if node.get("info", {}).get("props", {}).get("object.serial") == serial:
            if suspended(node):
                return False
            if same_controls(read_controls(node), expected):
                return True
        raise EQError("PipeWire did not confirm the new EQ settings. The change was not saved.")

    async def _set(self, node_id, values):
        await self.run("pw-cli", "set-param", node_id, "Props", json.dumps(control_payload(values)))

    async def apply(self, eq, max_age=0.0, boost=0.0):
        """Write controls and read them back. With max_age (the monitor's heartbeat), an unchanged
        setting whose last readback is younger than max_age is left alone."""
        if not self.running:
            return
        expected = controls(eq, boost)
        age = time.monotonic() - self.verified_at
        cached = self.last_controls is not None and self.node is not None and self.sink_present
        if cached and expected == self.last_controls and age < max_age:
            return
        try:
            await self._write(expected, cached and age < BASELINE_SECONDS and not suspended(self.node))
        except BaseException:
            # A failed or cancelled write leaves the node unknown: the next apply (e.g. a rollback)
            # must read it back instead of trusting the cache.
            self.last_controls, self.verified_at = None, -math.inf
            raise

    async def _write(self, expected, use_cache):
        if use_cache:
            # Start from the last readback; the writes below are still read back before confirming.
            node, actual = self.node, self.last_controls
        else:
            node = await self.dump_node(self.node["id"])
            actual = read_controls(node)
        node_id, serial = str(node["id"]), node.get("info", {}).get("props", {}).get("object.serial")
        if suspended(node):
            # No audio flows, so no level ramp is needed; the monitor verifies once audio resumes.
            if expected != self.last_controls:
                await self._set(node_id, expected)
            self.node, self.last_controls, self.verified_at = node, expected, -math.inf
            return
        if any(key not in actual for key in expected):
            raise EQError("The audio node no longer exposes the expected EQ controls.")
        if same_controls(actual, expected):
            self.node, self.last_controls, self.verified_at = node, expected, time.monotonic()
            return
        old = {key: actual[key] for key in expected}
        # Lower the level before changing filters and raise it only after they are confirmed, so a
        # partial write never plays new boosts at the old level. The stage glides each level change;
        # filter gains move in interpolated steps against zipper noise.
        low = min(old[LEVEL], expected[LEVEL])
        if old[LEVEL] > low:
            await self._set(node_id, {LEVEL: low})
        largest = max((abs(v - old[k]) for k, v in expected.items() if k.endswith(":Gain")), default=0.0)
        glides = {k for k, v in expected.items() if k.endswith(":Freq") and v > 0 and old[k] > 0 and v != old[k]}
        octaves = max((abs(math.log2(expected[k] / old[k])) for k in glides), default=0.0)
        steps = min(MAX_STEPS, max(1, math.ceil(largest / STEP_DB), math.ceil(octaves / STEP_OCTAVES)))
        for step in range(1, steps + 1):
            t = step / steps
            values = {k: frequency(old[k] * (v / old[k]) ** t) if k in glides else old[k] + (v - old[k]) * t
                      for k, v in expected.items() if k != LEVEL}
            await self._set(node_id, values)
        staged = {**expected, LEVEL: low}
        actual_node = await self.dump_node(node_id)
        confirmed = self._confirmed(actual_node, serial, staged)
        if expected[LEVEL] > low:
            await self._set(node_id, {LEVEL: expected[LEVEL]})
            actual_node = await self.dump_node(node_id)
            confirmed = self._confirmed(actual_node, serial, expected)
        self.node, self.last_controls = actual_node, expected
        self.verified_at = time.monotonic() if confirmed else -math.inf

    async def stop(self):
        """End the filter. WirePlumber then links its streams straight to the output."""
        if self.target and self.supported:
            try:
                # If Steam's output is the filter itself, hand it to the filter's own output first
                # rather than letting WirePlumber fall back to any other device.
                if (await self.run("pactl", "get-default-sink")).strip() == SINK_NAME:
                    await self.run("pactl", "set-default-sink", self.target)
            except EQError:
                pass
        if self.running:
            try:  # Back to neutral in steps, so playing on without the filter sounds the same.
                await self._write(neutral(), use_cache=False)
                await asyncio.sleep(SETTLE_SECONDS)
            except EQError:
                pass
        if self.process is not None and self.process.returncode is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.process.wait(), TERMINATE_SECONDS)
            except asyncio.TimeoutError:
                try:
                    # The worker leads its own session; kill the group so nothing can linger.
                    if hasattr(os, "killpg") and self.process.returncode is None:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    else:
                        self.process.kill()
                except (ProcessLookupError, PermissionError):
                    pass
                await self.process.wait()
        if self.reader:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        self.reader = self.process = self.node = self.last_controls = None
        self.target = None
