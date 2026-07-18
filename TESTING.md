# Testing Treehawk

How to build and verify the M1 core (`run` + `report`). Everything here runs on
an ordinary Linux box — kernel ≥ 5.4 with cgroup v2 (any modern distro).

## Prerequisites

- **Rust** via [rustup](https://rustup.rs) — the pinned toolchain in
  `rust-toolchain.toml` is selected automatically on first `cargo` invocation.
- **A C toolchain** for linking: `sudo apt-get install build-essential`.
- Optional, for the acceptance checks below:
  - `python3` with `pandas` + `pyarrow` (verifies the "open it straight from
    pandas" promise): `pip install pandas pyarrow`
  - `pidstat` for the overhead smoke check: `sudo apt-get install sysstat`

## The standard check (what CI runs)

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test
```

The same three steps run in GitHub Actions (`.github/workflows/ci.yml`) on every
push and pull request.

### What `cargo test` covers

**Unit tests** (inside `src/`):

| Area | What is tested |
|---|---|
| `cli` | interval parsing (range clamps, units), trailing-command parsing |
| `proc` | `/proc` `stat`/`status`/`smaps_rollup` parsers against fixture strings — including a hostile `comm` containing spaces and parens — plus a live self-inspection through the cached-fd path |
| `proc::host` | `/proc/stat`, `/proc/meminfo`, PSI parsers + a live two-tick host sample (delta math) |
| `cgroup` | PPID extraction, live PID-tree walk containing the test process, cgroup-v2 path resolution |
| `writer` | crash-safety: appends IPC batches, truncates the WAL mid-batch to simulate SIGKILL, verifies conversion to Parquet recovers every complete batch |
| `model` / `manifest` | Arrow batch construction, `session.json` round-trip |

**Integration tests** (`tests/run_mode.rs`) drive the real compiled binary:

- **FR-22** — `treehawk run -- sh -c 'exit 42'` exits with 42.
- **Clean finalize** — manifest gains its `finished` block; `samples-*.parquet`,
  `host-*.parquet`, `processes.parquet` exist; no `*-active.arrows` left behind.
- **AT-1** — `bash -c 'sleep 1 & disown; sleep 2'`: the disowned, reparented
  `sleep` still shows up in the report.
- **AT-4** — SIGKILL treehawk mid-run; the session stays readable (report flags
  "not finalized" and recovers data from the active chunk).
- Stub subcommands (`watch`, …) exit 2 with a clear message.

Notes:

- The integration tests spawn real process trees and sleep through real flush
  windows — expect the suite to take ~10 s. If your machine is heavily loaded,
  run them serially: `cargo test -- --test-threads=1`.
- Tests pass in **both** tracking modes. Under a systemd user session you get
  cgroup mode; in environments without cgroup delegation (bare TTY session,
  some containers) treehawk prints the fallback warning and uses PID-tree
  tracking — the suite still passes, since AT-1's disowned child is caught
  while its parent is alive.

## Manual acceptance checks (spec §9)

Build a release binary first:

```bash
cargo build --release
alias th=./target/release/treehawk
```

**AT-1 — no child escapes:**

```bash
th run --interval 20ms --out /tmp/th-at1 -- bash -c 'sleep 1 & disown; sleep 2'
th report /tmp/th-at1        # expect bash + two sleep rows
```

**FR-22 — exit-code transparency in a pipeline:**

```bash
th run --out /tmp/th-exit -- false; echo $?   # prints 1
```

**AT-4 — SIGKILL crash safety (loses ≤ one 5 s flush window):**

```bash
th run --out /tmp/th-kill -- sleep 60 &
sleep 8 && kill -9 "$(pgrep -x treehawk)"
th report /tmp/th-kill       # succeeds, notes "not finalized", shows recovered data
pkill -f 'sleep 60'          # clean up the orphaned target
```

**README promise — sessions open directly in pandas:**

```bash
python3 -c "import pandas as pd; print(pd.read_parquet('/tmp/th-at1/samples-00000.parquet'))"
```

## Overhead smoke check (NFR-1, informal)

Target: ≈ ≤ 1 % of one core at the default 10 Hz over ~50 processes. (The
formal, CI-gated benchmark lands in M6.)

```bash
th run --out /tmp/th-load -- bash -c 'for i in $(seq 50); do sleep 600 & done; wait' &
pidstat -p "$(pgrep -x treehawk)" 1 30    # watch %CPU for 30 s
kill %1 && pkill -f 'sleep 600'
```

Also check the manifest afterwards: `finished.overruns` should be 0 and
`finished.achieved_rate_hz` ≈ 10 in `/tmp/th-load/session.json`.
