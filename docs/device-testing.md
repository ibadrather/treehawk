# Device testing

How to verify treehawk on the reference hardware: an NVIDIA Jetson (Xavier), a Raspberry Pi 4/5
(64-bit OS), and an x86_64 Ubuntu laptop. Each device runs the same smoke checklist; the
per-device notes cover what differs.

## Install

On every device, the release installer needs no Rust toolchain:

```bash
curl -LsSf https://github.com/ibadrather/treehawk/releases/latest/download/treehawk-installer.sh | sh
```

Binaries are published for `x86_64` and `aarch64` Linux. Releases after v0.1.0 are built
against glibc 2.28, so they run on JetPack 5's Ubuntu 20.04 base; the v0.1.0 binaries need
glibc ≥ 2.35 — on JetPack 5 either use a newer release or build from source:

```bash
cargo install --git https://github.com/ibadrather/treehawk
```

## Pre-flight checks (any device)

```bash
uname -rm                          # kernel ≥ 5.4, aarch64 or x86_64
stat -fc %T /sys/fs/cgroup         # "cgroup2fs" means cgroup v2 is active
```

If cgroup v2 is unavailable (or delegation is not set up), treehawk still works: it prints a
warning and falls back to PID-tree tracking, with the documented reduced guarantee that
daemonizing (double-forking) descendants may be missed. The active mode is recorded as
`tracking_mode` in the session's `session.json` — check it after the first run so you know
which mode you exercised.

## Per-device notes

### Ubuntu laptop (x86_64, Ubuntu 26.04)

Works out of the box: cgroup v2 and systemd user delegation are the defaults, so expect
`tracking_mode: "cgroup"`. This is the fully supported configuration — run the whole checklist
including the overhead smoke check.

### Raspberry Pi 4/5 (64-bit Raspberry Pi OS or Ubuntu)

- Use a 64-bit OS — treehawk ships `aarch64` binaries only.
- Raspberry Pi OS Bookworm and Ubuntu 22.04+ default to cgroup v2; expect cgroup mode under a
  desktop or SSH login session.
- M1 needs only cgroup *membership* (`cgroup.procs`), not the memory/cpu controllers, so no
  `cmdline.txt` changes are required.
- The Pi is the slow-storage reference: after the checklist, re-run the overhead smoke check
  with `--interval 100ms` and confirm `finished.overruns` is 0 in `session.json` (SD-card
  flush stalls must not stall sampling — the writer runs on its own thread).

### Jetson Xavier (JetPack 5, L4T)

- JetPack 5 is Ubuntu 20.04-based with kernel 5.10 — above the 5.4 floor, but it boots with
  the hybrid cgroup v1 layout by default, so treehawk will likely start in PID-tree fallback
  mode. That is a valid test target in itself (it is the documented degraded mode).
- To test cgroup mode, switch the boot arguments to the unified hierarchy: append
  `systemd.unified_cgroup_hierarchy=1` to the `APPEND` line in
  `/boot/extlinux/extlinux.conf` and reboot, then re-check
  `stat -fc %T /sys/fs/cgroup`.
- GPU metrics are not expected — the Jetson sysfs GPU backend is a later milestone (M2);
  GPU columns in the output are null by design.

## Smoke checklist (run on every device)

These mirror the acceptance checks in [TESTING.md](../TESTING.md); `th` is the installed
`treehawk` binary.

```bash
# 1. FR-22 — exit codes pass through
th run --out /tmp/th-exit -- false; echo $?          # prints 1

# 2. AT-1 — the disowned child is recorded
th run --interval 20ms --out /tmp/th-at1 -- bash -c 'sleep 1 & disown; sleep 2'
th report /tmp/th-at1                                 # expect bash + two sleep rows

# 3. AT-4 — SIGKILL crash safety
th run --out /tmp/th-kill -- sleep 60 &
sleep 8 && kill -9 "$(pgrep -x treehawk)"
th report /tmp/th-kill                                # "not finalized", data recovered
pkill -f 'sleep 60'

# 4. Data opens in pandas (skip if python3/pandas is not on the device)
python3 -c "import pandas as pd; print(pd.read_parquet('/tmp/th-at1/samples-00000.parquet'))"

# 5. Overhead smoke — ~50 sleepers at 10 Hz, treehawk should stay ≈ ≤ 1 % of one core
th run --out /tmp/th-load -- bash -c 'for i in $(seq 50); do sleep 60 & done; wait' &
pidstat -p "$(pgrep -x treehawk)" 1 30
```

Record for each device: `uname -rm`, distro/JetPack version, install method,
`tracking_mode` from `session.json`, which checks passed, and `finished.overruns` /
`finished.achieved_rate_hz` from the overhead run. Verbose logs (`th -vv run ...`) are the
first thing to attach when filing a bug.
