"""Serialized, cancellation-safe application service, independent of Decky UI."""
from __future__ import annotations

import asyncio
import copy
import json
import time
from pathlib import Path

from .graph import SINK_NAME, headroom
from .model import (EQError, FACTORY_IDS, LIMITS, MAX_PRESETS, create_preset, decode_import, export_document,
                    flat_eq, name, number, presets, selected_preset, validate_eq, validate_state)

VERSION = json.loads((Path(__file__).resolve().parent.parent / "package.json").read_text(encoding="utf-8"))["version"]
VERIFY_SECONDS = 15  # Monitor heartbeat: re-reads the filter's controls; edits always read back.
DRAFT_SAVE_SECONDS = 1.0  # Slider edits reach the disk this long after the last one (not per step).
DRAFT_WARNING = "Your latest edits could not be saved: "
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
        self.draft_task = None  # Pending save of slider edits; any other change saves at once.

    async def start(self):
        # Serialized with close(): an unload during startup must not leave routing or a monitor behind.
        async with self.lock:
            if self.closed:
                return
            if self.runtime.supported:
                try:
                    await self._recover()
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
                    if changed and self.runtime.process is None:
                        await self._recover()  # A device owed a DeckyEQ 1.0 restore may have returned.
                    if time.monotonic() < self.retry_at:
                        continue
                    if not changed and self.runtime.running:
                        # Heartbeat: outputs only change with an event, so just re-verify the filter.
                        await self.runtime.apply(self.data["working"], VERIFY_SECONDS, self.data["boost"])
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
                        pass  # Without the filter, playback continues on the output directly.
                    self._failed(exc)
                await asyncio.sleep(ERROR_PAUSE_SECONDS)

    async def _recover(self):
        """Settle a journal left by DeckyEQ 1.0. Never fatal: it must not block EQ or the monitor."""
        try:
            await self.runtime.recover()
        except (EQError, OSError):
            pass

    async def _sync(self, data, max_age=0.0):
        """Bring the filter in line with data. It never changes outputs, streams or volumes."""
        runtime = self.runtime
        if not runtime.supported:
            if data["enabled"]:
                raise EQError(runtime.reason)
            self.phase, self.message = "unavailable", runtime.reason
            return
        if not data["enabled"]:
            if runtime.process is not None:
                await runtime.stop()
            self.phase, self.message, self.error = "off", "EQ is off. Your presets are kept.", None
            return
        outputs, default = await runtime.scan()
        target = next((o for o in outputs if o["name"] == data["target"]), None)
        if target is None or (data["speakers_only"] and not target["speaker"]):
            if runtime.process is not None:
                await runtime.stop()
            self.phase, self.error = "waiting" if target is None else "bypassed", None
            self.message = ("Waiting for the selected audio output." if target is None
                            else "Bypassed: the selected output is not using its speaker port.")
            return
        if not runtime.running or runtime.target != data["target"]:
            await runtime.start(data["working"], data["target"], data["boost"])
        else:
            await runtime.apply(data["working"], max_age, data["boost"])
        # Playback that Steam sends elsewhere does not pass the filter; apps that pick the target
        # themselves still do, so "standby" describes Steam's output, not every stream.
        playing = default in (data["target"], SINK_NAME)
        self.phase = "active" if playing else "standby"
        self.message = ("Live EQ · " + target["description"] if playing else
                        f"Standby · Steam is playing to another output. EQ applies to {target['description']}.")
        self.error, self.retry_at, self.failures = None, 0, 0

    def snapshot(self):
        selected = selected_preset(self.data)
        # The library travels once, in "presets" (with the factory presets), not again in "state".
        state = {key: copy.deepcopy(value) for key, value in self.data.items() if key != "presets"}
        return {"version": VERSION, "revision": self.revision, "state": state,
                "presets": presets(self.data), "dirty": self.data["working"] != selected["eq"],
                "audio": {"supported": self.runtime.supported, "phase": self.phase,
                          "message": self.message, "error": self.error,
                          "outputs": copy.deepcopy(self.runtime.outputs), "default": self.runtime.default,
                          **headroom(self.data["working"], self.data["boost"])},
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
                    await self._sync(self.data)
                    return {"ok": True, "state": self.snapshot()}
                if action == "export":
                    path = self._export(payload)
                    return {"ok": True, "state": self.snapshot(), "path": path}
                handler = HANDLERS.get(action)
                if handler is None:
                    raise EQError("Unknown DeckyEQ action.")
                updated = copy.deepcopy(self.data)
                note = await handler(self, updated, payload)
                # A draft edit keeps the previous settings backup; library changes refresh it.
                await self._commit(validate_state(updated), backup=action != "edit")
                return {"ok": True, "state": self.snapshot(), "message": note}
            except (ValueError, OSError, TypeError, KeyError) as exc:
                return {"ok": False, "error": str(exc), "state": self.snapshot()}

    async def _commit(self, updated, backup):
        """Apply a validated state to audio, then to disk; any failure restores the previous sound.
        Slider edits (backup=False) are saved DRAFT_SAVE_SECONDS after the last one."""
        old = self.data
        if updated == old:
            return
        # Editing a library entry must not touch audio.
        audio = [key for key in AUDIO_KEYS if updated[key] != old[key]]
        draft_only = bool(audio) and set(audio) <= {"working", "boost"}
        try:
            if draft_only and self.phase in ("active", "standby") and self.runtime.running:
                # A slider edit needs no output rescan.
                await self.runtime.apply(updated["working"], boost=updated["boost"])
            elif audio and not (draft_only and time.monotonic() < self.retry_at):
                # While audio is paused after a failure, a draft edit is only kept; the monitor
                # applies it when it retries, instead of every slider step restarting the filter.
                await self._sync(updated)
            if backup:
                self.store.save(updated, backup=True)
        except Exception as exc:
            try:
                if audio:
                    await self._sync(old)
            except Exception as rollback:
                try:
                    await self.runtime.stop()
                except Exception:
                    pass
                self._failed(EQError(f"{exc} Rollback: {rollback}"))
            raise EQError(str(exc)) from None
        self.data, self.revision = updated, self.revision + 1
        if backup:
            self._cancel_draft()
        else:
            self._cancel_draft()
            self.draft_task = asyncio.create_task(self._save_draft_later())

    def _cancel_draft(self):
        if self.draft_task is not None and not self.draft_task.done():
            self.draft_task.cancel()
        self.draft_task = None

    async def _save_draft_later(self):
        await asyncio.sleep(DRAFT_SAVE_SECONDS)
        async with self.lock:
            self._save_draft()

    def _save_draft(self):
        self.draft_task = None
        try:
            self.store.save(self.data, backup=False)
        except (EQError, OSError) as exc:
            self.store.warning = DRAFT_WARNING + str(exc)
            return
        if self.store.warning and self.store.warning.startswith(DRAFT_WARNING):
            self.store.warning = None

    def _export(self, payload):
        all_presets = payload.get("all", False)
        if type(all_presets) is not bool:
            raise EQError("Invalid export mode.")
        title = "DeckyEQ-presets" if all_presets else selected_preset(self.data)["name"]
        return self.files.write(export_document(self.data, all_presets), title, payload.get("directory"))

    # Action handlers edit a copy of the settings and may return a note for the user.

    async def _do_edit(self, state, payload):
        state["working"] = validate_eq(payload.get("eq"))
        if "boost" in payload:  # Dragged like a slider, so it travels with slider edits.
            state["boost"] = number(payload["boost"], *LIMITS["boost"], "Volume boost")

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
                raise EQError("Choose a connected audio output in Output first.")
            state["target"] = target["name"]

    async def _do_output(self, state, payload):
        if type(payload.get("speakers_only")) is not bool:
            raise EQError("Invalid speaker port setting.")
        state["target"], state["speakers_only"] = payload.get("target"), payload["speakers_only"]

    async def _do_select(self, state, payload):
        preset = selected_preset(state, payload.get("id"))
        state["selected"], state["working"] = preset["id"], preset["eq"]

    async def _do_save_as(self, state, payload):
        state["selected"] = create_preset(state, payload.get("name"), state["working"])["id"]

    async def _do_save(self, state, payload):
        self._user_preset(state, "Built-in presets are read-only. Use Save as new preset.")["eq"] = copy.deepcopy(state["working"])

    async def _do_rename(self, state, payload):
        self._confirmed(state, payload)
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
        return f"Imported {len(entries)} preset{'s' if len(entries) != 1 else ''}. Select one to hear it."

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
        # Cancel the monitor only between transactions, and keep unsaved slider edits.
        async with self.lock:
            if self.draft_task is not None:
                self._cancel_draft()
                self._save_draft()
            if self.monitor:
                self.monitor.cancel()
                await asyncio.gather(self.monitor, return_exceptions=True)
            await self.runtime.stop()
            await self.runtime.close_events()


AUDIO_KEYS = ("enabled", "working", "target", "speakers_only", "boost")
# The RPC action names; renaming a handler must not silently change the frontend contract.
HANDLERS = {"edit": Service._do_edit, "enable": Service._do_enable, "output": Service._do_output,
            "select": Service._do_select, "save_as": Service._do_save_as, "save": Service._do_save,
            "rename": Service._do_rename, "delete": Service._do_delete, "revert": Service._do_revert,
            "reset": Service._do_reset, "import": Service._do_import}
