# Learning Rust with Treehawk

A guided tour of Rust concepts using **this codebase** as the textbook, sequenced
from beginner to advanced. Written for an experienced Python programmer: every
section shows the real Rust code from this repo, maps it to the Python you
already know, then points at the file so you can read the surrounding context
(clickable `file:line` references).

**How to use this doc:** read a level, study the inline code, then open the
referenced files and trace the full implementation. Everything here is copied
from the M1 implementation — nothing is hypothetical. Run `cargo test` (on the
Linux box) as you go; the tests are also teaching material.

> Covers the codebase as of **M1** (`run` + `report`, commit era 2026-07).
> Line references verified 2026-07-18. When new milestones land, regenerate
> with the update prompt in `docs/Update-Learn.md`.

---

## Level 0 — Orientation: how a Rust project is shaped

### 0.1 Cargo = pip + venv + setuptools + make, in one

`Cargo.toml` is `pyproject.toml`'s equivalent, but Cargo also builds, tests,
and locks dependencies (`Cargo.lock` ≈ a lockfile from `uv`/`poetry`).

The dependency block (`Cargo.toml:13-22`):

```toml
[dependencies]
anyhow = "1"
arrow = { version = "59", default-features = false, features = ["ipc"] }
clap = { version = "4.6", features = ["derive", "wrap_help"] }
libc = "0.2"
parquet = { version = "59", default-features = false, features = ["arrow", "zstd"] }
rustix = { version = "1.1", features = ["fs", "process", "system", "thread", "time"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
thiserror = "2"
```

- `"1"` means "any 1.x", semver-compatible by default (like `^1` in poetry).
- `features = [...]` are **compile-time** optional pieces of a crate; turning
  off `default-features` keeps the binary small. Python's closest idea is
  `pip install pkg[extra]`, but that only adds *runtime* deps — Rust features
  actually compile different code.
- The lint policy lives in `Cargo.toml:34-83` — like a `ruff` config, but
  enforced by the compiler toolchain itself (`cargo clippy`, see §5.4).

### 0.2 Binary + library in one crate

Python: a package plus a `__main__.py` or console-script entry point. Rust
convention is the same idea. The entire executable entry point is 25 lines
(`src/main.rs:1-25`):

```rust
use std::process::ExitCode;

use clap::Parser;

use treehawk::cli::{Cli, Command};
use treehawk::cmd;

fn main() -> ExitCode {
    let cli = Cli::parse();
    let result = match cli.command {
        Command::Run(args) => return cmd::run::run(&args),
        Command::Report(args) => cmd::report::report(&args),
        Command::Watch | Command::Export | Command::Ls | Command::Config | Command::Service => {
            eprintln!("treehawk: this subcommand is not yet implemented (planned milestone)");
            return ExitCode::from(2);
        }
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            eprintln!("treehawk: error: {err:#}");
            ExitCode::FAILURE
        }
    }
}
```

It's tiny on purpose: parse args, dispatch, map errors to exit codes. All the
logic lives in the library root, `src/lib.rs:6-15`, which integration tests
(and, later, PyO3 bindings) import just like `from treehawk import ...`.

### 0.3 Modules are declared, not discovered

Python finds modules by scanning the filesystem. Rust requires an explicit
declaration — `src/lib.rs` *is* the module tree:

```rust
pub mod cgroup;
pub mod cli;
pub mod cmd;
pub mod manifest;
pub mod model;
pub mod proc;
pub mod sampler;
pub mod spawn;
pub mod util;
pub mod writer;
```

`pub mod sampler;` (`src/lib.rs:12`) is what makes `src/sampler.rs` part of
the crate. A directory module has a `mod.rs`: `src/proc/mod.rs:7` declares
`pub mod host;`, which pulls in `src/proc/host.rs`. `pub` = exported; without
it, items are private to the module (Python's `_underscore` convention, but
compiler-enforced).

`use` is `import`:

```rust
use crate::model::{ProcessRow, SampleRow};   // src/sampler.rs:15
use super::read_fd_to_string;                // src/proc/host.rs:6
```

≈ `from treehawk.model import ProcessRow, SampleRow` and a relative
`from . import read_fd_to_string`. `crate::` means "from this crate's root",
`super::` means "from the parent module".

---

## Level 1 — Core language: the stuff Python half-has

### 1.1 Structs = dataclasses, but fields are typed for real

`SampleRow` (`src/model.rs:22-43`) is the central data type — one process, one
tick. Side by side:

```rust
#[derive(Debug, Clone)]
pub struct SampleRow {
    pub t_mono_ns: u64,
    pub proc_id: u32,
    pub pid: i32,
    /// Nanoseconds since this process's previous sample; None on its first sample.
    pub dt_ns: Option<u64>,
    pub cpu_utime_ticks: Option<u64>,
    pub cpu_stime_ticks: Option<u64>,
    pub num_threads: u32,
    pub vm_rss_kb: u64,
    // ... more memory fields, then:
    /// Reserved for M2 (always None in M1).
    pub gpu_util_pct: Option<f32>,
    pub gpu_mem_bytes: Option<u64>,
}
```

```python
@dataclass
class SampleRow:
    t_mono_ns: int
    proc_id: int
    pid: int
    dt_ns: int | None      # None on first sample
    cpu_utime_ticks: int | None
    ...
    gpu_util_pct: float | None   # reserved for M2
```

Key differences:

- Rust integers are sized and signed/unsigned explicitly: `u64`, `i32`, `u32`,
  `f32`, `f64`. Python's `int` is arbitrary-precision; Rust's are machine
  words, so overflow is something you think about (see `saturating_sub`, §1.5).
- `#[derive(Debug, Clone)]` auto-implements printing and deep-copying — like a
  dataclass auto-generating `__repr__` and `copy`. You'll see richer derives
  later: `#[derive(Debug, Serialize, Deserialize)]` (`src/manifest.rs:13`).
- `///` doc comments attach to the item below them and become rendered docs
  (`cargo doc`) — like docstrings, but usable on individual fields.

### 1.2 `Option<T>` replaces `None`, and the compiler makes you check

Python: any variable can be `None` and you find out at runtime. Rust: a `u64`
can never be null. If a value may be absent, its type is `Option<u64>`, and you
**must** handle both cases before using the value.

Real usage — `dt_ns` is `None` on a process's first sample because there is no
previous tick to diff against (`src/sampler.rs:164-172`):

```rust
let (dt_ns, du, ds) = match handle.prev {
    Some((pu, ps, pt)) => (
        Some(t.saturating_sub(pt)),
        Some(s.stat.utime_ticks.saturating_sub(pu)),
        Some(s.stat.stime_ticks.saturating_sub(ps)),
    ),
    None => (None, None, None),
};
handle.prev = Some((s.stat.utime_ticks, s.stat.stime_ticks, t));
```

```python
if handle.prev is not None:
    pu, ps, pt = handle.prev
    dt_ns = t - pt
    du = stat.utime_ticks - pu
    ds = stat.stime_ticks - ps
else:
    dt_ns = du = ds = None
handle.prev = (stat.utime_ticks, stat.stime_ticks, t)
```

Things to notice:

- `handle.prev` has type `Option<(u64, u64, u64)>` — an optional *tuple*,
  declared right on the struct (`src/proc/mod.rs:125`). The `match` both tests
  for presence *and* unpacks the tuple in one step.
- `match` returns a value here (a 3-tuple assigned with one `let`). In Rust,
  `match`, `if`, and blocks are all *expressions* — Python's
  `x = a if cond else b`, generalized to everything.
- This `Option` flows all the way to disk: the Parquet schema marks `dt_ns`
  nullable (`src/model.rs:92`, `Field::new("dt_ns", DataType::UInt64, true)`)
  exactly where the struct has `Option`. The types document the data contract.

### 1.3 Enums are tagged unions, not just constants

Python's `Enum` is a set of named constants. Rust's `enum` is a **sum type** —
each variant can carry different data, and the compiler forces you to handle
every variant. Three real examples, small to large:

**A pure constant-style enum** — tracking mode (`src/cgroup.rs:17-29`):

```rust
pub enum TrackingMode {
    Cgroup,
    PidTree,
}

impl TrackingMode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Cgroup => "cgroup",
            Self::PidTree => "pid-tree",
        }
    }
}
```

**Variants carrying data** — how the target ended (`src/spawn.rs:73-90`):

```rust
pub enum TargetExit {
    Code(i32),
    Signal(i32),
    /// User pressed Ctrl-C twice: stop waiting, finalize, get out.
    Abandoned,
}

impl TargetExit {
    pub fn exit_code(self) -> u8 {
        match self {
            Self::Code(c) => c as u8,
            Self::Signal(s) => (128 + s) as u8,
            Self::Abandoned => 130,
        }
    }
}
```

The closest Python is a `Union` of dataclasses with `isinstance` checks —
except Rust *checks you handled every variant*. Add a fourth variant to
`TargetExit` and the compiler errors on every `match` in the codebase that
doesn't cover it. This is the refactoring safety net mypy only approximates.

**An enum as a state machine** — whether PSS is readable for a process
(`src/proc/mod.rs:110-115`), tri-state so the expensive probe happens exactly
once:

```rust
/// Whether PSS is readable for this process; tri-state so we probe exactly once.
enum PssState {
    Unprobed,
    Open(OwnedFd),
    Unavailable,
}
```

In Python you'd juggle `None` vs a sentinel vs a file object and document the
convention in a comment. Here the state — including the open file descriptor
*inside* the `Open` variant — is one type. The transition logic
(`src/proc/mod.rs:234-247`) reads like the state diagram:

```rust
fn read_pss(&mut self, scratch: &mut Vec<u8>) -> Option<u64> {
    if let PssState::Unprobed = self.smaps {
        let path = format!("/proc/{}/smaps_rollup", self.pid);
        self.smaps =
            match rustix::fs::open(path, OFlags::RDONLY | OFlags::CLOEXEC, Mode::empty()) {
                Ok(fd) => PssState::Open(fd),
                Err(_) => PssState::Unavailable,
            };
    }
    match &self.smaps {
        PssState::Open(fd) => parse_pss_kb(read_fd_to_string(fd, scratch).ok()?),
        _ => None,
    }
}
```

Also of this kind: `Command` (`src/cli.rs:20-36`) where `Run(RunArgs)` carries
a payload and `Watch` carries nothing, and the sampler's `Tracker`
(`src/sampler.rs:22-25`) which wraps two interchangeable tracking strategies —
see §2.5 for why an enum (not a base class) is the idiomatic choice here.

### 1.4 `match`, `if let`, `let else` — pattern matching as control flow

Python 3.10's `match` is close, but Rust leans on it much harder. The four
forms, all from real code:

**Full `match`** — dispatch on the subcommand (`src/main.rs:10-17`, shown in
§0.2). Note `|` for "or" patterns: `Command::Watch | Command::Export | ...`.

**`if let`** — "match one variant, ignore the rest" (`src/sampler.rs:238-240`):

```rust
if let Tracker::Cgroup(cgroup) = tracker {
    cgroup.cleanup();
}
```

```python
if isinstance(tracker, CgroupTracker):
    tracker.cleanup()
```

**`let ... else`** — "destructure or bail out": in the hot loop, a PID whose
`/proc` files can't be opened is skipped, not an error
(`src/sampler.rs:121-123`):

```rust
let Ok(handle) = PidHandle::open(pid, &mut scratch) else {
    continue;
};
```

```python
try:
    handle = PidHandle.open(pid, scratch)
except OSError:
    continue
```

Same shape without exceptions. The `else` branch *must* diverge (`continue`,
`return`, `break`) — the compiler checks that too. Another one, skipping
non-numeric `/proc` entries (`src/cgroup.rs:120-122`):

```rust
let Ok(pid) = entry.file_name().to_string_lossy().parse::<i32>() else {
    continue;
};
```

**Let chains** (Rust 2024 edition, this repo's preferred style per AGENTS.md) —
two fallible extractions in one condition, no nesting
(`src/sampler.rs:232-236`):

```rust
if let Some(code) = target_exit
    && let Some(row) = processes.iter_mut().find(|r| r.pid == target_pid)
{
    row.exit_code = Some(code);
}
```

```python
if code is not None and (row := next((r for r in processes if r.pid == target_pid), None)):
    row.exit_code = code
```

Another let chain guards reading a leftover WAL tail
(`src/cmd/report.rs:125-130`):

```rust
if wal.exists()
    && let Ok(reader) = StreamReader::try_new(File::open(&wal)?, None)
{
    // A truncated tail after SIGKILL simply ends the iteration early.
    batches.extend(reader.flatten());
}
```

### 1.5 Integer arithmetic is deliberate

`t.saturating_sub(pt)` (`src/sampler.rs:166`) instead of `t - pt`: if a clock
ever ran backwards, plain subtraction on a `u64` would underflow (panic in
debug builds, silently wrap in release). `saturating_sub` clamps at 0. The
host reader does the same for kernel counters, which can reset
(`src/proc/host.rs:116-123`):

```rust
let (dt_ns, busy, total) = match self.prev {
    Some((p, pt)) => (
        Some(t_mono_ns.saturating_sub(pt)),
        Some(totals.busy_ticks.saturating_sub(p.busy_ticks)),
        Some(totals.total_ticks.saturating_sub(p.total_ticks)),
    ),
    None => (None, None, None),
};
```

The observer must never crash because a counter jumped (NFR-4) — and the code
says so at every subtraction, instead of hoping.

Casts are explicit: `c as u8` (`src/spawn.rs:86`), `handles.len() as u32`
(`src/sampler.rs:215`), `interval_ns as f64` (`src/cmd/run.rs:149`). Python
coerces silently; Rust makes every narrowing visible. The repo allows the
pedantic cast lints with a written reason (`Cargo.toml:65-70`) because metrics
math converts kernel counters to rates constantly, on purpose.

### 1.6 `String` vs `&str` — your first ownership encounter

- `String` = owned, growable, heap-allocated (like Python's `str` object).
- `&str` = a borrowed *view* into string data someone else owns (like a
  `memoryview`, but for text, and checked at compile time).

The rule of thumb — function inputs are `&str`, stored fields are `String` —
is visible in one line each:

```rust
pub fn parse_stat(content: &str) -> Option<Stat> { ... }  // src/proc/mod.rs:46 — borrows
pub exe_name: String,                                     // src/model.rs:77   — owns
```

`parse_stat` only reads, so it borrows. `ProcessRow.exe_name` must outlive the
parse, so it owns. Where a borrowed value needs to be kept, the code copies it
out explicitly: `comm: stat.comm.clone()` when building the long-lived
`Identity` from a scratch-buffer parse (`src/proc/mod.rs:185-191`). Borrow to
parse, own to store.

### 1.7 Everything is an expression

No ternary operator needed — `if` and `match` produce values. Choosing the
tracking mode (`src/cmd/run.rs:56-60`):

```rust
let mode = if cgroup.is_some() {
    TrackingMode::Cgroup
} else {
    TrackingMode::PidTree
};
```

```python
mode = TrackingMode.CGROUP if cgroup is not None else TrackingMode.PID_TREE
```

Notice there are no `return`s and no semicolon after the branches' final
expressions: the last expression of a block is its value. The same rule makes
whole function bodies expressions — `stopping()` (`src/sampler.rs:52-54`) is
just `self.stop.load(Ordering::Acquire)` with no `return` keyword.

---

## Level 2 — Errors, iterators, closures: Rust's daily bread

### 2.1 `Result<T, E>` replaces exceptions

Python raises; Rust returns. Every fallible function says so in its type.
Reading the current cgroup members (`src/cgroup.rs:71-78`):

```rust
/// Current member PIDs, straight from the kernel (no inference).
pub fn members(&mut self, scratch: &mut Vec<u8>) -> io::Result<Vec<i32>> {
    let content = read_fd_to_string(&self.procs, scratch)?;
    Ok(content
        .split_ascii_whitespace()
        .filter_map(|t| t.parse().ok())
        .collect())
}
```

`io::Result<T>` is shorthand for `Result<T, io::Error>`. The caller cannot
forget errors exist; ignoring a `Result` is a compiler warning.

The `?` on line 3 is the ergonomic core: "if `Err`, return it to my caller
now; if `Ok`, unwrap." It's `try/except: raise` collapsed into one character —
but visible at every call site, so you can read a function and see exactly
which lines can fail:

```python
def members(self, scratch: bytearray) -> list[int]:
    content = read_fd_to_string(self.procs, scratch)   # may raise OSError
    return [int(t) for t in content.split() if t.isdigit()]
```

### 2.2 Two error styles: libraries type them, applications wrap them

**Typed errors in reusable code.** The low-level modules return `io::Result`
so callers can inspect `err.kind()`. When spawning the target fails, run mode
maps the kind to shell-convention exit codes (`src/cmd/run.rs:97-103`):

```rust
eprintln!("treehawk: failed to run {:?}: {err}", args.command[0]);
let code = match err.kind() {
    std::io::ErrorKind::NotFound => 127,
    std::io::ErrorKind::PermissionDenied => 126,
    _ => 125,
};
return Ok(ExitCode::from(code));
```

**`anyhow` for the application layer.** `src/cmd/run.rs` returns
`anyhow::Result` and adds human context at each step (`src/cmd/run.rs:42-43`
and `75-77`):

```rust
std::fs::create_dir_all(&session_dir)
    .with_context(|| format!("creating session dir {}", session_dir.display()))?;
...
manifest
    .write(&session_dir)
    .context("writing session.json")?;
```

Like exception chaining (`raise RuntimeError("writing session.json") from e`),
and `{err:#}` in `src/main.rs:21` prints the whole cause chain:
`treehawk: error: writing session.json: Permission denied (os error 13)`.

Deliberate error *swallowing* is also explicit — "I know this can fail and I
don't care" is written down, not silent:

```rust
let _ = tx.send(WriterMsg::Finalize { processes });   // src/sampler.rs:237
std::fs::remove_file(processes_wal.wal_path()).ok();  // src/writer.rs:271
```

`let _ =` binds the `Result` to the throwaway pattern; `.ok()` converts
`Result` to `Option` and drops it. Either way, a reviewer can see the decision.

### 2.3 Iterator chains — comprehensions, but lazy and allocation-free

Python comprehensions materialize lists; Rust iterator chains are lazy until a
consumer (`collect`, `sum`, `find_map`) runs them. Same expressiveness.

**Filter-map-collect** — parse whitespace-separated PIDs, skipping junk
(`src/cgroup.rs:74-77`, seen above):

```rust
content.split_ascii_whitespace()
    .filter_map(|t| t.parse().ok())
    .collect()
```

```python
[int(t) for t in content.split() if t.isdigit()]  # roughly
```

**`find_map` = "first non-None result"** — find the `0::` line in
`/proc/self/cgroup` (`src/cgroup.rs:94-101`):

```rust
fn own_cgroup_path() -> io::Result<String> {
    let content = std::fs::read_to_string("/proc/self/cgroup")?;
    content
        .lines()
        .find_map(|l| l.strip_prefix("0::"))
        .map(|p| p.trim().to_string())
        .ok_or_else(|| io::Error::other("no cgroup v2 entry in /proc/self/cgroup"))
}
```

```python
def own_cgroup_path() -> str:
    content = Path("/proc/self/cgroup").read_text()
    for line in content.splitlines():
        if line.startswith("0::"):
            return line[3:].strip()
    raise OSError("no cgroup v2 entry in /proc/self/cgroup")
```

Note the last line: `ok_or_else` converts "not found" (`None`) into a proper
error — `Option` → `Result`, the reverse of `.ok()`.

**An entire parser as one chain** — extract `Pss:` from `smaps_rollup`
(`src/proc/mod.rs:99-108`):

```rust
pub fn parse_pss_kb(content: &str) -> Option<u64> {
    content.lines().find_map(|l| {
        l.strip_prefix("Pss:")?
            .split_ascii_whitespace()
            .next()?
            .parse()
            .ok()
    })
}
```

The `?` here works on `Option` too: "if `strip_prefix` returned `None`, this
closure returns `None`". Python's closest is a `try/except/StopIteration`
tangle or a regex.

**Filesystem as data** — find all `samples-*.parquet` chunks
(`src/cmd/report.rs:105-114`):

```rust
let mut chunk_paths: Vec<PathBuf> = std::fs::read_dir(dir)?
    .filter_map(std::result::Result::ok)
    .map(|e| e.path())
    .filter(|p| {
        p.file_name()
            .and_then(|n| n.to_str())
            .is_some_and(|n| n.starts_with(&format!("{prefix}-")) && n.ends_with(".parquet"))
    })
    .collect();
chunk_paths.sort();
```

```python
chunk_paths = sorted(
    p for p in dir.iterdir()
    if p.name.startswith(f"{prefix}-") and p.name.endswith(".parquet")
)
```

More verbose in Rust — because every step that can fail (unreadable entry,
non-UTF-8 filename) is visibly handled instead of implicitly raising.

### 2.4 Closures — lambdas that can do more

`|x| ...` is `lambda x: ...`, but closures can be multi-line, capture by
reference or by move, and are free (no call-object overhead). Three uses here:

**Tiny local helper capturing its environment** — `parse_status` avoids
repeating the split-and-parse dance for each field (`src/proc/mod.rs:79-95`):

```rust
for line in content.lines() {
    let Some((key, rest)) = line.split_once(':') else {
        continue;
    };
    let value = || rest.split_ascii_whitespace().next()?.parse::<u64>().ok();
    match key {
        "Uid" => out.uid = value().unwrap_or(0) as u32,
        "VmRSS" => out.vm_rss_kb = value().unwrap_or(0),
        "voluntary_ctxt_switches" => out.voluntary_ctxt_switches = value(),
        _ => {}
    }
}
```

`value` is a zero-argument closure capturing `rest` — like defining a nested
`def value(): ...` inside the loop, but idiomatic and inlined by the compiler.

**A closure as a fallible column accessor** — the report gives a nice error if
a column is missing (`src/cmd/report.rs:71-81`):

```rust
let col = |name: &str| {
    batch
        .column_by_name(name)
        .with_context(|| format!("samples table missing column {name}"))
};
let t = col("t_mono_ns")?.as_primitive::<UInt64Type>();
let proc_id = col("proc_id")?.as_primitive::<UInt32Type>();
```

**A closure as a sort key** — order processes by mean CPU, descending
(`src/cmd/report.rs:247-258`):

```rust
proc_ids.sort_by(|a, b| {
    let mean = |id: &u32| {
        let s = &stats[id];
        if s.cpu_pcts.is_empty() {
            0.0
        } else {
            s.cpu_pcts.iter().sum::<f64>() / s.cpu_pcts.len() as f64
        }
    };
    mean(b).total_cmp(&mean(a))
});
```

```python
proc_ids.sort(key=lambda pid: -mean(stats[pid].cpu_pcts))
```

(`total_cmp` exists because floats aren't totally ordered — `NaN` — and Rust
makes you pick a policy instead of throwing at runtime like `sorted()` doesn't.)

### 2.5 `Option` combinators — the `None`-propagation toolkit

Instead of `if x is not None:` pyramids, chain adapters. The display-name
logic — executable basename, falling back to comm (`src/proc/mod.rs:146-155`):

```rust
impl Identity {
    /// Display name: executable basename, falling back to comm.
    pub fn exe_name(&self) -> String {
        self.exe_path
            .as_deref()
            .and_then(|p| p.rsplit('/').next())
            .unwrap_or(&self.comm)
            .to_string()
    }
}
```

```python
def exe_name(self) -> str:
    if self.exe_path is not None:
        return self.exe_path.rsplit("/", 1)[-1]
    return self.comm
```

The Rust version can't accidentally hit an `AttributeError` on `None` — each
adapter's type says what happens in the absent case.

More idioms from this repo:

- **`.transpose()`** turns `Option<Result<T>>` into `Result<Option<T>>` — "if
  we have a cgroup, opening its procs fd may fail; propagate that failure"
  (`src/cmd/run.rs:81-85`):

  ```rust
  let procs_fd = cgroup
      .as_ref()
      .map(super::super::cgroup::CgroupTracker::procs_write_fd)
      .transpose()
      .context("opening cgroup.procs for the child")?;
  ```

- **Boolean → Option** with `bool::then` (`src/cmd/run.rs:117`):

  ```rust
  labels: (!args.label.is_empty()).then(|| args.label.join(",")),
  ```

  ≈ `labels = ",".join(args.label) if args.label else None`.

- **`map_or_else`** — both arms at once, "if absent compute this, if present
  compute that" (`src/cmd/report.rs:283-289`):

  ```rust
  let name = info.map_or_else(
      || format!("proc#{proc_id}"),
      |i| match &i.labels {
          Some(l) => format!("{} [{}]", i.exe_name, l),
          None => i.exe_name.clone(),
      },
  );
  ```

### 2.6 Traits — protocols/ABCs, resolved at compile time

A trait is a `typing.Protocol` the compiler enforces. Three ways this repo
uses them:

1. **Derived**: `#[derive(Debug, Serialize, Deserialize)]`
   (`src/manifest.rs:13`) — trait implementations generated for you.
2. **Implemented by hand**: methods live in `impl` blocks, separate from the
   data definition (unlike Python's class body) — `impl TrackingMode { ... }`
   (`src/cgroup.rs:22-29`, shown in §1.3).
3. **As generic bounds**: "any type that can act as a file descriptor"
   (`src/proc/mod.rs:17`):

   ```rust
   pub fn read_fd_to_string(fd: impl AsFd, scratch: &mut Vec<u8>) -> io::Result<&str>
   ```

   Callers pass a `&File`, an `&OwnedFd`, whatever — like duck typing, but
   checked before the program runs, and *monomorphized*: the compiler stamps
   out a specialized copy per concrete type, so there's no dynamic-dispatch
   cost.

Worth pausing on: why is `Tracker` (`src/sampler.rs:22-33`) an **enum** and
not a trait with two implementors?

```rust
pub enum Tracker {
    Cgroup(CgroupTracker),
    PidTree(PidTreeTracker),
}

impl Tracker {
    fn members(&mut self, scratch: &mut Vec<u8>) -> std::io::Result<Vec<i32>> {
        match self {
            Self::Cgroup(c) => c.members(scratch),
            Self::PidTree(p) => p.members(),
        }
    }
}
```

In Python you'd reflexively write an ABC with two subclasses. In Rust, when
the set of implementations is closed and known (there are exactly two tracking
strategies), an enum is preferred: no heap allocation, no virtual calls, and
`match` forces every use-site to consider both. Trait objects (`dyn Trait` ≈
duck-typed base-class references) earn their keep when implementations are
open-ended — expect them in M2 when GPU backends land.

The ecosystem pattern to recognize: `clap`'s derive API (`src/cli.rs:8-55`)
and `serde`'s (`src/manifest.rs:13-32`) both say *describe your data as
structs + attributes, derive the behavior* (argument parsing, JSON) — like
`pydantic`/`click`, but at compile time with zero reflection
(`src/cli.rs:38-55`, trimmed):

```rust
#[derive(Args)]
pub struct RunArgs {
    /// Sampling interval, e.g. 10ms, 100ms, 1s (range: 1ms..60s)
    #[arg(long, default_value = "100ms", value_parser = parse_interval)]
    pub interval: Duration,

    /// Label attached to every recorded process (repeatable)
    #[arg(long)]
    pub label: Vec<String>,

    /// The command to run and everything after it as its arguments
    #[arg(required = true, trailing_var_arg = true, allow_hyphen_values = true)]
    pub command: Vec<String>,
}
```

The doc comments become `--help` text. `value_parser = parse_interval` plugs
in the hand-written parser at `src/cli.rs:63-86` — a good first function to
read in full, with its tests right below it.

---

## Level 3 — Ownership, borrowing, lifetimes: the Rust-only part

Python has one memory model: everything is a heap object with refcounting +
GC, shared freely. Rust has three modes, and the compiler tracks which one
every value is in:

1. **Owned** (`T`) — this variable is responsible for the value; when it goes
   out of scope, the value is freed. Passing it *moves* it (the old variable
   is dead — using it afterward is a compile error).
2. **Shared borrow** (`&T`) — read-only view, many allowed at once.
3. **Exclusive borrow** (`&mut T`) — read-write view, exactly one at a time,
   and no shared borrows can coexist with it.

Rule of thumb: `&` = "lend it", `&mut` = "lend it for modification", bare `T`
= "give it away".

### 3.1 The scratch buffer — `&mut` as a performance tool

The sampling hot path must not allocate (AGENTS.md: "the observer must never
disturb the measurement"). So one buffer is allocated once and *lent* to every
read (`src/sampler.rs:94`, then throughout the tick):

```rust
let mut scratch: Vec<u8> = Vec::with_capacity(4096);
...
let members = tracker.members(&mut scratch).unwrap_or_default();   // lend
let Ok(handle) = PidHandle::open(pid, &mut scratch) else { ... };  // lend again
if let Ok(s) = handle.sample(want_pss, &mut scratch) { ... }       // and again
```

Each callee fills and reads it; when the call returns, the borrow ends and the
next caller can borrow it. In Python you'd pass a `bytearray` around and
*hope* nobody keeps a reference; Rust's borrow checker guarantees nobody does.

### 3.2 A returned reference carries a lifetime

The function every read goes through (`src/proc/mod.rs:14-31`):

```rust
/// Reads a whole (small) /proc-style file via `pread` into a reused buffer.
///
/// Returns a `&str` borrowing from `scratch`, valid until its next reuse.
pub fn read_fd_to_string(fd: impl AsFd, scratch: &mut Vec<u8>) -> io::Result<&str> {
    let mut len = 0usize;
    loop {
        if scratch.len() < len + 1024 {
            scratch.resize(len + 4096, 0);
        }
        let n = rustix::io::pread(&fd, &mut scratch[len..], len as u64)?;
        if n == 0 {
            break;
        }
        len += n;
    }
    std::str::from_utf8(&scratch[..len])
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "non-UTF-8 proc data"))
}
```

The returned `&str` **borrows from `scratch`** — no copy is made. The
signature's elided lifetime says: "the returned `&str` is valid only as long
as the `scratch` borrow lives." Try to keep that `&str` around and then reuse
`scratch` — compile error. That's the feature: the compiler proves the parse
result can't dangle over the buffer it points into. (In C this exact pattern
is a use-after-free factory; in Python it would force a copy.)

Callers therefore parse immediately and copy out only what they keep:

```rust
let stat = parse_stat(read_fd_to_string(&self.stat_fd, scratch)?)   // borrow...
    .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "unparsable stat"))?;
...
let identity = Identity {
    comm: stat.comm.clone(),   // ...own what outlives the buffer
    ...
};
```

(`src/proc/mod.rs:182-191`.) Borrow to parse, own to store.

One more clone idiom: `row.exe_path.clone_from(&handle.identity.exe_path)`
(`src/sampler.rs:159`) — like `row.exe_path = other.clone()` but reuses the
existing allocation when possible. Clippy suggests it; nice to recognize.

### 3.3 Moves into threads and closures

Spawning the sampler thread (`src/sampler.rs:80-83`):

```rust
std::thread::Builder::new()
    .name("treehawk-sampler".into())
    .spawn(move || sampler_thread(&opts, tracker, target_pid, &tx, &stop))
    .expect("spawning the sampler thread cannot fail")
```

The `move` keyword transfers ownership of `opts`, `tracker`, `tx`, and `stop`
*into* the closure, which then runs on another thread. After this line, the
spawning code can't touch them — which is exactly what makes a data race
impossible. Python threads share everything and rely on the GIL plus
discipline; Rust threads share nothing unless you opt in (§4.2).

The same pattern shows up pre-exec (`src/spawn.rs:50-66`): the closure that
runs in the forked child captures a raw fd *by value*, so it never references
parent-owned data:

```rust
let raw_procs_fd = cgroup_procs.as_ref().map(rustix::fd::AsRawFd::as_raw_fd);
unsafe {
    command.pre_exec(move || {
        if libc::setpgid(0, 0) != 0 {
            return Err(io::Error::last_os_error());
        }
        if let Some(fd) = raw_procs_fd {
            // "0" migrates the writing process itself (cgroup v2 semantics).
            if libc::write(fd, c"0".as_ptr().cast(), 1) != 1 {
                return Err(io::Error::last_os_error());
            }
        }
        Ok(())
    });
}
```

### 3.4 The `Entry` API — HashMap without double lookup

Python: `if pid not in handles: handles[pid] = make()` then `h = handles[pid]`
— three hash lookups. Rust's entry API does it in one, and doubles as control
flow for "first sight of this PID" (`src/sampler.rs:118-145`, trimmed):

```rust
if let std::collections::hash_map::Entry::Vacant(vacant) = handles.entry(pid) {
    // First sight of this PID: open fds and record identity. Any
    // failure means it died already or is off-limits — skip (NFR-4).
    let Ok(handle) = PidHandle::open(pid, &mut scratch) else {
        continue;
    };
    let proc_id = next_proc_id;
    next_proc_id += 1;
    let row = ProcessRow { proc_id, pid, /* identity fields... */ };
    registry.insert(proc_id, row.clone());
    new_processes.push(row);
    vacant.insert(handle);
    proc_ids.insert(pid, proc_id);
}
```

And when PIDs leave the tracked set, in-place filtered removal — the
dict-comprehension rebuild you'd write in Python, without the rebuild
(`src/sampler.rs:199-200`):

```rust
handles.retain(|pid, _| member_set.contains(pid));
proc_ids.retain(|pid, _| member_set.contains(pid));
```

```python
handles = {pid: h for pid, h in handles.items() if pid in member_set}  # rebuilds
```

### 3.5 RAII / `Drop` — deterministic cleanup, no `with` needed

Python needs `with open(...)` because GC finalization is nondeterministic.
Rust frees resources at scope exit, *always*. Three escalating forms:

**Implicit** — `PidHandle` holds three open file descriptors
(`src/proc/mod.rs:117-126`):

```rust
pub struct PidHandle {
    pub pid: i32,
    stat_fd: OwnedFd,
    status_fd: OwnedFd,
    smaps: PssState,          // may hold a fourth fd inside Open(OwnedFd)
    pub identity: Identity,
    pub prev: Option<(u64, u64, u64)>,
}
```

When the sampler does `handles.remove(&pid)` (`src/sampler.rs:195`), the
handle drops and every fd closes. There are no `close()` calls anywhere in the
codebase — look for them — and yet nothing leaks (that's the 7-day-soak NFR).

**Explicit early drop** — `drop(cgroup_procs)` closes the parent's copy of the
cgroup fd immediately after spawn (`src/spawn.rs:68`), and `Table::rotate`
drops the WAL writer to close the file before converting it
(`src/writer.rs:129-130`).

**Consuming `self`** — cleanup that makes the object unusable afterward
(`src/cgroup.rs:80-90`):

```rust
/// Removes the cgroup; harmless to fail while stragglers are still inside.
pub fn cleanup(self) {
    drop(self.procs);
    if let Err(e) = std::fs::remove_dir(&self.dir) {
        eprintln!(
            "treehawk: warning: could not remove cgroup {} ({e}); \
             processes may still be running in it",
            self.dir.display()
        );
    }
}
```

The method takes `self` (not `&self`): calling it *moves the tracker into the
method*, so after `cgroup.cleanup()` the variable is dead — use-after-cleanup
is a compile error, not a runtime bug. A method taking `self` is Rust's way of
saying "this is the last thing you'll ever do with this object."

---

## Level 4 — Concurrency and `unsafe`: systems Rust

Treehawk's `run` mode is three threads: **main** (spawns target, waits,
forwards signals), **sampler** (ticks on a monotonic clock), **writer**
(batches to disk). The wiring is all in `src/cmd/run.rs:79-134`. No GIL, real
parallelism, and the compiler checks the sharing.

### 4.1 Channels — `queue.Queue`, but ownership-aware

The protocol between sampler and writer is *a type*
(`src/writer.rs:32-43`):

```rust
/// Messages from the sampler thread.
pub enum WriterMsg {
    Tick {
        samples: Vec<SampleRow>,
        host: Option<HostRow>,
        new_processes: Vec<ProcessRow>,
    },
    /// Clean shutdown: the full, corrected process table.
    Finalize { processes: Vec<ProcessRow> },
}
```

Creating the channel and handing the ends to threads
(`src/writer.rs:63-70`):

```rust
pub fn spawn_writer(opts: WriterOptions) -> (Sender<WriterMsg>, JoinHandle<Result<()>>) {
    let (tx, rx) = channel();
    let handle = std::thread::Builder::new()
        .name("treehawk-writer".into())
        .spawn(move || writer_thread(&opts, &rx))
        .expect("spawning the writer thread cannot fail");
    (tx, handle)
}
```

Sending *moves* the rows into the message (`src/sampler.rs:204-211`):

```rust
let msg = WriterMsg::Tick {
    samples,
    host: host_row,
    new_processes,
};
if tx.send(msg).is_err() {
    break; // writer gone (it logs its own error); stop sampling
}
```

After `send`, the sampler physically cannot mutate those `Vec`s — they're
gone. Python's `Queue` shares references; a producer can mutate an object the
consumer is reading. Not here.

The receive side turns the channel into the writer's entire event loop —
timeout doubles as the flush heartbeat, disconnection doubles as the shutdown
signal (`src/writer.rs:200-245`, trimmed):

```rust
loop {
    let timeout = next_flush.saturating_duration_since(Instant::now());
    match rx.recv_timeout(timeout) {
        Ok(WriterMsg::Tick { samples, host, new_processes }) => {
            pending_samples.extend(samples);
            pending_host.extend(host);
            pending_procs.extend(new_processes);
        }
        Ok(WriterMsg::Finalize { processes }) => {
            final_processes = Some(processes);
            break;
        }
        Err(RecvTimeoutError::Timeout) => {
            flush(/* ...append pending batches, fsync... */)?;
            next_flush = Instant::now() + opts.flush_interval;
        }
        // Sampler died without Finalize: preserve whatever we have.
        Err(RecvTimeoutError::Disconnected) => break,
    }
}
```

No sentinel objects, no `None` poison pills, no `queue.get(timeout=...)` +
`Empty` exception juggling: the channel closing *is* the protocol, and `match`
handles all four cases in one place.

### 4.2 Shared state: `Arc`, `Mutex`, atomics

When threads genuinely must share, you say so in the type. The stop flag is
the complete example — small enough to read whole (`src/sampler.rs:36-55`):

```rust
/// Set by the main thread when the target has exited.
#[derive(Default)]
pub struct StopSignal {
    stop: AtomicBool,
    /// The target's exit code (128+signal form), for the process table.
    target_exit_code: Mutex<Option<i32>>,
}

impl StopSignal {
    pub fn request_stop(&self, target_exit_code: Option<i32>) {
        if let Ok(mut guard) = self.target_exit_code.lock() {
            *guard = target_exit_code;
        }
        self.stop.store(true, Ordering::Release);
    }

    fn stopping(&self) -> bool {
        self.stop.load(Ordering::Acquire)
    }
}
```

Shared between main and sampler via `Arc` (`src/cmd/run.rs:112` and `122`):

```rust
let stop = Arc::new(StopSignal::default());
...
spawn_sampler(..., Arc::clone(&stop));
```

Unpacking the pieces:

- **`Arc`** = atomically refcounted shared pointer — like Python object
  sharing, but explicit and thread-safe. `Arc::clone` bumps the refcount
  (cheap); the value frees when the last clone drops.
- **`AtomicBool` with orderings**: the exit code is written *before*
  `store(true, Release)`; any thread that observes `true` via
  `load(Acquire)` is guaranteed to also see the exit code. Python simply has
  no user-facing concept here — the GIL hides it.
- **`Mutex<Option<i32>>`**: `.lock()` returns a *guard*; the lock releases
  when the guard drops at end of scope (RAII again — no
  `finally: lock.release()`). And note the honest handling: a poisoned mutex
  (a thread panicked while holding it) is an `Err`, not a surprise.
- Signal handlers can only touch atomics (`src/spawn.rs:10-20`), because only
  async-signal-safe operations are legal inside a handler:

  ```rust
  static SIGINT_COUNT: AtomicU32 = AtomicU32::new(0);
  static SIGTERM_COUNT: AtomicU32 = AtomicU32::new(0);

  extern "C" fn on_signal(sig: libc::c_int) {
      match sig {
          libc::SIGINT => SIGINT_COUNT.fetch_add(1, Ordering::Relaxed),
          libc::SIGTERM => SIGTERM_COUNT.fetch_add(1, Ordering::Relaxed),
          _ => 0,
      };
  }
  ```

  The type system documents the constraint: no allocation, no locks, just
  atomic increments read later by the wait loop (§4.4).

### 4.3 Thread lifecycle — `JoinHandle` returns a value

`spawn_sampler` returns `JoinHandle<SamplerStats>` (`src/sampler.rs:73-84`):
the thread's return value comes back through `.join()`, and a panic in the
thread arrives as an `Err` at the join (`src/cmd/run.rs:128-134`):

```rust
let stats: SamplerStats = sampler_handle
    .join()
    .map_err(|_| anyhow::anyhow!("sampler thread panicked"))?;
writer_handle
    .join()
    .map_err(|_| anyhow::anyhow!("writer thread panicked"))?
    .context("writer failed")?;
```

Compare Python's `threading.Thread`, which swallows exceptions and returns
nothing unless you build your own plumbing. The writer's handle is
`JoinHandle<Result<()>>` — two layers: did the thread panic, and did it return
an error — so disk errors surface at shutdown with full context.

### 4.4 `unsafe` — a marked, audited escape hatch

Safe Rust can't express raw syscalls like `fork`/`waitpid`/`sigaction`. An
`unsafe` block says "the compiler can't check this; I've checked it myself" —
and this repo requires every one to carry a `// SAFETY:` comment (clippy
enforces it, `Cargo.toml:47`). The waitpid loop is the meatiest example
(`src/spawn.rs:105-141`, trimmed):

```rust
pub fn wait_target(child: &mut Child) -> io::Result<TargetExit> {
    let pid = child.id() as i32;
    let mut forwarded_int = 0;
    loop {
        let mut status: libc::c_int = 0;
        // SAFETY: plain waitpid on our own direct child.
        let ret = unsafe { libc::waitpid(pid, &raw mut status, 0) };
        if ret == pid {
            if libc::WIFEXITED(status) {
                return Ok(TargetExit::Code(libc::WEXITSTATUS(status)));
            }
            if libc::WIFSIGNALED(status) {
                return Ok(TargetExit::Signal(libc::WTERMSIG(status)));
            }
            continue; // stopped/continued: keep waiting
        }
        let err = io::Error::last_os_error();
        if err.raw_os_error() != Some(libc::EINTR) {
            return Err(err);
        }
        // EINTR: a signal arrived — forward Ctrl-C to the whole process group.
        let ints = SIGINT_COUNT.load(Ordering::Relaxed);
        if ints >= 2 {
            eprintln!("treehawk: second interrupt: abandoning target, finalizing session");
            return Ok(TargetExit::Abandoned);
        }
        if ints > forwarded_int {
            forwarded_int = ints;
            forward_to_group(pid, libc::SIGINT);
        }
    }
}
```

Notice how *small* the unsafe part is: one line. Everything around it —
matching the exit status, EINTR handling, the double-Ctrl-C policy — is safe
code. `unsafe` doesn't turn the checks off globally; it fences the unchecked
part.

The complete census of `unsafe` in this codebase, all thin wrappers over libc,
all commented:

- Signal handler install: `src/spawn.rs:26-37` — SAFETY: handler only touches
  atomics.
- `pre_exec` hook: `src/spawn.rs:53-66` (shown in §3.3) — runs between `fork`
  and `exec`, where only async-signal-safe calls are legal; joins a process
  group and writes the child into the cgroup by fd. This closes a real race:
  a grandchild spawned in the first milliseconds could otherwise escape the
  cgroup.
- The `waitpid` loop above, and `kill(-pgid, sig)` in `forward_to_group`
  (`src/spawn.rs:143-148`).
- Plain sysconf queries: `src/manifest.rs:136-146`.
- One `libc::kill` with SIGKILL in a test (`tests/run_mode.rs:146`).

Also see AGENTS.md: prefer `rustix` — safe syscall wrappers — and drop to
`libc` only when rustix can't express it. Compare: the absolute-deadline sleep
below is rustix (safe), while process-group forwarding is libc (unsafe).

### 4.5 A real-time loop without drift

The sampler's ticker (`src/sampler.rs:104-226`) is worth reading end to end as
systems-programming practice, independent of Rust. The three load-bearing
decisions:

**Absolute deadlines, not relative sleeps** — `next += interval_ns`, and the
sleep targets a point on the monotonic clock (`src/sampler.rs:248-269`):

```rust
/// Sleeps until `deadline_ns` on `CLOCK_MONOTONIC` using absolute-deadline
/// `clock_nanosleep` (no drift accumulation), in ≤ 50 ms slices so a stop
/// request is honored promptly even at long intervals.
fn sleep_until(deadline_ns: u64, stop: &StopSignal) {
    const SLICE_NS: u64 = 50_000_000;
    loop {
        if stop.stopping() {
            return;
        }
        let now = monotonic_ns();
        if now >= deadline_ns {
            return;
        }
        let target = deadline_ns.min(now + SLICE_NS);
        let request = Timespec {
            tv_sec: (target / 1_000_000_000) as i64,
            tv_nsec: (target % 1_000_000_000) as i64,
        };
        // EINTR just re-enters the loop with a fresh deadline check.
        let _ = rustix::thread::clock_nanosleep_absolute(ClockId::Monotonic, &request);
    }
}
```

A Python `time.sleep(interval)` loop drifts: each iteration's processing time
is added to the schedule. Sleeping *to a deadline* can't drift.

**Overrun policy: skip, never queue** (`src/sampler.rs:218-225`):

```rust
next += interval_ns;
let now = monotonic_ns();
if now >= next {
    // Overrun: skip to the next aligned tick, never queue up (FR-13).
    let missed = (now - next) / interval_ns + 1;
    overruns += missed;
    next += missed * interval_ns;
}
```

If a tick took too long, missed ticks are *counted* (they end up in the
manifest as `overruns`) but never replayed in a burst.

**Responsive shutdown** — the ≤ 50 ms slices mean a stop request is noticed
within 50 ms even at a 60 s sampling interval.

---

## Level 5 — Ecosystem craft: Arrow, serde, testing, lints

### 5.1 Columnar data with Arrow (your pandas bridge)

`src/model.rs` is the schema layer. For each table it defines three things:
the row struct (§1.1), the Arrow schema, and a rows→`RecordBatch` converter.
The schema is nullable exactly where the struct is `Option`
(`src/model.rs:87-105`, trimmed):

```rust
pub fn samples_schema() -> Arc<Schema> {
    Arc::new(Schema::new(vec![
        Field::new("t_mono_ns", DataType::UInt64, false),   // false = NOT NULL
        Field::new("proc_id", DataType::UInt32, false),
        Field::new("dt_ns", DataType::UInt64, true),        // true  = nullable
        Field::new("pss_kb", DataType::UInt64, true),
        ...
    ]))
}
```

Rows → columns with typed builders (`src/model.rs:141-192`, trimmed):

```rust
pub fn samples_batch(rows: &[SampleRow]) -> Result<RecordBatch, ArrowError> {
    let mut t = UInt64Builder::with_capacity(rows.len());
    let mut dt = UInt64Builder::with_capacity(rows.len());
    ...
    for r in rows {
        t.append_value(r.t_mono_ns);     // non-null column
        dt.append_option(r.dt_ns);       // nullable column: Option in, null out
        ...
    }
    let arrays: Vec<ArrayRef> = vec![Arc::new(t.finish()), Arc::new(dt.finish()), ...];
    RecordBatch::try_new(samples_schema(), arrays)
}
```

The verbosity is the price of column-typed, null-tracked, zero-copy buffers —
the same memory layout pandas and polars read natively, which is why the
output needs no custom reader: `pd.read_parquet("samples-00000.parquet")` just
works.

The reverse direction — columns → values — is in the report
(`src/cmd/report.rs:76-99`):

```rust
let t = col("t_mono_ns")?.as_primitive::<UInt64Type>();
let dt = col("dt_ns")?.as_primitive::<UInt64Type>();
...
for row in 0..batch.num_rows() {
    ...
    if !dt.is_null(row) && dt.value(row) > 0 && !utime.is_null(row) {
        let cpu_seconds = (utime.value(row) + stime.value(row)) as f64 / clk_tck;
        let wall_seconds = dt.value(row) as f64 / 1e9;
        // Machine-normalized: 100% = every core busy (NFR-7).
        entry.cpu_pcts.push(cpu_seconds / wall_seconds / n_cpus * 100.0);
    }
}
```

That `::<UInt64Type>` syntax is the **turbofish**: explicitly picking the
generic type parameter when inference can't — like `cast(list[int], x)` in
spirit, but it changes which compiled code runs, not just what mypy believes.

### 5.2 The crash-safe writer — WAL + rotation as a pattern

The problem (`src/writer.rs:1-9` doc comment): Parquet needs its footer
written at close, so a SIGKILLed process would lose the whole open chunk. The
design: append batches to an Arrow IPC *stream* file (`samples-active.arrows`)
which is readable up to the last complete batch, fsync every flush window,
and convert to compressed Parquet at rotation boundaries.

The append path shows the durability discipline (`src/writer.rs:103-116`):

```rust
fn append(&mut self, batch: &RecordBatch) -> Result<()> {
    if self.wal.is_none() {
        let file = File::create(self.wal_path())
            .with_context(|| format!("create {}", self.wal_path().display()))?;
        self.wal = Some(StreamWriter::try_new(BufWriter::new(file), &self.schema)?);
        self.wal_batches = 0;
    }
    let wal = self.wal.as_mut().expect("just ensured above");
    wal.write(batch)?;
    wal.get_mut().flush()?;              // BufWriter → OS
    wal.get_mut().get_ref().sync_data()?; // OS → disk (fdatasync)
    self.wal_batches += 1;
    Ok(())
}
```

And rotation converts WAL → Parquet, tolerating a truncated tail
(`src/writer.rs:149-165`):

```rust
pub fn convert_arrows_to_parquet(...) -> Result<()> {
    let reader = StreamReader::try_new(File::open(arrows)?, None)?;
    ...
    for batch in reader.flatten() {
        // .flatten() drops the Err of a truncated tail — exactly the AT-4 contract.
        writer.write(&batch)?;
    }
    writer.close()?;
    Ok(())
}
```

The test `wal_survives_truncation_and_converts_to_parquet`
(`src/writer.rs:304-337`) literally truncates the file mid-batch to simulate
a SIGKILL:

```rust
// Simulate SIGKILL: truncate mid-way through the last batch, no finish().
let full_len = std::fs::metadata(&wal_path).expect("meta").len();
drop(table);
let file = std::fs::OpenOptions::new().write(true).open(&wal_path).expect("reopen wal");
file.set_len(full_len - 8).expect("truncate");

convert_arrows_to_parquet(&wal_path, &out, &samples_schema()).expect("convert");
...
// First complete batch survives; the truncated one is dropped.
assert_eq!(rows, 2);
```

Read it to see how you *test* crash-safety rather than assert it.

Also in this layer: `serde` for the manifest — derive
`Serialize`/`Deserialize` (`src/manifest.rs:13-32`, like pydantic's
`model_dump`/`model_validate`) — and atomic file writes via tmp + rename so a
crash never leaves a torn `session.json` (`src/manifest.rs:101-107`):

```rust
/// Writes atomically (tmp + rename) so a crash never leaves a torn manifest.
pub fn write(&self, session_dir: &Path) -> io::Result<()> {
    let json = serde_json::to_string_pretty(self)?;
    let tmp = session_dir.join(".session.json.tmp");
    std::fs::write(&tmp, json)?;
    std::fs::rename(&tmp, session_dir.join(MANIFEST_FILE))
}
```

### 5.3 Testing: three layers

**1. Unit tests live next to the code** in `#[cfg(test)] mod tests` —
compiled only for tests, zero cost in the shipped binary. The pattern to copy:
fixture-based parser tests with *hostile* inputs. A process named `fire (fox)`
breaks naive `/proc/stat` parsing, so the fixture includes it
(`src/proc/mod.rs:254-278`):

```rust
const STAT_FIXTURE: &str = "1234 (fire (fox)) S 1000 1234 1234 0 -1 4194560 \
    12345 678 90 12 4500 1500 30 40 20 0 17 0 98765 1073741824 25000 ...";

#[test]
fn stat_parses_fixture() {
    let stat = parse_stat(STAT_FIXTURE).expect("fixture parses");
    assert_eq!(
        stat,
        Stat {
            comm: "fire (fox)".into(),
            ppid: 1000,
            utime_ticks: 4500,
            stime_ticks: 1500,
            num_threads: 17,
            starttime_ticks: 98765,
        }
    );
}
```

(Same idea in `src/cgroup.rs:164-168`: a comm of `weird ) name)` against the
PPID extractor.)

**2. Live self-inspection tests** — no mocks, real `/proc`, the test samples
its own process (`src/proc/mod.rs:304-316`):

```rust
#[test]
fn live_self_inspection() {
    let mut scratch = Vec::new();
    let me = std::process::id() as i32;
    let mut handle = PidHandle::open(me, &mut scratch).expect("open self");
    let sample = handle.sample(true, &mut scratch).expect("sample self");
    assert!(sample.status.vm_rss_kb > 0);
    assert_eq!(handle.identity.uid, rustix::process::getuid().as_raw());
    // Repeated reads through the same cached fds must keep working.
    let again = handle.sample(false, &mut scratch).expect("resample self");
    assert!(again.stat.utime_ticks >= sample.stat.utime_ticks);
}
```

**3. Integration tests in `tests/`** exercise the built binary end to end —
this repo prefers these (AGENTS.md). `env!("CARGO_BIN_EXE_treehawk")` is Cargo
handing the test the path to the compiled binary
(`tests/run_mode.rs:10-12`), ≈ pytest invoking your CLI via `subprocess`. The
simplest one (`tests/run_mode.rs:45-51`):

```rust
/// FR-22: the target's exit code passes through untouched.
#[test]
fn exit_code_passthrough() {
    let dir = temp_session("exitcode");
    let output = run_target(&dir, "50ms", &["sh", "-c", "exit 42"]);
    assert_eq!(output.status.code(), Some(42));
    std::fs::remove_dir_all(&dir).ok();
}
```

And the most ambitious, `sigkill_leaves_readable_dataset`
(`tests/run_mode.rs:132-169`), SIGKILLs a live treehawk and asserts the
session is still readable — the AT-4 acceptance test as executable code. These
tests need Linux with cgroup v2; copy their skip-gracefully shape when adding
cgroup-dependent cases (AGENTS.md).

On `.expect("message")` vs `.unwrap()`: `.expect` is allowed in tests — the
message is assertion context, like `assert x, "why"`. In library code the repo
bans both (`unwrap_used` lint, `Cargo.toml:79`) and prefers `if let` / `?` /
combinators. The one `.expect` you'll see on the non-test path documents an
invariant: `.expect("just ensured above")` (`src/writer.rs:110`) right after
the `is_none()` check that makes it true.

### 5.4 Clippy as a curriculum

Run `cargo clippy` and read what it says — it's a Rust tutor. This repo runs
pedantic-level lints with a curated allow-list adapted from `uv`
(`Cargo.toml:34-83`), and CI treats warnings as errors. Notable choices to
understand:

- `unwrap_used = "warn"` (`Cargo.toml:79`) — forces you into
  `Option`/`Result` combinators.
- `undocumented_unsafe_blocks = "warn"` (`Cargo.toml:47`) — the `// SAFETY:`
  rule from §4.4, replacing uv's outright `unsafe_code` ban.
- The cast lints are allowed *with a written reason* (`Cargo.toml:65-70`) —
  lint policy is itself documented decision-making.

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
   as JSON instead of the table (clap arg + serde derives on the stats
   structs).
2. **Level 2:** Extend `parse_interval` (`src/cli.rs:63-86`) to accept `"max"`
   as a sentinel (the README promises it) — you'll need to change the parsed
   type from `Duration` to an enum. Watch the compiler walk you through every
   affected site.
3. **Level 3:** Add a `--sort rss|cpu` option to `report` — closures as sort
   keys over the stats map (`src/cmd/report.rs:247-258` is the current sort).
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
| `dataclass` | `struct` + `#[derive]` | `src/model.rs:22` |
| `Enum` / `Union` + isinstance | `enum` + `match` | `src/spawn.rs:73` |
| `None` checks | `Option<T>` + combinators | `src/proc/mod.rs:148` |
| `try/except/raise` | `Result<T, E>` + `?` | `src/cgroup.rs:72` |
| `raise X from e` | `anyhow` `.context()` | `src/cmd/run.rs:76` |
| list/dict comprehension | iterator chain + `collect` | `src/cgroup.rs:74` |
| `lambda` | closure `\|x\| ...` | `src/cmd/report.rs:71` |
| `x if cond else y` | `if`/`match` as expressions | `src/cmd/run.rs:56` |
| `typing.Protocol` / ABC | trait / `impl Trait` bound | `src/proc/mod.rs:17` |
| `with` context manager | RAII / `Drop` | `src/cgroup.rs:81` |
| `queue.Queue` | `std::sync::mpsc` channel | `src/writer.rs:64` |
| `threading.Thread` | `std::thread` + `JoinHandle<T>` | `src/sampler.rs:73` |
| shared object + GIL | `Arc<...>` + atomics/`Mutex` | `src/sampler.rs:38` |
| `pydantic` / `json` | `serde` derive | `src/manifest.rs:13` |
| `click` / `argparse` | `clap` derive | `src/cli.rs:8` |
| `ruff` | `clippy` (config in Cargo.toml) | `Cargo.toml:39` |
| `pytest` files | `#[cfg(test)]` + `tests/` | `src/proc/mod.rs:250` |
| `subprocess.run` on your CLI | `env!("CARGO_BIN_EXE_...")` | `tests/run_mode.rs:11` |

---
