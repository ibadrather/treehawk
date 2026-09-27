# AGENTS.md

Rules for anyone - human or AI agent - changing this repository.

## Type checking: ty

ty (from the makers of uv and ruff) is the type checker. Its settings live in
`ty.toml` - not `pyproject.toml` - and are as strict as ty allows: every rule is
an error, including the `unsound-*` rules that stop an `Any` from leaking
through a typed boundary. It checks `src/`, `tests/` and `.github/scripts/`.

**Never loosen or bypass the type checker.** In particular:

- Do not lower, remove or override any rule in `ty.toml`, and do not add
  per-file overrides or exclusions.
- Do not add `# ty: ignore` or `# type: ignore` comments (ty is set not to
  honour the latter anyway), or `@no_type_check`.
- Do not write `Any`, and do not use `cast()` to get past an error. ruff bans
  all three (`banned-api` in `ruff.toml`).

When ty reports an error, fix the code: annotate it, narrow the type, or
restructure it. Values read from a log record are `object` until narrowed - use
the helpers in `src/treehawk/core/values.py` (`as_record` turns parsed JSON into
a `Record`). Every function, tests included, is fully annotated, and a method
that overrides another carries `@override` (import it from
`treehawk.core.compat`).

If a suppression is ever truly unavoidable - a wrong third-party stub, say -
use `# ty: ignore[<rule>]` with the rule named, on that one line, and a comment
saying why. ty rejects blanket and unused ones.

ty assumes the oldest Python in `requires-python` unless told otherwise, so run
it through `make typecheck`, which passes the venv's version. CI passes each
version in its matrix.

## Lint and format: ruff

Configuration is in `ruff.toml`. Code must pass `ruff format --check` and
`ruff check`. Fix what ruff reports rather than suppressing it.

## Before calling a change done

```bash
uv run ruff format
uv run ruff check
make typecheck
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
