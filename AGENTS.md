<!-- Agent rules adapted from uv's AGENTS.md (https://github.com/astral-sh/uv),
     MIT OR Apache-2.0, adjusted to treehawk's policies (single-crate Linux CLI;
     unsafe allowed for libc with SAFETY comments). -->

- Read CONTRIBUTING.md for guidelines on how to run tools
- ALWAYS attempt to add a test case for changed behavior
- PREFER integration tests, e.g., at `tests/...`, over unit tests; unit tests live in
  `#[cfg(test)]` modules next to the code
- ALWAYS read and copy the style of similar tests when adding new cases
- Cgroup-dependent behavior only works on Linux with cgroup v2; tests that need it must skip
  gracefully elsewhere — copy the pattern used in `tests/run_mode.rs`
- PREFER running specific tests over running the entire test suite
- NEVER perform builds with the release profile, unless asked or reproducing performance issues
- AVOID using `panic!`, `unreachable!`, `.unwrap()` (outside tests), and clippy rule ignores
- PREFER patterns like `if let` to handle fallibility
- `unsafe` is permitted only for libc calls that `rustix` cannot express; ALWAYS write `// SAFETY:`
  comments explaining soundness (clippy enforces this)
- PREFER `#[expect(..., reason = "...")]` over `#[allow()]` if clippy must be disabled
- PREFER let chains (`if let` combined with `&&`) over nested `if let` statements
- NEVER update all dependencies in the lockfile and ALWAYS use `cargo update --precise` to make
  lockfile changes
- NEVER assume clippy warnings are pre-existing; `main` is kept warning-free and CI runs
  `-D warnings`
- PREFER top-level imports over local imports or fully qualified names
- AVOID shortening variable names, e.g., use `interval` instead of `iv`, and `sample_rate`
  instead of `sr`
- PREFER [`TypeName`] references when writing Rust doc comments
- Follow STYLE.md for user-facing text: CLI output, error messages, and documentation
- The observer must never disturb the measurement: sampling-path changes must not add
  allocations, syscalls, or blocking work per sample without discussion
