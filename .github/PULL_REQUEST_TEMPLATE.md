## What does this change?

<!-- One or two sentences: what and why. Link the issue if there is one. -->

## Checklist

- [ ] `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, and `cargo test` pass
      locally (see [TESTING.md](../TESTING.md)).
- [ ] Changed behavior has a test — integration tests in `tests/` preferred
      (see [AGENTS.md](../AGENTS.md) for the testing conventions).
- [ ] Sampling-path changes add no allocations, syscalls, or blocking work per sample
      (or the trade-off is discussed in the PR description).
- [ ] User-facing text (CLI output, errors, docs) follows [STYLE.md](../STYLE.md).
