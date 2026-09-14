# AGENTS.md

Rules for anyone - human or AI agent - changing this repository.

## Type checking: mypy, at maximum strictness

mypy is the type checker. Its settings live in `[tool.mypy]` in `pyproject.toml`
and are as strict as mypy allows: `strict`, every optional error code, and every
`disallow_any_*` flag except `disallow_any_expr`. It checks `src/`, `tests/` and
`.github/scripts/`.

**Never overwrite or ignore mypy rules.** In particular:

- Do not loosen, remove or override any `[tool.mypy]` setting, and do not add
  per-module overrides or exclusions.
- Do not add `# type: ignore` (with or without an error code), `# mypy:` file
  comments, or `@no_type_check`.
- Do not write `Any`, and do not use `cast()` to get past an error.

When mypy reports an error, fix the code: annotate it, narrow the type, or
restructure it. Values read from a log record are `object` until narrowed - use
the helpers in `src/treehawk/core/values.py`. Every function, tests included, is
fully annotated, and a method that overrides another carries `@override` (import
it from `treehawk.core.compat`).

## Lint and format: ruff

Configuration is in `ruff.toml`. Code must pass `ruff format --check` and
`ruff check`. Fix what ruff reports rather than suppressing it.

## Before calling a change done

```bash
uv run ruff format
uv run ruff check
uv run mypy
uv run pytest
```

CI runs all four on every push and pull request, on Python 3.10 to 3.14. A
change that fails any of them is not finished.

## Compatibility

Python 3.10 is the oldest supported version. Anything newer from the standard
library needs a fallback in `src/treehawk/core/compat.py`.

## Where things go inside a package

- Dataclasses and TypedDicts live in the package's `models.py` (run settings
  stay in `core/config.py`).
- Named numbers and strings live in the package's `constants.py`, as fields of
  a frozen dataclass exposed through one instance (`CORE`, `CHARTS`, `LINUX`,
  ...), so a call site reads `CORE.prune_keep` rather than a bare number.

## Versioning

Any change to `src/` or `pyproject.toml` must raise the version in
`pyproject.toml` (`uv version --bump patch`, or `minor` / `major`); CI rejects a
package change without it.
