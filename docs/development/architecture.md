# Architecture

treehawk is split so that the policy (what a workload is, how to sample it) knows
nothing about Linux, terminals or PDFs, and each of those can change without
touching the rest.

<figure class="diagram" markdown="span">
  ![Layers: cli builds platforms and sinks and runs the core monitor; platforms, sinks and gpu implement core's protocols; charts and ui share one palette](../assets/diagrams/architecture.light.svg#only-light)
  ![Layers: cli builds platforms and sinks and runs the core monitor; platforms, sinks and gpu implement core's protocols; charts and ui share one palette](../assets/diagrams/architecture.dark.svg#only-dark)
</figure>

## Layers

| Package | Holds |
|---|---|
| `core/` | platform-agnostic policy: models, protocols, membership, sampling, the log schema |
| `platforms/` | the operating-system implementations of core's protocols; `linux/` reads `/proc` and cgroup v2 and launches through `systemd-run` |
| `sinks/` | where records go: JSON Lines, CSV, plain console lines, the live dashboard, and a composite that fans out to several |
| `ui/` | Rich rendering: the palette, the dashboard, the summary views |
| `charts/` | matplotlib: log to series to pages to PDF |
| `gpu/` | the seam for extra metric collectors; nothing is registered yet |
| `cli/` | the composition root: options, wiring, commands |

Inside `core/`:

| Module | Responsibility |
|---|---|
| `interfaces.py` | the narrow protocols everything else depends on |
| `tracker.py` | membership: sticky admission and exit accounting |
| `strategies.py` | one class per membership rule |
| `matchers.py` | one class per way of naming the workload |
| `monitor.py` | the sampling loop |
| `aggregate.py` | counters to rates, samples to summary |
| `records.py` | the on-disk record format, in one place |

## The rules that keep it that way

**Dependencies point inward.** `core` never imports `platforms`, `sinks`, `ui` or
`charts`. The monitor sees only protocols, so it can be tested with a fake clock
and a fake process table, and ported by writing new implementations.

**One composition root.** `cli/` is the only package that knows every layer. It
reads arguments, picks implementations and hands them to the monitor.

**Registries, not edits.** Membership rules, matchers, file formats, PDF pages,
metric collectors and platforms are each looked up in a registry. Adding one means
adding a class and a registry entry, not editing the loop. See
[Extending treehawk](extending.md).

**One place for errors.** Everything below the CLI raises a subclass of
`TreehawkError` and lets it travel. The CLI turns it into one line of text and an
exit code, once. The exit code a failure deserves is an attribute of its class.

**One schema module.** Sinks receive ready-made dictionaries from `records.py`, so
a new output format never needs to know what a `Snapshot` is, and a schema change
touches one file.

**One palette.** `ui/theme.py` defines colours by the job they do. The dashboard
uses the terminal steps and the PDF the print steps of the same hues, so a process
keeps its colour whether you watch it live or read it back a week later.

## A sample, end to end

<figure class="diagram" markdown="span">
  ![One tick of the sampling loop, from the deadline to the sinks](../assets/diagrams/sampling-loop.light.svg#only-light)
  ![One tick of the sampling loop, from the deadline to the sinks](../assets/diagrams/sampling-loop.dark.svg#only-dark)
</figure>

[The sampling loop](../concepts/sampling.md) walks through each step.

## Testing

Every reader takes its root directory as an argument, so the unit tests run
against fake `/proc` and cgroup trees built in a temporary directory. They need no
privileges and no real workload. The integration tests start
`tests/workload.py`, which deliberately double-forks a detached child, and check
that the child is still in the log after its parent is gone.

## Typing

Everything, tests included, is fully annotated and checked by mypy at the
strictest settings it offers, including the protocols the layers meet at. Values
read back from a log are `object` until narrowed with the helpers in
`core/values.py`. See [Contributing](contributing.md#the-rules).
