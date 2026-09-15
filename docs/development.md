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

`tests/unit/` mirrors the package layout and runs against the fake kernels in
`tests/conftest.py`; `tests/integration/` spawns real processes.

CI runs all four on Python 3.10 to 3.14 on Linux, and on the oldest and newest
of those on macOS (Apple Silicon). mypy runs at its strictest settings and
they are never loosened; see [`AGENTS.md`](https://github.com/ibadrather/treehawk/blob/main/AGENTS.md).
Any change to `src/` or `pyproject.toml` must raise the version with
`uv version --bump patch`; a new version on `main` is released automatically.

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
| an operating system | `ProcessSource`, `HostInfoSource` | `platforms/registry.py` |

macOS was added exactly that way, without touching `core/`. Two things are
worth copying from it. Make the syscall boundary an injectable protocol — as
`platforms/darwin/libproc.py` does with `ProcessTable` — so the backend can be
tested on a machine that does not run that OS. And bind any platform library
inside the builder rather than at import time, so the module still imports and
type-checks everywhere; a `sys.platform` guard would hide it from mypy on the
other runner.

## Docs

This site is MkDocs Material, built and published to GitHub Pages by
`.github/workflows/docs.yml`. Preview it locally:

```bash
uvx --with mkdocs-material mkdocs serve
```

Diagrams are [draw.io](https://www.drawio.com/) files in `docs/assets/diagrams/`.
Edit one, then export light and dark SVGs (needs draw.io desktop):

```bash
docs/scripts/render-diagrams.sh
```
