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
import subprocess
import time
import uuid
from pathlib import Path

import dk_fs
import dk_process

from .graph import (LEGACY_SINK_NAME, LEVEL, MUTED, PLAYBACK_NAME, SINK_NAME, STAGE_LIBRARY, controls, control_payload,
                    filter_config, neutral, read_controls, same_controls)
from .model import EQError, NodeGone, OutputGone, ToolError, flat_eq
from .storage import atomic_json, safe_directory

TOOLS = ("pipewire", "pw-cli", "pw-dump", "pactl", "python3")
UNITY_VOLUME = 65536  # PulseAudio's native 100% volume, independent of locale.
POLL_SECONDS = 1.5  # Fallback monitor interval when pactl subscribe is unavailable.
EVENT_QUIET = 0.15  # One output switch emits a burst of events; act once it settles,
EVENT_DRAIN_SECONDS = 1.0  # or after this long at most, however busy the event stream is.
TARGET_QUIET = 0.1  # Further changes of the target (a held volume key) settle this briefly; its first one and a card change wait for nothing.
AUDIO_EVENT = re.compile(rb"Event '([a-z]+)' on (sink|server|card) #(\d+)")
START_SECONDS = 6  # PipeWire must expose the filter's controls within this time.
START_POLL_SECONDS = 0.12
TERMINATE_SECONDS = 3
STALE_SINK_SECONDS = 6  # A listed DeckyEQ sink may be our last filter or a copy Decky is unloading (killed after ~5 s).
HANDOFF_SECONDS = 1  # Each pactl call that hands Steam's output back while Decky unloads us.
SETTLE_SECONDS = 0.05  # Lets the last neutral write reach the audio before the filter ends.
BASELINE_SECONDS = 15  # An edit may start from a readback this recent instead of a new pw-dump.
MAX_PENDING = 8  # Legacy volume restores kept for absent devices/ports; the oldest are dropped.
REGISTRY_MISS = re.compile(r'remote \d+: error id:\d+ seq:\d+ res:-2 \(No such file or directory\): (?:no global \d+|unknown resource \d+ op:\d+)')


def environment():
    # The user's audio session, without Decky's bundled libraries; untranslated pactl events and messages,
    # JSON numbers with a decimal point and device names in UTF-8.
    return dk_process.session_env(LC_ALL="C.UTF-8")


def private(text):
    """Text for the panel (e.g. PipeWire's own errors) with the home folder, which names the user, as ~."""
    home = (dk_process.session_env().get("HOME") or str(Path.home())).rstrip("/\\")
    return text.replace(home, "~") if home else text


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
        self.target_index = None  # The target's pactl index: its events (e.g. a port switch) need a quick reaction.
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
        self.closing = False  # Set on unload: a start stops waiting for an old DeckyEQ sink to go.
        self.started_at = -math.inf  # When the running filter became ready (monotonic).
        self.target_at = -math.inf  # The last event of the target: a burst of them is one rescan.
        self.linked = False  # WirePlumber has linked our filter in this session, so it is new enough to.

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
                    if size > limit:
                        raise EQError("Audio service response exceeded its size limit.")
                    return b"".join(chunks)
                size += len(chunk)
                if size <= limit:  # Past it, drain to the end: an unread pipe never lets the tool be reaped.
                    chunks.append(chunk)
        try:
            proc = await asyncio.create_subprocess_exec(executable, *args, env=self.env,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err, code = await asyncio.wait_for(asyncio.gather(
                bounded(proc.stdout, 8 * 1024 * 1024), bounded(proc.stderr, 65536), proc.wait()), timeout)
            errors = "\n".join(line for line in err.decode("utf-8", "replace").splitlines()
                               if not REGISTRY_MISS.fullmatch(line.strip()))
            # pw-cli exits 0 when a write fails. Edits are confirmed by reading them back anyway, but a write to a
            # suspended node and the mute in stop() have only this to go on.
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
                # Read the pipes to their end first: a full one is paused, and wait() waits for them to close.
                await asyncio.gather(proc.stdout.read(), proc.stderr.read(), proc.wait(), return_exceptions=True)

    async def wait_for_change(self, timeout):
        """Sleep until pactl reports a sink, card or server change, or the timeout ends.
        Returns False on timeout. Without a working subscription, polls every POLL_SECONDS."""
        subscriber = self.subscriber
        if subscriber is None or subscriber.returncode is not None:
            subscriber = self.subscriber = None
            if self.supported:
                try:
                    # Like the filter: it ends with the plugin even if Decky is killed.
                    subscriber = self.subscriber = await asyncio.create_subprocess_exec(
                        *dk_process.spawn_argv([self.tools["pactl"], "subscribe"], self.env), env=self.env,
                        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL)
                except OSError:
                    pass  # Polled below instead; the next call tries to subscribe again.
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
            # Headphones plugged in under the speaker EQ: rescan now, not after the burst settles; undrained
            # events just wake the next call. So does the target's first change after a quiet spell (its port
            # switching); further ones (each step of a held volume key) settle briefly into one rescan.
            target = lambda match: match and match[2] == b"sink" and int(match[3]) == self.target_index
            fast = target(event)
            if fast:
                first = time.monotonic() - self.target_at > TARGET_QUIET
                self.target_at = time.monotonic()
                if first:
                    return True
            quiet = TARGET_QUIET if fast else EVENT_QUIET
            drained, settled = time.monotonic() + EVENT_DRAIN_SECONDS, time.monotonic() + quiet
            try:
                while event[2] != b"card" and time.monotonic() < min(drained, settled):
                    line = await asyncio.wait_for(subscriber.stdout.readline(),
                                                  max(0.0, min(drained, settled) - time.monotonic()))
                    later = AUDIO_EVENT.match(line)
                    if not line or later and later[2] == b"card":
                        break
                    if target(later):
                        self.target_at = time.monotonic()
                    if not fast or target(later):
                        settled = time.monotonic() + quiet
            except asyncio.TimeoutError:
                pass
            return True
        except (OSError, ValueError):  # ValueError: an over-long line from the subscriber.
            await self.close_events()
            return True

    def abandon(self):
        """End the filter and the event subscriber at once, without the ramp (see Service.abandon).
        WirePlumber then links the streams straight to the output, as after a crash."""
        self.closing = True
        if self.target and self.supported:
            # Like stop(): Steam's output goes back to the filter's own output, not to any fallback. Blocking
            # but bounded, since nothing awaited here would finish.
            pactl = lambda *args: dk_process.run([self.tools["pactl"], *args], HANDOFF_SECONDS, env=self.env,
                                                 capture_output=True, check=True).stdout
            try:
                if pactl("get-default-sink").strip() == SINK_NAME:
                    pactl("set-default-sink", self.target)
            except (OSError, subprocess.SubprocessError):
                pass  # Decky is stopping us: WirePlumber picks an output itself, as after a crash.
            self.target = None  # Once: Decky 3.3 unloads us again for _uninstall, within the same ~5 s.
        for proc in (self.process, self.subscriber):
            if proc is not None and proc.returncode is None:
                try:
                    proc.send_signal(signal.SIGTERM)
                except ProcessLookupError:
                    pass

    async def close_events(self):
        subscriber, self.subscriber = self.subscriber, None
        if subscriber is not None and subscriber.returncode is None:
            try:
                subscriber.kill()
            except ProcessLookupError:
                pass
            await asyncio.gather(subscriber.stdout.read(), subscriber.wait(), return_exceptions=True)  # As in run().

    async def json_run(self, tool, *args, empty=None):
        text = await self.run(tool, *args)  # A ToolError (no answer) passes through unchanged.
        if empty is not None and not text.strip():
            return empty
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
        target = next((s for s in sinks if isinstance(s, dict) and self.target and s.get("name") == self.target), None)
        self.target_index = target.get("index") if target and type(target.get("index")) is int else None
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
        args = [str(node_id)] if node_id is not None else []
        # pw-dump <id> prints nothing, not [], once that object is gone (e.g. PipeWire restarted).
        node = self._own_node(await self.json_run("pw-dump", *args, empty=[] if args else None))
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
        saved = dk_fs.parse_json(dk_fs.read_bytes(str(self.journal), no_links=True))
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

    async def start(self, eq, target, boost=0.0, speakers_only=False):
        """Start the filter in front of target. Nothing else in the audio session changes. OutputGone: the
        target went away (or, with speakers_only, left its speaker port) meanwhile, so nothing runs."""
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
            if self.closing:  # The unload must not wait for this or start a filter.
                raise EQError("DeckyEQ is unloading.")
            if not self.sink_present or time.monotonic() - start >= STALE_SINK_SECONDS:
                break
            await asyncio.sleep(START_POLL_SECONDS)
        if target not in {o["name"] for o in self.outputs}:
            raise OutputGone("Selected output is not connected.")
        if self.sink_present and self.sink_owner != self.owner:
            raise EQError("Another DeckyEQ audio node is already active. Reload Decky before enabling EQ.")
        if not self.stage_library.is_file():
            raise EQError("DeckyEQ's audio stage (bin/deckyeq_dsp.so) is missing. Reinstall the plugin.")
        self.target = target
        # Started neutral, then moved to the settings like any edit: no cut when WirePlumber links it in.
        atomic_json(self.config, filter_config(flat_eq(), target, self.owner, self.stage_library))
        try:
            await self._spawn(eq, boost, speakers_only)
        except BaseException:
            await self.stop()
            raise

    async def _spawn(self, eq, boost, speakers_only=False):
        self.log = ""
        child_env = self.env.copy()
        # Do not inherit user filter fragments into our private client graph.
        child_env["PIPEWIRE_CONFIG_DIR"] = str(self.directory)
        child_env["PIPEWIRE_CONFIG_PREFIX"] = ""
        child_env.pop("PIPEWIRE_CONFIG_NAME", None)
        # Ends with the plugin even if Decky is killed (parent-death signal, dk_process).
        self.process = await asyncio.create_subprocess_exec(
            *dk_process.spawn_argv([self.tools["pipewire"], "-c", self.config.name], child_env), env=child_env,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE, start_new_session=True)
        self.reader = asyncio.create_task(self._log_reader())
        expected = controls(eq, boost)
        start = neutral()
        deadline = time.monotonic() + START_SECONDS
        while True:
            if self.closing:  # Unloading: start() stops the half-started filter instead of waiting it out.
                raise EQError("DeckyEQ is unloading.")
            if self.process.returncode is not None:
                raise EQError("PipeWire could not start the equalizer: " + private(self.log)[-240:])
            if time.monotonic() >= deadline:
                await self.scan()  # An output unplugged meanwhile is no WirePlumber problem.
                if self.target not in {o["name"] for o in self.outputs}:
                    raise OutputGone("Selected output is not connected.")
                if self.linked:  # It linked us before: restarting with PipeWire, not too old. Retried quietly.
                    raise ToolError("WirePlumber has not linked the equalizer yet.")
                raise EQError("WirePlumber did not insert the equalizer in front of the output. "
                              "DeckyEQ needs WirePlumber 0.5 or newer (SteamOS 3.7+).")
            objects = await self.json_run("pw-dump")
            node = self._own_node(objects)
            # Ready once the controls are live and WirePlumber has linked the filter to its output.
            if node is not None and same_controls(read_controls(node), start) and self._linked(objects):
                self.node, self.linked = node, True
                break
            await asyncio.sleep(START_POLL_SECONDS)
        self.last_controls, self.verified_at = start, time.monotonic()
        self.started_at = self.verified_at
        await self.scan()  # Learn our new sink's index, so its own events need no rescan, and the output's port now.
        output = next((o for o in self.outputs if o["name"] == self.target), None)
        if output is None or speakers_only and not output["speaker"]:
            # Unplugged, or headphones plugged in, while starting: never raised to the speaker EQ.
            raise OutputGone("Selected output is not connected." if output is None else
                             "Speakers only is on, so EQ is off on this output.")
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
        # the filters change in one write: PipeWire 1.6 clears a biquad's history on every change of
        # it, so intermediate steps would only add more clicks, not smooth one.
        low = min(old[LEVEL], expected[LEVEL])
        if old[LEVEL] > low:
            await self._set(node_id, {LEVEL: low})
        await self._set(node_id, {k: v for k, v in expected.items() if k != LEVEL})
        staged = {**expected, LEVEL: low}
        actual_node = await self.dump_node(node_id)
        confirmed = self._confirmed(actual_node, serial, staged)
        if expected[LEVEL] > low:
            await self._set(node_id, {LEVEL: expected[LEVEL]})
            actual_node = await self.dump_node(node_id)
            confirmed = self._confirmed(actual_node, serial, expected)
        self.node, self.last_controls = actual_node, expected
        self.verified_at = time.monotonic() if confirmed else -math.inf

    async def stop(self, mute=False):
        """End the filter. WirePlumber then links its streams straight to the output. mute ends it at
        once, silenced by one write instead of the neutral ramp (the speaker EQ must not reach headphones)."""
        if mute and self.running:
            try:  # The stage glides the level ~10 ms, so muting does not click.
                await self._set(str(self.node["id"]), {LEVEL: MUTED, "stage:gain": 0.0})
            except Exception:  # Best effort: the filter process must end whatever went wrong.
                pass
        if self.target and self.supported:
            try:
                # If Steam's output is the filter itself, hand it to the filter's own output first
                # rather than letting WirePlumber fall back to any other device.
                if (await self.run("pactl", "get-default-sink")).strip() == SINK_NAME:
                    await self.run("pactl", "set-default-sink", self.target)
            except Exception:
                pass  # Best effort: WirePlumber picks an output itself, and the filter must end regardless.
        if self.running and not mute:
            try:  # Back to neutral first, so playing on without the filter sounds the same.
                await self._write(neutral(), use_cache=False)
                await asyncio.sleep(SETTLE_SECONDS)
            except Exception:
                pass  # Best effort: the filter process must end whatever went wrong.
        if self.process is not None and self.process.returncode is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.process.wait(), TERMINATE_SECONDS)
            except asyncio.TimeoutError:
                try:
                    # The filter leads its own session; kill the group so nothing can linger.
                    if hasattr(os, "killpg") and self.process.returncode is None:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    else:
                        self.process.kill()
                except (ProcessLookupError, PermissionError):
                    pass
                try:  # Bounded: a child that left the group would keep its stderr pipe, and wait(), open.
                    await asyncio.wait_for(self.process.wait(), TERMINATE_SECONDS)
                except asyncio.TimeoutError:
                    pass
        if self.reader:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        self.reader = self.process = self.node = self.last_controls = None
        self.target = None
