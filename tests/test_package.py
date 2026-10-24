"""scripts/package.py ships only tracked sources, refuses untracked or deleted ones, and takes only a stage built from
the current C file."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_native  # noqa: E402
import package  # noqa: E402


class PackageInputs(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        root = patch.object(package, "ROOT", self.root)
        root.start()
        self.addCleanup(root.stop)

    @unittest.skipUnless(shutil.which("git"), "git lists the tracked files")
    def test_untracked_notes_stay_out_but_edits_go_in(self):
        (self.root / "docs").mkdir()
        for name in ("tracked.md", "notes.md"):
            (self.root / "docs" / name).write_text("committed or staged")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "docs/tracked.md"], cwd=self.root, check=True)
        (self.root / "docs" / "tracked.md").write_text("edited, not committed")
        self.assertEqual(package.source_files(), [(self.root / "docs/tracked.md", "DeckyEQ/docs/tracked.md")])

    @unittest.skipUnless(shutil.which("git"), "git lists the deleted files")
    def test_a_tracked_source_deleted_from_the_working_tree_is_refused(self):
        # Packaged without it, the plugin would fail to import on the device.
        (self.root / "deckyeq").mkdir()
        for name in ("runtime.py", "service.py"):
            (self.root / "deckyeq" / name).write_text("tracked")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "deckyeq"], cwd=self.root, check=True)
        package.check_untracked()
        (self.root / "deckyeq" / "service.py").unlink()
        with self.assertRaisesRegex(ValueError, "git rm them or restore them: deckyeq/service.py$"):
            package.check_untracked()

    @unittest.skipUnless(shutil.which("git"), "git lists the untracked files")
    def test_a_new_source_file_git_does_not_know_yet_is_refused_but_ignored_files_are_not(self):
        (self.root / "deckyeq").mkdir()
        (self.root / "deckyeq" / "runtime.py").write_text("tracked")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "deckyeq/runtime.py"], cwd=self.root, check=True)
        (self.root / ".gitignore").write_text("*.json\n")
        (self.root / "deckyeq" / "ignored.json").write_text("{}")
        (self.root / "deckyeq" / "picture.png").write_text("not a source suffix")
        (self.root / "deckyeq" / "notes.md").write_text("a private note, left out")
        package.check_untracked()
        (self.root / "deckyeq" / "loudness.py").write_text("new")
        with self.assertRaisesRegex(ValueError, "Untracked.*deckyeq/loudness.py"):
            package.check_untracked()

    @unittest.skipUnless(shutil.which("git"), "git must exist for the checkout to be what is missing")
    def test_outside_a_git_checkout_the_message_says_so(self):
        with patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": str(self.root.parent)}):  # Never find a parent repo.
            with self.assertRaisesRegex(ValueError, "git checkout"):
                package.source_files()

    @unittest.skipUnless(shutil.which("git") and os.name == "posix", "git lists the sources; symbolic links")
    def test_a_linked_input_is_refused(self):
        for name in package.REQUIRED:
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text("x")
        api = self.root / "node_modules" / "@decky" / "api"
        api.mkdir(parents=True)
        (api / "package.json").write_text(json.dumps({"version": package.DECKY_API_VERSION}))
        (api / "LICENSE").write_text("LGPL")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        package.collect_files()
        (self.root / "NOTICE").unlink()
        (self.root / "NOTICE").symlink_to(self.root / "LICENSE")  # Would pack whatever it points to.
        with self.assertRaisesRegex(ValueError, "unsafe input"):
            package.collect_files()

    def test_settings_and_caches_never_reach_the_archive(self):
        source = self.root / "x"
        source.write_text("x")
        names = list(package.RUNTIME_FILES)
        package.write_archive(self.root / "ok.zip", [(source, name) for name in names])
        package.verify_archive(self.root / "ok.zip")
        for extra in ("DeckyEQ/deckyeq/__pycache__/model.cpython-312.pyc", "DeckyEQ/settings.json"):
            with self.subTest(extra=extra):
                package.write_archive(self.root / "bad.zip", [(source, name) for name in names + [extra]])
                with self.assertRaisesRegex(ValueError, "Unexpected state"):
                    package.verify_archive(self.root / "bad.zip")

    def test_a_stage_not_built_from_the_current_source_is_refused(self):
        (self.root / "native").mkdir()
        (self.root / "bin").mkdir()
        source = self.root / build_native.SOURCE
        stage = self.root / build_native.OUTPUT
        source.write_text("int x;\n")
        stage.write_bytes(b"\x7fELF\x02" + bytes(13) + b"\x3e\x00")
        with self.assertRaisesRegex(ValueError, "not built from"):
            package.check_native()  # No stamp: built by hand or by an older script.
        (self.root / build_native.STAMP).write_text(build_native.stamp(self.root))
        package.check_native()
        stage.write_bytes(stage.read_bytes() + b"x")  # Same source, another library.
        with self.assertRaisesRegex(ValueError, "not built from"):
            package.check_native()
        (self.root / build_native.STAMP).write_text(build_native.stamp(self.root))
        source.write_text("int y;\n")
        with self.assertRaisesRegex(ValueError, "not built from"):
            package.check_native()

    def test_the_archive_has_the_same_bytes_on_windows_and_linux(self):
        (self.root / "b.py").write_bytes(b"print('b')\n" * 50)
        (self.root / "a.so").write_bytes(bytes(range(256)) * 8)
        files = [(self.root / "b.py", "DeckyEQ/b.py"), (self.root / "a.so", "DeckyEQ/bin/a.so")]
        archives = []
        for platform in ("win32", "linux"):  # ZipInfo picks create_system from sys.platform.
            with patch.object(sys, "platform", platform):
                package.write_archive(self.root / f"{platform}.zip", files[::-1] if platform == "linux" else files)
            archives.append((self.root / f"{platform}.zip").read_bytes())
        self.assertEqual(archives[0], archives[1])
        with zipfile.ZipFile(self.root / "linux.zip") as archive:
            self.assertEqual([(i.filename, i.create_system, i.external_attr >> 16) for i in archive.infolist()],
                             [("DeckyEQ/b.py", 3, 0o100644), ("DeckyEQ/bin/a.so", 3, 0o100644)])


if __name__ == "__main__":
    unittest.main()
