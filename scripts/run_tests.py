"""Run the Python tests; on Windows run them in WSL too, where the native stage and POSIX tests run."""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ["-W", "error", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]


def main():
    code = subprocess.call([sys.executable, *TESTS], cwd=ROOT)
    if sys.platform == "win32":
        if not shutil.which("wsl"):
            print("WSL is missing: the native stage and Linux-only tests were skipped.", file=sys.stderr)
        elif code == 0:
            print("Running the tests in WSL as well (native stage, Linux-only checks)...")
            code = subprocess.call(["wsl", "-e", "python3.14", *TESTS], cwd=ROOT)  # the Python Decky runs (STANDARD §4)
    sys.exit(code)


if __name__ == "__main__":
    main()
