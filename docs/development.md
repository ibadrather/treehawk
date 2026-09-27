# Development

```bash
git clone https://github.com/ibadrather/treehawk && cd treehawk
uv sync
uv run treehawk --help
```

## Checks

```bash
uv run ruff format
uv run ruff check
uv run mypy
uv run pytest            # tests/unit for the fast ones, tests/integration for real processes
```

`tests/unit/` mirrors the package layout and runs against the fakes in
`tests/conftest.py`: fake kernels (`/proc` and cgroup trees, a `ProcessTable`)
and the rest of the machine (`FakeClock`, `RecordingSink`, `FakeLauncher`, and
`fake_platform`, which bundles them into a `Platform`). The commands take their
platform, clock and PID from a `Runtime` (`cli/models.py`), so `tests/unit/cli/`
runs `watch` and `run` end to end through `CliRunner.invoke(obj=Runtime(...))`
without touching the real machine. `tests/integration/` spawns real processes:
`tests/integration/workload.py` deliberately double-forks a detached child, and
the tests check it is still in the log after its parent has gone.

CI runs all four on Python 3.10 to 3.14 on Linux, and on the oldest and newest
of those on macOS (Apple Silicon). mypy runs at its strictest settings and
they are never loosened; see [`AGENTS.md`](https://github.com/ibadrather/treehawk/blob/main/AGENTS.md).

## Releasing

The version is written in one place, `pyproject.toml`. Any change to `src/` or
`pyproject.toml` must raise it, or the **Version bump** workflow fails:

```bash
uv version --bump patch      # or minor / major
```

When `main` carries a version with no release yet, the **Release** workflow
runs the full CI matrix, builds the sdist and the wheel, and publishes GitHub
release `v<version>` with both, plus `install.sh`. Pre-release versions such as
`0.8.0rc1` are marked as pre-releases and never become "latest".

## Architecture

![Layers: cli wires platforms and sinks into the core monitor](assets/diagrams/architecture.light.svg#only-light)
![Layers: cli wires platforms and sinks into the core monitor](assets/diagrams/architecture.dark.svg#only-dark)

`core` holds the policy and knows nothing about any operating system;
`platforms`, `sinks` and `gpu` implement its protocols; `cli` is the only place
that wires them together. Extending treehawk means adding a class and a
registry entry:

| To add | Implement | Register in |
|---|---|---|
| a metric (GPU, I/O) | `MetricCollector` | `gpu/registry.py` |
| a membership rule | `ExpansionStrategy` | `core/strategies.py` |
| a way to name a process | `ProcessMatcher` | `core/matchers.py` |
| an output format | `Sink` | `sinks/factory.py` |
| a PDF page | `Page` | `charts/report.py` |
| an operating system | `ProcessSource`, `HostInfoSource`; optionally `GroupMetricSource`, `ProcessLauncher` | `platforms/registry.py` |

A platform that can start processes sets both `Platform.launcher` (used by
`run`) and `Platform.direct_launcher` (used by `run --no-isolate`); without the
second, `--no-isolate` fails with "this platform cannot start processes". Where
the OS cannot create a boundary, pass the same launcher to both, as macOS does.

macOS was added exactly that way, without touching `core/`. Two things are
worth copying from it. Make the syscall boundary an injectable protocol — as
`platforms/darwin/libproc.py` does with `ProcessTable` — and require it in the
constructor rather than defaulting to the real one: only the builder in
`platforms/registry.py` constructs the real backend, so the backend can be
tested on a machine that does not run that OS. And bind any platform library
inside the builder rather than at import time, so the module still imports and
type-checks everywhere; a `sys.platform` guard would hide it from mypy on the
other runner.

## Docs

This site is MkDocs Material, built and published to GitHub Pages by
`.github/workflows/docs.yml`. Preview it locally, or build it the way CI does:

```bash
make docs          # http://127.0.0.1:8000, with live reload
make docs-build    # mkdocs build --strict
```

Every command-line option has to appear somewhere in `docs/`:
`tests/unit/cli/test_docs_coverage.py` fails when a new flag is added without
documentation. Document an option on the page for the feature it belongs to, in
that page's options table.

Diagrams are [draw.io](https://www.drawio.com/) files in `docs/assets/diagrams/`.
Edit one, then export light and dark SVGs (needs draw.io desktop):

```bash
docs/scripts/render-diagrams.sh
```
