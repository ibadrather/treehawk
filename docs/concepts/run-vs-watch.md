# run and watch

treehawk has two ways to record a workload. They produce the same log, but they
know different things about it.

<figure class="diagram" markdown="span">
  ![run gives the command its own cgroup; watch infers membership inside a shared one](../assets/diagrams/run-vs-watch.light.svg#only-light)
  ![run gives the command its own cgroup; watch infers membership inside a shared one](../assets/diagrams/run-vs-watch.dark.svg#only-dark)
</figure>

## run is exact

`treehawk run` starts the command inside a transient systemd scope, which is a
cgroup of its own. Membership is then a fact the kernel keeps, not something
treehawk has to work out:

- Every descendant is in the scope, however it detached.
- The workload's CPU time comes from the scope's `cpu.stat`, which includes
  processes that were born and died between two samples.
- Memory comes from `memory.current` and `memory.peak`, the kernel's own charge
  for everything in the scope.
- Nothing happens before the first sample, because the workload does not exist
  until treehawk creates it.

## watch is very good

`treehawk watch` attaches to processes that already live in a shared cgroup, such
as your user session, alongside your editor and your shell. It cannot adopt that
cgroup, so it infers membership from `/proc` each sample with the
[four rules](membership.md). It is reliable in practice, with two gaps:

- A process that detaches **and** moves itself to an unrelated cgroup in the gap
  between two samples can be missed.
- A process that lives entirely between two samples is never seen, and its CPU
  time is lost from the total.

A shorter `--interval` narrows both gaps. `run` closes them.

## Side by side

| | `run` | `watch` |
|---|---|---|
| Who starts the workload | treehawk | you, earlier |
| Membership | the kernel's (a cgroup) | inferred from `/proc` |
| Workload CPU time | cgroup `cpu.stat` | per-process counters, plus members that exited |
| Short-lived processes | counted in the totals | invisible |
| `group_memory_bytes` | always | when a cgroup is accepted as the workload's |
| Stopping treehawk | stops the command too | leaves the workload running |
| Exit code | the command's | `0` |

## When run cannot isolate

`run` needs `systemd-run`, a systemd user session and cgroup v2. Without them (in
many containers and CI runners) it starts the command directly and tracks it the
way `watch` does, but still from the command's first instant. The log header's
`notes` say why. `--no-isolate` asks for that mode on purpose.

## Which to use

- **You can start the thing:** use `run`.
- **It is already running**, or something else starts it (a service manager, a
  scheduler, another team's script): use `watch`, with `--wait` if it has not
  started yet.
- **You are comparing runs:** use the same mode for all of them.
