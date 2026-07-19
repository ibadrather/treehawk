# Agents

The AI-agent infrastructure has three layers, each answering a different "how do agents behave
correctly here" question. Full explanation: `docs/AGENTIC.md`.

## Layer 1: rules in context — `AGENTS.md`

~20 short rules every coding agent reads at session start (prefer integration tests, no
`.unwrap()` outside tests, never build `--release`, and so on). `CLAUDE.md` includes it via
`@AGENTS.md`, so Claude Code, Codex, and humans all read one rulebook. Edit rules only in
`AGENTS.md`.

**When:** injected into every agent session, automatically.

## Layer 2: harness hooks — `agents/hooks/`

Scripts the agent harness runs mechanically at lifecycle events (see
[02-pre-commit-and-hooks.md](02-pre-commit-and-hooks.md)): auto-format after every edit, sandbox
setup at session start. Rules live in layer 1 because the agent *might* follow them; anything
that must happen 100% of the time lives here.

**When:** during any agent session, on the matching event.

## Layer 3: canned automations — `agents/prompts/` + GitHub workflows

Task prompts for repeatable jobs (issue triage, bug reproduction, PR security review, PR
rebase), with JSON Schemas in `agents/schemas/` constraining their output and the threat model
in `agents/references/threat-model.md` grounding the security review. Two workflows run them in
CI via `anthropics/claude-code-action`:

- `agent-issue-triage.yml` — when an issue is opened.
- `agent-security-review.yml` — when a PR is opened or marked ready for review.

**When:** both are opt-in and currently inert — they do nothing until the repo has an
`ANTHROPIC_API_KEY` secret and the `ENABLE_AGENT_TRIAGE` / `ENABLE_AGENT_REVIEW` repository
variables set to `true`. The prompts also work from a local checkout; see `agents/README.md`.
