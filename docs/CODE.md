# The code-quality setup, explained

What each quality/shipping file adapted from [uv](https://github.com/astral-sh/uv) does, why it
exists, and how it's enforced. Brief by intent — reading topics at the end.

## The philosophy (one paragraph)

Every rule lives in a checked-in config file, and CI enforces all of them with warnings treated
as errors. Nothing depends on you (or an agent) remembering anything: formatting is mechanical,
lints block merge, dependencies are policed, and the same commands run identically on your
machine, in hooks, and in CI. That's the entire uv trick — quality as configuration, not
discipline.

## File by file

### `rustfmt.toml` — formatting

`cargo fmt` rewrites all code into the one canonical style; the config just pins the 2024 style
edition so output is stable. **Why:** zero style debates, zero diff noise. **Enforced:** CI runs
`cargo fmt --check`; the pre-commit hook and the agent post-edit hook format for you.

### `Cargo.toml` `[lints]` — the lint policy

The core of it. Clippy is Rust's linter; lints are grouped, and this config turns on:

- **`clippy::pedantic`** — the strict optional group: lossy casts, sloppy matches, needless
  clones, undocumented panics. On because it teaches; individual too-noisy lints are allowed
  back with a written reason (uv's exact list, plus the four `cast_*` lints since metrics math
  converts counters to floats constantly).
- **`unwrap_used`** — bans `.unwrap()` in production code. `.unwrap()` crashes the program on
  `None`/`Err`; for a tool meant to run for days that's unacceptable. Tests may unwrap
  (`clippy.toml: allow-unwrap-in-tests`).
- **`undocumented_unsafe_blocks`** — every `unsafe` block needs a `// SAFETY:` comment proving
  it's sound. Our replacement for uv's blanket `unsafe_code` ban, which treehawk can't use
  because it needs raw `libc` calls.
- **`unreachable_pub`** — flags `pub` items nothing external can reach; keeps the API surface
  honest.
- A handful of restriction lints (`dbg_macro`, `get_unwrap`, `use_self`, …) catching debug
  leftovers and unidiomatic patterns.

**Enforced:** CI runs `cargo clippy --all-targets -- -D warnings` — `-D warnings` promotes every
warning to a hard error, so "warn" in the config really means "blocks merge".

### `clippy.toml` — lint knobs

Not levels, behavior: unwraps allowed in tests, and `doc-valid-idents` (words like NVML that
`doc_markdown` shouldn't demand backticks around).

### `deny.toml` — dependency policy (`cargo deny`)

Every dependency is attack surface and a license obligation. This rejects: crates with known
security advisories (RustSec database), licenses outside the permissive allow-list, and
non-crates.io sources. **Enforced:** the `cargo deny` CI job. If it flags a new dep's license,
either add the license to the list (if permissive) or pick another crate.

### `_typos.toml` + typos — spell check

Catches typos in code, comments, and docs; the config whitelists false positives (e.g. `WRONLY`,
the POSIX flag). **Enforced:** CI job and pre-commit hook. Cheap, and real: its first run on this
repo found one.

### `.pre-commit-config.yaml` — commit-time checks

Runs typos + rustfmt on every `git commit` after a one-time `uvx pre-commit install`. **Why:**
catch problems in seconds locally instead of minutes later in CI. Optional — CI is the real gate.

### `.editorconfig` — editor defaults

Tells any editor: UTF-8, LF line endings, final newline, 4-space Rust indent, 100-char markdown
lines. Prevents whitespace churn between machines/editors.

### `.github/workflows/ci.yml` — the enforcement point

Five parallel jobs on every push/PR: **typos**, **fmt**, **clippy**, **test**, **cargo deny**.
Split jobs = parallel runs and instantly-legible failures. uv patterns worth knowing: `--locked`
(builds must match `Cargo.lock` exactly — reproducible builds), `permissions: contents: read`
(least-privilege token), `concurrency` (superseded runs get cancelled), pinned toolchain via
`rust-toolchain.toml` (everyone compiles with the same rustc).

### `CONTRIBUTING.md` / `STYLE.md`

The human-readable layer: CONTRIBUTING is the "how do I run all this" manual; STYLE governs
user-facing *text* (CLI messages, docs) — terminology, error-message shape, stdout vs stderr —
which no linter can check.

## The commands, in practice

```bash
cargo fmt                                  # fix formatting
cargo clippy --all-targets -- -D warnings  # what CI runs; fix or #[expect] with a reason
cargo test                                 # unit + integration tests
uvx typos                                  # spell check
cargo deny check                           # dependency policy (cargo install cargo-deny once)
```

When clippy flags something you disagree with: fix it if reasonable, otherwise silence it at the
smallest scope with `#[expect(clippy::lint_name, reason = "...")]` — never crate-wide, and
`#[expect]` (not `#[allow]`) because it errors if the lint stops firing, so dead exceptions
clean themselves up.

## Topics worth reading (in this order)

1. **Clippy lint groups** (`correctness`, `style`, `pedantic`, `restriction`) — what exists and
   how levels work. The [Clippy lint list](https://rust-lang.github.io/rust-clippy/) is
   browsable.
2. **Error handling in Rust**: `Result`, `?`, and why `anyhow` (application errors) vs
   `thiserror` (library errors) — this is what the `.unwrap()` ban pushes you toward.
3. **`unsafe` Rust and SAFETY comments** — what `unsafe` actually permits and what a soundness
   argument is (the Rustonomicon, first chapters).
4. **`Cargo.lock` and `--locked`** — how Rust dependency resolution and reproducible builds work.
5. **cargo-deny** docs — advisories/licenses/bans/sources, one page.
6. **GitHub Actions fundamentals** — workflow/job/step, `permissions:`, caching; enough to read
   `ci.yml` comfortably.
7. **Rust API Guidelines** (the checklist) — the wider "what good Rust looks like" document that
   things like `unreachable_pub` gesture at.
