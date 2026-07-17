# Learning Rust with Treehawk

A guided tour of Rust concepts using **this codebase** as the textbook, sequenced
from beginner to advanced. Written for an experienced Python programmer: every
section maps the Rust idea to the Python idea you already know, then points at
real code in this repo (clickable `file:line` references).

**How to use this doc:** read a level, then open the referenced files and trace
the real code. Everything here exists in the M1 implementation — nothing is
hypothetical. Run `cargo test` (on the Linux box) as you go; the tests are also
teaching material.

> Covers the codebase as of **M1** (`run` + `report`, commit era 2026-07).
> When new milestones land, regenerate with the update prompt at the bottom.

---

## Level 0 — Orientation: how a Rust project is shaped

### 0.1 Cargo = pip + venv + setuptools + make, in one

`Cargo.toml` is `pyproject.toml`'s equivalent, but Cargo also builds, tests,
and locks dependencies (`Cargo.lock` ≈ a lockfile from `uv`/`poetry`).

- Dependencies: `Cargo.toml:13-22` — note version specs like `"1"` mean
  "any 1.x", semver-compatible by default (like `^1` in poetry).
- Feature flags: `arrow = { version = "59", default-features = false, features = ["ipc"] }`
  — Rust crates ship optional compile-time features; turning off defaults keeps
  the binary small. Python has no real equivalent (closest: `pip install pkg[extra]`).
- Lint policy lives in `Cargo.toml:34-79` — like a `ruff` config, but enforced
  by the compiler toolchain itself (`cargo clippy`).

### 0.2 Binary + library in one crate

Python: you'd have a package plus a `__main__.py` or console-script entry point.
Rust convention is the same idea:

- `src/main.rs` — the executable entry point (≈ `__main__.py`). It's tiny on
  purpose: parse args, dispatch, map errors to exit codes (`src/main.rs:8-25`).
- `src/lib.rs` — the library root that declares the module tree
  (`src/lib.rs:6-15`). Integration tests and future PyO3 bindings import
  through this, just like `from treehawk import ...`.

### 0.3 Modules are declared, not discovered

Python finds modules by scanning the filesystem. Rust requires an explicit
declaration: `pub mod sampler;` in `src/lib.rs:12` is what makes
`src/sampler.rs` part of the crate. A directory module has a `mod.rs`
(see `src/proc/mod.rs`, which itself declares `pub mod host;` for
`src/proc/host.rs`). `pub` = exported; without it, items are private to the
module (Python's `_underscore` convention, but compiler-enforced).

`use` is `import`: `use crate::model::SampleRow;` ≈
`from treehawk.model import SampleRow`. `crate::` means "from this crate's
root"; `super::` means "from the parent module" (see `src/proc/host.rs:6`).

---

## Level 1 — Core language: the stuff Python half-has

### 1.1 Structs = dataclasses, but fields are typed for real

`SampleRow` (`src/model.rs:23-43`) is exactly what you'd write as a frozen
dataclass:

```python
@dataclass
class SampleRow:
    t_mono_ns: int
    pid: int
    dt_ns: int | None   # nanoseconds since previous sample
    ...
```

```rust
pub struct SampleRow {
    pub t_mono_ns: u64,
    pub pid: i32,
    pub dt_ns: Option<u64>,   // None on first sample
    ...
}
```

Key differences:
- Rust integers are sized and signed/unsigned explicitly: `u64`, `i32`, `u32`,
  `f32`, `f64`. Python's `int` is arbitrary-precision; Rust's are machine words,
  so overflow is something you think about (see `saturating_sub` in §1.5).
- `#[derive(Debug, Clone)]` (`src/model.rs:22`) auto-implements printing and
  copying — like dataclass auto-generating `__repr__` and `copy`.

### 1.2 `Option<T>` replaces `None`, and the compiler makes you check

Python: any variable can be `None` and you find out at runtime.
Rust: a `u64` can never be null. If a value may be absent, its type is
`Option<u64>` and you **must** handle both cases before using it.

Real usage — `dt_ns` is `None` on a process's first sample because there's no
previous tick to diff against (`src/sampler.rs:159-167`):

```rust
let (dt_ns, du, ds) = match handle.prev {
    Some((pu, ps, pt)) => (Some(...), Some(...), Some(...)),
    None => (None, None, None),
};
```

This is why the Parquet schema has nullable columns (`src/model.rs:92`,
`nullable: true`) exactly where the struct has `Option` — the types document
the data contract.

### 1.3 Enums are tagged unions, not just constants

Python's `Enum` is a set of named constants. Rust's `enum` is a **sum type** —
each variant can carry different data (like a union of dataclasses that the
compiler forces you to `match` exhaustively):

- `Command` (`src/cli.rs:21-36`): `Run(RunArgs)` carries a payload, `Watch`
  carries nothing. The closest Python is a `Union[Run, Report, ...]` with
  isinstance checks — except Rust checks you handled every variant.
- `TargetExit` (`src/spawn.rs:74-79`): `Code(i32)` vs `Signal(i32)` vs
  `Abandoned`. The `exit_code()` method (`src/spawn.rs:84-90`) `match`es all
  three; add a fourth variant and the compiler errors on every unhandled match
  in the codebase. This is the refactoring safety net Python's mypy only
  approximates.
- `PssState` (`src/proc/mod.rs:111-115`) is a beautiful small example: a
  tri-state (`Unprobed` / `Open(fd)` / `Unavailable`) so the expensive probe
  happens exactly once. In Python you'd juggle `None` vs a sentinel vs a value.

### 1.4 `match`, `if let`, `let else` — pattern matching as control flow

Python 3.10 `match` is close, but Rust leans on it much harder:

- Full match: `src/main.rs:10-17` dispatches on the subcommand.
- `if let` — "match one variant, ignore the rest":
  `src/sampler.rs:235-237` (`if let Tracker::Cgroup(cgroup) = tracker { ... }`).
- `let ... else` — "destructure or bail out of this iteration":
  `src/sampler.rs:121` — `let Ok(handle) = PidHandle::open(...) else { continue; };`
  ≈ Python's "try, except: continue" but without exceptions.
- **Let chains** (Rust 2024 edition, and this repo's preferred style per
  AGENTS.md): `src/sampler.rs:229-233` combines `if let Some(code) = ... &&
  let Some(row) = ...` — two fallible extractions in one condition, no nesting.

### 1.5 Integer arithmetic is deliberate

`t.saturating_sub(pt)` (`src/sampler.rs:161`) instead of `t - pt`: if a clock
ever ran backwards, plain subtraction on a `u64` would underflow (panic in
debug builds, wrap in release). `saturating_sub` clamps at 0. You'll see this
throughout the metrics code (`src/proc/host.rs:119-120`) — kernel counters can
reset, and the observer must never crash because of it (NFR-4).

Casts are explicit: `n as u32`, `value as u64`. Python coerces silently;
Rust makes every narrowing visible (the repo allows the pedantic cast lints in
`Cargo.toml:60-65` because metrics math does this constantly, on purpose).

### 1.6 `String` vs `&str` — your first ownership encounter

- `String` = owned, growable, heap-allocated (like Python `str` the object).
- `&str` = a borrowed *view* into string data someone else owns (like a
  `memoryview`, but for text, and checked at compile time).

`ProcessRow.exe_name: String` (`src/model.rs:80`) **owns** its name — the row
outlives the parse. `parse_stat(content: &str)` (`src/proc/mod.rs:46`)
**borrows** — it only reads. Function inputs are `&str`, stored fields are
`String`. That's the 90% rule.

---

## Level 2 — Errors, iterators, closures: Rust's daily bread

### 2.1 `Result<T, E>` replaces exceptions

Python raises; Rust returns. Every fallible function says so in its type:

```rust
pub fn members(&mut self, scratch: &mut Vec<u8>) -> io::Result<Vec<i32>>
```

(`src/cgroup.rs:72`) — `io::Result<T>` is shorthand for `Result<T, io::Error>`.
The caller cannot forget errors exist; ignoring a `Result` is a compiler
warning.

The `?` operator is the ergonomic core: `read_fd_to_string(&self.procs, scratch)?`
(`src/cgroup.rs:73`) means "if Err, return it to my caller now; if Ok, unwrap".
It's `try/except: raise` collapsed into one character, but visible at every
call site — you can read a function and see exactly which lines can fail.

### 2.2 Two error styles: libraries type them, applications wrap them

- **Typed errors** in reusable code: the low-level modules return `io::Result`
  so callers can inspect `err.kind()` — see the exit-code mapping when spawn
  fails (`src/cmd/run.rs:98-102`): `NotFound` → 127, `PermissionDenied` → 126,
  matching shell conventions.
- **`anyhow` for the application layer**: `src/cmd/run.rs` returns
  `anyhow::Result` and adds human context at each step:
  `.context("writing session.json")` (`src/cmd/run.rs:76-77`),
  `.with_context(|| format!("creating session dir {}", ...))`
  (`src/cmd/run.rs:42-43`). Like exception chaining (`raise X from e`), and
  `{err:#}` in `src/main.rs:21` prints the whole cause chain.

Deliberate error *swallowing* is also explicit: `let _ = tx.send(...)`
(`src/sampler.rs:234`) or `.ok()` (`src/proc/mod.rs:167-168`) — "I know this
can fail and I don't care" is written down, not silent.

### 2.3 Iterator chains — comprehensions, but lazy and allocation-free

Python comprehensions materialize lists; Rust iterator chains are lazy until a
consumer (`collect`, `sum`, `find_map`) runs them. Same expressiveness:

```rust
// src/cgroup.rs:74-77 — parse whitespace-separated PIDs, skipping junk
content.split_ascii_whitespace()
    .filter_map(|t| t.parse().ok())
    .collect()
```

```python
[int(t) for t in content.split() if t.isdigit()]  # roughly
```

More worth tracing:
- `find_map` = "first non-None result": `own_cgroup_path`
  (`src/cgroup.rs:96-100`) finds the `0::` line in `/proc/self/cgroup`.
- Closure-based parsing: `parse_pss_kb` (`src/proc/mod.rs:100-108`) is an
  entire parser as one iterator chain.
- Building a report: `src/cmd/report.rs:105-114` — read a directory, filter by
  filename shape, collect paths. Compare with `pathlib.glob` + list comp.
- `|x| ...` is `lambda x: ...`, but closures can also *capture by move* (see
  §4.1) and be passed without the performance tax Python lambdas carry.

### 2.4 `Option` combinators — the `None`-propagation toolkit

Instead of `if x is not None:` pyramids, chain adapters:

```rust
// src/sampler.rs:126-132 — basename of exe path, falling back to comm
let exe_name = id.exe_path.as_deref()
    .and_then(|p| p.rsplit('/').next())
    .unwrap_or(&id.comm)
    .to_string();
```

Python equivalent: `(exe_path.rsplit("/")[-1] if exe_path else comm)` — but the
Rust version can't accidentally hit an `AttributeError` on `None`.

Also idiomatic here: `.map()`, `.transpose()` (turns `Option<Result<T>>` into
`Result<Option<T>>`, `src/cmd/run.rs:81-84`), and boolean-to-Option:
`(!args.label.is_empty()).then(|| args.label.join(","))` (`src/cmd/run.rs:117`).

### 2.5 Traits — protocols/ABCs, resolved at compile time

A trait is a `typing.Protocol` the compiler enforces. Three ways this repo uses
them:

1. **Derived**: `#[derive(Debug, Clone, Serialize, Deserialize)]` — trait
   implementations generated for you (`src/manifest.rs:13`).
2. **Implemented by hand**: `impl TrackingMode { fn as_str(...) }`
   (`src/cgroup.rs:22-29`) — methods live in `impl` blocks, separate from the
   data definition (unlike Python's class body).
3. **As generic bounds**: `fn read_fd_to_string(fd: impl AsFd, ...)`
   (`src/proc/mod.rs:17`) — "any type that can act as a file descriptor".
   Like duck typing, but checked before the program runs, and *monomorphized*:
   the compiler stamps out a specialized copy per concrete type, so there's no
   dynamic-dispatch cost.

`clap`'s derive API (`src/cli.rs:8-55`) and `serde`'s (`src/manifest.rs:13-32`)
show the ecosystem pattern: describe your data as structs + attributes, derive
the behavior (argument parsing, JSON) — like `pydantic`/`click`, but at compile
time with zero reflection.

---

## Level 3 — Ownership, borrowing, lifetimes: the Rust-only part

Python has one memory model: everything is a heap object with refcounting +
GC, shared freely. Rust has three modes, and the compiler tracks which one
every value is in:

1. **Owned** (`T`) — this variable is responsible for the value; when it goes
   out of scope, the value is freed. Passing it *moves* it (the old variable is
   dead — using it is a compile error).
2. **Shared borrow** (`&T`) — read-only view, many allowed at once.
3. **Exclusive borrow** (`&mut T`) — read-write view, exactly one at a time,
   and no shared borrows can coexist with it.

Rule of thumb: `&` = "lend it", `&mut` = "lend it for modification", bare `T`
= "give it away".

### 3.1 The scratch buffer — `&mut` as a performance tool

The sampling hot path must not allocate (AGENTS.md's "observer must never
disturb the measurement"). So one buffer is allocated once
(`src/sampler.rs:94`) and *lent* to every read:

```rust
let mut scratch: Vec<u8> = Vec::with_capacity(4096);
...
tracker.members(&mut scratch)      // borrow it
handle.sample(want_pss, &mut scratch)  // borrow it again
```

Each callee fills and reads it, then the borrow ends. In Python you'd pass a
`bytearray` around and *hope* nobody keeps a reference; Rust's borrow checker
guarantees nobody does.

### 3.2 A returned reference carries a lifetime

`read_fd_to_string` (`src/proc/mod.rs:17-31`) returns `io::Result<&str>` —
a string slice **borrowing from `scratch`**. The signature's elided lifetime
says: "the returned `&str` is valid only as long as the `scratch` borrow
lives." Try to keep that `&str` and then reuse `scratch` — compile error.
This is the feature: the compiler proves the parse result can't dangle over
the buffer it points into. (In C this exact pattern is a use-after-free
factory; in Python it would force a copy.)

Note how callers immediately copy out what they keep:
`stat.comm.clone()` into `Identity` (`src/proc/mod.rs:174-180`) — borrow to
parse, own to store.

### 3.3 Moves into threads and closures

`std::thread::Builder::spawn(move || sampler_thread(...))`
(`src/sampler.rs:80-83`): the `move` keyword transfers ownership of `opts`,
`tracker`, `tx` *into* the closure, which then runs on another thread. After
this line, the spawning code can't touch them — which is exactly what makes
the data race impossible. Python threads share everything and rely on the GIL
plus discipline; Rust threads share nothing unless you opt in (§4.2).

Same pattern pre-exec: `command.pre_exec(move || ...)` captures the raw fd *by
value* (`src/spawn.rs:50-66`) so the closure that runs in the forked child
doesn't reference parent-owned data.

### 3.4 The `Entry` API — HashMap without double lookup

Python: `if pid not in handles: handles[pid] = make(); h = handles[pid]` —
three hash lookups. Rust's entry API does it in one, and doubles as control
flow (`src/sampler.rs:118-150`):

```rust
if let Entry::Vacant(vacant) = handles.entry(pid) {
    // first sight of this PID: open fds, record identity...
    vacant.insert(handle);
}
```

Also note `handles.retain(|pid, _| member_set.contains(pid))`
(`src/sampler.rs:196`) — in-place filtered removal, the dict-comprehension
rebuild you'd write in Python, without the rebuild.

### 3.5 RAII / `Drop` — deterministic cleanup, no `with` needed

Python needs `with open(...)` because GC finalization is nondeterministic.
Rust frees resources at scope exit, *always*:

- `OwnedFd` (`src/proc/mod.rs:120-122`) closes its file descriptor when the
  `PidHandle` is dropped. No `close()` calls anywhere — look for them, there
  are none, and yet nothing leaks (that's the 7-day-soak NFR).
- Explicit early drop: `drop(cgroup_procs)` (`src/spawn.rs:68`) closes the
  parent's copy of the cgroup fd immediately after spawn.
- Consuming self: `pub fn cleanup(self)` (`src/cgroup.rs:81-90`) takes
  ownership — after `cgroup.cleanup()`, the tracker is *gone*, compiler-
  enforced. You cannot use-after-cleanup. A method taking `self` (not `&self`)
  is Rust's way of saying "this is the last thing you'll ever do with this
  object".

---

## Level 4 — Concurrency and `unsafe`: systems Rust

Treehawk's `run` mode is three threads: **main** (spawns target, waits,
forwards signals), **sampler** (ticks on a monotonic clock), **writer**
(batches to disk). Wiring in `src/cmd/run.rs:79-134`. No GIL, real
parallelism, and the compiler checks the sharing.

### 4.1 Channels — `queue.Queue`, but ownership-aware

The sampler *sends* each tick's rows to the writer over an mpsc channel
(multi-producer, single-consumer):

- Create + hand ends to threads: `spawn_writer` (`src/writer.rs:63-70`).
- Send: `tx.send(WriterMsg::Tick { samples, host, new_processes })`
  (`src/sampler.rs:201-208`) — the rows are *moved* into the message; the
  sampler physically cannot mutate them after sending. Python's `Queue` shares
  references; a producer can mutate an object the consumer is reading. Not here.
- Receive with timeout as the writer's heartbeat:
  `rx.recv_timeout(timeout)` (`src/writer.rs:200-244`) — `Timeout` means
  "flush window elapsed, write to disk", `Disconnected` means "sampler died,
  preserve what we have". The channel's closure *is* the shutdown protocol:
  no sentinel objects, no `None` poison pills.
- Messages as an enum: `WriterMsg::Tick` vs `WriterMsg::Finalize`
  (`src/writer.rs:33-43`) — the protocol between threads is a type.

### 4.2 Shared state: `Arc`, `Mutex`, atomics

When threads genuinely must share, you say so in the type:

- `Arc<StopSignal>` (`src/cmd/run.rs:112`) — atomically refcounted shared
  pointer (like Python object sharing, but explicit and thread-safe). Cloned
  once per thread: `Arc::clone(&stop)`.
- `AtomicBool` with memory orderings (`src/sampler.rs:39-55`): the stop flag
  is written with `Ordering::Release` and read with `Ordering::Acquire` — a
  publish/subscribe pair guaranteeing the exit code written before the flag is
  visible after it. Python simply has no user-facing concept here; the GIL
  hides it.
- `Mutex<Option<i32>>` for the exit code (`src/sampler.rs:41-49`):
  `self.target_exit_code.lock()` returns a guard; the lock releases when the
  guard drops (RAII again — no `finally: lock.release()`). Note the honest
  handling: `if let Ok(mut guard) = ...` — a poisoned mutex (a thread panicked
  while holding it) is a `Result`, not a surprise.
- Signal handlers can only touch `AtomicU32` (`src/spawn.rs:11-20`) because
  only async-signal-safe operations are legal in a handler — the type system
  documents the constraint.

### 4.3 Thread lifecycle — `JoinHandle` returns a value

`spawn` returns `JoinHandle<SamplerStats>` (`src/sampler.rs:73-84`):
the thread's return value comes back through `.join()`
(`src/cmd/run.rs:128-134`). A panic in the thread arrives as `Err` at the join
— compare Python's `Thread`, which swallows exceptions unless you build your
own plumbing. The writer even returns `Result<()>` through its handle, so disk
errors surface at shutdown with full context.

### 4.4 `unsafe` — a marked, audited escape hatch

Safe Rust can't express raw syscalls like `fork`/`waitpid`/`sigaction`. The
`unsafe` block says "the compiler can't check this; I've checked it myself" —
and this repo requires every one to carry a `// SAFETY:` comment (clippy
enforces it, `Cargo.toml:42`):

- Signal handler install: `src/spawn.rs:26-37` — SAFETY: handler only touches
  atomics.
- `pre_exec` hook: `src/spawn.rs:52-66` — runs between `fork` and `exec`,
  where only async-signal-safe calls are legal; the hook joins a process group
  and writes the child into the cgroup by fd. This closes a real race
  (grandchild spawned before the parent could move the child into the cgroup).
- `waitpid` loop: `src/spawn.rs:110-140` — EINTR from a signal wakes the loop
  to forward SIGINT/SIGTERM to the whole process group.
- Plain sysconf queries: `src/manifest.rs:136-146`.

Count them: five-ish `unsafe` blocks in the whole codebase, all thin wrappers
over libc, all commented. That's the discipline — `unsafe` doesn't turn the
checks off globally, it fences the unchecked part. (Also see AGENTS.md: prefer
`rustix` — safe syscall wrappers — and drop to `libc` only when rustix can't
express it, e.g. `sleep_until`'s absolute-deadline `clock_nanosleep` is rustix,
`src/sampler.rs:248-266`, while process-group forwarding is libc.)

### 4.5 A real-time loop without drift

`sampler_thread`'s ticker (`src/sampler.rs:104-223`) is worth reading end to
end as systems-programming practice, independent of Rust:

- Absolute deadlines (`next += interval_ns`), not `sleep(interval)` — no
  accumulated drift.
- Overrun policy (`src/sampler.rs:216-222`): if a tick took too long, *skip*
  the missed ticks and count them, never queue them up.
- Sleep in ≤ 50 ms slices so a stop request is honored promptly
  (`src/sampler.rs:248-266`).

Python's `time.sleep`-based loops get all three of these wrong by default.

---

## Level 5 — Ecosystem craft: Arrow, serde, testing, lints

### 5.1 Columnar data with Arrow (your pandas bridge)

`src/model.rs` is the schema layer: for each table it defines the row struct,
the Arrow `Schema` (`samples_schema`, `src/model.rs:87-105`), and a
rows→`RecordBatch` converter using typed builders
(`samples_batch`, `src/model.rs:141-192`). The verbosity is the price of
column-typed, null-tracked, zero-copy buffers — the same memory layout pandas
and polars read natively, which is why the output needs no custom reader
(`pd.read_parquet` just works).

The reverse direction — reading columns — is in the report:
`col("t_mono_ns")?.as_primitive::<UInt64Type>()` then `.value(row)` /
`.is_null(row)` (`src/cmd/report.rs:71-99`). That `::<...>` syntax is the
"turbofish": explicitly picking the generic type when inference can't.

### 5.2 The crash-safe writer — WAL + rotation as a pattern

`src/writer.rs` implements: append batches to an Arrow IPC *stream* file
(readable up to the last complete batch even if the process is SIGKILLed),
fsync every flush window, convert to compressed Parquet at rotation boundaries
(`Table::rotate`, `src/writer.rs:125-137`). The test
`wal_survives_truncation_and_converts_to_parquet` (`src/writer.rs:304-337`)
literally truncates the file mid-batch to simulate a kill — read it to see how
you *test* crash-safety rather than assert it.

Also here: `serde` for the manifest (`src/manifest.rs:13-32` — derive
`Serialize`/`Deserialize`, like pydantic's model_dump/model_validate) and
atomic file writes via tmp + rename (`src/manifest.rs:102-107`).

### 5.3 Testing: three layers

1. **Unit tests live next to the code** in `#[cfg(test)] mod tests`
   (compiled only for tests — zero cost in the shipped binary). Pattern to
   copy: fixture-based parser tests (`src/proc/mod.rs:229-277`) with hostile
   inputs — a process named `fire (fox)` breaks naive `/proc/stat` parsing,
   so the fixture includes it.
2. **Live self-inspection tests**: `live_self_inspection`
   (`src/proc/mod.rs:280-291`) samples the test process itself — no mocks,
   real `/proc`.
3. **Integration tests** in `tests/` exercise the built binary end to end
   (`tests/run_mode.rs`) — this repo prefers these (AGENTS.md), and they show
   the cgroup-v2 skip-gracefully pattern for non-Linux machines.

`.expect("message")` is allowed in tests (the message is the assertion
context); in library code the repo bans `.unwrap()` and prefers `if let` /
`?` — see AGENTS.md.

### 5.4 Clippy as a curriculum

Run `cargo clippy` and read what it says — it's a Rust tutor. This repo runs
pedantic-level lints with a curated allow-list (`Cargo.toml:40-79`), and CI
treats warnings as errors. Notable choices to understand:

- `unwrap_used = "warn"` — forces you into `Option`/`Result` combinators.
- `undocumented_unsafe_blocks = "warn"` — the `// SAFETY:` rule from §4.4.
- The cast lints are allowed *with a written reason* — lint policy is itself
  documented decision-making.

### 5.5 Reading order for the whole codebase

If you want one path through everything, in dependency order:

1. `src/util.rs` — pure functions, easiest Rust in the repo
2. `src/cli.rs` — clap derive + a hand-written parser + tests
3. `src/proc/mod.rs` — parsing, fd caching, lifetimes, the `PssState` enum
4. `src/proc/host.rs` — same patterns again (repetition = retention)
5. `src/cgroup.rs` — RAII, fallback strategy, filesystem as API
6. `src/model.rs` — Arrow schemas and builders
7. `src/writer.rs` — channel consumer, WAL, rotation
8. `src/sampler.rs` — the hot loop: borrowing, entry API, atomics, ticker
9. `src/spawn.rs` — unsafe, fork/exec, signals
10. `src/cmd/run.rs` — where every thread and resource is wired together
11. `src/cmd/report.rs` — Arrow reading, formatting, sorting with closures

### 5.6 Exercises (in increasing order of ambition)

Do these on a branch; each one touches a concept from above and has a natural
test to write (AGENTS.md: always add a test, prefer integration tests):

1. **Level 1–2:** Add a `treehawk report --json` flag that prints the summary
   as JSON instead of the table (clap arg + serde on the stats structs).
2. **Level 2:** Extend `parse_interval` (`src/cli.rs:64-86`) to accept `"max"`
   as a sentinel (the README promises it) — you'll need to change the parsed
   type from `Duration` to an enum. Watch the compiler walk you through every
   affected site.
3. **Level 3:** Add a `--sort rss|cpu` option to `report` — closures as sort
   keys over the stats map (`src/cmd/report.rs:250-261` is the current sort).
4. **Level 4:** Add a periodic stderr status line to `run` (e.g. every 5 s:
   ticks, live processes) — you'll have to decide which thread owns it and how
   it learns the numbers (channel? atomic counters?). There's no single right
   answer; that's the point.
5. **Level 5:** Add a new nullable column end to end: struct field → schema →
   builder → writer → report. The M2 GPU columns (`src/model.rs:39-42`) show
   the reserved-column pattern you'd follow.

---

## Appendix: Python ↔ Rust phrasebook (as used in this repo)

| Python | Rust | Example here |
|---|---|---|
| `dataclass` | `struct` + `#[derive]` | `src/model.rs:23` |
| `Enum` / `Union` + isinstance | `enum` + `match` | `src/spawn.rs:74` |
| `None` checks | `Option<T>` + combinators | `src/sampler.rs:126` |
| `try/except/raise` | `Result<T, E>` + `?` | `src/cgroup.rs:72` |
| `raise X from e` | `anyhow` `.context()` | `src/cmd/run.rs:76` |
| list/dict comprehension | iterator chain + `collect` | `src/cgroup.rs:74` |
| `lambda` | closure `\|x\| ...` | `src/cmd/report.rs:71` |
| `typing.Protocol` / ABC | trait | `src/proc/mod.rs:17` |
| `with` context manager | RAII / `Drop` | `src/cgroup.rs:81` |
| `queue.Queue` | `std::sync::mpsc` channel | `src/writer.rs:64` |
| `threading.Thread` | `std::thread` + `JoinHandle<T>` | `src/sampler.rs:73` |
| shared object + GIL | `Arc<...>` + atomics/`Mutex` | `src/sampler.rs:38` |
| `pydantic` / `json` | `serde` derive | `src/manifest.rs:13` |
| `click` / `argparse` | `clap` derive | `src/cli.rs:8` |
| `ruff` | `clippy` (config in Cargo.toml) | `Cargo.toml:40` |
| `pytest` files | `#[cfg(test)]` + `tests/` | `src/proc/mod.rs:225` |

---

