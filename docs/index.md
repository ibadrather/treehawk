# treehawk { .th-home-title }

<div class="th-hero" markdown>

<img class="th-hero__logo" src="assets/brand/logo.svg" alt="">

<p class="th-kicker">Process monitoring for Linux</p>

<h1>tree<span>hawk</span></h1>

<p class="th-hero__tagline">
Log the CPU and RAM a process uses, and every process it spawns,
<strong>including children that daemonize and detach themselves</strong>.
</p>

[Get started](getting-started/installation.md){ .md-button .md-button--primary }
[How it works](concepts/membership.md){ .md-button }

</div>

Point treehawk at something already running, or let it start the command. Either
way it samples until the workload ends, then leaves you a log you can summarise
in the terminal, turn into a PDF report, or load into pandas.

```console
$ treehawk run -- python train.py --epochs 10
```

<figure class="terminal" markdown="span">
  ![The treehawk live dashboard: CPU and memory sparklines, and a table of four processes found by match, tree and cgroup](assets/output/dashboard.svg)
  <figcaption>The live dashboard, part-way through a run. The <code>found</code> column says how each process was discovered.</figcaption>
</figure>

## Highlights

<div class="th-cards" markdown>

<div class="th-card" markdown>
**Follows detached children**

A child that forks, calls `setsid` and forks again stays tracked after its parent is gone. [Membership →](concepts/membership.md)
</div>

<div class="th-card" markdown>
**Exact under `run`**

The command gets a cgroup of its own, so every descendant is counted by the kernel. [run and watch →](concepts/run-vs-watch.md)
</div>

<div class="th-card" markdown>
**Honest memory numbers**

RSS, PSS and the cgroup's own charge, side by side, each wrong in a different way. [Memory numbers →](concepts/memory.md)
</div>

<div class="th-card" markdown>
**Runs until the work is done**

No duration to guess. `Ctrl-C` or a shutdown `SIGTERM` still writes a complete summary. [Sampling →](concepts/sampling.md)
</div>

<div class="th-card" markdown>
**Logs that survive a crash**

JSON Lines, flushed per sample; a killed run still reads back. [Log format →](reference/log-format.md)
</div>

<div class="th-card" markdown>
**A report worth sending**

Eight PDF pages, each answering one question about the run. [The PDF report →](guides/pdf-report.md)
</div>

</div>

## The problem it solves

Monitoring "a process and its children" by walking parent-child links works right
up until a child daemonizes. The survivor is re-parented to PID 1 or to a
subreaper such as `systemd --user`, it has left its parent's session, and there is
no link left to follow. A naive monitor reports that the workload finished while
the daemon is still burning a core.

<figure class="diagram" markdown="span">
  ![How a daemonized child escapes a parent-child walk, and how treehawk keeps it](assets/diagrams/daemonize.light.svg#only-light)
  ![How a daemonized child escapes a parent-child walk, and how treehawk keeps it](assets/diagrams/daemonize.dark.svg#only-dark)
</figure>

treehawk admits a process once, by any of four rules, and never evicts it until
that exact process exits. Colours follow the process everywhere: in the dashboard,
in `treehawk report`, and on every page of the PDF.

## A quick tour

```bash
# Attach to something already running
treehawk watch train.py                       # substring of the command line
treehawk watch --exact "python train.py"      # the whole command line
treehawk watch --regex 'worker-\d+'
treehawk watch --pid 4213

# Or start it, which is exact
treehawk run -- python train.py --epochs 10

# Afterwards, read the log back
treehawk report treehawk-20260913-100000.jsonl
treehawk pdf    treehawk-20260913-100000.jsonl
```

Metrics come straight from `/proc` and cgroup v2, not from `ps`, `top` or a
third-party library. treehawk runs on Linux with Python 3.10 or newer.

## Next steps

- [Install treehawk](getting-started/installation.md), then take the
  [first steps](getting-started/first-steps.md).
- Attach to a running process: [Watching a running process](guides/watch.md).
- Measure a command from start to finish: [Starting a command](guides/run.md).
- Understand what the numbers mean: [Memory numbers](concepts/memory.md).
- Add a metric, a rule or an output format: [Extending treehawk](development/extending.md).
