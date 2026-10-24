"""Build bin/deckyeq_dsp.so, the LADSPA stereo stage. On Windows it compiles through WSL."""
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE, OUTPUT = "native/deckyeq_dsp.c", "bin/deckyeq_dsp.so"
FLAGS = ["-std=c11", "-O2", "-fPIC", "-shared", "-fvisibility=hidden", "-Wall", "-Wextra", "-Werror",
         "-fstack-protector-strong", "-fstack-clash-protection", "-fcf-protection=full", "-D_FORTIFY_SOURCE=2", "-Wl,-z,relro,-z,now", "-Wl,--as-needed"]
STAMP = "bin/deckyeq_dsp.so.source"  # What the library was built from; package.py checks it. Not shipped.


def stamp(root=ROOT):
    """The C source and compiler flags, and the library they produced."""
    source = hashlib.sha256((root / SOURCE).read_bytes() + " ".join(FLAGS).encode()).hexdigest()
    return f"{source} {hashlib.sha256((root / OUTPUT).read_bytes()).hexdigest()}\n"


def main():
    (ROOT / "bin").mkdir(exist_ok=True)
    command = ["gcc", *FLAGS, "-o", OUTPUT, SOURCE, "-lm"]
    if sys.platform == "win32":
        if not shutil.which("wsl"):
            raise SystemExit("Building the audio stage needs gcc; on Windows install WSL with gcc.")
        command = ["wsl", "-e", *command]
    subprocess.run(command, cwd=ROOT, check=True)
    (ROOT / STAMP).write_text(stamp(), encoding="ascii")
    print(f"Built {OUTPUT}")


if __name__ == "__main__":
    main()
