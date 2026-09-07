"""Deterministic source-inclusive Decky archive. Never deploys or connects to devices."""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def package():
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    project = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    if version != project["version"] or version != lock["version"] or version != lock["packages"][""]["version"]:
        raise ValueError("Version metadata does not match.")
    if manifest["name"] != "DeckyEQ" or project["name"] != "deckyeq" or manifest["flags"] != []:
        raise ValueError("Unexpected plugin identity or root permissions.")
    required = ["main.py", "plugin.json", "package.json", "package-lock.json", "rollup.config.js", "tsconfig.json",
                "README.md", "DeckyEQ.png", "NOTICE", "LICENSE", "dist/index.js", "dist/index.js.map", "scripts/package.py"]
    files = [(ROOT / name, "DeckyEQ/" + name) for name in required]
    for directory in ("deckyeq", "src", "docs", "schemas", "presets"):
        for path in sorted((ROOT / directory).rglob("*")):
            if path.is_file() and path.suffix in (".py", ".ts", ".tsx", ".md", ".json"):
                files.append((path, "DeckyEQ/" + path.relative_to(ROOT).as_posix()))
    api = ROOT / "node_modules" / "@decky" / "api"
    if json.loads((api / "package.json").read_text())["version"] != "1.1.3":
        raise ValueError("Update NOTICE before changing the bundled Decky API version.")
    files.append((api / "LICENSE", "DeckyEQ/THIRD_PARTY_LICENSES/decky-api-LGPL-2.1.txt"))
    for path in sorted(api.rglob("*")):
        if path.is_file() and "node_modules" not in path.relative_to(api).parts:
            files.append((path, "DeckyEQ/THIRD_PARTY_SOURCES/decky-api-1.1.3/" + path.relative_to(api).as_posix()))
    for path, _ in files:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing or unsafe input: {path}")
    bundle = (ROOT / "dist/index.js").read_text(encoding="utf-8")
    if any(marker in bundle for marker in ("PreviewEngine", "UI preview", "alsa_output.preview", "preview-modal")):
        raise ValueError("Development preview leaked into the production bundle.")
    if "SP_REACT" not in bundle or "DFL" not in bundle:
        raise ValueError("Expected Decky/Steam frontend externals were not found.")
    output = ROOT / "artifacts" / f"DeckyEQ-{version}.zip"
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, name in sorted(files, key=lambda item: item[1]):
            info = zipfile.ZipInfo(name, (2026, 9, 7, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    with zipfile.ZipFile(output) as archive:
        if archive.testzip(): raise ValueError("Archive integrity check failed.")
        names = archive.namelist()
        if len(names) != len(set(names)): raise ValueError("Duplicate archive paths.")
        for name in names:
            if "__pycache__" in name or "/preview/" in name or name.endswith("settings.json"):
                raise ValueError("Unexpected state or development file in package.")
        for name in ("DeckyEQ/main.py", "DeckyEQ/deckyeq/runtime.py", "DeckyEQ/deckyeq/worker.py", "DeckyEQ/dist/index.js"):
            if name not in names: raise ValueError(f"Required runtime file missing: {name}")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(f"{digest}  {output.name}\n", encoding="ascii")
    print(f"Packaged {len(files)} files: {output}")
    print(f"Size: {output.stat().st_size} bytes")
    print(f"SHA-256: {digest}")
    return output


if __name__ == "__main__":
    try: package()
    except (OSError, ValueError, KeyError) as exc:
        print(f"Package failed: {exc}", file=sys.stderr)
        sys.exit(1)
