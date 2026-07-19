# Pre-commit and hooks

Two separate hook systems exist, and they fire at different moments. Both only fix or flag
things early — CI enforces the same checks regardless, so skipping them never lets a mistake
through.

## Git pre-commit hook (optional, human-facing)

`.pre-commit-config.yaml` declares two checks that run on every `git commit`, but only after you
install the hook once:

```bash
uvx pre-commit install   # or: pipx run pre-commit install
```

From then on, each commit runs:

1. **typos** — spell check on the staged files.
2. **rustfmt** — formats staged `.rs` files.

If a check fails, the commit is aborted; fix (or accept the auto-fix), re-stage, and commit
again. Without the install step this file does nothing — it is inert configuration.

## Agent hooks (automatic, agent-facing)

When a coding agent (Claude Code, Codex) works in this repo, its harness runs scripts from
`agents/hooks/` at fixed lifecycle points, wired via `.claude/settings.json` and
`.codex/hooks.json`:

- **`post-edit-format.py`** — after every file edit the agent makes, runs `cargo fmt` on the
  edited `.rs` file. This is why agent-written code never reaches CI unformatted.
- **`session-start.sh`** — when an agent session starts. A no-op locally; in remote (web)
  sandboxes it installs `gh`, rustfmt, and clippy.

These run mechanically — the agent cannot forget them. See [04-agents.md](04-agents.md) and
`docs/AGENTIC.md` for the reasoning behind this split.
