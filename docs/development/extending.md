# Extending treehawk

Every point where treehawk can grow is a protocol plus a registry. This page shows
each one with a small, complete example. The examples follow the project's
[rules](contributing.md#the-rules): fully annotated, and clean under mypy's
strictest settings.

## Add a metric

A `MetricCollector` adds keys to every sample under its own namespace. The loop,
the schema and the sinks are untouched. This one records the machine's load
average:

```python title="src/treehawk/gpu/loadavg.py"
from __future__ import annotations

import pathlib
from collections.abc import Mapping

from treehawk.core.models import Snapshot


class LoadAverageCollector:
    """Adds the machine's one-minute load average to every sample."""

    @property
    def namespace(self) -> str:
        return "loadavg"

    def collect(self, snapshot: Snapshot) -> Mapping[str, object]:
        del snapshot  # a machine-wide figure: nothing to read from the workload
        one_minute = pathlib.Path("/proc/loadavg").read_text(encoding="utf-8").split()[0]
        return {"one_minute": float(one_minute)}

    def close(self) -> None:
        """Nothing to release."""
```

Register it by name in `src/treehawk/gpu/registry.py`:

```python
COLLECTOR_KINDS: dict[str, CollectorFactory] = {"loadavg": LoadAverageCollector}
```

and ask for it in `WatchConfig(collectors=("loadavg",))`. Each sample then carries
`"loadavg": {"one_minute": 1.42}`. There is no command-line flag for collectors
yet; add one in `cli/options.py` when the first real collector lands.

A collector that raises is skipped for that sample rather than stopping the run,
and `close()` is called once when the run ends. A GPU collector would read NVML in
`collect()` and release it in `close()`.

## Add a way to name the workload

A `ProcessMatcher` decides whether a process is the one the user asked for. This
one matches the kernel's process name exactly:

```python title="src/treehawk/core/matchers.py"
class NameMatcher:
    """The kernel's process name (``comm``), matched exactly."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def needs_cmdline(self) -> bool:
        return False  # comm comes with the cheap scan

    @property
    def names_one_process(self) -> bool:
        return False  # many processes can share a name

    def matches(self, *, info: ProcInfo, cmdline: str) -> bool:
        del cmdline
        return info.comm == self._name

    def describe(self) -> Record:
        return {"kind": "name", "value": self._name}
```

Register it:

```python
MATCHER_KINDS: dict[str, MatcherFactory] = {
    # ...
    "name": lambda value: NameMatcher(str(value)),
}
```

then add a `--name` option to `watch` in `cli/app.py` and a branch for it in
`_select_matcher`. `describe()` is what the log header records as `matcher`.

## Add a membership rule

An `ExpansionStrategy` returns `{pid: reason}` for processes to adopt. It gets an
`ExpansionContext` with the process table, the current members, the processes new
since the last sample, and a cached cgroup lookup. This one adopts members of a
process group led by a member:

```python title="src/treehawk/core/strategies.py"
class ProcessGroupExpansion:
    """Adopt processes in a process group whose leader is tracked."""

    @property
    def name(self) -> str:
        return "pgroup"

    def expand(self, context: ExpansionContext) -> Mapping[int, str]:
        leaders = {
            pid
            for pid in context.tracked_pids
            if (info := context.procs.get(pid)) is not None and info.pgid == pid
        }
        return {
            pid: self.name
            for pid, info in context.procs.items()
            if info.pgid in leaders and pid not in context.tracked_pids and pid != context.self_pid
        }
```

Then:

1. add `PGROUP = "pgroup"` to `ExpansionName` in `core/config.py` (the CLI offers
   the enum's values as `--expand` choices), and to `DEFAULT_EXPANSIONS` if it
   should be on by default;
2. register it in `STRATEGY_KINDS`;
3. say how views should present it in `DISCOVERY_GROUPS` in `ui/theme.py`, as
   `"child"` or `"detached"`.

The tracker applies the new rule with the others, repeating until nothing new
turns up, and never adopts treehawk or its ancestors whatever a rule returns.

## Add an output

A `Sink` receives `open(header)`, then `sample(record)` per sample, then
`close(summary)`. Subclass `BaseSink` to override only what you need. This one keeps
the busiest sample and writes it when the run ends:

```python title="src/treehawk/sinks/peak.py"
from __future__ import annotations

import json
import pathlib

from treehawk.core.compat import override
from treehawk.core.interfaces import Record
from treehawk.core.values import as_float
from treehawk.sinks.base import BaseSink


class PeakCpuSink(BaseSink):
    """Writes the sample with the highest CPU when the run ends."""

    def __init__(self, path: str) -> None:
        self._path = pathlib.Path(path)
        self._peak: Record | None = None

    @override
    def sample(self, record: Record) -> None:
        cpu = as_float(record.get("cpu_percent"))
        best = as_float(self._peak.get("cpu_percent")) if self._peak is not None else None
        if cpu is not None and (best is None or cpu > best):
            self._peak = record

    @override
    def close(self, summary: Record) -> None:
        del summary
        self._path.write_text(json.dumps(self._peak, default=str), encoding="utf-8")
```

Add it next to the log in `_build_sinks` in `cli/app.py`:

```python
sinks.add_sink(PeakCpuSink(f"{path}.peak.json"))
```

To offer it as a log format instead, add a member to `LogFormat` and a factory to
`FILE_FORMATS` in `sinks/factory.py`. Records are plain dictionaries: narrow the
values you read with the helpers in `core/values.py`. A sink that raises is
recorded and reported after the run; it never costs the other sinks their data.

## Add a PDF page

A page is a class with a `title`, a `subtitle` and `draw()`. `draw()` returns
`False` when the log lacks what the page needs, and the page is left out:

```python title="src/treehawk/charts/pages.py"
class ProcessCountPage(Page):
    """How many processes were alive at once?"""

    title = "Processes over time"
    subtitle = "live processes in the workload, sample by sample"

    @override
    def draw(self, fig: Figure, *, series: RunSeries, palette: Palette = PRINT) -> bool:
        if not series.t:
            return False
        style.draw_heading(fig, title=self.title, subtitle=self.subtitle, palette=palette)
        axes = fig.add_subplot()
        axes.step(series.t, series.n_procs, where="post", color=palette.slot(0))
        axes.set_xlabel("elapsed (seconds)")
        axes.set_ylabel("processes")
        return True
```

Append an instance to `PAGES` in `charts/report.py`. `RunSeries` holds the log as
columns (`t`, `cpu_percent`, `rss`, `pss`, `group_memory`, `n_procs`, and more) plus
one `ProcessTrack` per process. Colour by role from the palette, never with literal
hex values, so the page matches the others.

## Add an operating system

Implement the platform protocols in `platforms/<os>/`:

| Protocol | Needed | Provides |
|---|---|---|
| `ProcessSource` | yes | the process table, command lines, memory detail |
| `HostInfoSource` | yes | CPU count, clock ticks, page size, total RAM |
| `GroupMetricSource` | no | an accounting boundary's members and totals |
| `ProcessLauncher` | no | starting a command inside such a boundary, for `run` |

then register a builder in `platforms/registry.py`:

```python
PLATFORM_BUILDERS: dict[str, PlatformBuilder] = {"linux": build_linux, "darwin": build_macos}
```

Nothing in `core/`, `sinks/`, `ui/` or `charts/` changes. Keep each reader's root
configurable, as the Linux readers' `proc_root` is, so the tests can run against
fixture trees.
