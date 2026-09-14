# First steps

This page takes you from a fresh install to a finished report. It assumes
`treehawk` is on your `PATH`; see [Installation](installation.md) if not.

## Ask for help

treehawk has four commands. Every one of them explains itself with `--help`.

```console
$ treehawk --help

 Usage: treehawk [OPTIONS] COMMAND [ARGS]...

 Log the CPU and RAM of a process and every process it spawns, including ones that detach.

╭─ Commands ───────────────────────────────────────────────────────────────────────╮
│ watch   Watch a process that is already running, until it ends.                  │
│ run     Start a command and watch it, until it ends.                             │
│ report  Summarise a finished run.                                                │
│ pdf     Render a finished run as a multi-page PDF report.                        │
╰──────────────────────────────────────────────────────────────────────────────────╯
```

`watch` and `run` record; `report` and `pdf` read a finished log back.

## Measure a command

Put the command after `--`:

```console
$ treehawk run -- python train.py
logging to treehawk-20260914-100012.jsonl
```

While it runs you get a live dashboard on the terminal:

<figure class="terminal" markdown="span">
  ![The live dashboard for a four-process run](../assets/output/dashboard.svg)
</figure>

- **cpu** is the workload's total. `100%` is one core fully used, so four busy
  processes read about `400%`.
- **mem** is the best memory figure available: the cgroup's own charge when
  there is a cgroup, otherwise PSS, otherwise RSS. The right-hand column says
  which one it is.
- **found** says how each process joined the workload. `match` is the process
  you named, `tree` is an ordinary child, and `cgroup`, `session` or `orphan`
  mark one that had to be recognised after it detached.

There is no duration to set: treehawk stops when the last process of the workload
exits. Press `Ctrl-C` to stop early; the log is still closed with a complete
summary, and under `run` the command receives the signal too.

When the run ends, the dashboard is replaced by a summary that stays in your
scrollback.

## Read the log back

Every run writes a log, by default `treehawk-<timestamp>.jsonl` in the current
directory. Summarise it at any time:

```console
$ treehawk report treehawk-20260914-100012.jsonl
```

<figure class="terminal" markdown="span">
  ![treehawk report output: CPU, memory and run facts, and the top processes by CPU time and peak RSS](../assets/output/report.svg)
</figure>

Or render it as a PDF:

```console
$ treehawk pdf treehawk-20260914-100012.jsonl
wrote treehawk-20260914-100012.pdf (8 pages)
```

!!! example "No workload handy?"

    Download the [sample log](../assets/output/sample-run.jsonl) these pages are
    illustrated with, and run `treehawk report sample-run.jsonl` or
    `treehawk pdf sample-run.jsonl` on it.

## Attach to something already running

`watch` finds a running process by a piece of its command line:

```console
$ treehawk watch train.py
```

It follows everything that process spawns from then on, and stops when they have
all exited. [Watching a running process](../guides/watch.md) covers the other ways
to name a process, and waiting for one that has not started yet.

## Where next

- [Starting a command](../guides/run.md): why `run` is exact, and how it passes
  exit codes and signals through.
- [Reading a log](../guides/reading-logs.md): `jq`, pandas and CSV recipes.
- [Membership](../concepts/membership.md): how treehawk decides what belongs to
  the workload.
