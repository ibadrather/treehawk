# Treehawk 🦅

**Watches your process tree like a hawk. Run any command and log CPU, RAM & GPU usage of it and all its children.**

[![CI](https://github.com/ibadrather/treehawk/actions/workflows/ci.yml/badge.svg?branch=main&event=push)](https://github.com/ibadrather/treehawk/actions/workflows/ci.yml)

> ⚠️ **Status: M1 usable.** `treehawk run` (cgroup tracking, CPU+RAM at up to 1 kHz, crash-safe Parquet output) and `treehawk report` work end-to-end. GPU metrics, watch mode, event capture, and service mode are still being built — see [Roadmap](#roadmap). Nothing is released yet.

---

## Why

You're testing new software — a robotics node, an ML service, a data pipeline — and you need to know how it behaves over hours or days: does memory creep, does CPU spike, does the GPU stay busy, what did that helper process it spawned cost you?

Existing tools don't fit this job. `top`/`htop` are interactive and don't log. `nvidia-smi` only knows NVIDIA GPUs and misses CPU/RAM. Hand-rolled `psutil` scripts are slow, miss short-lived children, and can't see the GPU at all. Full observability stacks (Prometheus & co.) are heavyweight and per-host, not per-process-tree.

Treehawk is one small, fast binary that does exactly this one thing:

```bash
treehawk run -- python3 camera_node.py
```

It launches your command, tracks **it and every process it spawns** — even daemonized ones that detach from the parent — and writes a high-rate, timestamped log of CPU, memory, and GPU usage for each of them. When the run ends, you get a dataset you can open in Python.

## Key features

- **No child escapes.** The target runs in a dedicated cgroup, so descendants are tracked by kernel guarantee, not by guessing from the PID tree. Short-lived processes (milliseconds) are caught via exec/exit events.
- **CPU, RAM, GPU per process.** GPU support is vendor-neutral: AMD & Intel via the kernel's DRM fdinfo interface, NVIDIA via NVML, Jetson/Tegra via sysfs.
- **Fast and light.** Rust, no runtime deps. Default 10 Hz sampling, configurable up to 1 kHz — or `interval = "max"` to sample as fast as the machine sustains under a self-overhead cap. Budget: < 1 % of one core at defaults; the observer must never disturb the measurement.
- **Watch mode.** Alternatively monitor the *whole system* and record processes that exceed thresholds (CPU % means % of the whole machine) or match your keywords (`match = ["camera", "slam"]`). Runs until stopped — no timers.
- **Always-on option.** Ships a systemd unit + TOML config: `treehawk service install` and it starts at boot, records for as long as the machine is up, flushes cleanly at shutdown, and starts a fresh session on the next boot — with bounded disk usage.
- **Analysis-ready output.** Sessions are Parquet files + a JSON manifest — open them directly with pandas/polars/DuckDB. No custom API needed to read your data.
- **Keyword labels, not command-line dumps.** Processes are recorded by executable name plus your labels (`--label camera`, or config rules that tag anything matching `*camera*`). Full command lines are opt-in, so tokens and passwords in arguments never end up in logs by accident.
- **Optional power logging.** CPU package/DRAM via RAPL, GPU power, battery, and Jetson board sensors (`--power`).

## Quick start (planned interface)

```bash
# Track a command and everything it spawns
treehawk run --label camera -- python3 camera_node.py

# Higher rate, named output
treehawk run --interval 10ms --out runs/cam-test -- python3 camera_node.py

# Watch the whole machine until stopped, log anything heavy or anything matching "camera"
treehawk watch --threshold cpu=10% mem=1GB --match camera

# Human-readable summary
treehawk report runs/cam-test

# Always-on service
treehawk config init
treehawk service install --user
```

## Analyzing results

Sessions are ordinary Parquet files — no special tooling required:

```python
import pandas as pd
df = pd.read_parquet("runs/cam-test/samples.parquet")
df[df.label == "camera"].plot(x="t", y="cpu_pct")
```

Plus `treehawk report <session>` for a quick terminal summary and `treehawk export --format csv` for everything else. A dedicated Python API (`treehawk.load()`, live streaming into dashboards) is planned, but deferred until the core is solid.

## How it works (short version)

| Problem | Approach |
|---|---|
| Track all descendants, no escapes | Dedicated **cgroup v2** per run (PID-tree fallback without privileges) |
| Catch 5 ms-lived processes | **eBPF / netlink** exec+exit events, independent of sampling rate |
| GPU usage per process | **DRM fdinfo** (AMD/Intel), **NVML** (NVIDIA), **sysfs** (Jetson) — the kernel keeps no GPU stats in `/proc`, which is why `top` can't show them |
| High rate, low jitter | Dedicated sampling thread on a monotonic ticker, Rust, zero-copy Arrow batches |
| Crash-safe multi-day logs | Rotating Parquet chunks, flushed every ≤ 5 s, size-capped |

## Requirements

- Linux, kernel ≥ 5.4, cgroup v2 (default on all modern distros). eBPF capture of short-lived processes needs kernel ≥ 5.8; older kernels fall back to the netlink proc connector
- x86_64 or aarch64 — reference platforms: Intel CPU + NVIDIA discrete GPU, and Raspberry Pi 4 (64-bit OS)
- Optional: GPU driver (NVIDIA ≥ R470 / amdgpu / i915 / xe) for GPU metrics. On Raspberry Pi, GPU metrics are best-effort (v3d fdinfo on recent kernels); CPU/RAM tracking is fully supported
- No root needed for the core; eBPF event capture and some system-wide metrics need elevated privileges and are auto-detected

## Performance budget

These are release-gated commitments, measured in CI:

- ≤ 1 % of one CPU core and < 50 MB RSS at 10 Hz tracking 50 processes
- 1 kHz sustained on a small process tree without missed ticks
- 7-day continuous run with zero growth in memory/file descriptors

## Roadmap

1. **M1** — `run` mode: cgroup tracking, CPU+RAM, Parquet output, `report` ✅
2. **M2** — GPU backends: NVML first (reference hardware), then DRM fdinfo; Jetson later
3. **M3** — `watch` mode + config file
4. **M4** — eBPF/netlink event capture for short-lived processes
5. **M5** — systemd service mode (boot-to-shutdown recording) + power metrics
6. **M6** — hardening: overhead benchmarks in CI, multi-day soak test
7. **Later** — Python package with native bindings + streaming API (Parquet is directly readable in the meantime)

## Developing & testing

M1 (`run` + `report`) is implemented in Rust — see [TESTING.md](TESTING.md) for
how to build it, what the test suite covers, and how to run the acceptance and
overhead checks by hand.

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

Dual-licensed under **MIT OR Apache-2.0**, at your option (the Rust ecosystem convention). Currently developed internally; planned to be open-sourced.
