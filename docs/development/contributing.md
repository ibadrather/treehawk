# Contributing

treehawk uses [uv](https://docs.astral.sh/uv/) for everything: environments,
running tools, building and versioning.

## Set up

```console
$ git clone https://github.com/ibadrather/treehawk
$ cd treehawk
$ uv sync
$ uv run treehawk --help
```

## Before calling a change done

```console
$ uv run ruff format
$ uv run ruff check
$ uv run mypy
$ uv run pytest
```

CI runs all four on every push and pull request, on Python 3.10 to 3.14. A change
that fails any of them is not finished.

For a faster loop, skip the tests that start real processes:

```console
$ uv run pytest -m "not integration"
```

## The rules

The full text is in [`AGENTS.md`](https://github.com/ibadrather/treehawk/blob/main/AGENTS.md),
and it applies to humans and AI agents alike.

**mypy at maximum strictness.** Its settings in `[tool.mypy]` are as strict as mypy
allows, and they are never loosened. No `# type: ignore`, no `Any`, no `cast()` to
get past an error: fix the code instead. Values read from a log are `object` until
narrowed with the helpers in `src/treehawk/core/values.py`. Every function, tests
included, is fully annotated, and an overriding method carries `@override` from
`treehawk.core.compat`.

**ruff for lint and format.** Configuration is in `ruff.toml`. Fix what it reports
rather than suppressing it.

**Python 3.10 is the floor.** Anything newer from the standard library needs a
fallback in `src/treehawk/core/compat.py`.

## Versioning and releases

Any change to `src/` or `pyproject.toml` must raise the version:

```console
$ uv version --bump patch      # or minor, or major
```

The **Version bump** workflow fails a pull request or a push to `main` that changes
the package without it. When `main` then carries a version with no release, the
**Release** workflow runs the full CI matrix, builds the sdist and the universal
wheel, and publishes GitHub release `v<version>` with both and `install.sh`
attached. Pre-release versions such as `0.4.0rc1` are marked as pre-releases and
never become "latest".

<figure class="diagram" markdown="span">
  ![CI, the version check and the docs build run on every push; merging a new version to main releases it and deploys the docs](../assets/diagrams/release-pipeline.light.svg#only-light)
  ![CI, the version check and the docs build run on every push; merging a new version to main releases it and deploys the docs](../assets/diagrams/release-pipeline.dark.svg#only-dark)
</figure>

## Documentation

This site is built with [MkDocs](https://www.mkdocs.org/) and
[Material for MkDocs](https://squidfunk.github.io/mkdocs-material/). Pages live in
`docs/`, navigation in `mkdocs.yml`, and the dependencies are pinned in
`docs/requirements.txt`. Preview it with live reload:

```console
$ uvx --with-requirements docs/requirements.txt mkdocs serve
```

The **Docs** workflow builds the site with `--strict` on every push and pull
request, so a broken link or a page missing from the navigation fails the check.
Pushes to `main` publish it to GitHub Pages.

To change the pinned versions, edit `docs/requirements.in` and recompile:

```console
$ uv pip compile docs/requirements.in -o docs/requirements.txt --universal --python-version 3.12
```

### Diagrams

Every diagram is a [draw.io](https://www.drawio.com/) file in
`docs/assets/diagrams/`. Open one in draw.io desktop, the
[VS Code extension](https://marketplace.visualstudio.com/items?itemName=hediet.vscode-drawio)
or [app.diagrams.net](https://app.diagrams.net/), edit it, and export it again:

```console
$ docs/scripts/render-diagrams.sh
```

This needs draw.io desktop on your `PATH` (or `DRAWIO=/path/to/drawio`). Each
diagram is exported twice, for light and dark pages, and both exports are
committed, so neither the site build nor the README on GitHub needs draw.io. A page
shows the export that matches the reader's theme:

```markdown
<figure class="diagram" markdown="span">
  ![What the diagram shows](../assets/diagrams/memory.light.svg#only-light)
  ![What the diagram shows](../assets/diagrams/memory.dark.svg#only-dark)
</figure>
```

and the README does the same with a `<picture>` element.

The fills follow the process colours of the dashboard and the PDF: blue for the
matched process, orange for ordinary children, green for detached ones.

### Screenshots and sample output

The dashboard and report images, the sample log, and the PDF pages are real output,
not mock-ups. Re-record them after a change to what treehawk displays:

```console
$ docs/scripts/render-output.sh
```

It runs `docs/scripts/demo/train.py` under `treehawk run`, renders the PDF and its
pages (with `pdftoppm`), and draws the terminal output with treehawk's own
renderers. It needs a systemd user session and `poppler-utils`.
