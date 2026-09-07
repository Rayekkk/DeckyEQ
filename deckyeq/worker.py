"""Exec PipeWire with a Linux parent-death signal; no orphan DSP on Decky exit."""
import ctypes
import os
import signal
import sys


def main():
    parent, executable, config = int(sys.argv[1]), sys.argv[2], sys.argv[3]
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise OSError(ctypes.get_errno(), "Cannot attach audio process to Decky lifecycle")
    if os.getppid() != parent:
        return
    os.execve(executable, [executable, "-c", config], os.environ.copy())


if __name__ == "__main__":
    main()
