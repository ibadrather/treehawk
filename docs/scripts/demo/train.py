"""The workload the docs are illustrated with: a small "training job".

It loads data, starts two ordinary data-loader workers, then a metrics daemon
that detaches itself (fork, setsid, fork), and trains. The daemon outlives the
training process, which is exactly the case treehawk exists to catch.

    python3 train.py
"""

from __future__ import annotations

import os
import time

SLICE = 0.1


def burn(*, seconds: float, megabytes: int, duty: float = 1.0) -> None:
    """Hold ``megabytes`` of resident memory, busy for ``duty`` of each slice."""
    block = bytearray(megabytes * 1024 * 1024)
    for index in range(0, len(block), 4096):
        block[index] = 1  # touch every page so it is really resident
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        busy_until = time.monotonic() + SLICE * duty
        while time.monotonic() < busy_until:
            sum(index * index for index in range(2000))
        time.sleep(SLICE * (1 - duty))


def spawn(*, seconds: float, megabytes: int, duty: float, detach: bool) -> None:
    """Start a child that burns, optionally daemonizing it first."""
    if os.fork() > 0:
        return
    if detach:
        os.setsid()
        if os.fork() > 0:
            os._exit(0)  # the intermediate child exits, orphaning the daemon
    burn(seconds=seconds, megabytes=megabytes, duty=duty)
    os._exit(0)


def main() -> None:
    burn(seconds=3, megabytes=180)  # load the dataset
    spawn(seconds=9, megabytes=120, duty=1.0, detach=False)  # data-loader worker
    spawn(seconds=12, megabytes=140, duty=0.8, detach=False)  # data-loader worker
    burn(seconds=1, megabytes=0)
    spawn(seconds=16, megabytes=60, duty=0.35, detach=True)  # metrics daemon
    burn(seconds=12, megabytes=260)  # the training loop


if __name__ == "__main__":
    main()
