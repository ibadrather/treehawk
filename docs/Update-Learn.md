## Updating this document

When the codebase grows (new milestones, new modules), regenerate or extend
this file with the following prompt:

> Update `docs/LEARN.md`, the beginner-to-advanced Rust learning guide for this
> repository, to match the current state of the code. The reader is an advanced
> Python programmer learning Rust through this codebase.
>
> 1. Read every file under `src/` and `tests/`, plus `Cargo.toml`, `AGENTS.md`,
>    and the README roadmap, and compare against what LEARN.md currently covers
>    (it states the milestone/commit era it was written for near the top).
> 2. Fix anything stale first: `file:line` references that moved, renamed or
>    deleted symbols, milestone status, and exercises that have since been
>    implemented (replace completed exercises with new ones of similar scope).
> 3. For **new** code, add coverage where it teaches a Rust concept not yet in
>    the doc (e.g. when M2+ lands: FFI bindings to NVML, trait objects /
>    `dyn Trait` for GPU backend abstraction, `Box`, feature-gated
>    compilation, async if introduced, eBPF integration). Slot each new
>    concept into the existing level structure (Level 0–5) by difficulty
>    rather than appending chronologically; create a new level only if a
>    genuinely more advanced tier appears.
> 4. Keep the doc's contract: every concept must be anchored to real code with
>    a `file:line` reference, include a Python analogy where one exists, stay
>    concrete (no hypothetical code presented as if it were in the repo), and
>    keep the phrasebook table and reading order current.
> 5. Update the "covers the codebase as of" line, verify all `file:line`
>    references against the actual files before finishing, and keep this
>    update prompt at the bottom of the file.
