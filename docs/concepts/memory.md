# Memory numbers

"How much memory does it use?" has no single answer for a group of processes.
treehawk records three measures, because each is wrong in a different way, and
says which is which.

<figure class="diagram" markdown="span">
  ![Three processes share a 100 MiB library: summed RSS counts it three times, PSS divides it, the cgroup charges it once](../assets/diagrams/memory.light.svg#only-light)
  ![Three processes share a 100 MiB library: summed RSS counts it three times, PSS divides it, the cgroup charges it once](../assets/diagrams/memory.dark.svg#only-dark)
</figure>

## The three measures

`rss_bytes`: resident set size, summed over the workload
:   Always available, and cheap to read. But a page shared by several processes,
    such as a library or memory a parent shared with the children it forked, is
    counted once *per process*. Over a fork tree the sum overstates real use,
    sometimes by a lot.

`pss_bytes`: proportional set size, summed
:   Each shared page is divided among the processes that map it, so the sum is
    what the workload actually costs the machine. It is read from
    `/proc/<pid>/smaps_rollup`, which needs permission to inspect the process
    (normally, the same user). Where that is not allowed, it is `null`.

`group_memory_bytes`: the cgroup's own charge
:   `memory.current` for the workload's cgroup, with `group_memory_peak_bytes`
    from `memory.peak`. This is the kernel's figure, and it also includes page
    cache and kernel memory charged to the group. Exact when there is a cgroup,
    `null` when there is not.

`swap_bytes` is recorded beside them, summed over the workload.

## Which one to trust

| Question | Use |
|---|---|
| Will it fit on this machine? | `group_memory_peak_bytes`, else `pss_bytes` |
| Which process is the hog? | per-process `pss_bytes`, else `rss_bytes` |
| What did `top` show? | `rss_bytes` |
| Is it growing? | any of them, as long as you compare like with like |

treehawk never invents a value it could not read. A `null` means "not
available", not zero.

## Where each appears

- The **dashboard** shows the best figure the sample has, in the order
  cgroup, PSS, RSS, and labels which one it is.
- `treehawk report` shows all three peaks.
- The PDF's *Memory over time* page draws all three together, and *Memory by
  process* stacks RSS, which is why it reads high.

## The cost of PSS

Reading `smaps_rollup` makes the kernel walk the process' memory map, which costs
more than reading `stat`. At the default one-second interval it does not matter.
With hundreds of processes and a short interval, `--no-pss` skips it; `pss_bytes`
is then `null`, and the dashboard falls back to the cgroup figure or RSS.
