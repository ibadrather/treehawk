---
title: CPU and memory usage report as HTML, PDF or in the terminal
description: >-
  Turn a treehawk log into a CPU and memory usage report: an HTML page with charts, a PDF, a terminal summary, or JSON.
keywords:
  - cpu usage report html
  - cpu usage report pdf
  - memory usage report
  - log cpu usage over time
  - peak memory usage
  - resource usage charts
---

# Read the results

Every run leaves a log of CPU and memory usage over time. `report` turns it
into an HTML page with charts, written beside the log; `--pdf` writes a PDF
instead, and `--cli` or `--json` print a summary in the terminal. Every format
works on either kind of log (a `watch`/`run` log or a `top` directory) and
tells them apart by the log's header. They also work on the log of an
interrupted run, because the summary is recomputed from the samples that were
written.

```bash
treehawk report run.jsonl            # writes run.html
treehawk report run.jsonl --pdf      # writes run.pdf
treehawk report run.jsonl --cli      # summary in the terminal
treehawk report run.jsonl --json     # header and summary as JSON
treehawk report treehawk-top --since 2h
```

![treehawk report output](assets/output/report.svg)

## report

| Option | Default | |
|---|---|---|
| `PATH` | | a log file, or for `top` a log directory, boot directory or single file |
| `--html` | yes | write an HTML page with charts |
| `--pdf` | | write a multi-page PDF report |
| `--cli` | | print the summary in the terminal |
| `--json` | | print the summary as JSON |
| `-o`, `--output PATH` | beside the log, as `.html` or `.pdf` | where to write the HTML or PDF |
| `--since TIME` | | `top` logs only: start here |
| `--until TIME` | | `top` logs only: stop here |

Give at most one of `--html`, `--pdf`, `--cli` and `--json`. `TIME` is either a
moment (`"2026-09-27 14:00"`, ISO 8601, local time unless a zone is given) or a
span ago (`90`, `30m`, `2h`, `1d`).

## The HTML report

One self-contained file: the styles, the charts and a few lines of script are
inside it, so it opens offline in any browser, mails as an attachment and
follows the reader's light or dark setting. For a workload log it shows, top
to bottom:

| Part | What it holds |
|---|---|
| Header | the command, its exit status, the host, and when the run started and ended |
| Key figures | peak and mean CPU, CPU time, peak memory, processes seen |
| Warnings | a failed exit, an interrupted log, late samples, sleep, and the platform's notes |
| Timeline | CPU, memory and processes alive over time; hover to read every series at one moment |
| Biggest consumers | the processes that used the most CPU time and held the most memory |
| Process tracks | every process with a bar for when it was alive; click a column to sort |
| Run details | sampling, host, boundary, memory measure and where the log came from |

A long run stays small: each chart is cut to a few hundred points, each the
peak of its stretch, so a one-sample spike is never averaged away. A `top` log
gets the same page on a wall-clock axis, with spikes and leak suspects listed
and marked on the charts.

## The PDF report

`--pdf` writes beside the log as `.pdf`, or wherever `-o` says.
A workload log becomes eight pages, each answering one question.
[Download a sample report](assets/output/sample-run.pdf).

| Page | Question |
|---|---|
| Overview | What happened, in five numbers, and how each process was found |
| CPU over time | Was it busy, and did it stay busy? |
| Memory over time | How much, by each of the three measures |
| Process lifetimes | Who was alive, when: one bar per process |
| CPU by process | Which process was burning the CPU |
| Memory by process | Which process was holding the memory |
| Biggest consumers | The two league tables |
| Sampling quality | Can you trust the other seven pages? |

Pages that need per-process detail are left out of an `--aggregate-only` log
rather than printed blank.

![Overview page of the CPU and memory usage PDF report](assets/output/pdf-page-1.png)

![CPU usage over time page](assets/output/pdf-page-2.png)

![Process lifetimes page](assets/output/pdf-page-4.png)

A `top` log becomes five pages: machine overview; the whole machine's CPU and
memory, with spikes marked; CPU by process; memory by process; and spikes and
leak suspects. The charts keep the peak of each time bucket, so a spike still
shows in a month of data.

## The workload log

`watch` and `run` write JSON Lines by default: a `header` record, one `sample`
per interval, and a `summary` at the end. Each line is flushed as it is written,
so a killed run still leaves a readable log.

```jsonc
{"type":"header","mode":"run","argv":["python3","train.py"],"interval":0.5,
 "memory_kind":"pss","host":{"ncpu":32},"notes":[]}
{"type":"sample","seq":12,"t":6.0,"asleep_s":0.0,"n_procs":4,
 "cpu_percent":315.5, "cpu_percent_norm":9.9, "cpu_seconds_total":18.4,
 "rss_bytes":642269184, "pss_bytes":614628352, "group_memory_bytes":615354368,
 "overrun":false,
 "procs":[{"pid":65438,"ppid":1,"name":"python3","cpu_percent":34.0,"rss_bytes":68263936,"via":"cgroup"}]}
{"type":"summary","samples":42,"duration_s":20.5,"asleep_s":0.0,"peak_cpu_percent":319.64,"exit_code":0,
 "top_by_cpu":[...],"top_by_memory":[...]}
```

The fields you will reach for most:

| Field | |
|---|---|
| `memory_kind` | header: which fair measure `pss_bytes` carries, `pss` (Linux) or `phys_footprint` (macOS); a log without it is `pss` |
| `metric_source` | header: where the readings come from, `/proc` (Linux) or `libproc` (macOS); `null` in a log written before 0.9.0 |
| `notes` | header: anything treehawk could not measure on this machine, and why |
| `exit_code` | summary, `run` only: the command's exit status; negative is the signal that ended it (`-2` after `Ctrl-C`), `null` under `watch` |
| `t`, `duration_s` | seconds since the first sample, by the wall clock: time the machine slept counts |
| `asleep_s` | of `t` (or, in the summary, of `duration_s`), the seconds the machine spent in system sleep |
| `cpu_percent` | CPU over the last interval, summed across processes; `100` is one core, `null` in the first sample |
| `cpu_percent_norm` | the same, as a share of the whole machine |
| `cpu_seconds_total` | lifetime CPU time, including exited children |
| `cpu_seconds_used` | CPU time since treehawk attached |
| `rss_bytes`, `pss_bytes`, `swap_bytes` | memory; see [which one to trust](how-it-works.md#memory) |
| `group_memory_bytes`, `group_memory_peak_bytes` | the kernel's own charge for the boundary; `null` without one, and always on macOS |
| `overrun` | this sample arrived more than 1.5 intervals late |
| `procs[].via` | which [membership rule](how-it-works.md#membership) found the process |

`null` always means the value could not be read; treehawk never writes a guess.
`--aggregate-only` drops `procs`.

A few `jq` recipes:

```bash
tail -n 1 run.jsonl | jq .                                               # the summary
jq -s 'map(select(.type == "sample")) | max_by(.cpu_percent) | {t, cpu_percent}' run.jsonl
jq -r 'select(.type == "sample") | [.t, .cpu_percent, .pss_bytes] | @csv' run.jsonl
```

### CSV

`--csv` writes four files instead of one:

| File | |
|---|---|
| `run.csv` | one row per sample |
| `run.procs.csv` | one row per process per sample, joined to `run.csv` on `seq`; not written with `--aggregate-only` |
| `run.header.json` | the header |
| `run.summary.json` | the summary |

### What the workload printed

`run` keeps the command's stdout and stderr, together and in order, in
`<log>.out`, so `run.jsonl` is accompanied by `run.out`. It is the raw stream,
escape codes included:

```bash
less -R run.out                # with the colours
grep -n "Traceback" run.out    # what went wrong, next to what it cost
```

Only `run` writes it, and only when it captured the output (see [What the
command prints](workload.md#what-the-command-prints)). `watch` never does,
because the process was already running and its output was never treehawk's to
read.

## The top log

`top` writes a directory of segments, one directory per boot:

```
treehawk-top/<boot>/top-20260927-140000.jsonl      the segment being written
treehawk-top/<boot>/top-20260927-130000.jsonl.gz   a closed one, compressed
```

Each segment is complete on its own: it opens with a `header`, ends with a
`summary`, and in between holds:

| Record | |
|---|---|
| `proc` | who a process is (`pid`, `starttime`, `name`, `cmdline`), written the first time a segment mentions it |
| `sample` | the machine line (`host`: `cpu_percent`, `mem_used_bytes`, `mem_available_bytes`, `swap_used_bytes`) plus a compact row per top-N process in `top` |
| `event` | `enter` or `leave` the top N, `spike`, or `creep`, with the `value`, `baseline` or `slope_per_hour` that triggered it |

Each row in `top` is a list in the order the header's `row_fields` names:
`pid`, `starttime`, `cpu_percent`, `rss_bytes`, `pss_bytes`, `swap_bytes`,
`gpu_memory_bytes`, `reasons`. `reasons` says why the process is there: `c` for
CPU, `m` for memory, or `cm` for both. Because a command line is written once
per segment and not in every row, a log can run for the lifetime of a machine.
