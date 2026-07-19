# Treehawk 🦅

**Watches your process tree like a hawk. Run any command and log CPU, RAM & GPU usage of it and all its children.**

[![CI](https://github.com/ibadrather/treehawk/actions/workflows/ci.yml/badge.svg?branch=main&event=push)](https://github.com/ibadrather/treehawk/actions/workflows/ci.yml)

> ⚠️ **Status: M1 released.** `treehawk run` and `treehawk report` work end-to-end; everything
> else is still being built — see [What works today](#what-works-today) and [Roadmap](#roadmap).

---

## What works today

- `treehawk run -- <command>` — runs a command and records it plus every descendant, contained in
  a dedicated cgroup (v2), with automatic PID-tree fallback where cgroups are unavailable.
- CPU and RAM sampling per process, `--interval` from 1 ms to 60 s (default 100 ms / 10 Hz),
  measured at < 1 % of one core at defaults.
- Crash-safe output: rotating Parquet chunks plus a JSON manifest, flushed every ≤ 5 s, readable
  directly with pandas/polars/DuckDB.
- `--label` to tag processes and `--out` to name the session directory.
- `treehawk report <session>` for a terminal summary, and a global `-v`/`-vv` flag for progress
  logging on stderr.

## Not built yet

These appear in the CLI and docs but are placeholders for future releases (see
[Roadmap](#roadmap) for detail):

- **GPU metrics** (M2) — NVML, DRM fdinfo, and Jetson sysfs backends; the schema already reserves
  the columns.
- **Watch mode and config** (M3) — `treehawk watch` with thresholds and keyword matching,
  `treehawk config`, `treehawk ls`, `treehawk export`, and `interval "max"` self-tuning.
- **Event capture** (M4) — eBPF/netlink exec+exit events; today a process must live for about one
  sampling tick to be seen.
- **Service mode and power logging** (M5) — `treehawk service install` (systemd) and `--power`.
- **Hardening** (M6) — overhead budgets as CI release gates, 7-day soak test, musl static builds.
- **Python API** (deferred) — `treehawk.load()` and live streaming; Parquet files are directly
  readable in the meantime.

## Why

You're testing new software — a robotics node, an ML service, a data pipeline — and you need to know how it behaves over hours or days: does memory creep, does CPU spike, does the GPU stay busy, what did that helper process it spawned cost you?

Existing tools don't fit this job. `top`/`htop` are interactive and don't log. `nvidia-smi` only knows NVIDIA GPUs and misses CPU/RAM. Hand-rolled `psutil` scripts are slow, miss short-lived children, and can't see the GPU at all. Full observability stacks (Prometheus & co.) are heavyweight and per-host, not per-process-tree.

Treehawk is one small, fast binary that does exactly this one thing:

```bash
treehawk run -- python3 camera_node.py
```

It launches your command, tracks **it and every process it spawns** — even daemonized ones that detach from the parent — and writes a high-rate, timestamped log of CPU, memory, and GPU usage for each of them. When the run ends, you get a dataset you can open in Python.

## Key features

- **No child escapes.** The target runs in a dedicated cgroup, so descendants are tracked by kernel guarantee, not by guessing from the PID tree. *(Planned — M4: exec/exit event capture, so even millisecond-lived processes are caught; today a process must live for about one sampling tick to be seen.)*
- **CPU & RAM per process today; GPU next.** *(Planned — M2.)* GPU support is vendor-neutral: AMD & Intel via the kernel's DRM fdinfo interface, NVIDIA via NVML, Jetson/Tegra via sysfs. The output schema already reserves the GPU columns, so M2 adds data without breaking existing sessions.
- **Fast and light.** Rust, no runtime deps. Default 10 Hz sampling, configurable up to 1 kHz — or `interval = "max"` to sample as fast as the machine sustains under a self-overhead cap. Budget: < 1 % of one core at defaults; the observer must never disturb the measurement.
- **Watch mode.** *(Planned — M3.)* Alternatively monitor the *whole system* and record processes that exceed thresholds (CPU % means % of the whole machine) or match your keywords (`match = ["camera", "slam"]`). Runs until stopped — no timers.
- **Always-on option.** *(Planned — M5.)* Ships a systemd unit + TOML config: `treehawk service install` and it starts at boot, records for as long as the machine is up, flushes cleanly at shutdown, and starts a fresh session on the next boot — with bounded disk usage.
- **Analysis-ready output.** Sessions are Parquet files + a JSON manifest — open them directly with pandas/polars/DuckDB. No custom API needed to read your data.
- **Keyword labels, not command-line dumps.** Processes are recorded by executable name plus your labels (`--label camera` today; config rules that tag anything matching `*camera*` arrive with M3). Full command lines are opt-in, so tokens and passwords in arguments never end up in logs by accident.
- **Optional power logging.** *(Planned — M5.)* CPU package/DRAM via RAPL, GPU power, battery, and Jetson board sensors (`--power`).

## Installation

Install the latest release with a single command (no Rust toolchain required):

```bash
curl -LsSf https://github.com/ibadrather/treehawk/releases/latest/download/treehawk-installer.sh | sh
```

Prebuilt binaries are available for Linux on x86_64 and aarch64. To build from
source instead, install [Rust](https://rustup.rs) and run:

```bash
cargo install --git https://github.com/ibadrather/treehawk
```

## Quick start

Everything here works today:

```bash
# Track a command and everything it spawns
treehawk run --label camera -- python3 camera_node.py

# Higher rate, named output
treehawk run --interval 10ms --out runs/cam-test -- python3 camera_node.py

# Human-readable summary
treehawk report runs/cam-test

# Verbose progress on stderr (-v info, -vv debug)
treehawk -v run -- python3 camera_node.py
```

Planned interface for later milestones (see [Roadmap](#roadmap)):

```bash
# Watch the whole machine until stopped, log anything heavy or anything matching "camera" (M3)
treehawk watch --threshold cpu=10% mem=1GB --match camera

# Always-on service (M5)
treehawk config init
treehawk service install --user
```

## Analyzing results

Sessions are ordinary Parquet files — no special tooling required:

```python
import pandas as pd
df = pd.read_parquet("runs/cam-test/samples-00000.parquet")
df[df.label == "camera"].plot(x="t", y="cpu_pct")
```

Plus `treehawk report <session>` for a quick terminal summary; `treehawk export --format csv`
arrives with M3 for everything else. A dedicated Python API (`treehawk.load()`, live streaming into dashboards) is planned, but deferred until the core is solid.

## How it works (short version)

| Problem | Approach |
|---|---|
| Track all descendants, no escapes | Dedicated **cgroup v2** per run (PID-tree fallback without privileges) |
| Catch 5 ms-lived processes | **eBPF / netlink** exec+exit events, independent of sampling rate *(planned — M4)* |
| GPU usage per process | **DRM fdinfo** (AMD/Intel), **NVML** (NVIDIA), **sysfs** (Jetson) — the kernel keeps no GPU stats in `/proc`, which is why `top` can't show them *(planned — M2)* |
| High rate, low jitter | Dedicated sampling thread on a monotonic ticker, Rust, zero-copy Arrow batches |
| Crash-safe multi-day logs | Rotating Parquet chunks, flushed every ≤ 5 s, size-capped |

## Requirements

- Linux, kernel ≥ 5.4, cgroup v2 (default on all modern distros; without it treehawk warns and falls back to PID-tree tracking). The planned M4 eBPF capture of short-lived processes will need kernel ≥ 5.8, with a netlink fallback below that
- x86_64 or aarch64 — reference platforms: Intel CPU + NVIDIA discrete GPU, Raspberry Pi 4 (64-bit OS), and Jetson Xavier; see [docs/device-testing.md](docs/device-testing.md)
- Optional, once M2 lands: GPU driver (NVIDIA ≥ R470 / amdgpu / i915 / xe) for GPU metrics. On Raspberry Pi, GPU metrics will be best-effort (v3d fdinfo on recent kernels); CPU/RAM tracking is fully supported today
- No root needed for the core; the planned eBPF event capture and some system-wide metrics need elevated privileges and will be auto-detected

## Performance budget

These are release-gated commitments, measured in CI:

- ≤ 1 % of one CPU core and < 50 MB RSS at 10 Hz tracking 50 processes
- 1 kHz sustained on a small process tree without missed ticks
- 7-day continuous run with zero growth in memory/file descriptors

## Roadmap

Milestone detail lives in [docs/plan.md](docs/plan.md); the full formal spec is
[docs/requirements.md](docs/requirements.md).

| Milestone | Scope | Status |
|---|---|---|
| **M1 — core run mode** | `treehawk run` with cgroup containment (PID-tree fallback), CPU+RAM sampling at up to 1 kHz, crash-safe Parquet output, `treehawk report` | ✅ released |
| **M2 — GPU backends** | `GpuBackend` trait behind one per-process schema: NVML first (per-process memory always; utilization where the driver provides it, null otherwise), then DRM fdinfo (AMD/Intel), Jetson sysfs later | next up |
| **M3 — watch mode + config** | whole-system monitoring with thresholds and `--match` keyword labels, combined `run --watch-others`, TOML config with `config init/show`, `interval = "max"` self-tuning | planned |
| **M4 — event capture** | exec/exit events so sub-tick processes still get identity + lifetime rows: eBPF (kernel ≥ 5.8) with netlink proc-connector fallback, both auto-detected | planned |
| **M5 — service + power** | `treehawk service install` (hardened systemd unit, boot-to-shutdown sessions, rollover + retention) and `--power` via RAPL / NVML / hwmon / battery | planned |
| **M6 — hardening** | overhead budgets as CI release gates, 7-day soak test, musl static builds | planned |
| **Later** | Python package with native bindings + streaming API (Parquet is directly readable in the meantime) | deferred |

## Developing & testing

M1 (`run` + `report`) is implemented in Rust — see [TESTING.md](TESTING.md) for
how to build it, what the test suite covers, and how to run the acceptance and
overhead checks by hand. For verifying releases on the reference hardware
(Jetson Xavier, Raspberry Pi, x86_64 Ubuntu), follow
[docs/device-testing.md](docs/device-testing.md).

## FAQ

**Why not just use `top`/`htop`?** They're interactive viewers, not loggers, and they can't attribute GPU usage (the kernel exposes no GPU accounting in `/proc` — GPU stats live in vendor drivers).

**Why not `nvidia-smi` in a loop?** It's NVIDIA-only, process-spawning per sample (heavy), misses CPU/RAM, and can't follow a process tree. Treehawk links the underlying NVML library directly instead.

**Why Rust?** Predictable low overhead at high sampling rates (no GC), memory safety for week-long runs, and first-class Python bindings via PyO3.

## Contributing

Coding standards, lint policy, and CI setup are adapted from
[uv](https://github.com/astral-sh/uv) by [Astral](https://astral.sh) (MIT OR Apache-2.0) — thanks
to them for keeping exemplary Rust project standards public. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the workflow and [STYLE.md](STYLE.md) for user-facing text
conventions.

## License

Dual-licensed under **MIT OR Apache-2.0**, at your option (the Rust ecosystem convention).
