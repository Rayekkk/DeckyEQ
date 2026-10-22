"""Atomic private storage and explicit, bounded preset file exchange."""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from pathlib import Path

from .model import EQError, MAX_IMPORT_BYTES, initial_state, validate_state


def safe_directory(path: Path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise EQError("A settings or export directory is a symbolic link.")
        if part.exists() and not part.is_dir():
            raise EQError("A directory path points to a file.")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def atomic_json(path: Path, value):
    safe_directory(path.parent)
    if path.is_symlink():
        raise EQError("Refusing to replace a symbolic link.")
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    fd, temp = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def read_json(path: Path, limit=512 * 1024):
    if path.is_symlink():
        raise EQError("Symbolic link files are not supported.")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise EQError("Choose a regular file.")
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise EQError("File size limit exceeded.")
    return json.loads(raw.decode("utf-8-sig"))


class Store:
    def __init__(self, directory):
        self.directory = safe_directory(Path(directory))
        self.path = self.directory / "settings.json"
        self.backup = self.directory / "settings.last-good.json"
        self.warning = None

    def load(self):
        if not self.path.exists() and not self.path.is_symlink() and not self.backup.exists():
            return initial_state()
        for path in (self.path, self.backup):
            try:
                result = validate_state(read_json(path))
                if path == self.backup:
                    self.warning = "Recovered the last good settings. Check your most recent edits."
                return result
            except (OSError, ValueError, UnicodeError, RecursionError, TypeError):
                continue
        raise EQError("Settings could not be read. Existing files were preserved; restore a valid backup before editing.")

    def save(self, state):
        state = validate_state(state)
        # Commit the authoritative file last. A failed write never changes live state.
        # The backup tracks the previously committed state, never a proposed write.
        if self.path.exists():
            try:
                old = validate_state(read_json(self.path))
            except (OSError, ValueError, UnicodeError, RecursionError):
                old = None
            if old is not None:
                atomic_json(self.backup, old)
        atomic_json(self.path, state)


class PresetFiles:
    def __init__(self, home):
        self.home = Path(home).resolve()
        self.default_directory = self.home / "Documents" / "DeckyEQ"

    def _inside_home(self, path):
        if not isinstance(path, str) or not path or len(path) > 4096:
            raise EQError("Choose a path in your home directory.")
        raw = Path(path)
        if not raw.is_absolute():
            raise EQError("An absolute file path is required.")
        try:
            candidate = raw.resolve()  # Raises RuntimeError on Python < 3.13 for a symlink loop.
            candidate.relative_to(self.home)
        except (OSError, RuntimeError):
            raise EQError("This path cannot be resolved.") from None
        except ValueError:
            raise EQError("Choose a path inside your home directory.") from None
        for part in (raw, *raw.parents):
            if part.is_symlink():
                raise EQError("Symbolic link paths are not supported for preset exchange.")
        return candidate

    def read(self, path):
        path = self._inside_home(path)
        if path.suffix.lower() != ".json":
            raise EQError("Choose a .json preset file.")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise EQError("Choose a regular preset file.")
            raw = handle.read(MAX_IMPORT_BYTES + 1)
        if len(raw) > MAX_IMPORT_BYTES:
            raise EQError("Preset files must be no larger than 256 KiB.")
        try:
            return raw.decode("utf-8-sig")
        except UnicodeError:
            raise EQError("Preset files must use UTF-8 text.") from None

    def write(self, document, title, directory=None):
        folder = self._inside_home(directory or str(self.default_directory))
        safe_directory(folder)
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", title).strip("-")[:60] or "preset"
        payload = (json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
        for index in range(1, 1001):
            path = folder / f"{slug}{'' if index == 1 else '-' + str(index)}.json"
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            return str(path)
        raise EQError("Too many exports with this name. Choose another folder.")
