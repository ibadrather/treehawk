"""A synthetic workload for exercising treehawk.

It does the thing that breaks naive process monitors: spawns a child that
double-forks and calls ``setsid``, so the child is re-parented to PID 1 and
leaves the parent's session entirely. The parent then exits early, while the
detached grandchild keeps burning CPU and holding memory.

    python tests/integration/workload.py [--seconds N] [--mb N] [--children N] [--detach]
"""

from __future__ import annotations

import argparse
import os
import sys
import time


def burn(seconds: float, megabytes: int, label: str) -> None:
    """Hold ``megabytes`` of resident memory while spinning for ``seconds``."""
    block = bytearray(megabytes * 1024 * 1024) if megabytes else bytearray()
    for index in range(0, len(block), 4096):
        block[index] = 1  # touch every page so it is really resident
    deadline = time.monotonic() + seconds
    total = 0
    while time.monotonic() < deadline:
        total += sum(index * index for index in range(5000))
    print(f"{label} pid={os.getpid()} done ({total % 7})", file=sys.stderr)


def detach() -> bool:
    """Daemonize: fork, setsid, fork again. Returns True in the grandchild."""
    if os.fork() > 0:
        return False  # original parent
    os.setsid()
    if os.fork() > 0:
        os._exit(0)  # intermediate child exits, orphaning the grandchild
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=6.0)
    parser.add_argument("--mb", type=int, default=64)
    parser.add_argument("--children", type=int, default=1)
    parser.add_argument("--detach", action="store_true", help="daemonize the children")
    parser.add_argument("--parent-seconds", type=float, default=1.0)
    parser.add_argument(
        "--spawn-delay",
        type=float,
        default=0.0,
        help="wait before spawning, so a monitor attaches first",
    )
    args = parser.parse_args()

    if args.spawn_delay:
        burn(args.spawn_delay, 4, "pre-spawn")

    for _ in range(args.children):
        if args.detach:
            if detach():
                burn(args.seconds, args.mb, "detached-child")
                os._exit(0)
        elif os.fork() == 0:
            burn(args.seconds, args.mb, "child")
            os._exit(0)

    burn(args.parent_seconds, 8, "parent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
