# Roadmap and feature tracker

The single consolidated view of every planned feature, grouped by milestone, with current status.
Detail lives elsewhere and is linked, not duplicated: the formal spec is
[requirements.md](requirements.md) (FR/NFR/AT numbers below refer to it), the implementation plan
is [plan.md](plan.md), and test findings come from the
[v0.1.1](../test-run-reports/treehawk-v0.1.1-test-report.md) and
[v0.1.2](../test-run-reports/treehawk-v0.1.2-test-report.md) reports.

**Status legend:** ✅ done and verified · 🔶 partially done · ⬜ not started

**As of:** v0.1.2, 2026-07-19.

## Milestones at a glance

| Milestone | Scope | Status |
|---|---|---|
| [M1 — core run mode](#m1--core-run-mode) | `run` + `report`, cgroup containment, CPU+RAM sampling, crash-safe Parquet | ✅ released |
| [M2 — GPU backends](#m2--gpu-backends) | NVML, DRM fdinfo, host GPU context | ⬜ next up |
| [M3 — watch mode + config](#m3--watch-mode--config) | `watch`, thresholds, TOML config, `export`/`ls`, `interval "max"` | ⬜ planned |
| [M4 — event capture](#m4--event-capture) | eBPF/netlink exec+exit events for sub-tick processes | ⬜ planned |
| [M5 — service + power](#m5--service--power) | systemd service mode, rollover/retention, `--power` | ⬜ planned |
| [M6 — hardening](#m6--hardening) | overhead CI gates, 7-day soak, musl builds | 🔶 CI exists, gates don't |
| [Backlog](#backlog-unscheduled-feature-requests-and-known-issues) | feature requests and known issues from test reports, not yet scheduled | mixed |
| [Deferred — post-v1](#deferred--post-v1) | Python API, containers, live streaming, and other explicit deferrals | ⬜ deferred |

## M1 — core run mode

Released as v0.1.1; all five issues from the v0.1.1 test report were fixed and verified in
v0.1.2.

| Feature | Spec | Status |
|---|---|---|
| Launch target preserving args, env, cwd, stdio, and exit code; forward SIGINT/SIGTERM | FR-1, FR-22 | ✅ |
| Track all descendants via a dedicated cgroup v2 | FR-2, FR-3 | ✅ |
| PID-tree fallback with a clear warning when cgroup delegation is unavailable | FR-3 | ✅ |
| CPU metrics: raw jiffy deltas, user/system split, threads, context switches | FR-7 | ✅ |
| Memory metrics: RSS, swap, virtual size, periodic PSS | FR-7 | ✅ |
| Host context every tick: total CPU, memory, PSI pressure | FR-9 | ✅ |
| Configurable `--interval` 1 ms–60 s, default 100 ms; ~1 kHz achievable | FR-11, NFR-2 | ✅ |
| Monotonic timestamps + one realtime anchor per session | FR-12 | ✅ |
| Overrun handling: skip to next aligned tick, count in manifest | FR-13 | ✅ |
| Rotating Parquet chunks, directly readable by pandas/polars/DuckDB | FR-15 | ✅ |
| Self-describing `session.json` manifest | FR-16 | ✅ |
| Crash-safe: streaming `-active.arrows` chunk, bounded flush window | FR-17, AT-4 | ✅ |
| `treehawk report` with per-process peak/mean/p95 CPU, peak RSS, lifetime, exit codes | FR-19 | ✅ |
| Privacy default: exe basename + `--label`; argv only via opt-in `--cmdline` | FR-30 | ✅ |
| CPU% shown in per-core convention (matches `top`) | NFR-7 | ✅ |
| `--out` refuses a non-empty directory instead of overwriting | — | ✅ |
| Unattributed short-lived CPU surfaced from cgroup `cpu.stat` in `report` | — | ✅ |
| Overhead at defaults ≈ 0.4% of one core, ~10 MB RSS (informal check; CI gate is M6) | NFR-1 | ✅ |
| Robustness: mid-read process death never crashes a session | NFR-4 | ✅ |
| Verbose progress logging (`-v`/`-vv`) on stderr | — | ✅ |

## M2 — GPU backends

Next up. The output schema already reserves nullable GPU columns, so M2 is additive, not a schema
break.

| Feature | Spec | Status |
|---|---|---|
| GPU columns reserved (nullable) in the per-process schema | FR-8, NFR-8 | ✅ |
| `GpuBackend` trait: pluggable, auto-detected per device, vendors coexist | FR-8 | ⬜ |
| NVML backend (NVIDIA discrete): per-process memory always; per-process utilization where the driver provides it, null otherwise — never estimated | FR-8, OQ-3 | ⬜ |
| DRM fdinfo backend (AMD amdgpu, Intel i915/xe, v3d) | FR-8 | ⬜ |
| Device-level GPU utilization, temperature, and power in host context | FR-9 | ⬜ |
| Graceful degradation: no GPU/driver/backend → null columns, no failure | FR-8, NFR-4 | ⬜ |
| Jetson/Tegra sysfs backend (integrated NVIDIA, aarch64) | FR-8 | ⬜ deferrable |

## M3 — watch mode + config

The CLI already ships `watch`, `export`, `ls`, and `config` as stubs that exit 2 with a "planned
milestone" message.

| Feature | Spec | Status |
|---|---|---|
| `treehawk watch` — whole-system scan with `--threshold cpu/mem/gpu` (CPU% = % of whole machine) | FR-5 | ⬜ |
| `--match` keyword rules; matched processes carry the keyword as a label | FR-5 | ⬜ |
| Cool-down: keep recording for a period after dropping below thresholds | FR-5 | ⬜ |
| No fixed duration: runs until signal or service shutdown | FR-5b | ⬜ |
| Combined mode: `run --watch-others` | FR-6 | ⬜ |
| TOML config with documented precedence (CLI > env > user > system > defaults) | FR-27 | ⬜ |
| `config init` (commented defaults) and `config show` (effective merged view) | FR-27 | ⬜ |
| `interval "max"` self-tuning under a `max_self_cpu` ceiling | FR-11b | ⬜ |
| `export` to CSV / JSONL | FR-15 | ⬜ |
| `ls` — list recorded sessions | FR-21 | ⬜ |
| `cmdline = "full"` config option with redaction regexes (flag exists since v0.1.2) | FR-30 | 🔶 |

## M4 — event capture

| Feature | Spec | Status |
|---|---|---|
| exec/exit events so sub-tick processes get identity, lifetime, and exit status rows | FR-4, AT-2 | ⬜ |
| eBPF tracepoints via CO-RE (kernel ≥ 5.8) | FR-4, OQ-2 | ⬜ |
| Netlink proc-connector fallback on older kernels | FR-4, OQ-2 | ⬜ |
| Both optional, auto-detected, privilege needs clearly reported | NFR-5 | ⬜ |

## M5 — service + power

The `service` subcommand ships as a stub today.

| Feature | Spec | Status |
|---|---|---|
| `service install/uninstall/status`, system and `--user` units | FR-28 | ⬜ |
| Hardened, self-limiting unit (`ProtectSystem`, `CPUQuota`, `MemoryMax`); sd_notify readiness; clean shutdown flush | FR-28 | ⬜ |
| Session per boot (boot ID in manifest); journald logging | FR-28 | ⬜ |
| Rollover at configurable boundaries; retention cap (`--max-disk`, oldest-first deletion) | FR-18, FR-29 | ⬜ |
| `--power`: RAPL powercap, GPU power (NVML/hwmon), battery, Jetson INA sensors — each labeled by domain, with the documented wall-power underestimate caveat | FR-26 | ⬜ |

## M6 — hardening

| Feature | Spec | Status |
|---|---|---|
| Basic CI: fmt, clippy `-D warnings`, tests, cargo-deny, typos | — | ✅ |
| Prebuilt release binaries for x86_64 + aarch64 (glibc 2.28 baseline) | NFR-6 | ✅ |
| Overhead benchmarks as CI release gates (both reference platforms) | NFR-1, NFR-2, AT-3 | ⬜ |
| 7-day soak: no unbounded memory/fd growth | NFR-3 | ⬜ |
| musl static builds | NFR-6 | ⬜ |
| Documentation pass: semantics of every metric stated | NFR-7 | ⬜ |

## Backlog — unscheduled feature requests and known issues

Collected from the v0.1.1 and v0.1.2 test reports. Not assigned to a milestone yet; several are
natural `report`/M3 companions.

| Item | Source | Status |
|---|---|---|
| argv capture (`--cmdline` flag, off by default, null column without it) | v0.1.1 FR #1 | ✅ v0.1.2 |
| `--out` overwrite protection | v0.1.1 issue 2 | ✅ v0.1.2 |
| Unattributed short-lived CPU shown in `report` (cgroup `cpu.stat`) | v0.1.1 issue 3 | ✅ v0.1.2 |
| SIGKILL loss bounded to one flush window (streaming `.arrows`) | v0.1.1 issue 4 | ✅ v0.1.2 |
| Per-core CPU% display convention | v0.1.1 issue 1 | ✅ v0.1.2 |
| Whole-tree summary row — total tree CPU line exists; peak aggregate RSS missing | v0.1.1 wish | 🔶 |
| Tree-indented `report` view (ppid is already recorded) | v0.1.1 wish | ⬜ |
| `report --json` for scripting | v0.1.1 wish | ⬜ |
| `--kill-orphans` — tear down surviving descendants at exit instead of only warning | v0.1.1 wish | ⬜ |
| ASCII sparkline/timeline per process in `report` | v0.1.1 wish | ⬜ |
| Per-process disk I/O, fd count, network bytes behind flags | v0.1.1 wish, FR-10 | ⬜ |
| Sanitize newlines/tabs in cmdline for `report` display (breaks table alignment) | v0.1.2 finding 1 | ⬜ |
| CPU% peak/p95 can exceed 100% for single-threaded processes (`clk_tck` quantization); smooth or document | v0.1.2 finding 2 | ⬜ |
| Sortable session dir names — `<timestamp>-<short-suffix>` instead of bare UUID | v0.1.2 finding 4 | ⬜ |
| Decide `schema_version` bump policy for additive columns (stayed 1 when `cmdline` landed) | v0.1.2 note | ⬜ |
| Adaptive sampling: reduce rate for idle processes, restore on activity | FR-14 (MAY) | ⬜ |
| Live in-terminal view: `run --live` | FR-20 (MAY) | ⬜ |

Accepted trade-off, tracked as a measurement baseline rather than a task: 1 ms-interval overhead
roughly doubled in v0.1.2 (~25% of one core vs ~13%) as the cost of streaming durability;
default-interval overhead is unchanged (v0.1.2 finding 3).

## Deferred — post-v1

Explicit decisions, recorded 2026-07-17 in [requirements.md §10](requirements.md).

| Item | Decision |
|---|---|
| Python API: `treehawk.load()`, programmatic `run`, streaming callback, PyO3 wheels | FR-24/25, deferred until the core is solid |
| Aggregate per-cgroup rows in watch mode | OQ-1 |
| BMC/IPMI/Redfish wall power | OQ-5 |
| Non-systemd init templates (OpenRC, runit, container packaging) | OQ-6 |
| Container follow-through (`run -- docker run ...`) | OQ-7, NG5 |
| Live remote streaming / push mode | OQ-8 — v1 copies session files off the machine |
| 32-bit armv7 builds | OQ-9 — 64-bit only |
| Target stdout/stderr capture | OQ-4 — rejected, not deferred |

## Keeping this file honest

When a release ships or a test report lands, update the status columns here — this file, the
README roadmap table, and nothing else. New feature requests go into the backlog table with
their source; promote them into a milestone section when scheduled.
