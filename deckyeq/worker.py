"""Supervise PipeWire for one EQ session; no orphan DSP and no 100% hardware after a crash.

If Decky's backend dies, the parent-death signal delivers SIGTERM here. The DeckyEQ sink
then disappears and WirePlumber moves its streams to the hardware, which EQ holds at 100%.
So restore the user's EQ volume from the journal first, then stop PipeWire. A normal
Runtime.stop() restores routing itself and terminates this process while Decky is alive.
"""
import ctypes
import json
import os
import signal
import subprocess
import sys

PR_SET_PDEATHSIG = 1
MAX_VOLUME = 10 * 65536


def restore(journal, pactl):
    try:
        with open(journal, encoding="utf-8") as handle:
            saved = json.load(handle)
        if saved.get("volume_committed") is not True:
            return  # Not routed yet: the hardware still has its own volume.
        target, level = saved["volume"]["target"], saved["volume"]["last"]
        values, mute = level["values"], level["mute"]
        if (not isinstance(target, str) or type(mute) is not bool or not isinstance(values, list) or
                not 1 <= len(values) <= 32 or any(type(v) is not int or not 0 <= v <= MAX_VOLUME for v in values)):
            return
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return
    for args in (("set-sink-volume", target, *map(str, values)), ("set-sink-mute", target, "1" if mute else "0")):
        try:
            subprocess.run((pactl, *args), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=3, check=False)
        except (OSError, subprocess.SubprocessError):
            pass


def main():
    parent, executable, config, journal, pactl = int(sys.argv[1]), *sys.argv[2:6]
    libc = ctypes.CDLL(None, use_errno=True)
    signals = {signal.SIGTERM, signal.SIGCHLD}
    signal.pthread_sigmask(signal.SIG_BLOCK, signals)  # Handled synchronously with sigwaitinfo().
    if libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Cannot attach audio process to Decky lifecycle")
    if os.getppid() != parent:
        return 0  # Decky exited before routing changed.

    def child_setup():
        signal.pthread_sigmask(signal.SIG_UNBLOCK, signals)
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)

    child = subprocess.Popen((executable, "-c", config), stdin=subprocess.DEVNULL, preexec_fn=child_setup)
    while True:
        if signal.sigwaitinfo(signals).si_signo == signal.SIGCHLD:
            if child.poll() is not None:
                return child.returncode
            continue
        if os.getppid() != parent:
            restore(journal, pactl)
        child.terminate()
        try:
            child.wait(2)  # Runtime waits longer (TERMINATE_SECONDS) before killing the group.
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        return 0


if __name__ == "__main__":
    sys.exit(main())
