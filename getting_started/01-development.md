# Development

## Setup (once)

Install Rust via [rustup](https://rustup.rs). That's it — `rust-toolchain.toml` pins the
toolchain version, and rustup installs it automatically the first time you run any `cargo`
command in this repo. You need a C toolchain for linking (`sudo apt-get install
build-essential`) and, for one of the five checks, a one-time `cargo install cargo-deny`.

Treehawk targets Linux with cgroup v2; develop and test on a Linux machine.

## The daily loop

Edit, then build and test as usual:

```bash
cargo build            # debug build (never --release unless asked)
cargo test             # or a specific test: cargo test --test run_mode
```

Before pushing, run the full check suite — CI runs exactly these five commands, so a clean run
locally means a green CI:

```bash
cargo fmt                                      # format (CI runs --check)
cargo clippy --all-targets -- -D warnings      # lint, warnings are errors
cargo test                                     # unit + integration tests
uvx typos                                      # spell check
cargo deny check                               # advisories, licenses, sources
```

## Which config file controls what

| File | Controls | Read more |
|---|---|---|
| `rust-toolchain.toml` | pinned Rust version | — |
| `rustfmt.toml` | formatting (edition pin only) | `docs/CODE.md` |
| `Cargo.toml` `[lints]` + `clippy.toml` | the lint policy (pedantic on, no `.unwrap()`) | `docs/CODE.md` |
| `deny.toml` | allowed licenses, advisory policy | `docs/CODE.md` |
| `_typos.toml` | spell-check exceptions | — |
| `.editorconfig` | indentation/whitespace for editors | — |

Coding conventions (test style, error handling, `// SAFETY:` comments) live in `AGENTS.md`; the
test suite layout and acceptance checks live in `TESTING.md`.
