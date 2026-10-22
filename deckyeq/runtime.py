"""Unprivileged PipeWire lifecycle, live control readback and reversible routing."""
from __future__ import annotations

import asyncio
import json
import math
import os
import platform
import re
import shutil
import stat
import time
import uuid
from pathlib import Path

from .graph import SINK_NAME, PLAYBACK_NAME, controls, control_payload, filter_config, read_controls, same_controls
from .model import EQError
from .storage import atomic_json, read_json, safe_directory

TOOLS = ("pipewire", "pw-cli", "pw-dump", "pactl", "python3")
UNITY_VOLUME = 65536  # PulseAudio's native 100% volume, independent of locale.
MAX_PENDING = 8  # Volume restores kept for absent devices/ports; the oldest are dropped.
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
        props = sink.get("properties")
        props = props if isinstance(props, dict) else {}
        if not isinstance(name, str) or name == SINK_NAME:
            continue
        # Exclude EasyEffects/other virtual processors to avoid accidental double EQ.
        if not name.startswith(("alsa_output.", "bluez_output.", "bluez_sink.")):
            continue
        port = active_port(sink)
        speaker = "speaker" in port.lower() and not name.startswith("bluez")
        result.append({"name": name, "description": str(sink.get("description") or name),
                       "index": sink.get("index"), "port": port, "speaker": speaker,
                       "serial": str(props.get("object.serial", ""))})
    return result


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
        self.supported = platform.system() == "Linux" and getattr(os, "geteuid", lambda: 0)() != 0 and all(self.tools.values())
        self.reason = None if self.supported else "Requires a non-root Decky session on Linux with system PipeWire and PulseAudio compatibility tools."
        self.process = None
        self.reader = None
        self.log = ""
        self.node = None
        self.target = None
        self.previous_default = None
        self.last_controls = None
        self.verified_at = 0.0
        self.moves = {}
        self.outputs = []
        self.default = ""
        self.sink_present = False
        self.volume = None
        self.volume_committed = False
        self.pending = []  # Restores for earlier sessions whose device or port is absent.

    @property
    def running(self):
        return self.process is not None and self.process.returncode is None and self.node is not None

    async def run(self, tool, *args, timeout=4):
        executable = self.tools.get(tool)
        if not executable:
            raise EQError(f"System tool {tool} is unavailable.")
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
                raise EQError(f"{tool}: {(errors.strip() or 'audio command failed')[:240]}")
            return out.decode("utf-8", "strict")
        except (OSError, UnicodeError, asyncio.TimeoutError):
            raise EQError("Could not communicate with the desktop audio service.") from None
        finally:
            if proc is not None and proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()

    async def json_run(self, tool, *args):
        try:
            return json.loads(await self.run(tool, *args))
        except (ValueError, RecursionError):
            raise EQError("The audio service returned invalid data.") from None

    async def scan(self):
        sinks, default = await asyncio.gather(self.json_run("pactl", "--format=json", "list", "sinks"),
                                              self.run("pactl", "get-default-sink"))
        self.outputs, self.default = normalize_sinks(sinks), default.strip()
        self.sink_present = any(s.get("name") == SINK_NAME for s in sinks if isinstance(s, dict))
        if self.volume and self.volume_committed:
            virtual = next((s for s in sinks if s.get("name") == SINK_NAME), None)
            if virtual:
                level = sink_level(virtual)
                if level != self.volume["last"]:
                    self.volume["last"] = level
                    self._save_journal()
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

    def _session_entry(self):
        """The restore owed for the running session: its last EQ volume once committed."""
        if not self.volume:
            return None
        committed = self.volume_committed
        return {"target": self.volume["target"], "port": self.volume["port"], "force": not committed,
                "level": self.volume["last"] if committed else self.volume["original"]}

    async def dump_node(self):
        objects = await self.json_run("pw-dump")
        if not isinstance(objects, list):
            raise EQError("Invalid PipeWire graph.")
        candidates = []
        for item in objects:
            if not isinstance(item, dict) or item.get("type") != "PipeWire:Interface:Node":
                continue
            props = item.get("info", {}).get("props", {})
            if props.get("node.name") == SINK_NAME and props.get("deckyeq.owner") == self.owner:
                candidates.append(item)
        if len(candidates) != 1:
            raise EQError("DeckyEQ's live audio node is unavailable.")
        node = candidates[0]
        if type(node.get("id")) is not int:
            raise EQError("Invalid audio node identity.")
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

    async def _inputs(self):
        inputs = await self.json_run("pactl", "--format=json", "list", "sink-inputs")
        if not isinstance(inputs, list) or len(inputs) > 2048:
            raise EQError("Invalid playback stream list.")
        return [i for i in inputs if isinstance(i, dict) and type(i.get("index")) is int]

    @staticmethod
    def _identity(item):
        props = item.get("properties") or {}
        serial = props.get("object.serial")
        return str(serial) if serial is not None else ""

    @staticmethod
    def _own_input(item):
        props = item.get("properties") or {}
        return props.get("node.name") == PLAYBACK_NAME or props.get("media.name") == "DeckyEQ Playback"

    def _save_journal(self):
        atomic_json(self.journal, {"version": 3, "previous": self.previous_default,
                                  "target": self.target, "moves": self.moves,
                                  "volume": self.volume, "volume_committed": self.volume_committed,
                                  "pending": self.pending})

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
                if not await self._restore_level(entry, sinks):
                    remaining.append(entry)
            if self.default == SINK_NAME and previous in names:
                await self.run("pactl", "set-default-sink", previous)
            # A parent-death signal removes the old virtual sink. WirePlumber moves
            # its streams to a real output; do not claim or kill an unknown process.
            self.pending = remaining
            if remaining:
                self._save_journal()
            else:
                self.journal.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError, KeyError):
            raise EQError("Could not restore audio routing from the previous session.") from None

    async def start(self, eq, target):
        if not self.supported:
            raise EQError(self.reason)
        if self.process is not None:
            await self.stop()
        # Restores still waiting for an absent device or port stay pending and do not block EQ.
        await self.recover()
        await self.scan()
        if target not in {o["name"] for o in self.outputs}:
            raise EQError("Selected output is not connected.")
        if self.sink_present:
            raise EQError("Another DeckyEQ audio node is already active. Reload Decky before enabling EQ.")
        self.previous_default, self.target, self.moves = self.default, target, {}
        sinks = await self.json_run("pactl", "--format=json", "list", "sinks")
        hardware = next((s for s in sinks if isinstance(s, dict) and s.get("name") == target), None)
        if hardware is None:
            raise EQError("Selected output is not connected.")
        level = sink_level(hardware)
        self.volume = {"target": target, "port": active_port(hardware), "original": level, "last": level}
        self.volume_committed = False
        self._save_journal()  # Restoration is durable before the first routing write.
        atomic_json(self.config, filter_config(eq, target, self.owner))
        self.log = ""
        try:
            child_env = self.env.copy()
            # Do not inherit user filter fragments into our private client graph.
            child_env["PIPEWIRE_CONFIG_DIR"] = str(self.directory)
            child_env["PIPEWIRE_CONFIG_PREFIX"] = ""
            child_env.pop("PIPEWIRE_CONFIG_NAME", None)
            self.process = await asyncio.create_subprocess_exec(self.tools["python3"], "-I", str(Path(__file__).with_name("worker.py")),
                str(os.getpid()), self.tools["pipewire"], self.config.name, str(self.journal), self.tools["pactl"], env=child_env,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE, start_new_session=True)
            self.reader = asyncio.create_task(self._log_reader())
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                if self.process.returncode is not None:
                    raise EQError("PipeWire could not start the equalizer: " + self.log[-240:])
                try:
                    node = await self.dump_node()
                    if same_controls(read_controls(node), controls(eq)):
                        self.node = node
                        break
                except EQError:
                    pass
                await asyncio.sleep(0.12)
            if self.node is None:
                raise EQError("PipeWire did not expose all required live EQ controls.")
            self.last_controls, self.verified_at = controls(eq), time.monotonic()
            # Keep the audible setting on the virtual sink, then remove the
            # hardware attenuation before selecting/routing through DeckyEQ.
            await self._set_level(SINK_NAME, level)
            await self._set_level(target, {"values": [UNITY_VOLUME] * len(level["values"]), "mute": True})
            await self.run("pactl", "set-default-sink", SINK_NAME)
            if (await self.run("pactl", "get-default-sink")).strip() != SINK_NAME:
                raise EQError("The EQ output could not be selected.")
            # Move existing streams as well as future/default streams.
            sinks = await self.json_run("pactl", "--format=json", "list", "sinks")
            sources = {s["index"]: s["name"] for s in sinks if s.get("name") in (target, self.previous_default)}
            for stream in await self._inputs():
                if stream.get("sink") not in sources or self._own_input(stream):
                    continue
                serial = self._identity(stream)
                if not serial:
                    continue
                self.moves[serial] = sources[stream["sink"]]
                self._save_journal()
                try:
                    await self.run("pactl", "move-sink-input", str(stream["index"]), SINK_NAME)
                except EQError:
                    # Disappearing playback is benign; a surviving failed move isn't.
                    if any(self._identity(s) == serial for s in await self._inputs()):
                        raise
            self.default = SINK_NAME
            await self._set_level(target, {"values": [UNITY_VOLUME] * len(level["values"]), "mute": False})
            self.volume_committed = True
            self._save_journal()
        except BaseException:
            await self.stop()
            raise

    async def apply(self, eq, max_age=0.0):
        """Write and read back controls. Polling passes max_age to skip the costly pw-dump
        while scan() still lists our sink and the last readback is recent and unchanged."""
        if not self.running:
            return
        expected = controls(eq)
        if expected == self.last_controls and self.sink_present and time.monotonic() - self.verified_at < max_age:
            return
        node = await self.dump_node()
        actual_controls = read_controls(node)
        if any(key not in actual_controls for key in expected):
            raise EQError("The audio node no longer exposes the expected EQ controls.")
        old = {key: actual_controls[key] for key in expected}
        if same_controls(actual_controls, expected):
            self.node, self.last_controls, self.verified_at = node, expected, time.monotonic()
            return
        # Lower gain before changing filters; restore the new level only afterwards.
        # Small interpolated steps reduce zipper noise without restarting the graph.
        level = min(old["level:Mult"], expected["level:Mult"])
        node_id, serial = node["id"], node.get("info", {}).get("props", {}).get("object.serial")
        await self.run("pw-cli", "set-param", str(node_id), "Props", json.dumps(control_payload({"level:Mult": level})))
        steps = min(5, max(1, math.ceil(max(abs(expected[k] - old.get(k, expected[k]))
                                         for k in expected if k.endswith(":Gain")) / 1.5)))
        for step in range(1, steps + 1):
            values = {k: old.get(k, v) + (v - old.get(k, v)) * step / steps for k, v in expected.items()}
            values["level:Mult"] = level
            await self.run("pw-cli", "set-param", str(node_id), "Props", json.dumps(control_payload(values)))
        # A partial write must not raise the level while an old boosted filter
        # could still be active. Verify the new filters at the lower level first.
        staged = {**expected, "level:Mult": level}
        actual = await self.dump_node()
        if actual.get("info", {}).get("props", {}).get("object.serial") != serial or not same_controls(read_controls(actual), staged):
            raise EQError("PipeWire did not confirm the new EQ settings. The change was not saved.")
        await self.run("pw-cli", "set-param", str(node_id), "Props", json.dumps(control_payload(expected)))
        actual = await self.dump_node()
        if actual.get("info", {}).get("props", {}).get("object.serial") != serial or not same_controls(read_controls(actual), expected):
            raise EQError("PipeWire did not confirm the new EQ settings. The change was not saved.")
        self.node, self.last_controls, self.verified_at = actual, expected, time.monotonic()

    async def stop(self):
        error = None
        restored = True
        completed = False  # Cancellation or an unexpected error must keep the restore journal.
        # Without a session there is nothing to journal; pending restores belong to recover().
        session = self.target is not None or self.process is not None
        try:
            if self.supported and self.target:
                await self.scan()
                names = {o["name"] for o in self.outputs}
                current = self.default
                fallback = self.previous_default if self.previous_default in names else self.target if self.target in names else None
                sinks = await self.json_run("pactl", "--format=json", "list", "sinks")
                # Restore hardware level before returning default playback or
                # moving streams back. scan() captured the current EQ volume.
                entry = self._session_entry()
                restored = entry is None or await self._restore_level(entry, sinks)
                if current == SINK_NAME and fallback:
                    await self.run("pactl", "set-default-sink", fallback)
                    current = fallback
                sinks = await self.json_run("pactl", "--format=json", "list", "sinks")
                own_index = next((s["index"] for s in sinks if s.get("name") == SINK_NAME), None)
                if own_index is not None:
                    for stream in await self._inputs():
                        if stream.get("sink") != own_index or self._own_input(stream):
                            continue
                        # Respect a manual output switch made while EQ was active.
                        destination = current if self.default != SINK_NAME and current in names else self.moves.get(self._identity(stream))
                        if destination not in names:
                            destination = current if current in names else fallback
                        if destination:
                            try:
                                await self.run("pactl", "move-sink-input", str(stream["index"]), destination)
                            except EQError:
                                if any(self._identity(s) == self._identity(stream) for s in await self._inputs()):
                                    raise
            completed = True
        except (EQError, OSError) as exc:
            error = exc
        finally:
            entry = self._session_entry()
            if error is not None and entry and self.process is not None:
                # Restore failed while the hardware may be at 100%: mute it before streams fall back to it.
                try:
                    await self.run("pactl", "set-sink-mute", entry["target"], "1")
                except Exception:
                    pass
            if self.process is not None and self.process.returncode is None:
                try:
                    self.process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(self.process.wait(), 3)
                except asyncio.TimeoutError:
                    try:
                        self.process.kill()
                    except ProcessLookupError:
                        pass
                    await self.process.wait()
            if self.reader:
                self.reader.cancel()
                await asyncio.gather(self.reader, return_exceptions=True)
            self.reader = self.process = self.node = self.last_controls = None
            self.target = None
            self.volume, self.volume_committed = None, False
            if not (completed and restored) and entry:
                self.pending = (self.pending + [entry])[-MAX_PENDING:]
            if completed:
                self.moves = {}
            if session and (self.pending or not completed):
                self._save_journal()  # Keep what still has to be restored, e.g. after a port change.
            elif session:
                self.journal.unlink(missing_ok=True)
        if error:
            raise EQError("EQ stopped, but audio routing could not be fully restored: " + str(error))
