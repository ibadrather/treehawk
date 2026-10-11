---
title: Monitor CPU and memory on Linux and macOS
description: >-
  How treehawk tracks CPU and memory usage on Linux (procfs, cgroups) and macOS including Apple Silicon (libproc), and what differs between them.
keywords:
  - monitor cpu usage macos
  - process memory monitor linux
  - apple silicon cpu monitor
  - cgroup memory usage
---

# Platforms

treehawk runs on **Linux** and on **macOS**, including Apple Silicon. Both read
the kernel directly — no `ps`, no `top`, no third-party library.

What differs is what the kernel will do for you. Linux gives a workload a
cgroup of its own and totals it up. macOS has nothing of the kind: its nearest
equivalent, the coalition, is the whole terminal *app*, not the workload. So
`run` is exact on Linux, and on macOS it is inferred — carefully, and with the
limits stated below.

## What works where

| | Linux | macOS |
|---|---|---|
| source of metrics | `/proc`, cgroup v2 | `libproc`, `sysctl` |
| the boundary a fork cannot escape | cgroup, one per workload | coalition, one per app |
| `tree` rule | yes | yes |
| `session` rule | yes | yes |
| `orphan` rule | yes | under `run` |
| `cgroup` rule | yes | only for a coalition treehawk is not in |
| kernel-side group totals | yes | **no** |
| `run` creates its own boundary | yes | **no** |
| processes born and dying between samples | counted under `run` | not counted |
| fair memory measure (`memory_kind`) | `pss` | `phys_footprint` |
| per-process CPU resolution | `CLK_TCK`, usually 10 ms | nanoseconds |
| other users' processes | visible, without `pss_bytes` | not visible |
| `top`: machine-wide cpu and memory line | yes | **no**, processes only |
| `top` as a service | yes, systemd | **no** |

Anything treehawk cannot measure on your machine is stated in the log header's
`notes` and printed when the run starts, rather than quietly omitted.

## Coalitions: the macOS boundary

On Linux the membership rules rest on one property a forking process cannot
shed: its cgroup. macOS has a property like that too, the **coalition**, and a
process that daemonizes — `fork`, `setsid`, `fork` again, original parent
exits — is indeed still in the coalition it started in.

But a coalition is not the workload. It is the **application**: the terminal
you typed the command in, its shells, treehawk, the workload, and every helper
and XPC service `launchd` starts on that application's behalf. The coalition of
a shell in VS Code holds VS Code, all of its helpers, and a few dozen services
that come and go on their own schedule.

That matters for the `orphan` rule, which adopts a new process that has been
re-parented and sits in a tracked process' boundary. On Linux that describes a
daemonized child. On macOS it also describes every one of those services: new,
parented to PID 1, in the workload's coalition. Before 0.9.0 treehawk adopted
them, and a `run` of three processes could report thirty-eight.

So in a boundary treehawk itself is inside, being there is not evidence. The
rule asks for more — [the run id](#the-run-id) — and without one it adopts
nothing. The `cgroup` rule already worked this way: a boundary treehawk is
inside is broader than the workload and is never adopted wholesale.

A coalition cannot account either. macOS exposes no CPU or memory total for one
without entitlements, so `group_memory_bytes` is always `null` there.

## The run id

`treehawk run` exports one extra variable to the command it starts:

```
TREEHAWK_RUN_ID=7097c761417f4a599f3a36538f7055e0
```

The value is drawn fresh for each run. Everything the command forks inherits
it, through `setsid`, a double fork and re-parenting to PID 1, and nothing
`launchd` starts for the terminal has it. When the `orphan` rule meets a
candidate in a boundary it shares with treehawk, it reads that process'
environment (`KERN_PROCARGS2` on macOS, `/proc/<pid>/environ` on Linux) and
adopts it only if the entry is there.

Only that one entry is looked for. Nothing else in the environment is read
out, kept or written to the log, and the read happens only for a new,
re-parented process in the shared boundary: a handful of times in a run.

Two limits follow from how it works:

- **`watch` has no run id**, because it started nothing. In a shared boundary
  the `orphan` rule is off, and the log header says so: `orphan rule inactive:
  the workload shares its coalition with the terminal`. Children the workload
  spawns normally are still followed by the `tree` rule; one that detaches is
  not. Use `run` when that matters.
- **A detached child can hide or drop the variable.** macOS withholds the
  environment of Apple's own binaries while System Integrity Protection is on,
  so a detached `/bin/sh` or `/bin/sleep` cannot be recognised; and a program
  that starts its daemon with a scrubbed environment (`env -i`, some service
  managers) leaves nothing to find. Such a child is missed rather than guessed
  at.

On Linux `run` normally has a cgroup of its own and never needs the run id. It
matters there with `--no-isolate`, or where there is no systemd to make a scope.

## Why `run` cannot isolate on macOS

`treehawk run` on Linux starts the command with `systemd-run --user --scope`,
which puts it in a fresh cgroup. Placing a process in a *new* coalition needs
entitlements treehawk does not have, so on macOS `run` starts the command in
its own session, with a run id, and nothing more.

That is still the mode to use: nothing is missed before the first sample, the
workload gets a session of its own for the `session` rule to follow, and a
child that detaches can be told from a stranger. It just is not the kernel fact
that `run` is on Linux.

## Containers run somewhere else

`docker compose up`, `docker run`, `podman run` and `nerdctl run` are
**clients**. They ask a container engine to start something and then mostly
wait. The containers are children of the engine, not of the client, and on
macOS the engine is inside Docker Desktop's virtual machine, in a different
coalition altogether.

So a run of `docker compose up` measures the client: a few megabytes and almost
no CPU, whatever the containers are doing. That is a property of the workload
rather than of treehawk, and the summary and `report` say so with a `hint`
line when they see one of these clients among the tracked processes. To measure
the containers, ask the engine (`docker stats`), or on Linux `watch` the
container's own processes.

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
- **`watch` does not follow a process that detaches**, because it has no run
  id to recognise one by. `run` does, with the two exceptions listed under
  [the run id](#the-run-id).
- **Containers are not in the tracked tree.** See [Containers run somewhere
  else](#containers-run-somewhere-else).
- **Processes that live and die between two samples are lost.** On Linux the
  cgroup counter still catches them under `run`; there is no equivalent here.
  Shorten `--interval` if that matters.
- **Only your own processes are visible.** A root-owned process does not appear
  in a scan at all, and another user's refuses both its command line and its
  footprint. This is no obstacle to watching your own workload, but treehawk is
  not a whole-machine monitor on macOS.
- **No per-process swap.** macOS compresses memory rather than swapping it per
  process and exposes no figure for either, so `swap_bytes` is `null`.

To add support for another operating system, see
[Development](development.md#architecture).
