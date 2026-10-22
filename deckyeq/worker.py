"""Exec a command (PipeWire, pactl subscribe) with a Linux parent-death signal: nothing outlives Decky.

usage: worker.py <parent pid> <executable> [arguments...]
"""
import ctypes
import os
import signal
import sys

PR_SET_PDEATHSIG = 1  # From <linux/prctl.h>


def main():
    parent, command = int(sys.argv[1]), sys.argv[2:]
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Cannot attach audio process to Decky lifecycle")
    if os.getppid() != parent:
        return
    os.execve(command[0], command, os.environ.copy())


if __name__ == "__main__":
    main()
