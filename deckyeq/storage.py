"""Private settings on dk_fs and explicit, bounded preset file exchange inside the home folder."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import dk_fs

from .model import EQError, MAX_IMPORT_BYTES, initial_state, portable_state, validate_state

MAX_SETTINGS_BYTES = 4 * 1024 * 1024  # 64 presets and 200 game sounds stay under 1 MiB.
LEGACY_BACKUP = "settings.last-good.json"  # DeckyEQ 1.0-1.1: the previous good settings.


def safe_directory(path: Path):
    # Only the plugin's own folder must not be a link: links above it are the system's or the user's
    # choice (/home -> /var/home on Fedora Atomic, ~/homebrew moved to an SD card).
    path = Path(os.path.abspath(path))
    if path.is_symlink():
        raise EQError("A settings or export directory is a symbolic link.")
    for part in (path, *path.parents):
        if part.exists() and not part.is_dir():
            raise EQError("A directory path points to a file.")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def atomic_json(path: Path, value):
    """A private JSON file with no last-good copy (the filter's config, the 1.0 journal: a copy would bring a
    deleted journal back)."""
    safe_directory(path.parent)
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    dk_fs.write_atomic(str(path), text.encode("utf-8"), 0o600, no_links=True)


class Store:
    def __init__(self, directory):
        self.directory = safe_directory(Path(directory))
        self.path = self.directory / "settings.json"
        self.backup = Path(str(self.path) + dk_fs.LAST_GOOD)
        self.warning = None
        legacy = self.directory / LEGACY_BACKUP
        if legacy.is_file() and not legacy.is_symlink() and not os.path.lexists(self.backup):
            try:  # One save behind, so a recovery from it still asks the user to check their latest edits.
                os.replace(legacy, self.backup)
            except OSError:
                pass  # The first read or write makes a copy of the current settings instead.

    def load(self):
        try:
            loaded = dk_fs.load_json(str(self.path), limit=MAX_SETTINGS_BYTES, validate=validate_state, no_links=True)
        except OSError:  # E.g. no permission or a full disk while repairing: nothing more is touched.
            try:  # Start from the last good copy without writing anything, as 1.1 did.
                saved = dk_fs.parse_json(dk_fs.read_bytes(str(self.backup), MAX_SETTINGS_BYTES, no_links=True),
                                         validate_state)
            except (OSError, ValueError):
                raise EQError("Settings could not be read. Existing files were preserved.") from None
            self.warning = "Recovered the last good settings. Check your most recent edits."
            return validate_state(saved)
        if loaded.status == "missing":
            return initial_state()
        if loaded.status == "lost":
            raise EQError("Settings could not be read. Existing files were preserved; restore a valid backup before editing.")
        if loaded.status == "recovered":
            self.warning = "Recovered the last good settings. Check your most recent edits."
        return validate_state(loaded.value)  # As validated: settings from 1.0 come back in the current format.

    def save(self, state):
        state = validate_state(state)
        try:  # The file, then its last-good copy; a failed write never changes live state.
            dk_fs.write_json(str(self.path), portable_state(state), limit=MAX_SETTINGS_BYTES, no_links=True)
        except (OSError, ValueError):  # E.g. a full disk. No path in the message, which names the user (see PresetFiles.read).
            raise EQError("Settings could not be saved. Check free space.") from None


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
            # Links inside the home folder are refused; links above it (such as /home itself) are not.
            if part.is_symlink() and self._below_home(part.parent):
                raise EQError("Symbolic link paths are not supported for preset exchange.")
        return candidate

    def _below_home(self, folder):
        try:
            folder.resolve().relative_to(self.home)
            return True
        except (OSError, RuntimeError, ValueError):
            return False

    def read(self, path):
        path = self._inside_home(path)
        if path.suffix.lower() != ".json":
            raise EQError("Choose a .json preset file.")
        # Short messages without the path, which names the user: toasts can be seen on a stream.
        try:
            raw = dk_fs.read_bytes(str(path), MAX_IMPORT_BYTES, no_links=True)
        except (FileNotFoundError, NotADirectoryError):
            raise EQError("That file is no longer there. Choose another one.") from None
        except dk_fs.UnsafePath:
            raise EQError("Choose a regular preset file.") from None
        except OSError:
            raise EQError("That file cannot be read. Choose another one.") from None
        except dk_fs.Damaged:
            raise EQError(f"Preset files must be no larger than {MAX_IMPORT_BYTES // 1024} KiB.") from None
        try:
            return raw.decode("utf-8-sig")
        except UnicodeError:
            raise EQError("Preset files must use UTF-8 text.") from None

    def write(self, document, title, directory=None):
        folder = self._inside_home(directory or str(self.default_directory))
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", title).strip("-")[:60] or "preset"
        payload = (json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
        try:  # E.g. no permission, a read-only card, a full disk: no path in the message (see read()).
            safe_directory(folder)
            for index in range(1, 1001):
                # O_EXCL: an export never replaces a file (dk_fs only has replacing writes).
                path = folder / f"{slug}{'' if index == 1 else '-' + str(index)}.json"
                try:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
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
        except OSError:
            raise EQError("Cannot save there. Choose another folder.") from None
        raise EQError("Too many exports with this name. Choose another folder.")
