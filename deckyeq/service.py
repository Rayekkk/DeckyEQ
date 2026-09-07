"""Serialized, cancellation-safe application service, independent of Decky UI."""
from __future__ import annotations

import asyncio
import copy
import time

from .graph import SINK_NAME, headroom
from .model import (EQError, FACTORY_IDS, MAX_PRESETS, create_preset, decode_import, export_document,
                    flat_eq, name, presets, selected_preset, validate_eq, validate_state)


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

    async def start(self):
        if self.runtime.supported:
            try:
                await self.runtime.recover()
                await self._sync(self.data)
            except (EQError, OSError) as exc:
                self._failed(exc)
        else:
            self.phase, self.message = "unavailable", self.runtime.reason
        self.monitor = asyncio.create_task(self._watch())

    def _failed(self, exc):
        self.error = str(exc)
        self.phase, self.message = "error", "Audio changes are paused. Retry after checking your output."
        self.retry_at = time.monotonic() + 8

    async def _watch(self):
        while not self.closed:
            try:
                await asyncio.sleep(1.5)
                async with self.lock:
                    if self.closed or not self.runtime.supported or time.monotonic() < self.retry_at:
                        continue
                    await self._sync(self.data)
            except asyncio.CancelledError:
                break
            except (EQError, OSError) as exc:
                async with self.lock:
                    try:
                        await self.runtime.stop()
                    except (EQError, OSError):
                        pass
                    self._failed(exc)

    async def _sync(self, data, explicit=False):
        runtime = self.runtime
        if not runtime.supported:
            if data["enabled"]:
                raise EQError(runtime.reason)
            self.phase, self.message = "unavailable", runtime.reason
            return
        outputs, default = await runtime.scan()
        target = next((o for o in outputs if o["name"] == data["target"]), None)
        if not data["enabled"]:
            if runtime.process is not None:
                await runtime.stop()
            else:
                await runtime.recover()
            self.phase, self.message, self.error = "off", "EQ is off. Your presets are kept.", None
            return
        if target is None:
            if runtime.process is not None:
                await runtime.stop()
            self.phase, self.message, self.error = "waiting", "Waiting for the selected audio output.", None
            return
        if data["speakers_only"] and not target["speaker"]:
            if runtime.process is not None:
                await runtime.stop()
            self.phase, self.message, self.error = "bypassed", "Bypassed: the selected output is not using its speaker port.", None
            return
        if not explicit and default not in (SINK_NAME, data["target"]):
            if runtime.process is not None:
                await runtime.stop()
            self.phase, self.message, self.error = "bypassed", "Bypassed: another audio output is selected in Steam.", None
            return
        if not runtime.running or runtime.target != data["target"]:
            await runtime.start(data["working"], data["target"])
        else:
            await runtime.apply(data["working"])
        self.phase, self.message, self.error = "active", "Live EQ · " + target["description"], None
        self.retry_at = 0

    def snapshot(self):
        selected = selected_preset(self.data)
        return {"version": "1.0.0", "revision": self.revision, "state": copy.deepcopy(self.data),
                "presets": presets(self.data), "dirty": self.data["working"] != selected["eq"],
                "audio": {"supported": self.runtime.supported, "phase": self.phase,
                          "message": self.message, "error": self.error,
                          "outputs": copy.deepcopy(self.runtime.outputs), "default": self.runtime.default,
                          **headroom(self.data["working"])},
                "warning": self.store.warning, "home": str(self.files.home),
                "export_directory": str(self.files.default_directory)}

    async def status(self):
        async with self.lock:
            return self.snapshot()

    async def action(self, action, payload, revision):
        if self.closed:
            return {"ok": False, "error": "DeckyEQ is unloading."}
        # A canceled RPC must not release the lock midway through a hardware/save transaction.
        task = asyncio.create_task(self._action(action, payload, revision))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
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
                    await self._sync(self.data, explicit=True)
                    return {"ok": True, "state": self.snapshot()}
                if action == "export":
                    all_presets = payload.get("all", False)
                    if type(all_presets) is not bool:
                        raise EQError("Invalid export mode.")
                    document = export_document(self.data, all_presets)
                    title = "DeckyEQ-presets" if all_presets else selected_preset(self.data)["name"]
                    path = self.files.write(document, title, payload.get("directory"))
                    return {"ok": True, "state": self.snapshot(), "path": path}
                updated = copy.deepcopy(self.data)
                note = None
                explicit = False
                if action == "edit":
                    updated["working"] = validate_eq(payload.get("eq"))
                elif action == "enable":
                    if type(payload.get("enabled")) is not bool:
                        raise EQError("Invalid enable setting.")
                    updated["enabled"] = payload["enabled"]
                    explicit = True
                    if updated["enabled"] and not updated["target"]:
                        if not self.runtime.supported:
                            raise EQError(self.runtime.reason)
                        outputs, default = await self.runtime.scan()
                        target = next((o for o in outputs if o["name"] == default and
                                       (o["speaker"] or not updated["speakers_only"])), None)
                        target = target or next((o for o in outputs if o["speaker"]), None)
                        if not target:
                            raise EQError("Choose a connected audio output in Output & settings first.")
                        updated["target"] = target["name"]
                elif action == "output":
                    if type(payload.get("speakers_only")) is not bool:
                        raise EQError("Invalid speaker port setting.")
                    updated["target"] = payload.get("target")
                    updated["speakers_only"] = payload["speakers_only"]
                    explicit = True
                elif action == "select":
                    preset = selected_preset(updated, payload.get("id"))
                    updated["selected"], updated["working"] = preset["id"], preset["eq"]
                elif action == "save_as":
                    preset = create_preset(updated, payload.get("name"), updated["working"])
                    updated["selected"] = preset["id"]
                elif action == "save":
                    if updated["selected"] in FACTORY_IDS:
                        raise EQError("Built-in presets are read-only. Use Save as new preset.")
                    for preset in updated["presets"]:
                        if preset["id"] == updated["selected"]:
                            preset["eq"] = copy.deepcopy(updated["working"])
                elif action == "rename":
                    title = name(payload.get("name"))
                    if updated["selected"] in FACTORY_IDS:
                        raise EQError("Built-in presets cannot be renamed.")
                    if any(p["name"].casefold() == title.casefold() and p["id"] != updated["selected"] for p in presets(updated)):
                        raise EQError("A preset already uses that name.")
                    for preset in updated["presets"]:
                        if preset["id"] == updated["selected"]:
                            preset["name"] = title
                elif action == "delete":
                    if updated["selected"] in FACTORY_IDS:
                        raise EQError("Built-in presets cannot be deleted.")
                    updated["presets"] = [p for p in updated["presets"] if p["id"] != updated["selected"]]
                    updated["selected"], updated["working"] = "flat", flat_eq()
                elif action == "revert":
                    updated["working"] = selected_preset(updated)["eq"]
                elif action == "reset":
                    updated["working"] = flat_eq()
                elif action == "import":
                    entries = decode_import(self.files.read(payload.get("path")))
                    if len(updated["presets"]) + len(entries) > MAX_PRESETS:
                        raise EQError("Not enough room in the preset library. Remove unused presets first.")
                    for entry in entries:
                        create_preset(updated, entry["name"], entry["eq"])
                    note = f"Imported {len(entries)} preset{'s' if len(entries) != 1 else ''}. Select one to hear it."
                else:
                    raise EQError("Unknown DeckyEQ action.")
                updated = validate_state(updated)
                if updated != self.data:
                    old = self.data
                    try:
                        # Editing a library entry must not steal a user's manual output selection.
                        audio_change = any(updated[k] != old[k] for k in ("enabled", "working", "target", "speakers_only"))
                        if audio_change:
                            await self._sync(updated, explicit=explicit)
                        self.store.save(updated)
                    except (EQError, OSError) as exc:
                        try:
                            if audio_change:
                                # Force reapplication: a partial control write must not hit the cache.
                                self.runtime.last_controls = None
                                await self._sync(old, explicit=explicit)
                        except (EQError, OSError) as rollback:
                            try:
                                await self.runtime.stop()
                            except (EQError, OSError):
                                pass
                            self._failed(EQError(f"{exc} Rollback: {rollback}"))
                        raise EQError(str(exc)) from None
                    self.data = updated
                    self.revision += 1
                return {"ok": True, "state": self.snapshot(), "message": note}
            except (EQError, OSError, TypeError, KeyError) as exc:
                return {"ok": False, "error": str(exc), "state": self.snapshot()}

    async def close(self):
        self.closed = True
        if self.monitor:
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
        async with self.lock:
            await self.runtime.stop()
