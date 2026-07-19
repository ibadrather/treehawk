# treehawk v0.1.1 — Test Report

**Date:** 2026-07-19 · **Host:** proart, i9-13980HX (32 CPUs), Linux 7.0.0-27-generic · **Install:** release installer script → `~/.cargo/bin/treehawk` (9.2 MB binary)

## What it does

`treehawk run -- <cmd>` places the target command in a dedicated cgroup, samples every process in it (the whole descendant tree) at a configurable interval, and writes a session directory containing `session.json` plus Parquet files (`processes`, `samples-*`, `host-*`). `treehawk report <dir>` prints a per-process summary table. `watch`, `export`, `ls`, `config`, `service` are stubs that clearly report "not yet implemented (planned milestone)".

**Verdict: it does what it promises, and does it accurately.** The core loop (run → record → report) is solid, the numbers check out against ground truth, and error handling is unusually polished for a v0.1.x. The issues found are presentation/ergonomics, not correctness of the recorded data.

## What was tested and passed

| Test | Result |
|---|---|
| Install one-liner | Works; installs to `~/.cargo/bin` |
| Full-tree capture | Workload with 6 processes across 3 levels (bash → python/bash → gzip/head): all captured, labels applied |
| Memory accuracy | Python allocating 300 MiB → reported RSS peak 309.7 MiB ✓ |
| CPU accuracy (raw data) | Busy-loop process → exactly 100.0% of one core when recomputed from raw ticks in `samples-*.parquet` ✓ |
| Exit code propagation | `exit 42` → treehawk exits 42; missing command → 127; SIGINT → 130 ✓ |
| Ctrl-C (SIGINT) | Session finalized cleanly, exit code recorded, report works ✓ |
| kill -9 mid-run | `report` recovers gracefully with an honest "session was not finalized" note ✓ (but see issue 4) |
| Orphaned descendants | Detected and warned: "N descendant(s) still alive at exit" + busy-cgroup warning ✓ |
| High-rate sampling | `--interval 1ms` for 5 s: 988 Hz achieved, 0 overruns ✓ |
| Overhead | 100 ms interval: ~0.4% of one core, ~10 MB RSS. 1 ms interval: ~13% of one core. Negligible for the default ✓ |
| stdin/stdout passthrough | Both pass through transparently ✓ |
| Input validation | `--interval 0ms` rejected with range shown; `report` on a non-session gives a clear error ✓ |

The recorded data model is genuinely good: raw CPU ticks (so any statistic is recomputable), RSS + swap + VmSize + periodic PSS, context switches, per-tick host CPU/memory and PSI pressure, and GPU columns (null on this machine — untested, no NVIDIA GPU sampled).

## Issues found (ranked)

1. **CPU% is machine-normalized and unlabeled.** A process saturating one core displays as **3.1%** (= 100/32) in the report. The raw data is correct; only the display convention differs from `top`/`htop`, where that process would show 100%. On big machines every process looks idle. Either switch to the per-core convention, or label the column (e.g. `CPU% of 32 cores`), or show both.
2. **`--out` silently overwrites an existing session.** Pointing `--out` at a directory that already holds a session replaces its `session.json` and data with no warning — the old session is destroyed. Refuse unless the directory is empty (or add `--force`).
3. **Short-lived processes are invisible.** 200 consecutive `/bin/true` invocations under 100 ms sampling: zero captured, and their CPU time is attributed to nothing. Inherent to sampling, but the cgroup's own `cpu.stat` could be read at session end so the report can show "total tree CPU: X s, of which Y s unattributed to sampled processes."
4. **SIGKILL loses the entire active chunk.** After 1.5 s of recording (~15 ticks), kill -9 left only `session.json` — no samples survived. For long sessions consider periodic flushing so loss is bounded. Related nit: the crashed-session report says "data recovered from the active chunk" and "target likely exited within one interval" even when nothing was recovered and the target ran for seconds.
5. **No command-line capture.** `processes.parquet` stores `exe_path`/`exe_name` but not argv. Two `python3.14` rows in one session are indistinguishable in the report. `/proc/<pid>/cmdline` at first sight would fix this.

## Feature requests

- **argv/cmdline column** (issue 5) — highest value, tiny cost.
- **Tree rendering in `report`** — `ppid` is already recorded; indenting children under parents would live up to the name and disambiguate identical binaries.
- **A whole-tree summary row** — total CPU-seconds and peak aggregate RSS across the tree; that's usually the question being asked ("how much did my build use?").
- **`export` to CSV/JSON prioritized** (planned M3) — Parquet is the right storage format, but most users won't have pyarrow handy; even `report --json` would unlock scripting.
- **Per-process disk I/O** — `/proc/<pid>/io` read/write bytes would round out the picture.
- **Orphan policy flag** — `--kill-orphans` to tear down surviving descendants at exit instead of only warning.
- **Simple timeline output** — even an ASCII sparkline per process (RSS/CPU over time) in `report`, ahead of any full plotting.

## Reproduction artifacts

Session directories from all tests are in the scratchpad (`tree-test`, `cpu2b`, `shortlived`, `sigint-test`, `crashed`, `fast`, `oh1`, `oh2`); the multi-process workload script is `workload.sh` there.
