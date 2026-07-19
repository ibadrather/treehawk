# Agent instructions: treehawk release test run

You are testing **treehawk**, a CLI that runs a command inside a cgroup, samples CPU/RAM of the command and every descendant process at a fixed interval, writes the session to a directory (`session.json` + Parquet files), and prints a summary via `treehawk report <dir>`.

Your job: install the release you are given, verify every claim below against ground truth you control, and produce a written report. Do not just check that commands exit 0 — verify the **numbers** are right.

## Ground rules

- Work in a scratch directory. Pass `--out <dir>` explicitly on every `run` so sessions land where you expect and never collide (as of v0.1.1, `--out` into an existing session dir silently overwrites it — do not rely on collision protection).
- Record the exact command, full output, and exit code (`echo $?`) for every test. Exit codes are part of the contract.
- Linux only; requires cgroup v2. Note the host specs (CPU model, core count from `nproc`, kernel) in your report — CPU% interpretation depends on core count.
- You need a Parquet reader for the verification steps: `pip install pyarrow` (or use an existing pandas/duckdb).
- Where a test needs a target workload, write a small script; keep every workload short (2–5 s) and deterministic.

## Phase 1 — Install and discover

1. Install using the installer command provided for the release. Confirm the binary location, `treehawk --version` matches the release tag, and note the binary size.
2. Run `treehawk --help` and `--help` on each subcommand. Record which subcommands are implemented vs. stubs (stubs should say "not yet implemented (planned milestone)" and exit non-zero). Any stub that silently exits 0, or any implemented command missing from earlier releases, is a finding.

## Phase 2 — Core correctness (the important part)

Each test states its **pass criterion**. Verify against ground truth, not against treehawk's own report.

### 2.1 Smoke test
`treehawk -v run --out <dir> -- sleep 2`, then `treehawk report <dir>`.
**Pass:** exit 0; session dir contains `session.json`, `processes.parquet`, `samples-*.parquet`, `host-*.parquet`; report shows one `sleep` row with exit code 0, lifetime ~2 s, 0 overruns, achieved rate ≈ configured rate.

### 2.2 Full-tree capture
Write a workload where a bash script backgrounds: (a) a Python busy-loop (CPU burner, ~3 s), (b) a Python process that allocates a known amount, e.g. `bytearray(300*1024*1024)` and touches every page, (c) a nested `bash -c 'gzip -c /dev/urandom | head -c 50M > /dev/null'` — then `wait`s. Run with `--interval 50ms --label test=workload`.
**Pass:** the report lists every process in the tree including grandchildren (2× bash, 2× python, gzip, head), all carrying the label.

### 2.3 Memory accuracy
From test 2.2: the allocator process's RSS peak must be within ~10% of the allocated size (expect allocation + interpreter overhead, e.g. 300 MiB → ~310 MiB).

### 2.4 CPU accuracy — verify from raw data, not the report
Known trap: as of v0.1.1 the report displays CPU% normalized to **all cores** (a core-saturating process shows 100/nproc, e.g. 3.1% on 32 cores). Do not fail the run on the displayed number alone; verify the raw data:

```python
import pyarrow.parquet as pq
t = pq.read_table("<dir>/samples-00000.parquet").to_pydict()
rows = [(d,u,s) for d,u,s,p in zip(t['dt_ns'],t['cpu_utime_ticks'],
        t['cpu_stime_ticks'],t['pid']) if p == BURNER_PID and d]
cpu_s  = sum(u+s for _,u,s in rows) / CLK_TCK   # clk_tck from session.json
wall_s = sum(d for d,_,_ in rows) / 1e9
print(f"{100*cpu_s/wall_s:.1f}% of one core")
```

**Pass:** the busy-loop process computes to 95–100% of one core. Separately report what the summary table *displays* for it and whether the column is labeled with its convention. If a future release changes the display convention, that's expected — note it.

Also run a 2-core variant (fork two busy-loop children — use `multiprocessing.set_start_method("fork")` in a script **file**, not `python -c`, or forkserver breaks) and confirm the raw data shows ~200% of one core total across the two children.

### 2.5 Exit-code contract
- `run -- bash -c 'exit 42'` → treehawk exits **42**.
- `run -- nonexistent-command-xyz` → clear error, exit **127**.
- `report /nonexistent/path` → clear error ("not a treehawk session"), exit **1**.
- `run --interval 0ms -- true` → rejected with the valid range shown, exit **2**.

### 2.6 Signal handling
- **SIGINT:** start `run -- sleep 30` in the background, `kill -INT` the treehawk PID after ~2 s. **Pass:** session finalizes (log line + complete parquet files), treehawk exits 130, `report` works and records exit 130 for the target.
- **SIGKILL:** same setup, `kill -9` treehawk after ~1.5 s. Then: (a) check whether the target `sleep 30` survived (expected: yes — kill it), (b) list the session dir and note which files survived, (c) run `report` on it. **Pass:** `report` does not crash and clearly flags the session as not finalized. **Record as a finding** how much sample data was lost (v0.1.1 baseline: the entire unflushed chunk — only `session.json` survives a 1.5 s run) and whether the crashed-session message matches reality.

### 2.7 Orphaned descendants
Run a target whose child outlives it (e.g. `bash -c 'sleep 30 & exit 0'`). **Pass:** treehawk exits promptly with the target's code and warns that descendants are still alive (v0.1.1 also warns it cannot remove the busy cgroup). Kill the orphan afterwards.

### 2.8 Known limitation probes (record results; don't fail on v0.1.1 behavior)
- **Short-lived processes:** `run -- bash -c 'for i in $(seq 200); do /bin/true; done'` at default interval. Count captured `true` rows (v0.1.1 baseline: 0). If a release starts reporting unattributed tree CPU from cgroup `cpu.stat`, verify it.
- **Output-dir overwrite:** `run --out` twice at the same dir. Check whether the second run refuses, warns, or silently replaces the first session (v0.1.1: silent replace). A silent replace remains an open issue; a refusal is the fix landing.

## Phase 3 — Performance and robustness

### 3.1 Sampling under stress
`run --interval 1ms -- sleep 5` with `-v`. **Pass:** achieved rate ≥ ~950 Hz, overruns 0 or near-0, session finalizes.

### 3.2 Overhead
`/usr/bin/time -f "cpu=%Us+%Ss maxrss=%MKB" treehawk run --out <dir> -- sleep 5` at 100 ms and at 1 ms. **Pass:** ≤ ~1% of one core at 100 ms; note maxrss (v0.1.1 baseline: ~10 MB at 100 ms, ~0.7 s CPU total at 1 ms). Regressions > 2× baseline are findings.

### 3.3 I/O passthrough
`echo hello | treehawk run --out <dir> -- cat` prints `hello`; target stdout/stderr appear live during `run` (verify during 2.2 — the pipeline output/tracebacks must be visible).

## Phase 4 — Data integrity

Open all three Parquet files from the 2.2 session with pyarrow and check:
- `processes.parquet`: one row per observed process; `ppid` links form a tree rooted at the target; labels present; exit_code populated at least for the root.
- `samples-*.parquet`: monotonically non-decreasing `t_mono_ns`; first sample per process has null `dt_ns`/tick deltas; `pss_kb` populated periodically (every N ticks per `session.json` → `sampling.pss_every_ticks`).
- `host-*.parquet`: one row per tick; `mem_total_kb` constant; PSI columns present.
- `session.json`: `schema_version`, version matches binary, `finished` block present with tick count consistent with the parquet row counts.
- Note whether argv/cmdline is recorded anywhere (v0.1.1: no — `exe_name`/`exe_path` only; identical binaries are indistinguishable). If a release adds it, verify against a run with distinctive arguments.

## Phase 5 — Report

Write a markdown report containing:

1. **Verdict up front**: does the tool do what it claims, and are the numbers accurate?
2. Host specs, release version, install method.
3. A pass/fail table for every test above, with the measured values (not just ✓).
4. **Issues found, ranked by severity**, each with an exact reproduction command and observed vs. expected behavior. Distinguish *regressions* (v0.1.1 baseline behavior that got worse), *known open issues* (still present), and *new findings*.
5. Changes since the previous report: fixed issues (verify the fix, don't assume), new features (test them), behavior changes.
6. Feature suggestions only if grounded in friction you actually hit while testing.

Baseline for comparison: `treehawk-v0.1.1-test-report.md` in this directory. Known open issues as of v0.1.1: machine-normalized unlabeled CPU% display; silent `--out` overwrite; short-lived processes uncaptured/unattributed; full active-chunk loss on SIGKILL (plus misleading crashed-session message); no argv capture. Untested to date: GPU sampling (needs an NVIDIA machine), `watch`/`export`/`ls`/`config`/`service` (stubs).
