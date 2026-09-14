# The sampling loop

treehawk polls. Every interval it reads the process table, updates membership,
measures every member, and hands one record to each output. This page describes
that loop and what it guarantees.

<figure class="diagram" markdown="span">
  ![One tick: sleep until the deadline, scan, refresh membership, enrich, aggregate, collect, then write the record to every sink](../assets/diagrams/sampling-loop.light.svg#only-light)
  ![One tick: sleep until the deadline, scan, refresh membership, enrich, aggregate, collect, then write the record to every sink](../assets/diagrams/sampling-loop.dark.svg#only-dark)
</figure>

## One tick

1. **Wait for the deadline.** Sample `n` is due at `start + n × interval`.
2. **Scan.** One pass over `/proc` reads the cheap facts of every visible process:
   parent, session, state, CPU ticks, RSS.
3. **Refresh membership.** Members that exited are removed and their final CPU
   time is banked; then the [rules](membership.md) adopt anything new.
4. **Enrich.** Only members get the costlier reads: command line, PSS and swap
   from `smaps_rollup`, cgroup.
5. **Aggregate.** Counters become rates, per process and for the workload, and the
   cgroup's own totals are read when there is one.
6. **Collect.** Any registered metric collectors add their keys to the sample.
7. **Write.** The record goes to every sink: the log, and the dashboard or plain
   lines on screen.

## Deadlines do not drift

Deadlines are absolute, not "sleep one interval after the last sample finished",
so a slow sample never pushes the next one late, and after an hour the samples are
still on the grid.

A sample that arrives more than 1.5 intervals after the previous one is flagged
`overrun`. Overruns are counted in the summary and marked on the PDF's CPU chart;
a steady trickle of them means the interval is too short for the machine or the
workload, and a longer `--interval` or `--no-pss` will help.

## CPU percentages

CPU is measured as clock ticks consumed, so a rate needs two readings:

- `cpu_percent` is the CPU time used since the previous sample, divided by the
  time between them. `100%` is one core fully used.
- `cpu_percent_norm` divides that by the number of CPUs: the share of the whole
  machine.
- The first sample has no previous reading, so its rates are `null`, and so are a
  process' rates in the first sample it appears in.

`cpu_seconds_total` is the workload's lifetime CPU time, including members that
have already exited. `cpu_seconds_used` counts only what was used after treehawk
attached, which is what you usually want under `watch`.

Under `run`, the workload figures come from the cgroup's `cpu.stat`, which also
covers processes that lived and died between two samples.

## When the loop ends

The loop ends when the workload has no processes left, when `--duration` has
elapsed, or when `Ctrl-C` or `SIGTERM` asks it to stop. Writing the summary is in a
`finally` block, so a log always ends with one, unless treehawk itself is killed
with `SIGKILL`. Even then, every sample written so far is on disk, and `report`
recomputes the summary from them.

## Failures stay contained

- A metric collector that raises is skipped for that sample; the sample is still
  written.
- An output that fails, say the terminal went away, is recorded rather than
  raised. The other outputs keep their data, and treehawk reports the error once
  the run is over.
- A process that exits halfway through being read is treated as gone, not as an
  error.
