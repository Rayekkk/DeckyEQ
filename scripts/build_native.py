"""Build bin/deckyeq_dsp.so, the LADSPA stereo stage. On Windows it compiles through WSL.
`npm run package` builds it every time, so a ZIP never carries a library older than native/deckyeq_dsp.c."""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE, OUTPUT = "native/deckyeq_dsp.c", "bin/deckyeq_dsp.so"
FLAGS = ["-std=c11", "-O2", "-fPIC", "-shared", "-fvisibility=hidden", "-Wall", "-Wextra", "-Werror",
         "-fstack-protector-strong", "-fstack-clash-protection", "-fcf-protection=full", "-D_FORTIFY_SOURCE=2", "-Wl,-z,relro,-z,now", "-Wl,--as-needed"]


def main():
    (ROOT / "bin").mkdir(exist_ok=True)
    command = ["gcc", *FLAGS, "-o", OUTPUT, SOURCE, "-lm"]
    if sys.platform == "win32":
        if not shutil.which("wsl"):
            raise SystemExit("Building the audio stage needs gcc; on Windows install WSL with gcc.")
        command = ["wsl", "-e", *command]
    subprocess.run(command, cwd=ROOT, check=True)
    print(f"Built {OUTPUT}")


if __name__ == "__main__":
    main()
