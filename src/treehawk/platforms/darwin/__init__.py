"""macOS implementation, built on libproc and sysctl only - no third-party
libraries and no shelling out to ps or top for metrics.

The shapes differ from Linux but the seams are the same: ``/proc`` becomes
libproc, and the cgroup becomes the coalition - the boundary a forked child
cannot leave, which is what makes the orphan rule work here too."""

from treehawk.platforms.darwin.coalition import CoalitionSource
from treehawk.platforms.darwin.host import DarwinHostInfoSource
from treehawk.platforms.darwin.libproc import LibProc, ProcessTable
from treehawk.platforms.darwin.source import DarwinProcessSource

__all__ = [
    "CoalitionSource",
    "DarwinHostInfoSource",
    "DarwinProcessSource",
    "LibProc",
    "ProcessTable",
]
