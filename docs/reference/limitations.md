# Limitations

treehawk tries to be clear about what it cannot see. These are the known limits.

## Polling misses what happens between samples

A process that starts and exits between two samples is never observed. Under
`run` its CPU still reaches the workload total, because the cgroup's counter sees
it; under `watch` that CPU time is lost. A shorter `--interval` narrows the gap.
The PDF's *Sampling quality* page shows how tight the sampling really was.

## watch can miss a double escape

Under `watch`, a process that detaches **and** moves itself into an unrelated
cgroup in the gap between two samples can be missed. Use `run` when exactness
matters. See [run and watch](../concepts/run-vs-watch.md).

## Other users' processes

The kernel lets anyone read another user's `stat`, but not their `smaps_rollup`,
so `pss_bytes` is `null` for those processes. treehawk records `null` rather than a
guess.

## Linux only

Metrics come from `/proc` and cgroup v2. On a machine without cgroup v2,
cgroup-level totals are unavailable and detached children are tracked by the
`tree`, `session` and `orphan` rules alone; the log header's `notes` say so. The
platform layer is built to take other operating systems; see
[Extending treehawk](../development/extending.md#add-an-operating-system).

## run needs a systemd user session to isolate

Without `systemd-run` and a user session, which is common in containers and CI,
`run` falls back to tracking through `/proc`, and records why in the header.

## treehawk never adopts its own ancestors

Your shell, `uv`, `timeout` and anything else between you and treehawk carry the
keyword you typed, so they are never part of the workload. If the process you want
really is one of them, name it with `--pid`.

## Logs grow

Nothing inside treehawk grows without bound during a long watch, but the log does:
roughly 1 KB per sample per five processes, about 90 MB a day at the default
interval. Use `--aggregate-only` or a longer interval for runs measured in days;
see [Leaving a watch running](../guides/automation.md#leaving-a-watch-running).
