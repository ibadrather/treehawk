# Treehawk — Formal Requirements Specification

**Version:** 0.2
**Status:** Accepted (all open questions resolved 2026-07-17, see §10)
**Tool name:** Treehawk — *it watches the process tree like a hawk*
**Target platform:** Linux (x86_64, aarch64 — 64-bit only, see OQ-9), kernel ≥ 5.4 (eBPF event capture requires ≥ 5.8, see OQ-2), cgroup v2
**Reference platforms:** (1) x86_64, Intel CPU + NVIDIA discrete GPU; (2) Raspberry Pi 4 with 64-bit OS (aarch64, CPU/RAM focus)
**License:** MIT OR Apache-2.0 (dual). Internal during initial development; to be open-sourced later.

> **Naming note (checked 2026-07-15):** web/GitHub search found no existing software
> project, CLI, package, or dev-tool company named "Treehawk" (only unrelated
> non-software uses). Earlier candidates were rejected due to collisions: Prowl
> (macOS agent orchestrator with a `prowl` CLI, prowlapp.com clients, Prowler cloud
> security), Remora (TACC's REMORA — an existing command-wrapping resource monitor),
> Stoat (Revolt chat rebrand, stoat.dev), Saluki (DataDog telemetry toolkit), Holter
> (holter.sh uptime monitoring), Windhover (Windhover Labs drone software). Before
> first publication, re-verify and reserve the name on github.com, pypi.org, and
> crates.io, and register `treehawk` as the binary/package name in all three.

---

## 1. Purpose

Treehawk is a lightweight command-line observability tool for measuring the long-running
resource behavior (CPU, RAM, GPU, and optionally power) of software under test on Linux. It is designed for
engineers validating new software (e.g. robotics nodes, ML services) over hours or days,
where existing tools (`top`, `htop`, `nvidia-smi`) are interactive-only, lossy, or too
heavy to leave running.

Treehawk produces machine-readable time-series logs (Parquet) that are directly
readable from Python for analysis and dashboards; a dedicated Python API is
planned but deferred (see 5.6).

## 2. Goals

- G1: Track the complete resource footprint of a launched command **including every
  descendant process it spawns**, with no escapes.
- G2: Optionally observe the whole system and record only processes that cross
  configurable resource thresholds.
- G3: Sample at the highest practical rate with strictly bounded overhead.
- G4: Produce durable, structured, analysis-friendly output.
- G5: Be trivially usable: one binary, sane defaults, good `--help`.
- G6: Support GPUs from any vendor through pluggable backends, and run
  permanently as a configured system service when desired.

## 3. Non-Goals

- NG1: Not a distributed/cluster monitoring system (single host only).
- NG2: Not an APM / code-level profiler (no stack sampling, no flamegraphs in v1).
- NG3: Not a real-time alerting system (logs and reports only in v1).
- NG4: No Windows/macOS support in v1.
- NG5: Containerized targets are out of scope for v1: Treehawk tracks host
  processes. Following a `docker run` into a container's namespace is deferred
  (see OQ-7).

## 4. Terminology

- **Run mode** — Treehawk launches a target command and tracks it plus all descendants.
- **Watch mode** — Treehawk observes all processes on the system and records those
  exceeding thresholds.
- **Session** — one invocation of Treehawk producing one output dataset.
- **Sample** — one snapshot of one process's metrics at one timestamp.
- **Label** — a short user-defined keyword (e.g. `camera`) attached to processes
  via `--label` in run mode or match rules in config, used to identify and group
  processes without recording their full command lines.

## 5. Functional Requirements

### 5.1 Process tracking

- FR-1: In run mode, Treehawk SHALL launch the target command exactly as given
  (`treehawk run -- python3 camera_node.py --arg x`) preserving arguments, environment,
  working directory, stdio, exit code, and signal forwarding (SIGINT/SIGTERM).
- FR-2: Treehawk SHALL track all descendant processes of the target, including those
  created via fork, vfork, clone, posix_spawn, and processes that daemonize
  (double-fork / reparent to PID 1).
- FR-3: Descendant containment SHALL be implemented by placing the target in a
  dedicated cgroup v2, so membership is kernel-guaranteed rather than inferred from
  the PID tree. When cgroup delegation is unavailable, Treehawk SHALL fall back to
  PID-tree tracking and print a clear warning about reduced guarantees.
- FR-4: Treehawk SHALL detect process creation and exit via an event mechanism
  (eBPF `sched_process_exec`/`exit` tracepoints, or the netlink proc connector as
  fallback) so that processes shorter than one sampling interval are still recorded
  with at least their identity, lifetime, and exit status.
- FR-5: In watch mode (`treehawk watch`), Treehawk SHALL scan all system processes
  and record any process that (a) exceeds user-defined resource thresholds
  (e.g. `--threshold cpu=5% mem=2% gpu=5%`) and/or (b) matches a configured
  keyword rule (e.g. `--match camera`, or `match = ["camera", "slam"]` in
  config; matched against executable name). Matched processes carry their
  keyword as a label in the output. A process SHALL keep being recorded for a
  configurable cool-down period after dropping below thresholds, so bursts
  produce contiguous data. CPU thresholds are interpreted as percent of total
  machine capacity (100% = all cores busy: a process saturating one core of a
  four-core machine reads 25%).
- FR-5b: Watch mode SHALL have no fixed duration: it runs until stopped by
  signal or service shutdown. Long-run semantics are boot-to-shutdown when
  deployed as a service (FR-28); a `--duration` limit is not required.
- FR-6: Run mode and watch mode SHALL be combinable in one session (track a command
  and additionally record any other heavy process on the machine).

### 5.2 Metrics

- FR-7: Per process and per sample, Treehawk SHALL collect at minimum:
  - identity: PID, PPID, cgroup, executable path, full command line, UID, start time;
  - CPU: raw CPU-time deltas (utime+stime), from which both per-core
    utilization (default presentation, matching `top`) and machine-normalized
    utilization are derivable; user/system split, number of
    threads, voluntary/involuntary context switches;
  - memory: RSS, PSS (when readable), swap, virtual size;
  - GPU (per process, per device): compute/SM utilization, encoder/decoder
    utilization where available, dedicated GPU memory used.
- FR-8: GPU support SHALL be vendor-neutral, implemented as pluggable backends
  behind one common per-process GPU metric schema:
  - **DRM fdinfo** (`/proc/<pid>/fdinfo`, the kernel's standardized per-process
    GPU stats interface) SHALL be the primary, generic backend. It covers AMD
    (amdgpu), Intel (i915/xe), and any other driver implementing the DRM
    client-stats spec.
  - **NVML** SHALL be used for NVIDIA discrete GPUs (including
    `nvmlDeviceGetProcessUtilization`), linked directly as a library — never by
    shelling out to `nvidia-smi`, which is merely a CLI frontend to NVML.
  - **Jetson/Tegra** (integrated NVIDIA on aarch64) MAY be supported later via
    its sysfs interfaces, since it exposes neither fdinfo nor full NVML.
  - Implementation priority follows the reference platforms: NVML first, DRM
    fdinfo second. On Raspberry Pi (VideoCore/v3d), per-process GPU stats are
    best-effort via fdinfo on recent kernels; CPU/RAM tracking is unaffected.
  - Backends SHALL be auto-detected per device; absence of a GPU, driver, or
    backend SHALL degrade gracefully (GPU columns null, no failure), and multiple
    GPUs of different vendors in one machine SHALL work simultaneously.
  - *Rationale:* the kernel keeps no GPU accounting in `/proc` scheduler/memory
    stats, which is why `top` cannot show GPU usage; GPU data exists only in
    vendor driver interfaces, so these backends are the only route to it.
- FR-9: Treehawk SHALL additionally record per-sample host context: total CPU
  utilization, total memory pressure (PSI when available), per-GPU global
  utilization, temperature, and power draw where exposed. This allows normalizing
  process behavior against machine load. Host context SHALL be recorded at every
  tick even when no process currently qualifies for recording, so every session
  contains a continuous machine baseline.
- FR-10: Optional metrics behind flags (off by default to protect overhead budget):
  per-process disk I/O (`/proc/<pid>/io`), open file-descriptor count, and network
  bytes where obtainable.
- FR-26 (optional, `--power` / `power = true` in config): Treehawk SHOULD record
  host power draw as per-sample host context, from whatever sources the machine
  exposes, each labeled by measurement domain rather than presented as a single
  "total" figure:
  - CPU package and DRAM energy via RAPL (`/sys/class/powercap`, Intel and modern
    AMD);
  - GPU power via NVML (`nvmlDeviceGetPowerUsage`) or hwmon (amdgpu);
  - battery discharge rate via `/sys/class/power_supply` (laptops);
  - board input power via onboard INA sensors on Jetson (hwmon) — on such
    platforms this IS effectively total system power.
  Documentation SHALL state clearly that on ordinary desktops/servers RAPL+GPU
  underestimates wall power (PSU losses, peripherals); true wall power requires
  an external meter or BMC (see OQ-5). Absence of all sources SHALL degrade
  gracefully.

### 5.3 Sampling

- FR-11: The sampling interval SHALL be configurable from 1 ms to 60 s
  (`--interval 100ms`). Default: 100 ms (10 Hz). There is no separate service
  default: the config file value applies, and `treehawk config init` writes an
  explicit, commented value.
- FR-11b (`max` rate): `interval = "max"` SHALL make Treehawk sample at the
  highest rate it can sustain subject to a self-overhead ceiling
  (`max_self_cpu`, default 5% of one core): the sampler measures its own cost
  and adjusts the rate to stay under the ceiling. The achieved effective rate
  SHALL be recorded in the session manifest.
- FR-12: Treehawk SHALL timestamp every sample with CLOCK_MONOTONIC (for deltas) and
  record the CLOCK_REALTIME anchor once per session (for wall-clock alignment).
- FR-13: If a sampling cycle overruns the interval, Treehawk SHALL skip to the next
  aligned tick (no unbounded queueing) and count overruns in session metadata.
- FR-14: An adaptive mode MAY reduce the sampling rate for processes that have been
  idle for a configurable time, restoring full rate on activity.

### 5.4 Output and logging

- FR-15: Treehawk SHALL write samples to an append-only, crash-safe on-disk format.
  Primary format: Apache Parquet written in rotating chunks (columnar, compressed,
  directly readable by pandas/polars/DuckDB). Secondary format for debugging and
  piping: line-delimited JSON (`--format jsonl`) and CSV export.
- FR-16: A session SHALL be self-describing: a `session.json` manifest containing
  the exact command, environment hash, host info (kernel, CPU model, GPU model,
  driver versions), Treehawk version, sampling config, and clock anchors.
- FR-17: Data SHALL be flushed at a bounded interval (default 1 s) so that a
  crash of the target, the host, or Treehawk itself loses at most that window.
- FR-18: Treehawk SHALL support log rotation and a retention cap (`--max-disk`)
  for multi-day runs. Defaults: run mode writes to `./treehawk/<uuid>`
  (override with `--out`); service mode writes to `/var/lib/treehawk` (system)
  or `~/.local/share/treehawk` (user) with a 1 GB default cap enforced by
  deleting oldest sessions first — deliberately conservative for SD-card
  devices such as the Raspberry Pi.
- FR-19: `treehawk report <session>` SHALL print a human-readable summary: per-process
  peak/mean/p95 CPU, peak RSS, peak GPU memory, lifetime, exit codes, and total
  session statistics.
- FR-20: Live view: `treehawk run --live -- <cmd>` MAY render a minimal in-terminal
  table (top consumers in the tracked set), without affecting logging.

### 5.5 CLI

- FR-21: The CLI SHALL follow standard conventions: subcommands
  (`run`, `watch`, `report`, `export`, `ls`, `config`, `service`), `--help` on
  every level, `--version`, meaningful exit codes, and colored output only when
  attached to a TTY.
- FR-22: In run mode, Treehawk SHALL exit with the target's exit code so it can wrap
  commands transparently in CI pipelines.
- FR-23: Representative invocations:

```
treehawk run -- python3 camera_node.py
treehawk run --interval 10ms --out ./runs/cam-test -- python3 camera_node.py
treehawk run --label camera -- python3 camera_node.py
treehawk watch --threshold cpu=10% gpu=5% mem=1GB --match camera
treehawk run --watch-others --threshold cpu=20% -- ./stress_test.sh
treehawk run --power -- python3 camera_node.py
treehawk report runs/cam-test
treehawk export runs/cam-test --format csv
treehawk config init                # write commented default config
treehawk service install --user     # enable always-on watch mode via systemd
treehawk service status
```

### 5.6 Python API (DEFERRED — post-v1)

> Deprioritized by decision 2026-07: the core tool comes first. Until this
> section is implemented, analysis relies on the Parquet output being directly
> readable (`pandas.read_parquet` / polars / DuckDB), `treehawk report`, and
> CSV export — which is sufficient for dashboards that read session files.

- FR-24: A Python package (`pip install treehawk`) SHALL provide:
  - `treehawk.load(path) -> Session` — lazy access to samples as pandas or polars
    DataFrames (`session.samples`, `session.processes`, `session.host`);
  - `treehawk.run(cmd, interval=..., on_sample=None) -> Session` — programmatic
    launching of a monitored command, with an optional streaming callback for
    live dashboards;
  - convenience analytics: `session.summary()`, `session.timeline(pid)`,
    `session.peaks()`.
- FR-25: Bindings SHALL be native (PyO3/maturin wheels for manylinux, x86_64 and
  aarch64), not subprocess wrappers, so streaming access has low latency. Reading
  completed sessions SHALL also work with zero native code (plain Parquet).

### 5.7 Configuration and service mode

- FR-27 (config file): All options settable via CLI flags SHALL also be settable
  in a TOML configuration file. Lookup order: path given via `--config`, then
  `~/.config/treehawk/config.toml` (per user), then `/etc/treehawk/config.toml`
  (system-wide). Precedence: CLI flags > environment variables (`TREEHAWK_*`) >
  user config > system config > built-in defaults. `treehawk config init` SHALL
  generate a fully commented default config; `treehawk config show` SHALL print
  the effective merged configuration and where each value came from.
- FR-28 (systemd service): Treehawk SHALL ship a systemd unit as part of the
  installation (packages and `treehawk service install`), so it can run
  permanently in watch mode configured entirely from the config file — starting
  at boot and recording until shutdown, which is the intended "long run" model:
  - `treehawk service install|uninstall|status` SHALL install/enable, remove, and
    report the unit (`treehawk.service`), supporting both system units and user
    units (`--user`, no root required);
  - the service SHALL integrate properly with systemd: start at boot
    (`WantedBy=multi-user.target`), `Type=notify` readiness via sd_notify, clean
    SIGTERM shutdown at system shutdown with a final flush so the tail of the
    session survives, structured logs to journald, `Restart=on-failure`;
  - each boot SHALL begin a new session (boot ID recorded in the manifest);
  - the shipped unit SHALL apply hardening and self-limiting directives
    (e.g. `ProtectSystem=strict` with explicit output path, `CPUQuota`,
    `MemoryMax`) so the observer itself is provably bounded;
  - session output directories and retention (FR-18) SHALL be config-driven so
    an always-on service produces rotating, bounded, dated session datasets.
- FR-29 (service data lifecycle): When running as a service, Treehawk SHALL roll
  over to a new session at a configurable boundary (e.g. daily or max size) so
  that long-lived deployments yield analyzable, bounded session files rather
  than one endless session.

### 5.8 Privacy of recorded data

- FR-30 (identity & secret hygiene): The default recorded identity of a process
  SHALL be its executable basename plus any labels (from `--label` or match
  rules) — not its full command line. Full command-line recording SHALL be
  opt-in (the `--cmdline` flag; `cmdline = "full"` once config lands), with a
  configurable list of redaction regexes
  applied before writing to disk. Process environment variables SHALL NOT be
  recorded. Rationale: command lines routinely contain secrets, and keywords
  ("camera") identify a process for analysis just as well.

## 6. Non-Functional Requirements

- NFR-1 (overhead): At the default 10 Hz tracking ≤ 50 processes, Treehawk's own CPU
  usage SHALL be < 1% of one core, and RSS < 50 MB. At 100 Hz / 50 processes it
  SHALL stay < 5% of one core. On Raspberry Pi 4 class hardware (Cortex-A72),
  the 10 Hz default SHALL cost < 5% of one core. Overhead SHALL be measured and
  published per release for both reference platforms.
- NFR-2 (rate): On reference hardware (4-core x86_64), Treehawk SHALL sustain 1 kHz
  sampling of a single process tree of ≤ 10 processes without missed ticks.
- NFR-3 (endurance): A 7-day continuous session SHALL show no unbounded growth in
  Treehawk's memory or file-descriptor usage.
- NFR-4 (robustness): Death of the target, races on short-lived PIDs, unreadable
  `/proc` entries, and GPU driver absence SHALL never crash a session.
- NFR-5 (privileges): Core functionality SHALL work as an unprivileged user for
  the user's own processes. Features requiring elevation (eBPF, system-wide PSS,
  netlink connector) SHALL be optional, auto-detected, and clearly reported.
- NFR-6 (distribution): Shipped as a single static binary (musl) plus Python
  wheels; no runtime dependencies beyond glibc/musl and optional GPU drivers.
- NFR-7 (accuracy): CPU utilization derived from jiffy deltas SHALL be exact with
  respect to kernel accounting. All CPU percentages presented by Treehawk
  (thresholds, report, defaults in exported data) use the per-core convention
  (100% = one core busy, as in `top`, so multi-core processes exceed 100%);
  machine-normalized utilization remains derivable from the stored raw
  counters. Documentation SHALL state the semantics of every metric.
- NFR-8 (compatibility): Output schema SHALL be versioned; the Python API SHALL
  read all prior schema versions.

## 7. Architecture (informative)

- Language: Rust. CLI via `clap`; async runtime not required on the hot path —
  a dedicated sampling thread with a monotonic ticker keeps jitter low.
- Data sources: `/proc/<pid>/stat`, `status`, `smaps_rollup`; cgroup v2 stats for
  the tracked tree; DRM fdinfo (generic), NVML (`nvml-wrapper`, NVIDIA discrete),
  and Tegra sysfs (Jetson) for GPU; RAPL powercap, hwmon, and power_supply for
  power; eBPF (`aya` crate) or netlink proc connector for exec/exit events.
- Writer: double-buffered sample batches handed to a writer thread; Arrow in
  memory, Parquet chunks on disk (`arrow-rs`/`parquet` crates).
- Python: PyO3 + maturin; DataFrame interop via Arrow so no copies are needed.

## 8. Milestones (suggested)

1. **M1 — Core run mode:** cgroup containment, CPU+RSS sampling, Parquet output,
   `report`. Usable end-to-end.
2. **M2 — GPU backends:** DRM fdinfo (AMD/Intel) + NVML (NVIDIA) behind one
   schema; host GPU context; Jetson backend if hardware available.
3. **M3 — Watch mode + config:** thresholds, cool-down, combined mode; TOML
   config file with `config init/show`.
4. **M4 — Events:** eBPF/netlink exec-exit capture of short-lived processes.
5. **M5 — Service & power:** systemd units with `service install`,
   boot-to-shutdown sessions, rollover, optional power metrics
   (RAPL/hwmon/battery).
6. **M6 — Hardening:** overhead benchmarks in CI, multi-day soak test, docs.
7. **Later — Python API:** native bindings, wheels, streaming callback for live
   dashboards (5.6).

## 9. Acceptance Tests (samples)

- AT-1: `treehawk run -- bash -c 'sleep 1 & disown; sleep 2'` records the disowned
  child despite reparenting.
- AT-2: A target spawning 100 children of 5 ms each at 10 Hz sampling still yields
  100 process records with lifetimes and exit codes (via FR-4 events).
- AT-3: Treehawk's self-CPU measured by an external observer meets NFR-1.
- AT-4: Killing Treehawk with SIGKILL mid-run leaves a readable dataset missing at
  most the last flush window (FR-17).
- AT-5: `treehawk.load()` on a 24 h / 10 Hz / 30-process session opens in < 2 s and
  filtering by PID returns correct series.

## 10. Resolved Questions

All open questions were resolved on 2026-07-17. The original question text is
kept for context; the numbering (OQ-n) remains valid for cross-references
elsewhere in this document.

- OQ-1: Should watch mode also record *aggregate* per-cgroup usage (containers,
  systemd services) in addition to per-process rows?
  **Decision: deferred post-v1.** Per-process rows plus the host baseline
  (FR-9) cover the stated use cases; the versioned schema (NFR-8) allows adding
  aggregate rows later without breakage.
- OQ-2: Minimum supported kernel — 5.4 (broad) vs. 5.8+ (simpler eBPF via CO-RE)?
  **Decision: split floor.** The sampling/cgroup core supports ≥ 5.4; the eBPF
  event feature (FR-4, M4) requires ≥ 5.8 (CO-RE) and auto-falls-back to the
  netlink proc connector on older kernels — a fallback FR-4 requires anyway.
- OQ-3: Per-process GPU utilization on NVIDIA requires driver accounting support;
  define fallback semantics (attribute device-level utilization proportionally?).
  **Decision: record null, never estimate.** When the driver cannot provide
  per-process utilization, that column is null; per-process GPU memory and
  device-level utilization (host context) are still recorded. No fabricated
  proportional attribution.
- OQ-4: Should `treehawk run` optionally capture target stdout/stderr into the
  session for correlation with resource spikes?
  **Decision: no.** The goal is capturing usage; target stdio is inherited
  untouched (FR-1) and output capture is out of scope.
- OQ-5: True wall-socket power on desktops/servers: support BMC/IPMI or Redfish
  sensor polling as an optional power source, or declare external meters out of
  scope for v1?
  **Decision: out of scope for v1.** RAPL/hwmon/battery sources only (FR-26),
  with the documented underestimate caveat.
- OQ-6: Which non-systemd init systems (if any) warrant service templates
  (OpenRC, runit, Docker container deployment as an alternative "always-on"
  packaging)?
  **Decision: none in v1.** systemd only (FR-28); others deferred until
  demand exists.
- OQ-7: Container follow-through: should `treehawk run -- docker run ...` track
  processes inside the container (requires crossing cgroup/PID namespaces), and
  is that v2 material?
  **Decision: v2 material, deferred** — consistent with NG5.
- OQ-8: Data hand-off to the future dashboard: v1 assumes session files are
  copied off the test machine; is a push/serve mode (live remote streaming)
  needed later?
  **Decision: deferred.** v1 keeps the copy-files-off model; revisit alongside
  the Python API (5.6).
- OQ-9: Is a 32-bit armv7 build needed? Pending check of the Pi's OS
  (`uname -m`: `aarch64` = covered, `armv7l` = new build target required).
  **Decision: no.** 64-bit only (x86_64 + aarch64); the reference Pi 4 runs a
  64-bit OS. armv7 stays deferred unless a concrete need appears.