# Agent infrastructure

Treehawk's setup for agentic development — repo rules, lifecycle hooks, automation prompts, and
structured-output schemas — is adapted from [uv](https://github.com/astral-sh/uv) by
[Astral](https://astral.sh) (MIT OR Apache-2.0). Thanks to them for publishing a working example
of agent-assisted maintenance on a serious Rust project.

## Layout

| Path | Purpose |
|---|---|
| `/AGENTS.md` | Repo rules every coding agent must follow. `CLAUDE.md` includes it via `@AGENTS.md`, so Claude Code, Codex, and anything reading `AGENTS.md` share one source of truth. |
| `hooks/session-start.sh` | Claude Code / Codex `SessionStart` hook. No-op locally; in remote (web) sandboxes it dispatches to `session-start-web.sh` to install `gh`, rustfmt, and clippy, and set `GH_REPO`. |
| `hooks/post-edit-format.py` | `PostToolUse` hook: runs `cargo fmt` on any `.rs` file an agent edits, so formatting never reaches CI. Silently no-ops when cargo is absent. |
| `prompts/` | Task prompts for automation: issue triage, bug reproduction, PR security review, conflicted-PR rebase. All treat GitHub content as untrusted input. |
| `schemas/` | JSON Schemas (copied verbatim from uv) constraining the structured output of the triage and review prompts. |
| `references/threat-model.md` | The authoritative threat model the security-review prompt judges changes against. |

The hooks are wired in `.claude/settings.json` (Claude Code) and `.codex/hooks.json` (Codex).

## CI automations (opt-in)

`.github/workflows/agent-issue-triage.yml` and `.github/workflows/agent-security-review.yml` run
the triage and security-review prompts via `anthropics/claude-code-action`. They are disabled by
default; to enable, add an `ANTHROPIC_API_KEY` repository secret and set the repository variables
`ENABLE_AGENT_TRIAGE=true` / `ENABLE_AGENT_REVIEW=true`.

## Running prompts locally

Every prompt also works from a local checkout. Create the event-context file the prompt expects,
then run it headless, e.g.:

```bash
gh issue view 42 --json number,title,body,author,labels,createdAt > .issue-triage-event.json
claude -p "$(cat agents/prompts/triage-issue.md)"
```

The `.issue-triage-event.json` / `.pull-request-review-event.json` / `.pull-request-review.diff`
scratch files are gitignored.
