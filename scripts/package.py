"""Deterministic source-inclusive Decky archive. Never deploys or connects to devices."""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DECKY_API_VERSION = "1.1.3"  # Bundled under THIRD_PARTY_SOURCES; NOTICE names this version.
REQUIRED = ["main.py", "plugin.json", "package.json", "package-lock.json", "rollup.config.js", "tsconfig.json",
            "README.md", "DeckyEQ.png", "NOTICE", "LICENSE", "dist/index.js", "dist/index.js.map", "scripts/package.py",
            "scripts/build_native.py", "bin/deckyeq_dsp.so"]
SOURCE_DIRECTORIES = ("deckyeq", "src", "docs", "schemas", "presets", "native")
SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".md", ".json", ".c")
RUNTIME_FILES = ("DeckyEQ/main.py", "DeckyEQ/deckyeq/runtime.py", "DeckyEQ/deckyeq/worker.py",
                 "DeckyEQ/deckyeq/limits.json", "DeckyEQ/dist/index.js", "DeckyEQ/bin/deckyeq_dsp.so")
PREVIEW_MARKERS = ("PreviewEngine", "UI preview", "alsa_output.preview", "preview-modal")
FIXED_TIMESTAMP = (2026, 9, 7, 0, 0, 0)  # Same bytes for the same inputs.


def read_json(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def checked_version():
    manifest, project, lock = read_json("plugin.json"), read_json("package.json"), read_json("package-lock.json")
    version = manifest["version"]
    if version != project["version"] or version != lock["version"] or version != lock["packages"][""]["version"]:
        raise ValueError("Version metadata does not match.")
    if manifest["name"] != "DeckyEQ" or project["name"] != "deckyeq" or manifest["flags"] != []:
        raise ValueError("Unexpected plugin identity or root permissions.")
    return version


def collect_files():
    files = [(ROOT / name, "DeckyEQ/" + name) for name in REQUIRED]
    for directory in SOURCE_DIRECTORIES:
        for path in sorted((ROOT / directory).rglob("*")):
            if path.is_file() and path.suffix in SOURCE_SUFFIXES:
                files.append((path, "DeckyEQ/" + path.relative_to(ROOT).as_posix()))
    api = ROOT / "node_modules" / "@decky" / "api"
    if json.loads((api / "package.json").read_text())["version"] != DECKY_API_VERSION:
        raise ValueError("Update NOTICE before changing the bundled Decky API version.")
    files.append((api / "LICENSE", "DeckyEQ/THIRD_PARTY_LICENSES/decky-api-LGPL-2.1.txt"))
    for path in sorted(api.rglob("*")):
        if path.is_file() and "node_modules" not in path.relative_to(api).parts:
            files.append((path, f"DeckyEQ/THIRD_PARTY_SOURCES/decky-api-{DECKY_API_VERSION}/" + path.relative_to(api).as_posix()))
    for path, _ in files:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing or unsafe input: {path}")
    return files


def check_bundle():
    stage = (ROOT / "bin/deckyeq_dsp.so").read_bytes()
    if stage[:4] != b"\x7fELF" or stage[4] != 2 or stage[18:20] != b"\x3e\x00":
        raise ValueError("bin/deckyeq_dsp.so is not an x86-64 Linux library; run scripts/build_native.py.")
    bundle = (ROOT / "dist/index.js").read_text(encoding="utf-8")
    if any(marker in bundle for marker in PREVIEW_MARKERS):
        raise ValueError("Development preview leaked into the production bundle.")
    if "SP_REACT" not in bundle or "DFL" not in bundle:
        raise ValueError("Expected Decky/Steam frontend externals were not found.")


def write_archive(output, files):
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, name in sorted(files, key=lambda item: item[1]):
            info = zipfile.ZipInfo(name, FIXED_TIMESTAMP)
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())


def verify_archive(output):
    with zipfile.ZipFile(output) as archive:
        if archive.testzip():
            raise ValueError("Archive integrity check failed.")
        names = archive.namelist()
    if len(names) != len(set(names)):
        raise ValueError("Duplicate archive paths.")
    if any("__pycache__" in name or "/preview/" in name or name.endswith("settings.json") for name in names):
        raise ValueError("Unexpected state or development file in package.")
    for name in RUNTIME_FILES:
        if name not in names:
            raise ValueError(f"Required runtime file missing: {name}")


def package():
    version = checked_version()
    files = collect_files()
    check_bundle()
    output = ROOT / "artifacts" / f"DeckyEQ-{version}.zip"
    output.parent.mkdir(exist_ok=True)
    write_archive(output, files)
    verify_archive(output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(f"{digest}  {output.name}\n", encoding="ascii")
    print(f"Packaged {len(files)} files: {output}")
    print(f"Size: {output.stat().st_size} bytes")
    print(f"SHA-256: {digest}")
    return output


if __name__ == "__main__":
    try:
        package()
    except (OSError, ValueError, KeyError) as exc:
        print(f"Package failed: {exc}", file=sys.stderr)
        sys.exit(1)
