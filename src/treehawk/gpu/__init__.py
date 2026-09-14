"""GPU metrics - the seam, ahead of the implementation.

A collector returns a flat mapping that the monitor merges into each sample
record under the collector's namespace, so adding GPU support means registering
a class here: the sampling loop, the record schema and the sinks stay as they
are. Nothing is registered yet, so ``build_collectors()`` returns an empty list
and no GPU keys appear in the output.
"""

from treehawk.gpu.registry import COLLECTOR_KINDS, CollectorFactory, build_collectors

__all__ = ["COLLECTOR_KINDS", "CollectorFactory", "build_collectors"]
