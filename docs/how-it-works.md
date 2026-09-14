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
| `cgroup` | anything in a kernel boundary the workload owns, the one thing a fork cannot escape |
| `session` | children re-parented away that kept the session |
| `orphan` | a new process that lost its parent and sits in a tracked process' boundary |

That boundary is a **cgroup** on Linux and a **coalition** on macOS — the rule
keeps its Linux name, and so does the `via` value in the log. Either way it is
adopted only if treehawk is not inside it and every process in it is already
tracked, so your login session is never swallowed. The `orphan` rule spots
re-parenting by noticing that the adopting reaper lives in a *different*
boundary.

Each process in the log records the rule that found it in `via`. The dashboard and
PDF colour processes by it: matched (blue), child (orange), detached (green).

## run and watch

![run gives the command its own cgroup; watch infers membership](assets/diagrams/run-vs-watch.light.svg#only-light)
![run gives the command its own cgroup; watch infers membership](assets/diagrams/run-vs-watch.dark.svg#only-dark)

On Linux `run` creates the cgroup, so membership is a kernel fact, and CPU and
memory totals come from `cpu.stat` and `memory.current`. `watch` infers
membership from `/proc`. It is reliable in practice, but a process that detaches
*and* changes boundary between two samples can be missed, and CPU used by a
process that lives and dies between two samples is lost. Use `run` when
exactness matters.

On macOS `run` cannot create a boundary — that needs entitlements treehawk does
not have — so it behaves like a very well-informed `watch`: nothing is missed
before the first sample, and membership is inferred from coalitions, sessions
and the process tree. See [Platforms](platforms.md).

## Memory

![Summed RSS counts shared pages per process; PSS divides them; the cgroup charges them once](assets/diagrams/memory.light.svg#only-light)
![Summed RSS counts shared pages per process; PSS divides them; the cgroup charges them once](assets/diagrams/memory.dark.svg#only-dark)

| Field | |
|---|---|
| `rss_bytes` | always available, but counts shared pages once per process, so it reads high |
| `pss_bytes` | the platform's fair measure: what the workload really costs; needs same-user access |
| `group_memory_bytes` | the kernel's own charge for the boundary; `null` without one |

Which fair measure `pss_bytes` carries depends on the platform, so the header
names it in `memory_kind`: `pss` on Linux (each shared page divided among the
processes mapping it) and `phys_footprint` on macOS (the kernel's own charge
for what a process costs, as Activity Monitor reports it). macOS has no PSS,
and treehawk will not put another measure behind that name without saying so.

treehawk never writes a value it could not read: `null` means unavailable.

## Sampling

Samples are taken on absolute deadlines, so they do not drift. A sample that
arrives more than 1.5 intervals late is flagged `overrun` and counted in the
summary. `cpu_percent` is CPU time over the last interval, where `100` is one core;
it is `null` in the first sample.

## Limitations

- Linux (`/proc` and cgroup v2) and macOS (`libproc` and `sysctl`); see
  [Platforms](platforms.md) for what each kernel will and will not report.
- Polling cannot see processes that start and end between samples (under
  `watch`, and anywhere on macOS).
- Other users' processes have no `pss_bytes`, and on macOS are not visible at
  all.
- The log grows about 1 KB per sample per five processes; use `--aggregate-only`
  or a longer interval for runs measured in days.
