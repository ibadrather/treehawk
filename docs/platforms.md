---
title: Platforms
---

# Platforms

treehawk runs on **Linux** and on **macOS**, including Apple Silicon. Both read
the kernel directly — no `ps`, no `top`, no third-party library — and the
membership rules work the same way on each.

What differs is how much the kernel is willing to total up for you. That is the
whole of the difference, and it is why `run` is exact on Linux and merely very
good on macOS.

## What works where

| | Linux | macOS |
|---|---|---|
| source of metrics | `/proc`, cgroup v2 | `libproc`, `sysctl` |
| the boundary a fork cannot escape | cgroup | coalition |
| `tree` rule | yes | yes |
| `session` rule | yes | yes |
| `orphan` rule | yes | yes |
| `cgroup` rule | yes | yes, over coalitions |
| kernel-side group totals | yes | **no** |
| `run` creates its own boundary | yes | **no** |
| processes born and dying between samples | counted under `run` | not counted |
| fair memory measure (`memory_kind`) | `pss` | `phys_footprint` |
| per-process CPU resolution | `CLK_TCK`, usually 10 ms | nanoseconds |
| other users' processes | visible, without `pss_bytes` | not visible |

Anything treehawk cannot measure on your machine is stated in the log header's
`notes` and printed when the run starts, rather than quietly omitted.

## Coalitions: the macOS boundary

The membership rules rest on there being *one* property a forking process
cannot shed. On Linux that is the cgroup. On macOS it is the **coalition**.

A process that daemonizes — `fork`, `setsid`, `fork` again, original parent
exits — is re-parented to `launchd` and has left its session, but it is still in
the coalition it started in. Unrelated applications sit in coalitions of their
own. That is exactly the property the `orphan` rule needs, so the
daemonization gap closes on macOS for the same reason it closes on Linux.

What a coalition cannot do is account. macOS exposes no CPU or memory total for
one without entitlements, so `group_memory_bytes` is always `null` there. The
membership half is the useful half.

Everything started from one terminal shares a coalition, which is usually
treehawk's own. That is handled by the rule that was already there: a boundary
treehawk is inside is broader than the workload, so it is never adopted
wholesale. The coalition still informs the `orphan` rule.

## Why `run` cannot isolate on macOS

`treehawk run` on Linux starts the command with `systemd-run --user --scope`,
which puts it in a fresh cgroup. Placing a process in a *new* coalition needs
entitlements treehawk does not have, so on macOS `run` starts the command in
its own session and nothing more.

That is still worth using over `watch`: nothing is missed before the first
sample, and the workload gets a session of its own for the `session` rule to
follow. It just is not the kernel fact that `run` is on Linux.

## Memory: PSS and phys_footprint

Every platform can report resident memory, and summing RSS over a fork tree
counts each shared page once per child. What each offers instead differs:

- **Linux** has **PSS**, which divides each shared page among the processes
  mapping it. Read from `smaps_rollup`, which needs same-user access.
- **macOS** has **`phys_footprint`**, the kernel's own charge for what a
  process costs the machine — the number Activity Monitor shows. There is no
  PSS on macOS.

Both land in the log's `pss_bytes`, and the header's **`memory_kind`** says
which one it is: `pss` or `phys_footprint`. The dashboard, the report and the
PDF read that field and label the column accordingly, so a figure is never
presented as something it is not. A log written before the field existed is
`pss` — treehawk was Linux-only then.

## CPU on Apple Silicon

`libproc` reports task CPU time in **mach absolute units**, not nanoseconds.
The conversion comes from `mach_timebase_info`, and it is **125/3 on Apple
Silicon** where an Intel Mac reports 1/1 — so reading the raw number as
nanoseconds would understate CPU time by about 41x.

treehawk converts, and reports `clk_tck` as `1000000000` on macOS with
`cpu_ticks` in nanoseconds. That keeps `cpu_ticks / clk_tck` equal to seconds
exactly as it is on Linux, and keeps the resolution that POSIX's `SC_CLK_TCK`
of 100 would have rounded away to 10 ms.

Apple Silicon also uses **16 KiB pages** where x86 uses 4 KiB. treehawk reports
the real page size in the log header rather than assuming either.

## What macOS cannot do

Stated plainly, so nothing here is a surprise in the middle of a run:

- **No kernel-side group totals.** `group_memory_bytes` and
  `group_memory_peak_bytes` are always `null`, and the workload's CPU total is
  summed from its processes rather than read from a counter.
- **No isolation under `run`**, for the reason above.
- **Processes that live and die between two samples are lost.** On Linux the
  cgroup counter still catches them under `run`; there is no equivalent here.
  Shorten `--interval` if that matters.
- **Only your own processes are visible.** A root-owned process does not appear
  in a scan at all, and another user's refuses both its command line and its
  footprint. This is no obstacle to watching your own workload, but treehawk is
  not a whole-machine monitor on macOS.
- **No per-process swap.** macOS compresses memory rather than swapping it per
  process and exposes no figure for either, so `swap_bytes` is `null`.

## Adding another OS

Implement `ProcessSource` and `HostInfoSource` — and optionally
`GroupMetricSource` and `ProcessLauncher` — under `platforms/<os>/`, then
register a builder in `platforms/registry.py`. Nothing in `core/` changes;
macOS was added exactly that way.

A builder that can start processes fills two fields: `Platform.launcher`, which
`run` uses to start the command inside a boundary, and
`Platform.direct_launcher`, which `run --no-isolate` uses. Leave the second out
and `--no-isolate` fails with "this platform cannot start processes". Where the
OS cannot create a boundary, pass the same launcher to both, as macOS does.

Two things are worth copying from it:

- **Make the syscall boundary an injectable protocol.** The Linux readers take
  their root directory as an argument, which is what lets the tests point them
  at a fake `/proc`. macOS has no directory to point at, so
  `platforms/darwin/libproc.py` defines a `ProcessTable` protocol and the tests
  substitute a fake one. Constructors require that boundary rather than
  defaulting to the real one (`DarwinProcessSource(table)`,
  `ScopeLauncher(cgroups, proc_root=...)`), so the builder is the only place a
  real backend is made. The backend is therefore testable on a machine that
  does not run that OS.
- **Bind the platform library inside the builder, not at import.** The module
  then imports and type-checks everywhere. A `sys.platform` guard around the
  module body would make mypy skip the code entirely on the other runner.
