# Treehawk — Implementation Plan (M1 detailed, M2–M6 outlined)

## Context

Treehawk is a greenfield Rust CLI that runs a command and logs CPU/RAM/GPU usage of it and every descendant process, at high rate with strictly bounded overhead. The repo currently contains only documentation: `README.md` and a thorough formal spec (`requirements.md`, v0.1). No code exists yet.

This plan was preceded by resolving all 9 open questions in the spec with the user (2026-07-17). Development and testing will happen on an Ubuntu machine with an Intel i9 + NVIDIA RTX 3000-series GPU — exactly reference platform 1 from the spec. Scope decision: **detailed plan for M1 (core run mode), brief outline for M2–M6**, with the architecture shaped so later milestones slot in.

## Resolved open questions (decisions to record)

| OQ | Decision |
|---|---|
| OQ-1 cgroup aggregate rows in watch mode | **Deferred post-v1.** Schema is versioned (NFR-8) so rows can be added later. |
| OQ-2 kernel floor | **Core requires ≥ 5.4; eBPF event capture requires ≥ 5.8 (CO-RE)**, auto-falling back to the netlink proc connector on older kernels (FR-4 requires that fallback anyway). |
| OQ-3 NVIDIA per-process GPU util fallback | **Record null, never estimate.** Per-process GPU memory and device-level util (host context) are still recorded. |
| OQ-4 stdout/stderr capture | **No.** Target stdio is inherited/pass-through only; capturing output is not Treehawk's job. |
| OQ-5 BMC/IPMI wall power | **Deferred.** RAPL/hwmon/battery only (M5), with the documented underestimate caveat. |
| OQ-6 non-systemd init templates | **Deferred.** systemd only in v1. |
| OQ-7 container follow-through | **Deferred** (already NG5). |
| OQ-8 live remote streaming | **Deferred.** v1 assumes session files are copied off the machine. |
| OQ-9 32-bit armv7 | **Not needed.** 64-bit only: x86_64 + aarch64 (Pi 4 on 64-bit OS). |

## Deliverable 0 — Docs: save plan + record decisions

Save this plan as `plan.md` in the repo root (so it lives with the spec and is versioned).

Edit `requirements.md`:
- Rewrite §10 as "Resolved Questions" with the table above (keep original question text, add decision + date).
- Bump header to Version 0.2, Status: Accepted.
- Fold the OQ-2 decision into the platform line ("kernel ≥ 5.4; eBPF events ≥ 5.8").

Edit `README.md`: one-line note under Requirements that eBPF short-lived-process capture needs kernel ≥ 5.8 (netlink fallback below that).

## Deliverable 1 — M1: Core run mode

**Goal (spec M1):** `treehawk run -- <cmd>` with cgroup containment, CPU+RAM sampling, Parquet output, and `treehawk report`. Usable end-to-end. Linux-only; code must build on macOS is a non-goal (development happens on the Ubuntu box).

### Project scaffolding

- Single binary crate `treehawk` (no workspace yet; split into `treehawk-core` + bindings crate only when the post-v1 PyO3 work starts).
- `rust-toolchain.toml` pinning current stable; edition 2024. Targets: `x86_64-unknown-linux-gnu` now; musl static + aarch64 wired up in M6 packaging.
- Dependencies (hot path deliberately minimal):
  - `clap` (derive) — CLI (FR-21)
  - `rustix` (+ a little `libc`) — syscalls: `clock_nanosleep`, signals, pidfd
  - `arrow` + `parquet` (arrow-rs) — in-memory batches and on-disk chunks (FR-15, §7)
  - `serde`/`serde_json` — `session.json` manifest (FR-16)
  - `thiserror`/`anyhow` — errors
  - **No** `procfs` crate and no async runtime: /proc parsing is hand-rolled with reused buffers to meet the overhead budget (NFR-1), per §7 ("async not required on the hot path").
- Minimal CI from day one (GitHub Actions, ubuntu-latest): fmt, clippy `-D warnings`, `cargo test`. Overhead benchmarks are M6.

### Module map (`src/`)

```
main.rs            — arg parse, dispatch
cli.rs             — clap definitions: run, report (+ stubs erroring "not yet implemented" for watch/export/ls/config/service)
cmd/run.rs         — run-mode orchestration
cmd/report.rs      — session summary (FR-19)
cgroup.rs          — cgroup v2 create/enumerate/cleanup + PID-tree fallback (FR-3)
spawn.rs           — launch target, signal forwarding, exit-code plumbing (FR-1, FR-22)
proc/mod.rs        — per-PID handle caching open fds; stat/status/smaps_rollup readers (FR-7)
proc/host.rs       — host context: /proc/stat, /proc/meminfo, /proc/pressure/* (FR-9)
sampler.rs         — dedicated sampling thread, monotonic ticker, overrun skip+count (FR-11–13)
model.rs           — sample structs, Arrow schemas, schema_version const (NFR-8)
writer.rs          — writer thread, double-buffered batches, chunk rotation, bounded flush (FR-15, FR-17, FR-18)
manifest.rs        — session.json write/finalize (FR-16)
```

### Key designs

**1. Launch + containment (FR-1–3).** `std::process::Command` with `pre_exec`: the child moves *itself* into a freshly created cgroup (write own PID to `cgroup.procs`) before `exec`, eliminating the race where early-spawned grandchildren escape. The cgroup is created as a child of Treehawk's own cgroup (path from `/proc/self/cgroup`), named `treehawk-<session-id>` — works unprivileged under systemd's per-user delegated subtree. Member enumeration each tick = read `cgroup.procs`. If cgroup creation fails (no delegation, e.g. non-systemd session), print the FR-3 warning and fall back to PID-tree walking (snapshot `/proc/*/stat` PPIDs, follow descendants; known to miss double-forkers — that's the documented reduced guarantee). Record which mode was active in the manifest.

**2. Signals + exit (FR-1, FR-22).** Forward SIGINT/SIGTERM to the target (child runs in its own process group; forward to the group). Reap with `waitpid`; exit with the target's exit code, or `128+signal` if signal-killed. Second SIGINT = give up waiting, flush and exit. Always finalize the session (flush + manifest) before exiting.

**3. Sampling loop (FR-11–13).** One dedicated thread. Absolute-deadline ticks via `clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME)` — no drift accumulation. On overrun: skip to next aligned tick, increment an overrun counter surfaced in the manifest. Per tick: enumerate cgroup members → read per-PID files (fd cache per PID: open once, `pread` from offset 0 each tick, drop on exit) → compute CPU deltas from previous tick → append host-context row (every tick, even if idle — FR-9) → hand batch to writer. Interval: `--interval` 1 ms–60 s, default 100 ms (FR-11). `interval = "max"` (FR-11b) is deferred to M3 alongside config, but the ticker keeps an achieved-rate stat from day one so the manifest field exists.

**4. Metrics (FR-7, M1 subset = CPU+RAM).** From `stat`: utime/stime (raw jiffy deltas stored; machine-normalized % is the derived presentation per NFR-7), num_threads, starttime. From `status`: VmRSS, VmSwap, VmSize, voluntary/nonvoluntary_ctxt_switches. From `smaps_rollup`: PSS "when readable" — measured cost first; if it blows the budget at high rates, decimate PSS to ~1 Hz (independent knob), null between reads. Clocks (FR-12): every sample stamped with CLOCK_MONOTONIC; one CLOCK_REALTIME anchor pair in the manifest.

**5. Identity + privacy (FR-30).** Samples carry `pid` + a session-scoped `proc_id` (PID reuse safety: identity = pid+starttime). A separate `processes` table stores per-process identity: proc_id, pid, ppid, exe path + basename, uid, start/end time, exit code, labels (from `--label`). **No cmdline, no environment** in M1 (full-cmdline opt-in arrives with config in M3).

**6. Output (FR-15–18).** Session dir `./treehawk/<timestamp>` or `--out`. Contents:
- `samples-NNNNN.parquet` — per-process rows, rotating chunks
- `host-NNNNN.parquet` — host-context rows
- `processes.parquet` — written at finalize
- `session.json` — manifest (FR-16): schema_version, exact command, host info (kernel, CPU model, treehawk version), sampling config, clock anchors, tracking mode, overruns
- `samples-active.arrows` / `host-active.arrows` — the **active chunk as an Arrow IPC stream file** (append-safe, readable up to the last complete batch). Crash-safety design: Parquet needs its footer, so a killed process would lose the whole open chunk; instead the writer appends IPC batches every flush (≤ 5 s, FR-17) and converts the stream to a numbered `.parquet` chunk at rotation boundaries (size/time capped, FR-18). `report`/`export` read the leftover `.arrows` tail transparently and finalize it. This satisfies AT-4 (SIGKILL loses ≤ one flush window) without producing thousands of tiny Parquet files.

**7. `treehawk report <session>` (FR-19).** Read manifest + Parquet (+ any `.arrows` tail): per-process peak/mean/p95 CPU (machine-normalized), peak RSS, lifetime, exit code; session totals, overruns, duration. Plain table, color only on TTY (FR-21).

**8. Robustness (NFR-4).** Every per-PID read can hit ESRCH/ENOENT mid-read (process died) — always "drop this PID this tick", never propagate. GPU columns don't exist yet in M1; the schema reserves them as nullable so M2 is additive, not a schema break.

### Implementation order

1. Scaffold: crate, CI, clap skeleton with `run`/`report` + stub subcommands.
2. `spawn.rs`: launch, process group, signal forwarding, exit-code pass-through. *(Testable immediately: `treehawk run -- false` exits 1.)*
3. `cgroup.rs`: create/join/enumerate/cleanup + fallback + warning.
4. `proc/`: stat/status/smaps_rollup + host readers, delta math, fd caching. Unit tests against fixture strings + live self-inspection tests.
5. `sampler.rs`: ticker, overrun handling, batch assembly.
6. `model.rs` + `writer.rs` + `manifest.rs`: Arrow schemas, IPC-WAL + Parquet rotation, flush loop, finalize path.
7. `cmd/report.rs`.
8. Integration tests (see Verification) + a short overhead smoke check.
9. Deliverable 0 doc updates + a README status bump ("M1 usable").

### M1 acceptance criteria (from spec)

- AT-1: `treehawk run -- bash -c 'sleep 1 & disown; sleep 2'` records the disowned child (cgroup mode).
- AT-4: SIGKILL mid-run leaves a readable dataset missing ≤ the last flush window.
- FR-22: exit code transparency in a pipeline.
- `pd.read_parquet(...)` opens the samples directly (README promise).
- Informal NFR-1 check: ~1% of one core at 10 Hz / 50 procs via `pidstat` (formal CI gate is M6).

## M2–M6 outline (later milestones, for orientation)

- **M2 — GPU backends:** `gpu/` module with a `GpuBackend` trait behind one per-process schema (FR-8). NVML first via `nvml-wrapper` (reference GPU is the RTX 3000): per-process memory always; per-process util when the driver provides it, **null otherwise (OQ-3)**; device-level util/temp/power into host context. Then DRM fdinfo backend (generic AMD/Intel/v3d). Auto-detect per device; vendors coexist.
- **M3 — Watch mode + config:** `/proc` full-scan enumeration path beside the cgroup path; thresholds (machine-normalized CPU semantics, FR-5) + `--match` keyword labels + cool-down; combined `run --watch-others`; TOML config with documented precedence chain, `config init/show` (FR-27); `interval = "max"` self-tuning (FR-11b).
- **M4 — Events:** exec/exit capture so <1-tick processes still get identity+lifetime rows (FR-4). eBPF via `aya` (CO-RE, kernel ≥ 5.8 per OQ-2); netlink proc connector fallback; both optional and auto-detected (NFR-5).
- **M5 — Service + power:** `service install/uninstall/status`, hardened systemd unit, sd_notify, boot-id sessions, rollover + retention (FR-18, FR-28, FR-29); `--power` sources RAPL powercap / NVML / hwmon / power_supply, each labeled by domain (FR-26, OQ-5 deferred).
- **M6 — Hardening:** overhead benchmarks as CI release gates (NFR-1/2), 7-day soak (NFR-3), musl static builds + aarch64, docs.

## Verification (M1, on the Ubuntu box)

1. `cargo fmt --check && cargo clippy -- -D warnings && cargo test` (unit + fixture tests).
2. Integration script (checked in as `tests/` integration tests where possible):
   - `treehawk run -- bash -c 'sleep 1 & disown; sleep 2'` → session contains 3+ processes incl. the disowned sleep (AT-1).
   - `treehawk run -- false; echo $?` → `1` (FR-22).
   - Start a run, `kill -9` treehawk, then `treehawk report <dir>` succeeds and data ends ≤ 5 s before the kill (AT-4).
   - `python3 -c "import pandas as pd; print(pd.read_parquet('<dir>/samples-00000.parquet'))"`.
3. Overhead smoke: 10-minute run at 10 Hz over a ~50-process tree (script spawning sleepers), measure treehawk's own CPU with `pidstat -p <pid> 1` → expect ≈ ≤ 1% of one core.
