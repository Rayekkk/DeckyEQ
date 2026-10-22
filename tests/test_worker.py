import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

WORKER = Path(__file__).resolve().parent.parent / "deckyeq" / "worker.py"
FAKE_PIPEWIRE = """#!/usr/bin/env python3
import signal, sys, time
def stop(*_):
    open(sys.argv[0] + ".stopped", "w").close()
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
open(sys.argv[0] + ".started", "w").write(" ".join(sys.argv[1:]))
time.sleep(60)
"""
PARENT = """import os, subprocess, sys, time
subprocess.Popen([sys.executable, "-I", sys.argv[1], str(os.getpid()), *sys.argv[2:]])
time.sleep(60)
"""


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@unittest.skipUnless(sys.platform.startswith("linux"), "the worker uses Linux prctl and real processes")
class WorkerTests(unittest.TestCase):
    def test_pipewire_stops_when_decky_dies(self):
        with tempfile.TemporaryDirectory() as temp:
            pipewire = Path(temp) / "pipewire"
            pipewire.write_text(FAKE_PIPEWIRE)
            pipewire.chmod(0o755)
            parent = subprocess.Popen([sys.executable, "-I", "-c", PARENT, str(WORKER), str(pipewire), "deckyeq.conf"])
            try:
                started = Path(str(pipewire) + ".started")
                self.assertTrue(wait_for(started.exists))
                self.assertEqual(started.read_text(), "-c deckyeq.conf")  # exec'd in place of the worker
                parent.send_signal(signal.SIGKILL)
                self.assertTrue(wait_for(Path(str(pipewire) + ".stopped").exists))
            finally:
                if parent.poll() is None:
                    parent.kill()
                parent.wait()


if __name__ == "__main__":
    unittest.main()
