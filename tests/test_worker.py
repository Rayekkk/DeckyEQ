import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

WORKER = Path(__file__).resolve().parent.parent / "deckyeq" / "worker.py"
FAKE_PACTL = """#!/usr/bin/env python3
import os, sys
stopped = os.path.exists(os.path.join(os.path.dirname(sys.argv[0]), "pipewire.stopped"))
with open(sys.argv[0] + ".log", "a") as log:
    log.write(" ".join(sys.argv[1:]) + (" AFTER-PIPEWIRE-STOPPED" if stopped else "") + "\\n")
"""
FAKE_PIPEWIRE = """#!/usr/bin/env python3
import signal, sys, time
def stop(*_):
    open(sys.argv[0] + ".stopped", "w").close()
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
open(sys.argv[0] + ".started", "w").close()
time.sleep(int(sys.argv[2]) if sys.argv[2].isdigit() else 60)
sys.exit(3)
"""
PARENT = """import subprocess, sys, time
worker = subprocess.Popen([sys.executable, "-I", *sys.argv[1:2], str(__import__("os").getpid()), *sys.argv[2:]])
print(worker.pid, flush=True)
time.sleep(60)
"""


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@unittest.skipUnless(sys.platform.startswith("linux"), "the supervisor uses Linux prctl and real processes")
class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.pactl, self.pipewire, self.journal = root / "pactl", root / "pipewire", root / "routing.json"
        for path, text in ((self.pactl, FAKE_PACTL), (self.pipewire, FAKE_PIPEWIRE)):
            path.write_text(text)
            path.chmod(0o755)
        self.journal.write_text(json.dumps({"version": 3, "volume_committed": True, "volume": {
            "target": "alsa_output.speaker", "port": "speaker", "original": {"values": [9, 9], "mute": False},
            "last": {"values": [20000, 20000], "mute": False}}}))
        self.log = Path(str(self.pactl) + ".log")

    def spawn_with_parent(self, config="60"):
        parent = subprocess.Popen([sys.executable, "-I", "-c", PARENT, str(WORKER), str(self.pipewire), config,
                                   str(self.journal), str(self.pactl)], stdout=subprocess.PIPE, text=True)
        def cleanup():
            if parent.poll() is None:
                parent.kill()
            parent.wait()
            parent.stdout.close()
        self.addCleanup(cleanup)
        worker = int(parent.stdout.readline())
        self.assertTrue(wait_for(Path(str(self.pipewire) + ".started").exists))
        return parent, worker

    def test_parent_death_restores_volume_before_stopping_pipewire(self):
        parent, worker = self.spawn_with_parent()
        parent.kill()
        self.assertTrue(wait_for(Path(str(self.pipewire) + ".stopped").exists))
        self.assertEqual(self.log.read_text().splitlines(),
                         ["set-sink-volume alsa_output.speaker 20000 20000", "set-sink-mute alsa_output.speaker 0"])
        self.assertTrue(wait_for(lambda: not Path(f"/proc/{worker}").exists() or
                                 "Z" in Path(f"/proc/{worker}/stat").read_text().split()[2]))

    def test_normal_stop_leaves_volume_to_the_runtime(self):
        parent, worker = self.spawn_with_parent()
        os.kill(worker, signal.SIGTERM)
        self.assertTrue(wait_for(Path(str(self.pipewire) + ".stopped").exists))
        self.assertFalse(self.log.exists())

    def test_worker_exits_with_pipewire(self):
        parent, worker = self.spawn_with_parent(config="0")
        self.assertTrue(wait_for(lambda: not Path(f"/proc/{worker}").exists() or
                                 "Z" in Path(f"/proc/{worker}/stat").read_text().split()[2]))
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
