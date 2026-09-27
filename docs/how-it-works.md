# How it works

The first four sections cover `watch` and `run`; [Whole-machine
tracking](#whole-machine-tracking) covers `top`.

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

![On Linux run gives the command its own cgroup and watch infers membership; on macOS both infer it](assets/diagrams/run-vs-watch.light.svg#only-light)
![On Linux run gives the command its own cgroup and watch infers membership; on macOS both infer it](assets/diagrams/run-vs-watch.dark.svg#only-dark)

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
arrives more than 1.5 intervals late is flagged `overrun`, counted in the
summary, and marked on the CPU chart. `cpu_percent` is CPU time over the last
interval, where `100` is one core; it is `null` in the first sample.

## Whole-machine tracking

`top` reads every process once per sample, which costs one `stat` read per
process. It ranks them by CPU and by memory, and reads only the top N of each in
depth (PSS, swap).

**`--interval auto`** lets sampling use about 5% of one core. A sample that
took 5 ms earns a 100 ms interval. The interval is kept between 0.1 s and 5 s,
and moves towards its target gradually rather than jumping.

**A spike** is a reading more than 4 standard deviations above that process'
own running baseline *and* above it by a floor, so a process idling at 0.1%
cannot fire by reaching 0.5%:

| | Floor | Baseline follows a new level in |
|---|---|---|
| process CPU | 25% of one core | 60 s |
| process memory | 64 MiB | 120 s |
| machine CPU | 20% of all cores | 60 s |
| machine memory | 256 MiB | 120 s |

A process has to be watched for 15 s before it can spike. After a spike it
stays quiet for 60 s.

**A creep**, or leak suspect, is memory growing in a straight line. Each minute
is reduced to its *lowest* reading, so a garbage collector's sawtooth reads as
its floor. A line is then fitted through the last 30 minutes. A process is named
when that line rises by at least 16 MiB an hour (64 MiB for the whole machine),
fits with r² ≥ 0.8, and the floor has grown by at least 8 MiB (64 MiB). It is
named again only after growing another 25%.

## Limitations

- Polling cannot see processes that start and end between two samples. On Linux,
  `run` still counts their CPU through the cgroup. Under `watch`, and anywhere
  on macOS, it is lost. `top` never names such a process, although on Linux its
  CPU still shows in the machine line. Shorten `--interval`, or use `run`.
- `watch` can miss a process that detaches *and* changes boundary between two
  samples.
- treehawk never adopts its own ancestors (your shell, `uv`, `timeout`), since
  they carry the keyword you typed.
- Other users' processes have no `pss_bytes`, and on macOS are not visible at
  all. See [Platforms](platforms.md) for everything macOS will not report.
- A workload log grows about 1 KB per sample per five processes, or about
  90 MB a day at the default interval. Use `--aggregate-only` or a longer
  interval for runs measured in days. Memory use does not grow: the history
  treehawk keeps on screen and for the summary is capped.
- A spike needs 15 s of history for that process, and a leak suspect needs 30
  minutes of steady growth.
- GPU usage is not collected yet. The `top` log already has a column for it.
