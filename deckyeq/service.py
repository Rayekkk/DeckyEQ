"""Serialized, cancellation-safe application service, independent of Decky UI."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from pathlib import Path

import dk_fs

from .graph import SINK_NAME, headroom
from .model import (APPID, EQError, FACTORY_IDS, LIMITS, MAX_GAMES, MAX_PRESETS, NodeGone, OutputGone, ToolError, create_preset,
                    decode_import, export_document, flat_eq, game_name, name, number, presets, selected_preset,
                    switch_scope, validate_eq, validate_state)
from .runtime import private

VERSION = json.loads((Path(__file__).resolve().parent.parent / "package.json").read_text(encoding="utf-8"))["version"]
VERIFY_SECONDS = 15  # Monitor heartbeat: re-reads the filter's controls; edits always read back.
DRAFT_SAVE_SECONDS = 1.0  # Slider edits reach the disk this long after the last one (not per step).
DRAFT_WARNING = "Your latest edits could not be saved: "
ERROR_PAUSE_SECONDS = 2  # After an unexpected monitor error, so a repeating failure cannot spin the loop.
TOOL_RETRIES = 3  # Heartbeat tool hiccups (e.g. pw-dump timing out while a game loads) tolerated in a row.
STABLE_SECONDS = 60  # Failures are forgotten only after the filter has run this long without one.
CRASH_SECONDS = 10  # A filter that ends sooner after its start is crashing; later (e.g. PipeWire restarted) it is rebuilt.


class Service:
    def __init__(self, store, runtime, files, log=None):
        self.store, self.runtime, self.files = store, runtime, files
        self.log = log or logging.getLogger(__name__)  # main.py gives Decky's logger.
        self.data = store.load()
        if store.warning:
            self.log.warning(store.warning)
        self.reported = None  # The (phase, error) last logged: the log hears changes, not heartbeats.
        self.revision = 0
        self.phase = "off"
        self.message = "EQ is off, so changes are not heard."
        self.error = None
        self.game_error = None  # Why the running game's sound could not be switched; the toast stays short.
        self.lock = asyncio.Lock()
        self.monitor = None
        self.closed = False
        self.retry_at = 0.0
        self.failures = 0
        self.failed_at = -STABLE_SECONDS
        self.tool_errors = 0
        self.owed = False  # An event's rescan failed on a tool hiccup: do it at the next chance.
        self.answered = False  # The audio session has answered a rescan; until then (at boot) an outage is "Connecting…".
        self.draft_task = None  # Pending save of slider edits; any other change saves at once.
        self.draft_dirty = False  # Slider edits not yet on disk, also after a failed save.
        self.game = None  # The running game the panel reports: {"appid", "name"}, or None.
        self.switched = 0  # The revision the last game switch produced: older edits are refused for it.

    async def start(self):
        # Serialized with close(): an unload during startup must not leave routing or a monitor behind.
        async with self.lock:
            if self.closed:
                return
            if self.data["scope"]:
                # A game's sound left active by a crash or reboot. No game is known yet; the panel
                # reports a running one within two seconds and its sound comes back then.
                updated = copy.deepcopy(self.data)
                switch_scope(updated, "")
                self.data = updated
                try:
                    self.store.save(updated)
                except (EQError, OSError):
                    pass  # The next save writes it; the sound playing is right either way.
            if self.runtime.supported:
                try:
                    await self._recover()
                    await self._sync(self.data)
                    if not self.data["enabled"]:
                        await self.runtime.scan()  # The idle monitor rescans only on audio events.
                except ToolError:  # E.g. at boot, before the user's audio session answers: the monitor retries.
                    self._owe()
                except Exception as exc:  # Never leave the plugin without a monitor for the session.
                    self._failed(exc)
            else:
                self.phase, self.message = "unavailable", self.runtime.reason
            self._report()
            if not self.closed:
                self.monitor = asyncio.create_task(self._watch())

    def _report(self):
        """Log the audio state when it changes (never per heartbeat), without paths or the user's names."""
        seen = (self.phase, self.error)
        if seen != self.reported:
            self.reported = seen
            if self.error:
                self.log.warning("Audio %s: %s (%s)", self.phase, self.message, self.error)
            else:
                self.log.info("Audio %s: %s", self.phase, self.message)

    def _failed(self, exc):
        self.error = dk_fs.shown(exc)
        self.phase, self.message = "error", "Audio changes are paused. Check your output, then use Reconnect EQ."
        self.failures += 1
        self.failed_at = time.monotonic()
        self.retry_at = self.failed_at + min(60, 8 * 2 ** (self.failures - 1))

    def _connecting(self):
        if self.data["enabled"] and self.phase in ("off", "active", "standby") and not self.runtime.running:
            # A start met a tool hiccup ("off" with EQ on: at boot): say so, not a filter that is not there.
            output = next((o for o in self.runtime.outputs if o["name"] == self.data["target"]), None)
            self.phase = "connecting"
            self.message = f"Connecting to {output['description'] if output else self.data['target']}…"

    def _owe(self):
        """A tool did not answer in a transaction. Like the monitor, keep the last confirmed sound
        playing and look again within half a second, instead of more timeouts under the lock."""
        self.owed = True
        self._connecting()
        if self.monitor is not None and not self.monitor.done():
            # It may be in a wait of up to VERIFY_SECONDS; we hold the lock, so it is between transactions.
            self.monitor.cancel()
            self.monitor = asyncio.create_task(self._watch())

    async def _watch(self):
        while not self.closed:
            try:
                retry_in = self.retry_at - time.monotonic()
                # An event owed during a backoff waits for the retry, not half a second at a time.
                wait = max(0.5, min(VERIFY_SECONDS, retry_in)) if retry_in > 0 else 0.5 if self.owed else VERIFY_SECONDS
                changed = await self.runtime.wait_for_change(wait) or self.owed
                self.owed = changed
                async with self.lock:
                    if self.closed or not self.runtime.supported:
                        continue
                    if changed and self.runtime.process is None:
                        await self._recover()  # A device owed a DeckyEQ 1.0 restore may have returned.
                    if time.monotonic() < self.retry_at:
                        continue
                    if not changed and self.runtime.running:
                        # Heartbeat: outputs only change with an event, so just re-verify the filter.
                        try:
                            await self.runtime.apply(self.data["working"], VERIFY_SECONDS, self.data["boost"])
                        except NodeGone:
                            await self._rebuild(self.data)
                        self.tool_errors, self.owed = 0, False
                        continue
                    if changed and not self.data["enabled"]:
                        await self.runtime.scan()  # Keep the output list current while EQ is off.
                    await self._sync(self.data, max_age=VERIFY_SECONDS)
                self.tool_errors, self.owed = 0, False
            except asyncio.CancelledError:
                break
            except Exception as exc:  # Unexpected tool output must pause audio, never end monitoring.
                if not isinstance(exc, EQError) and dk_fs.shown(exc) != self.error:  # A bug or OSError: its details, once.
                    self.log.exception("The DeckyEQ audio monitor failed")
                if isinstance(exc, ToolError) and (self.tool_errors < TOOL_RETRIES or not self.data["enabled"]
                                                   or not self.answered):
                    # A tool that did not answer says nothing about the sound, which is still the last
                    # confirmed setting: keep it playing and look again shortly. With EQ off, nothing
                    # plays through DeckyEQ, and before the audio session first answers (at boot) nothing
                    # can, so a lasting outage is no error either.
                    self.tool_errors += 1
                    self._connecting()
                    await asyncio.sleep(ERROR_PAUSE_SECONDS)
                    continue
                self.tool_errors, self.owed = 0, False  # The backoff decides when to look again.
                async with self.lock:
                    try:
                        await self.runtime.stop()
                    except Exception:
                        pass  # Without the filter, playback continues on the output directly.
                    self._failed(exc)
                await asyncio.sleep(ERROR_PAUSE_SECONDS)
            finally:
                self._report()

    async def _rebuild(self, data):
        """PipeWire restarted under the filter (its node is gone): build it again, no error."""
        try:
            await self.runtime.start(data["working"], data["target"], data["boost"], speakers_only=data["speakers_only"])
        except OutputGone:
            pass  # Nothing runs now; _sync finds where the output went.
        await self._sync(data)

    async def _recover(self):
        """Settle a journal left by DeckyEQ 1.0. Never fatal: it must not block EQ or the monitor."""
        try:
            await self.runtime.recover()
        except (EQError, OSError):
            pass  # recover() keeps what it could not settle and tries on the next event; main.py logs the first try.

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
            self.phase, self.message, self.error = "off", "EQ is off, so changes are not heard.", None
            self.retry_at, self.failures = 0, 0
            return
        outputs, default = await runtime.scan()
        self.answered = True
        target = next((o for o in outputs if o["name"] == data["target"]), None)
        if target is None or (data["speakers_only"] and not target["speaker"]):
            if runtime.process is not None:
                # Its own output switched to headphones: silence the speaker EQ at once, no ramp.
                await runtime.stop(mute=target is not None and runtime.target == data["target"])
            self._idle(bypassed=target is not None)
            return
        if (runtime.process is not None and runtime.process.returncode is not None
                and time.monotonic() - runtime.started_at < CRASH_SECONDS):
            # The filter ended on its own soon after it started. Starting it again on every event would cut the
            # sound in a loop under "EQ on": pause like any failure instead, and let the backoff retry.
            self.log.warning("The DeckyEQ filter exited with %s: %s", runtime.process.returncode, runtime.log[-1024:])
            reason = private(runtime.log).strip()[-240:] or f"exit code {runtime.process.returncode}"
            await runtime.stop()
            self._failed(EQError("The equalizer stopped: " + reason))
            return
        try:
            if not runtime.running or runtime.target != data["target"]:
                await runtime.start(data["working"], data["target"], data["boost"], speakers_only=data["speakers_only"])
            else:
                try:
                    await runtime.apply(data["working"], max_age, data["boost"])
                except NodeGone:  # PipeWire restarted under the filter: build it again, no error.
                    await runtime.start(data["working"], data["target"], data["boost"], speakers_only=data["speakers_only"])
        except OutputGone:
            # Unplugged, or headphones plugged in, while starting (e.g. Bluetooth changing profile): nothing
            # runs. Wait as above, with no backoff; the output's next event looks again.
            self._idle(bypassed=data["speakers_only"] and any(o["name"] == data["target"] and not o["speaker"]
                                                               for o in runtime.outputs))
            return
        # Playback that Steam sends elsewhere does not pass the filter; apps that pick the target
        # themselves still do, so "standby" describes Steam's output, not every stream.
        playing = default in (data["target"], SINK_NAME)
        self.phase = "active" if playing else "standby"
        self.message = ("EQ on " + target["description"] if playing else
                        f"Steam is playing to another output. EQ applies to {target['description']}.")
        self.error, self.retry_at = None, 0
        if time.monotonic() - self.failed_at > STABLE_SECONDS:  # A filter that keeps dying keeps its backoff.
            self.failures = 0

    def _idle(self, bypassed):
        """Nothing plays through DeckyEQ: its output is not connected, or on headphones under Speakers only."""
        self.phase, self.error = "bypassed" if bypassed else "waiting", None
        self.message = "Speakers only is on, so EQ is off on this output." if bypassed else "The selected output is not connected. Choose another under Output."
        self.retry_at, self.failures = 0, 0  # Nothing runs: no backoff to carry over.

    def snapshot(self):
        selected = selected_preset(self.data)
        # The library travels once, in "presets" (with the factory presets), not again in "state".
        state = {key: copy.deepcopy(value) for key, value in self.data.items() if key not in ("presets", "outside")}
        state["games"] = {appid: {"name": game["name"]} for appid, game in self.data["games"].items()}
        return {"version": VERSION, "revision": self.revision, "state": state, "game": copy.deepcopy(self.game),
                "presets": presets(self.data), "dirty": self.data["working"] != selected["eq"],
                "audio": {"supported": self.runtime.supported, "phase": self.phase,
                          "message": self.message, "error": self.error, "game_error": self.game_error,
                          "outputs": copy.deepcopy(self.runtime.outputs), "default": self.runtime.default,
                          **headroom(self.data["working"])},
                "warning": self.store.warning, "home": str(self.files.home),
                "export_directory": str(self.files.default_directory)}

    async def set_game(self, appid, title):
        """The panel saw a game start or end. A game with its own sound gets it; others the everyday one.
        Takes no revision (it follows Steam, not an edit in the panel); a scope switch bumps it."""
        if appid in (None, "", 0):
            game = None
        else:
            appid = str(appid)
            if not APPID.fullmatch(appid):
                return {"ok": False, "error": "Invalid game."}
            try:
                game = {"appid": appid, "name": game_name(title)}
            except EQError:
                game = {"appid": appid, "name": f"App {appid}"}
        if self.closed:
            return {"ok": False, "error": "DeckyEQ is unloading."}
        result = await self._shielded(self._set_game(game))
        self._report()
        return result

    async def _set_game(self, game):
        async with self.lock:
            if self.closed:
                return {"ok": False, "error": "DeckyEQ is unloading."}
            scope = game["appid"] if game and game["appid"] in self.data["games"] else ""
            if scope != self.data["scope"]:
                updated = copy.deepcopy(self.data)
                switch_scope(updated, scope)
                try:
                    await self._commit(validate_state(updated), backup=True)
                except (EQError, OSError) as exc:
                    # Shown with "could not be applied"; turning it on retries. A game whose own sound
                    # still plays stays shown with it until the panel's retry gets the switch through.
                    if not self.data["scope"]:
                        self.game = game
                    if self.game_error != dk_fs.shown(exc):  # The panel retries every 30 s: log the reason once.
                        self.log.warning("Could not switch to the sound for game %s: %s", scope or "none", exc)
                    self.game_error = dk_fs.shown(exc)
                    return {"ok": False, "error": dk_fs.shown(exc), "state": self.snapshot()}
                self.switched = self.revision
                self.log.info("Game %s: %s", (game or {}).get("appid", "none"),
                              "its own sound" if scope else "the everyday sound")
            self.game, self.game_error = game, None  # Only now: a poll mid-switch must not show the new game with the old sound.
            return {"ok": True, "state": self.snapshot()}

    async def status(self):
        # No lock: snapshot() never awaits, so it cannot observe half of a commit, and the panel
        # stays responsive while a start or stop holds the lock for seconds.
        return self.snapshot()

    async def action(self, action, payload, revision):
        if self.closed:
            return {"ok": False, "error": "DeckyEQ is unloading."}
        result = await self._shielded(self._action(action, payload, revision))
        name = str(action)[:32]
        if not result["ok"]:
            self.log.warning("Action %s refused: %s", name, result["error"])
        elif action != "edit":  # Slider edits come many times a second: only their refusals are logged.
            self.log.info("Action %s done", name)
        self._report()
        return result

    @staticmethod
    async def _shielded(transaction):
        # A canceled RPC must not release the lock midway through a hardware/save transaction.
        task = asyncio.create_task(transaction)
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
                if type(revision) is int and revision < self.switched:
                    raise EQError("The game's sound was switched. Your change was not applied; try again.")
                if type(revision) is not int or revision != self.revision:
                    raise EQError("Settings changed meanwhile. Check them and try again.")
                if not isinstance(payload, dict):
                    raise EQError("Invalid request.")
                # The sound the panel showed when the user acted (e.g. opened a dialog); a game that
                # started or ended since then has switched it.
                if "scope" in payload and payload["scope"] != self.data["scope"]:
                    raise EQError("The running game changed. Check the sound and try again.")
                if action == "refresh":
                    try:
                        await self._sync(self.data)
                    except ToolError:
                        self._owe()
                        raise
                    except Exception as exc:  # A half-done reconnect must not look like success.
                        if not isinstance(exc, EQError):  # Its text (an OSError names a path) goes to the log only.
                            self.log.exception("Reconnecting EQ failed")
                        try:
                            await self.runtime.stop()  # Like the monitor: no half-applied filter stays.
                        except Exception:
                            pass  # Without the filter, playback continues on the output directly.
                        self._failed(exc)
                        raise EQError(dk_fs.shown(exc)) from None
                    return {"ok": True, "state": self.snapshot()}
                if action == "export":
                    path = await self._export(payload)
                    return {"ok": True, "state": self.snapshot(), "path": path}
                handler = HANDLERS.get(action)
                if handler is None:
                    raise EQError("Unknown DeckyEQ action.")
                updated = copy.deepcopy(self.data)
                note = await handler(self, updated, payload)
                # A draft edit keeps the previous settings backup; library changes refresh it.
                await self._commit(validate_state(updated), backup=action != "edit")
                return {"ok": True, "state": self.snapshot(), "message": note}
            except Exception as exc:  # Reported to the panel, never a raw exception to Decky.
                if isinstance(exc, EQError):
                    error = str(exc) or type(exc).__name__
                else:  # A bug: the traceback goes to the log; str(exc) may name a path, so the panel gets none.
                    self.log.exception("DeckyEQ action %s failed", str(action)[:32])
                    error = f"Unexpected error ({type(exc).__name__}). Details are in the Decky log."
                return {"ok": False, "error": error, "state": self.snapshot()}

    async def _commit(self, updated, backup):
        """Apply a validated state to audio, then to disk; any failure restores the previous sound.
        Slider edits (backup=False) are saved DRAFT_SAVE_SECONDS after the last one."""
        old = self.data
        if updated == old:
            return
        # Editing a library entry must not touch audio.
        audio = [key for key in AUDIO_KEYS if updated[key] != old[key]] if self.runtime.supported else []
        draft_only = bool(audio) and set(audio) <= {"working", "boost"}
        # While audio is paused after a failure, or still connecting, a draft edit is only kept; the
        # monitor applies it when it retries, instead of every slider step restarting the filter.
        kept = draft_only and (time.monotonic() < self.retry_at or self.owed and not self.runtime.running)
        try:
            if draft_only and self.phase in ("active", "standby") and self.runtime.running:
                try:  # A slider edit needs no output rescan.
                    await self.runtime.apply(updated["working"], boost=updated["boost"])
                except NodeGone:
                    await self._rebuild(updated)
            elif audio and not kept:
                await self._sync(updated)
            if backup:
                self.store.save(updated)
                if self.store.warning and self.store.warning.startswith(DRAFT_WARNING):
                    self.store.warning = None
        except Exception as exc:
            if not isinstance(exc, EQError):  # Its text (an OSError names a path) goes to the log only.
                self.log.exception("Applying the change failed")
            try:
                if isinstance(exc, ToolError):
                    self._owe()  # A rollback would only time out again under the lock: the monitor does it.
                elif audio:
                    await self._sync(old)
            except ToolError:
                self._owe()
            except Exception as rollback:
                if not isinstance(rollback, EQError):
                    self.log.exception("Restoring the previous sound failed")
                try:
                    await self.runtime.stop()
                except Exception:
                    pass  # Without the filter, playback continues on the output directly.
                self._failed(EQError(f"{dk_fs.shown(exc)} Rollback: {dk_fs.shown(rollback)}"))
            raise EQError(dk_fs.shown(exc)) from None
        self.data, self.revision = updated, self.revision + 1
        self._cancel_draft()
        self.draft_dirty = not backup
        if not backup:
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
            self.store.save(self.data)
        except (EQError, OSError) as exc:
            if self.store.warning != DRAFT_WARNING + dk_fs.shown(exc):  # Each later slider edit tries again: log once.
                self.log.warning(DRAFT_WARNING + str(exc))
            self.store.warning = DRAFT_WARNING + dk_fs.shown(exc)
            return  # Still dirty: the next edit or close() tries again.
        self.draft_dirty = False
        if self.store.warning and self.store.warning.startswith(DRAFT_WARNING):
            self.store.warning = None

    async def _export(self, payload):
        all_presets = payload.get("all", False)
        if type(all_presets) is not bool:
            raise EQError("Invalid export mode.")
        title = "DeckyEQ-presets" if all_presets else selected_preset(self.data)["name"]
        # In a thread: a folder on a stalled mount must not freeze the plugin (status and unload included).
        return await asyncio.to_thread(self.files.write, export_document(self.data, all_presets), title,
                                       payload.get("directory"))

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
        if "boost" in preset:  # A built-in tuned with a Volume boost brings it along.
            state["boost"] = preset["boost"]

    async def _do_save_as(self, state, payload):
        state["selected"] = create_preset(state, payload.get("name"), state["working"])["id"]

    async def _do_save(self, state, payload):
        self._user_preset(state, "Built-in presets are read-only. Use Save as new… to keep your changes.")["eq"] = copy.deepcopy(state["working"])

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
        # Game and everyday sounds that came from it keep their sound, now as an edited Flat.
        for sound in [*state["games"].values(), *([state["outside"]] if state["outside"] else [])]:
            if sound["selected"] == preset["id"]:
                sound["selected"] = "flat"

    async def _do_own_sound(self, state, payload):
        if type(payload.get("enabled")) is not bool:
            raise EQError("Invalid game sound setting.")
        if self.game is None or payload.get("appid") != self.game["appid"]:
            raise EQError("The running game changed. Check the sound and try again.")
        appid = self.game["appid"]
        if payload["enabled"]:
            if appid not in state["games"]:
                if len(state["games"]) >= MAX_GAMES:
                    raise EQError(f"At most {MAX_GAMES} games can have their own sound. Forget one first.")
                # Starts as the sound playing now; changes from here on apply to this game only.
                state["games"][appid] = {"name": self.game["name"], "selected": state["selected"],
                                         "working": copy.deepcopy(state["working"])}
            switch_scope(state, appid)
        elif appid in state["games"]:
            switch_scope(state, "")
            del state["games"][appid]

    async def _do_forget_game(self, state, payload):
        appid = payload.get("appid")
        if not isinstance(appid, str) or appid not in state["games"]:
            raise EQError("That game has no sound of its own.")
        if state["scope"] == appid:
            switch_scope(state, "")
        del state["games"][appid]

    async def _do_revert(self, state, payload):
        self._confirmed(state, payload)
        state["working"] = selected_preset(state)["eq"]

    async def _do_reset(self, state, payload):
        state["working"] = flat_eq()

    async def _do_import(self, state, payload):
        entries = decode_import(await asyncio.to_thread(self.files.read, payload.get("path")))  # As in _export.
        room = MAX_PRESETS - len(state["presets"])
        if len(entries) > room:
            raise EQError(f"This file has {len(entries)} presets; there is room for {room}." if room else
                          "The preset library is full. Delete some presets first.")
        for entry in entries:
            create_preset(state, entry["name"], entry["eq"])
        return f"Imported {len(entries)} preset{'s' if len(entries) != 1 else ''}. Select one to hear it."

    @staticmethod
    def _user_preset(state, factory_error):
        if state["selected"] in FACTORY_IDS:
            raise EQError(factory_error)
        preset = next((p for p in state["presets"] if p["id"] == state["selected"]), None)
        if preset is None:
            raise EQError("Preset no longer exists. Try again.")
        return preset

    @staticmethod
    def _confirmed(state, payload):
        # A confirmation dialog must not apply to a preset selected after it opened.
        if payload.get("id", state["selected"]) != state["selected"]:
            raise EQError("The selected preset changed. Check it and try again.")

    def abandon(self):
        """Close for Decky's stop without awaiting: Decky 3.3 busy-loops once it closes the plugin's
        socket, so nothing awaited in _unload would ever finish. Keeps unsaved edits, ends the audio."""
        self.closed = True
        try:
            self._cancel_draft()
            if self.draft_dirty:
                self._save_draft()
        finally:  # Whatever the save raised, the audio must end.
            if self.monitor:
                self.monitor.cancel()
            self.runtime.abandon()

    async def close(self):
        self.closed = self.runtime.closing = True
        # Keep unsaved slider edits before the lock: Decky kills us ~5 s after unloading, maybe mid-start.
        # Safe: self.data is swapped whole and store.save never awaits.
        self._cancel_draft()
        if self.draft_dirty:
            self._save_draft()
        # Cancel the monitor only between transactions, and keep slider edits made meanwhile.
        async with self.lock:
            self._cancel_draft()
            if self.draft_dirty:
                self._save_draft()
            if self.monitor:
                self.monitor.cancel()
                await asyncio.gather(self.monitor, return_exceptions=True)
            try:
                await self.runtime.stop()
            finally:
                await self.runtime.close_events()


AUDIO_KEYS = ("enabled", "working", "target", "speakers_only", "boost")
# The RPC action names; renaming a handler must not silently change the frontend contract.
HANDLERS = {"edit": Service._do_edit, "enable": Service._do_enable, "output": Service._do_output,
            "select": Service._do_select, "save_as": Service._do_save_as, "save": Service._do_save,
            "rename": Service._do_rename, "delete": Service._do_delete, "revert": Service._do_revert,
            "reset": Service._do_reset, "import": Service._do_import, "own_sound": Service._do_own_sound,
            "forget_game": Service._do_forget_game}
