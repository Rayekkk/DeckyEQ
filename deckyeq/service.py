"""Serialized, cancellation-safe application service, independent of Decky UI."""
from __future__ import annotations

import asyncio
import copy
import json
import time
from pathlib import Path

from .graph import SINK_NAME, headroom
from .model import (EQError, FACTORY_IDS, MAX_PRESETS, create_preset, decode_import, export_document,
                    flat_eq, name, presets, selected_preset, validate_eq, validate_state)

VERSION = json.loads((Path(__file__).resolve().parent.parent / "package.json").read_text(encoding="utf-8"))["version"]
VERIFY_SECONDS = 15  # Monitor heartbeat and full PipeWire readback interval; edits always verify.
ERROR_PAUSE_SECONDS = 2  # After an unexpected monitor error, so a repeating failure cannot spin the loop.


class Service:
    def __init__(self, store, runtime, files):
        self.store, self.runtime, self.files = store, runtime, files
        self.data = store.load()
        self.revision = 0
        self.phase = "off"
        self.message = "EQ is off. Your presets are kept."
        self.error = None
        self.lock = asyncio.Lock()
        self.monitor = None
        self.closed = False
        self.retry_at = 0.0
        self.failures = 0
        self.held = None  # Output picked directly in Steam while EQ ran; bypass until it changes.

    async def start(self):
        # Serialized with close(): an unload during startup must not leave routing or a monitor behind.
        async with self.lock:
            if self.closed:
                return
            if self.runtime.supported:
                try:
                    await self.runtime.recover()
                    await self._sync(self.data)
                    if not self.data["enabled"]:
                        await self.runtime.scan()  # The idle monitor rescans only on audio events.
                except (EQError, OSError) as exc:
                    self._failed(exc)
            else:
                self.phase, self.message = "unavailable", self.runtime.reason
            if not self.closed:
                self.monitor = asyncio.create_task(self._watch())

    def _failed(self, exc):
        self.error = str(exc)
        self.phase, self.message = "error", "Audio changes are paused. Retry after checking your output."
        self.failures += 1
        self.retry_at = time.monotonic() + min(60, 8 * 2 ** (self.failures - 1))

    async def _watch(self):
        while not self.closed:
            try:
                retry_in = self.retry_at - time.monotonic()
                changed = await self.runtime.wait_for_change(
                    max(0.5, min(VERIFY_SECONDS, retry_in)) if retry_in > 0 else VERIFY_SECONDS)
                async with self.lock:
                    if self.closed or not self.runtime.supported:
                        continue
                    if time.monotonic() < self.retry_at:
                        if self.runtime.process is None:
                            await self.runtime.recover()  # Owed volume restores do not wait for the backoff.
                        continue
                    if changed and not self.data["enabled"]:
                        await self.runtime.scan()  # Keep the output list current while EQ is off.
                    await self._sync(self.data, max_age=VERIFY_SECONDS)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # Unexpected tool output must pause audio, never end monitoring.
                async with self.lock:
                    try:
                        await self.runtime.stop()
                    except Exception:
                        pass  # stop() keeps its journal; the next start restores routing.
                    self._failed(exc)
                await asyncio.sleep(ERROR_PAUSE_SECONDS)

    async def _sync(self, data, explicit=False, force=False, max_age=0.0):
        """explicit: a user action, which resumes a manual bypass. force: Refresh, which also
        takes playback from another output that is selected in Steam."""
        runtime = self.runtime
        if not runtime.supported:
            if data["enabled"]:
                raise EQError(runtime.reason)
            self.phase, self.message = "unavailable", runtime.reason
            return
        if runtime.process is None:
            # Finish volume restores owed to a device or port that has returned; a file check otherwise.
            await runtime.recover()
        if not data["enabled"]:
            # No scan here: while EQ is off the monitor only checks for a pending restore journal.
            if runtime.process is not None:
                await runtime.stop()
            self.phase, self.message, self.error = "off", "EQ is off. Your presets are kept.", None
            return
        outputs, default = await runtime.scan()
        target = next((o for o in outputs if o["name"] == data["target"]), None)
        bypass = self._bypass(data, target, default, explicit, force)
        if bypass:
            if runtime.process is not None:
                await runtime.stop()
            self.phase, self.message, self.error = *bypass, None
            return
        if not runtime.running or runtime.target != data["target"] or default != SINK_NAME:
            await runtime.start(data["working"], data["target"])
        else:
            await runtime.apply(data["working"], max_age)
        self.phase, self.message, self.error = "active", "Live EQ · " + target["description"], None
        self.retry_at, self.failures = 0, 0

    def _bypass(self, data, target, default, explicit, force):
        """Why EQ must not play right now, as (phase, message), or None to play."""
        if target is None:
            return "waiting", "Waiting for the selected audio output."
        if data["speakers_only"] and not target["speaker"]:
            return "bypassed", "Bypassed: the selected output is not using its speaker port."
        if explicit:
            self.held = None
        elif self.runtime.running and default != SINK_NAME:
            # The hardware is at 100% while EQ runs; playing to it directly would be loud.
            self.held = default
        elif self.held is not None and self.held != default:
            self.held = None
        if force:
            return None
        if self.held is not None:
            return "bypassed", "Bypassed: an output was selected directly in Steam. Use Refresh in Output to resume EQ."
        if default not in (SINK_NAME, data["target"]):
            return "bypassed", "Bypassed: another audio output is selected in Steam."
        return None

    def snapshot(self):
        selected = selected_preset(self.data)
        # The library travels once, in "presets" (with the factory presets), not again in "state".
        state = {key: copy.deepcopy(value) for key, value in self.data.items() if key != "presets"}
        return {"version": VERSION, "revision": self.revision, "state": state,
                "presets": presets(self.data), "dirty": self.data["working"] != selected["eq"],
                "audio": {"supported": self.runtime.supported, "phase": self.phase,
                          "message": self.message, "error": self.error,
                          "outputs": copy.deepcopy(self.runtime.outputs), "default": self.runtime.default,
                          **headroom(self.data["working"])},
                "warning": self.store.warning, "home": str(self.files.home),
                "export_directory": str(self.files.default_directory)}

    async def status(self):
        # No lock: snapshot() never awaits, so it cannot observe half of a commit, and the panel
        # stays responsive while a start or stop holds the lock for seconds.
        return self.snapshot()

    async def action(self, action, payload, revision):
        if self.closed:
            return {"ok": False, "error": "DeckyEQ is unloading."}
        # A canceled RPC must not release the lock midway through a hardware/save transaction.
        task = asyncio.create_task(self._action(action, payload, revision))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():  # Repeated cancellation must not reach the transaction either.
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    pass
            raise

    async def _action(self, action, payload, revision):
        async with self.lock:
            try:
                if self.closed:
                    raise EQError("DeckyEQ is unloading.")
                if type(revision) is not int or revision != self.revision:
                    raise EQError("Settings changed in another view. Review the current settings and retry.")
                if not isinstance(payload, dict):
                    raise EQError("Invalid request.")
                if action == "refresh":
                    await self._sync(self.data, explicit=True, force=True)
                    return {"ok": True, "state": self.snapshot()}
                if action == "export":
                    path = self._export(payload)
                    return {"ok": True, "state": self.snapshot(), "path": path}
                handler = HANDLERS.get(action)
                if handler is None:
                    raise EQError("Unknown DeckyEQ action.")
                updated = copy.deepcopy(self.data)
                note, explicit = await handler(self, updated, payload) or (None, False)
                # A draft edit keeps the previous settings backup; library changes refresh it.
                await self._commit(validate_state(updated), backup=action != "edit", explicit=explicit)
                return {"ok": True, "state": self.snapshot(), "message": note}
            except (ValueError, OSError, TypeError, KeyError) as exc:
                return {"ok": False, "error": str(exc), "state": self.snapshot()}

    async def _commit(self, updated, backup, explicit):
        """Apply a validated state to audio, then to disk; any failure restores the previous sound."""
        old = self.data
        if updated == old:
            return
        # Editing a library entry must not touch audio or steal a manual output selection.
        audio = [key for key in AUDIO_KEYS if updated[key] != old[key]]
        try:
            if audio == ["working"] and self.phase == "active" and self.runtime.running:
                await self.runtime.apply(updated["working"])  # A slider edit needs no output rescan.
            elif audio:
                await self._sync(updated, explicit=explicit)
            self.store.save(updated, backup=backup)
        except Exception as exc:
            try:
                if audio:
                    await self._sync(old, explicit=explicit)
            except Exception as rollback:
                try:
                    await self.runtime.stop()
                except Exception:
                    pass
                self._failed(EQError(f"{exc} Rollback: {rollback}"))
            raise EQError(str(exc)) from None
        self.data, self.revision = updated, self.revision + 1

    def _export(self, payload):
        all_presets = payload.get("all", False)
        if type(all_presets) is not bool:
            raise EQError("Invalid export mode.")
        title = "DeckyEQ-presets" if all_presets else selected_preset(self.data)["name"]
        return self.files.write(export_document(self.data, all_presets), title, payload.get("directory"))

    # Action handlers edit a copy of the settings. They may return (note for the user, explicit),
    # where explicit marks a routing request that also resumes a manual bypass.

    async def _do_edit(self, state, payload):
        state["working"] = validate_eq(payload.get("eq"))

    async def _do_enable(self, state, payload):
        if type(payload.get("enabled")) is not bool:
            raise EQError("Invalid enable setting.")
        state["enabled"] = payload["enabled"]
        if state["enabled"] and not state["target"]:
            if not self.runtime.supported:
                raise EQError(self.runtime.reason)
            outputs, default = await self.runtime.scan()
            target = next((o for o in outputs if o["name"] == default and
                           (o["speaker"] or not state["speakers_only"])), None)
            target = target or next((o for o in outputs if o["speaker"]), None)
            if not target:
                raise EQError("Choose a connected audio output in Output & settings first.")
            state["target"] = target["name"]
        return None, True

    async def _do_output(self, state, payload):
        if type(payload.get("speakers_only")) is not bool:
            raise EQError("Invalid speaker port setting.")
        state["target"], state["speakers_only"] = payload.get("target"), payload["speakers_only"]
        return None, True

    async def _do_select(self, state, payload):
        preset = selected_preset(state, payload.get("id"))
        state["selected"], state["working"] = preset["id"], preset["eq"]

    async def _do_save_as(self, state, payload):
        state["selected"] = create_preset(state, payload.get("name"), state["working"])["id"]

    async def _do_save(self, state, payload):
        self._user_preset(state, "Built-in presets are read-only. Use Save as new preset.")["eq"] = copy.deepcopy(state["working"])

    async def _do_rename(self, state, payload):
        title = name(payload.get("name"))
        preset = self._user_preset(state, "Built-in presets cannot be renamed.")
        if any(p["name"].casefold() == title.casefold() and p["id"] != preset["id"] for p in presets(state)):
            raise EQError("A preset already uses that name.")
        preset["name"] = title

    async def _do_delete(self, state, payload):
        self._confirmed(state, payload)
        preset = self._user_preset(state, "Built-in presets cannot be deleted.")
        state["presets"].remove(preset)
        state["selected"], state["working"] = "flat", flat_eq()

    async def _do_revert(self, state, payload):
        self._confirmed(state, payload)
        state["working"] = selected_preset(state)["eq"]

    async def _do_reset(self, state, payload):
        state["working"] = flat_eq()

    async def _do_import(self, state, payload):
        entries = decode_import(self.files.read(payload.get("path")))
        if len(state["presets"]) + len(entries) > MAX_PRESETS:
            raise EQError("Not enough room in the preset library. Remove unused presets first.")
        for entry in entries:
            create_preset(state, entry["name"], entry["eq"])
        return f"Imported {len(entries)} preset{'s' if len(entries) != 1 else ''}. Select one to hear it.", False

    @staticmethod
    def _user_preset(state, factory_error):
        if state["selected"] in FACTORY_IDS:
            raise EQError(factory_error)
        preset = next((p for p in state["presets"] if p["id"] == state["selected"]), None)
        if preset is None:
            raise EQError("Preset no longer exists. Refresh and try again.")
        return preset

    @staticmethod
    def _confirmed(state, payload):
        # A confirmation dialog must not apply to a preset selected after it opened.
        if payload.get("id", state["selected"]) != state["selected"]:
            raise EQError("The selected preset changed. Review it and try again.")

    async def close(self):
        self.closed = True
        # Cancel the monitor only between transactions: a cancelled stop() cannot restore volume.
        async with self.lock:
            if self.monitor:
                self.monitor.cancel()
                await asyncio.gather(self.monitor, return_exceptions=True)
            await self.runtime.stop()
            await self.runtime.close_events()


AUDIO_KEYS = ("enabled", "working", "target", "speakers_only")
# The RPC action names; renaming a handler must not silently change the frontend contract.
HANDLERS = {"edit": Service._do_edit, "enable": Service._do_enable, "output": Service._do_output,
            "select": Service._do_select, "save_as": Service._do_save_as, "save": Service._do_save,
            "rename": Service._do_rename, "delete": Service._do_delete, "revert": Service._do_revert,
            "reset": Service._do_reset, "import": Service._do_import}
