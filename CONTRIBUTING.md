# Contributing

Treehawk's coding standards, lint policy, and CI setup are adapted from
[uv](https://github.com/astral-sh/uv) by [Astral](https://astral.sh) (MIT OR Apache-2.0) — a
codebase widely regarded as an example of well-run Rust engineering. Where treehawk deviates
(a single-crate CLI vs. uv's large workspace), the config files say so in comments. Thanks to the
Astral team for keeping their standards public and copyable.

## Setup

Install Rust via [rustup](https://rustup.rs). The toolchain version is pinned in
`rust-toolchain.toml`; rustup picks it up automatically the first time you run any `cargo` command
in this repo.

Treehawk targets Linux (cgroup v2). It builds on other platforms, but `treehawk run` only works on
Linux — develop and test on a Linux machine.

Optionally install the commit hooks (typos + rustfmt) so mistakes are caught before CI:

```bash
uvx pre-commit install   # or: pipx run pre-commit install
```

## The check suite

CI runs these five checks on every push and PR; run them locally before pushing:

```bash
cargo fmt                                      # format (CI runs --check)
cargo clippy --all-targets -- -D warnings      # lint, warnings are errors
cargo test                                     # unit + integration tests
uvx typos                                      # spell check
cargo deny check                               # advisories, licenses, sources
```

`cargo deny` needs a one-time `cargo install cargo-deny`. Everything else ships with the toolchain.

## Lint policy

The policy lives in `Cargo.toml` under `[lints]` and in `clippy.toml`:

- **`clippy::pedantic` is on.** It catches real sloppiness (lossy casts, missing docs punctuation,
  needless clones). Lints that are more noise than signal for this codebase are individually
  allowed in `Cargo.toml`, each with a reason.
- **Warnings block merge.** CI passes `-D warnings`, so a warning locally means CI will fail.
- **`.unwrap()` is banned in production code** (`clippy::unwrap_used`) — handle the error or use
  `.expect("why this cannot fail")`. Tests may unwrap freely (`allow-unwrap-in-tests` in
  `clippy.toml`).
- **Every `unsafe` block needs a `// SAFETY:` comment** explaining why it is sound
  (`clippy::undocumented_unsafe_blocks`).

If a lint fires and fixing it would make the code worse, silence it at the smallest possible
scope with `#[allow(clippy::lint_name, reason = "...")]` on the item — never crate-wide.

## Dependency policy

`deny.toml` (enforced by `cargo deny` in CI) rejects dependencies with known security advisories,
non-permissive licenses, or non-crates.io sources. If you add a dependency and CI flags its
license, either extend the allow list in `deny.toml` (if it is genuinely permissive) or choose a
different crate. Keep the dependency tree small — every crate is compile time and audit surface.

## Commits and PRs

- Keep `Cargo.lock` committed and up to date (CI builds with `--locked`).
- One logical change per PR; include tests for behavior changes.
- Write user-facing text (CLI output, docs) per [STYLE.md](STYLE.md).

## Testing

See [TESTING.md](TESTING.md) for the test suite layout, acceptance checks, and overhead
benchmarks.
