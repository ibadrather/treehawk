# Getting started

A short orientation for anyone overwhelmed by the tooling in this repo. Each page below covers
one stage of the development lifecycle: what runs, when it runs, and where (your machine, a git
hook, or GitHub Actions). These pages are deliberately brief — they link into the deeper docs
(`docs/CODE.md`, `docs/AGENTIC.md`, `CONTRIBUTING.md`, `TESTING.md`) when you want the full
story.

## The lifecycle at a glance

| Stage | When | Where | What runs |
|---|---|---|---|
| Editing | as you type / on agent edits | your machine | rustfmt (editor or `PostToolUse` hook) |
| `git commit` | on every commit (if installed) | your machine | pre-commit: typos + rustfmt |
| Before pushing | manually, your call | your machine | fmt, clippy, test, typos, cargo-deny |
| Push / PR | automatically | GitHub Actions | the same five checks, on x86_64 + aarch64 |
| PR opened | automatically (opt-in) | GitHub Actions | agent security review |
| Issue opened | automatically (opt-in) | GitHub Actions | agent issue triage |
| Tag pushed | automatically | GitHub Actions | cargo-dist builds binaries, creates a Release |

The philosophy (from `docs/CODE.md`): every rule lives in a checked-in config file, and CI
enforces all of them. Nothing depends on you remembering anything — if you forget a step
locally, CI catches it; nothing is lost except a round trip.

## The pages, in order

1. [Development](01-development.md) — toolchain setup, the five-command check suite, and which
   config file controls what.
2. [Pre-commit and hooks](02-pre-commit-and-hooks.md) — the optional git hook and the agent
   hooks that format code automatically.
3. [CI](03-ci.md) — what GitHub Actions runs on every push and pull request, and why a red X
   maps to a command you can run locally.
4. [Agents](04-agents.md) — the AI-agent infrastructure: repo rules, harness hooks, and the
   opt-in triage/review workflows.
5. [Release](05-release.md) — how pushing a version tag turns into published binaries.
