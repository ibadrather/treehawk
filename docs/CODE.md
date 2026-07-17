# The code-quality setup, explained

What each quality/shipping file adapted from [uv](https://github.com/astral-sh/uv) does, why it
exists, and how it's enforced — written for someone new to Rust tooling.

## The philosophy

Every rule lives in a checked-in config file, and CI enforces all of them with warnings treated
as errors. Nothing depends on you (or an agent) remembering anything: formatting is mechanical,
lints block merge, dependencies are policed, and the same commands run identically on your
machine, in hooks, and in CI. Quality as configuration, not discipline.

A useful mental model of the layers, from cheapest to most serious:

| Layer | Tool | Catches |
|---|---|---|
| Formatting | rustfmt | style — automatically rewritten, never argued about |
| Linting | clippy | code that compiles but is wrong, fragile, or unidiomatic |
| Compiling | rustc | type errors, ownership/borrow errors |
| Testing | cargo test | behavior regressions |
| Dependency policy | cargo-deny | vulnerable, badly-licensed, or untrusted dependencies |
| Spelling | typos | typos in code, comments, docs |

## File by file

### `rustfmt.toml` — formatting

`cargo fmt` parses your code and reprints it in the one canonical style; the config only pins
`edition`/`style_edition = "2024"` so the output never shifts under you. There is deliberately
nothing else to configure — the whole value is that *nobody customizes formatting*. **Enforced:**
CI runs `cargo fmt --check` (fails if anything would change); the pre-commit hook and the agent
post-edit hook format for you so you should never see that failure.

### `Cargo.toml` `[lints]` — the lint policy (the core of it)

Clippy is Rust's linter: hundreds of individual lints, organized in groups. Two levels matter
here: `allow` (off) and `warn` (report). CI passes `-D warnings`, which promotes every warning
to a hard error — so in this repo **"warn" effectively means "blocks merge"**, while staying
non-fatal during local editing.

What's turned on, with examples:

**`clippy::pedantic`** — the strict opt-in group. Typical catches:

```rust
// doc_markdown: bare identifier in docs
/// Reads cpu.stat from the cgroup.        // warned: wrap as `cpu.stat`

// needless_pass_by_value: takes ownership it doesn't need
fn parse(input: String) {}                  // warned: take &str

// redundant_closure_for_method_calls
iter.map(|s| s.trim())                      // warned: iter.map(str::trim)
```

Pedantic is on because it *teaches* — each warning links to an explanation of why the pattern is
bad. Lints that are more noise than signal for this codebase are individually allowed back in
`Cargo.toml`, each with a comment saying why. That list is uv's, plus four `cast_*` lints
(`cast_precision_loss` etc.) because metrics code converts kernel counters (`u64`) to rates
(`f64`) constantly and flagging every cast would drown real findings.

**`unwrap_used`** — bans `.unwrap()` in production code:

```rust
let file = File::open(path).unwrap();               // banned: panics on error
let file = File::open(path)
    .with_context(|| format!("could not open {}", path.display()))?;  // do this
```

`.unwrap()` crashes the whole process the moment the value is `Err`/`None`. Treehawk is meant to
run unattended for days; it must degrade (log, null sample, error return), not die. Tests may
unwrap freely — a panic in a test *is* a failure report — via `allow-unwrap-in-tests` in
`clippy.toml`. When a value truly cannot fail, use `.expect("why this cannot fail")` so the
impossible-case reasoning is written down.

**`undocumented_unsafe_blocks`** — every `unsafe` block needs a `// SAFETY:` comment:

```rust
// SAFETY: sysconf(_SC_CLK_TCK) reads a constant; no pointers involved,
// and a -1 return is handled below.
let ticks = unsafe { libc::sysconf(libc::_SC_CLK_TCK) };
```

`unsafe` means "the compiler cannot check this — I promise it's sound"; the comment forces the
promise to be argued, and gives reviewers something to check. This replaces uv's blanket
`unsafe_code = "warn"` ban, which treehawk can't use because raw `libc` calls (`fork`/`waitpid`
handling in `spawn.rs`) are part of its job.

**`unreachable_pub`** — flags `pub` items that nothing outside the crate can actually reach;
keeps the intentional API surface distinguishable from accidental `pub`.

**Restriction lints** (`dbg_macro`, `get_unwrap`, `use_self`, `rc_mutex`, …) — individually
chosen bans on debug leftovers and known-bad patterns.

**Deviations from uv, all commented in `Cargo.toml`:** uv bans `print_stdout`/`print_stderr`/
`exit` because all uv output flows through an internal printer; treehawk is a plain CLI whose
`report` command prints to stdout, so those stay off.

### `clippy.toml` — lint behavior knobs

Not levels — configuration: `allow-unwrap-in-tests = true`, and `doc-valid-idents` (words like
`NVML`, `RAPL` that the `doc_markdown` lint should accept without backticks). Add to that list
when clippy complains about a legitimate proper noun in docs.

### `deny.toml` — dependency policy (`cargo deny`)

Every crate you add is code you now ship and an obligation you now carry. `cargo deny check`
walks the whole dependency tree (treehawk's is ~100 crates once arrow/parquet expand) and
enforces:

- **Advisories** — cross-checks every crate version against the RustSec database of known
  vulnerabilities. A dependency with a published CVE fails CI until you upgrade it.
- **Licenses** — every crate's license must be on the allow-list (MIT, Apache-2.0, BSD, ISC,
  Zlib, …). These are *permissive* licenses: they let you use the code with attribution.
  *Copyleft* licenses (GPL family) impose conditions on your own code and are deliberately not
  listed. If CI flags a new dependency, either its license is permissive-but-missing (add it,
  with the comment pattern in the file) or you should pick a different crate.
- **Sources** — crates may only come from crates.io; a dependency pointing at a random git URL
  fails. Supply-chain hygiene: registry crates are immutable and auditable.
- **Duplicate versions** are warned (not failed): two versions of one crate bloat the binary,
  but the arrow ecosystem makes strict deduping impractical.

One-time local install: `cargo install cargo-deny`.

### `_typos.toml` + typos — spell check

`typos` is a fast source-aware spell checker (it understands `camelCase`, `snake_case`,
identifiers). The config whitelists false positives — it already carries `WRONLY` (the POSIX
`O_WRONLY` flag), which its first run on this repo flagged. When it's wrong about a word, add
the word under `[default.extend-identifiers]` or `[default.extend-words]` rather than ignoring
the failure. **Enforced:** CI job + pre-commit hook; run locally with `uvx typos`.

### `.pre-commit-config.yaml` — commit-time checks

[pre-commit](https://pre-commit.com) installs a git hook that runs configured checks on the
files in each commit — here: typos and rustfmt. One-time setup: `uvx pre-commit install`.
**Why:** feedback in seconds at commit time instead of minutes later in CI. It's optional
(CI is the real gate), and `git commit --no-verify` bypasses it in an emergency.

### `.editorconfig` — editor defaults

A cross-editor standard: UTF-8, LF line endings, trailing-whitespace trim, final newline,
4-space Rust indent, 100-char markdown wrap. Most editors honor it automatically. Prevents
whitespace churn when files are touched from different machines/editors.

### `rust-toolchain.toml` — pinned compiler

Pins the exact Rust version (and requires the rustfmt/clippy components). Any `cargo` command in
the repo — yours, CI's, an agent's — automatically uses that version via rustup. **Why:** new
Rust releases add lints and change formatting details; without a pin, CI and your machine drift.
Upgrading Rust becomes a deliberate one-line PR.

### `.github/workflows/ci.yml` — the enforcement point

Five parallel jobs on every push to `main` and every PR:

| Job | Command | Failure means |
|---|---|---|
| spell check | `typos` | typo, or a new false positive to whitelist in `_typos.toml` |
| rustfmt | `cargo fmt --check` | someone committed unformatted code — run `cargo fmt` |
| clippy | `cargo clippy --all-targets --locked -- -D warnings` | a lint fired — fix it or `#[expect]` it with a reason |
| tests | `cargo test --locked` | a test broke |
| cargo deny | `cargo deny check` | vulnerable dep, unknown license, or non-crates.io source |

Patterns worth understanding (all uv's):

- **`--locked`** — build must use exactly the versions in `Cargo.lock`, failing if the lockfile
  is out of date, instead of silently resolving new versions. Reproducibility: CI tests the same
  dependency tree you ran. (This is why `Cargo.lock` is committed, and why `AGENTS.md` mandates
  `cargo update --precise` for targeted updates.)
- **`permissions: contents: read`** — the workflow's GitHub token is read-only; a compromised
  dependency of a CI action can't push code with it.
- **`concurrency` + `cancel-in-progress`** — pushing a fix-up cancels the now-obsolete run
  instead of queueing behind it.
- **Split jobs** rather than one big script — parallel wall-clock time, and the failing check is
  visible from the PR page without opening logs.
- **`Swatinem/rust-cache`** — caches compiled dependencies between runs; without it every CI run
  recompiles the world (~minutes).

### `CONTRIBUTING.md` / `STYLE.md` — the human layer

CONTRIBUTING is the operating manual: setup, the five commands, the lint/dependency policies in
prose. STYLE governs user-facing *text* — terminology ("cgroup", "session", "sample"), error
message shape (`treehawk: error: lowercase clause`, remedy included), stdout-vs-stderr
discipline (data to stdout so it pipes; status to stderr) — things no linter can check but that
make a CLI feel coherent.

## The daily loop

```bash
# while developing
cargo check                                # fast: types/borrows only, no codegen
cargo test some_test_name                  # run the test you're working against

# before pushing (what CI will do to you)
cargo fmt
cargo clippy --all-targets -- -D warnings
cargo test
uvx typos
cargo deny check                           # only needed when dependencies changed
```

When clippy flags something and you disagree: first read the lint's explanation (the warning
prints a link). If the code is genuinely better as-is, silence it at the smallest possible
scope:

```rust
#[expect(clippy::too_many_lines, reason = "one flat match over all cgroup v2 files is clearest")]
fn read_all_stats(...) {}
```

`#[expect]` beats `#[allow]` because it *errors when the lint stops firing* — stale exceptions
remove themselves. Never silence crate-wide.

## Topics worth reading (in this order)

1. **Clippy lint groups** (`correctness`, `style`, `pedantic`, `restriction`) and levels —
   browse the [lint list](https://rust-lang.github.io/rust-clippy/) to see what exists.
2. **Error handling**: `Result`, the `?` operator, and `anyhow` (application errors, what
   treehawk uses) vs `thiserror` (typed library errors) — the `.unwrap()` ban pushes you
   straight into this; it's the most important Rust idiom to internalize.
3. **`unsafe` Rust** — what it actually permits and what a soundness argument is (first chapters
   of the Rustonomicon).
4. **Cargo dependency resolution** — `Cargo.toml` vs `Cargo.lock`, semver ranges, `--locked`.
5. **cargo-deny** docs — one page covering advisories/licenses/bans/sources.
6. **Software licenses in five minutes** — permissive (MIT/Apache/BSD) vs copyleft (GPL), and
   why dual `MIT OR Apache-2.0` is the Rust convention.
7. **GitHub Actions fundamentals** — workflow/job/step, `permissions:`, caching — enough to
   read `ci.yml` comfortably.
8. **Rust API Guidelines** — the community checklist for what good public Rust API design looks
   like; relevant once treehawk grows a library/Python surface.
