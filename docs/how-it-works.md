# How it works

## Membership

A process joins the workload when the matcher selects it, or when one of four
rules adopts it. It then stays a member until **that exact process** (PID plus
start time) exits. Losing its parent or its session changes nothing.

![The matcher seeds the tracker; four rules adopt more processes](assets/diagrams/membership-rules.light.svg#only-light)
![The matcher seeds the tracker; four rules adopt more processes](assets/diagrams/membership-rules.dark.svg#only-dark)

| Rule | Catches |
|---|---|
| `tree` | children and grandchildren |
| `cgroup` | anything in a control group the workload owns, the one boundary a fork cannot escape |
| `session` | children re-parented away that kept the session |
| `orphan` | a new process that lost its parent and sits in a tracked process' cgroup |

A cgroup is adopted only if treehawk is not inside it and every process in it is
already tracked, so your login session is never swallowed. The `orphan` rule spots
re-parenting by noticing that the adopting reaper lives in a *different* cgroup.

Each process in the log records the rule that found it in `via`. The dashboard and
PDF colour processes by it: matched (blue), child (orange), detached (green).

## run and watch

![run gives the command its own cgroup; watch infers membership](assets/diagrams/run-vs-watch.light.svg#only-light)
![run gives the command its own cgroup; watch infers membership](assets/diagrams/run-vs-watch.dark.svg#only-dark)

`run` creates the cgroup, so membership is a kernel fact, and CPU and memory
totals come from `cpu.stat` and `memory.current`. `watch` infers membership from
`/proc`. It is reliable in practice, but a process that detaches *and* changes
cgroup between two samples can be missed, and CPU used by a process that lives
between two samples is lost. Use `run` when exactness matters.

## Memory

![Summed RSS counts shared pages per process; PSS divides them; the cgroup charges them once](assets/diagrams/memory.light.svg#only-light)
![Summed RSS counts shared pages per process; PSS divides them; the cgroup charges them once](assets/diagrams/memory.dark.svg#only-dark)

| Field | |
|---|---|
| `rss_bytes` | always available, but counts shared pages once per process, so it reads high |
| `pss_bytes` | shared pages divided fairly: what the workload really costs; needs same-user access |
| `group_memory_bytes` | the kernel's own charge for the cgroup; `null` without one |

treehawk never writes a value it could not read: `null` means unavailable.

## Sampling

Samples are taken on absolute deadlines, so they do not drift. A sample that
arrives more than 1.5 intervals late is flagged `overrun` and counted in the
summary. `cpu_percent` is CPU time over the last interval, where `100` is one core;
it is `null` in the first sample.

## Limitations

- Linux only, reading `/proc` and cgroup v2.
- Polling cannot see processes that start and end between samples (under `watch`).
- Other users' processes have no `pss_bytes`.
- The log grows about 1 KB per sample per five processes; use `--aggregate-only`
  or a longer interval for runs measured in days.
