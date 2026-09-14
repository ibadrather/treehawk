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
uv run pytest            # -m "not integration" for the fast ones
```

CI runs all four on Python 3.10 to 3.14. mypy runs at its strictest settings and
they are never loosened; see [`AGENTS.md`](https://github.com/ibadrather/treehawk/blob/main/AGENTS.md).
Any change to `src/` or `pyproject.toml` must raise the version with
`uv version --bump patch`; a new version on `main` is released automatically.

## Architecture

![Layers: cli wires platforms and sinks into the core monitor](assets/diagrams/architecture.light.svg#only-light)
![Layers: cli wires platforms and sinks into the core monitor](assets/diagrams/architecture.dark.svg#only-dark)

`core` holds the policy and knows nothing about Linux; `platforms`, `sinks` and
`gpu` implement its protocols; `cli` is the only place that wires them together.
Extending treehawk means adding a class and a registry entry:

| To add | Implement | Register in |
|---|---|---|
| a metric (GPU, I/O) | `MetricCollector` | `gpu/registry.py` |
| a membership rule | `ExpansionStrategy` | `core/strategies.py` |
| a way to name a process | `ProcessMatcher` | `core/matchers.py` |
| an output format | `Sink` | `sinks/factory.py` |
| a PDF page | `Page` | `charts/report.py` |
| an operating system | `ProcessSource`, `HostInfoSource` | `platforms/registry.py` |

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
