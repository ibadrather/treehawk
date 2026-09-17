# Output

## The log

JSON Lines by default: a `header` record, one `sample` per interval, and a
`summary` at the end. Each line is flushed as it is written, so a killed run still
leaves a readable log.

```jsonc
{"type":"header","mode":"run","argv":["python3","train.py"],"interval":0.5,
 "memory_kind":"pss",        // which measure pss_bytes carries on this machine
 "host":{"ncpu":32},"notes":[]}
{"type":"sample","seq":12,"t":6.0,"n_procs":4,
 "cpu_percent":315.5,          // 100 = one core
 "cpu_percent_norm":9.9,       // of the whole machine
 "cpu_seconds_total":18.4,     // lifetime, including exited children
 "rss_bytes":642269184, "pss_bytes":614628352, "group_memory_bytes":615354368,
 "overrun":false,
 "procs":[{"pid":65438,"ppid":1,"name":"python3","cpu_percent":34.0,"rss_bytes":68263936,"via":"cgroup"}]}
{"type":"summary","samples":42,"duration_s":20.5,"peak_cpu_percent":319.64,"exit_code":0,
 "top_by_cpu":[...],"top_by_memory":[...]}
```

`memory_kind` names the fair-memory measure `pss_bytes` carries: `pss` on Linux,
`phys_footprint` on macOS. A log written before the field existed is `pss`.
`group_memory_bytes` is `null` wherever the kernel keeps no total for the
boundary, which is always the case on macOS.

`--aggregate-only` drops `procs`. `--csv` writes `run.csv` (one row per sample),
`run.procs.csv` (one row per process per sample, joined on `seq`), and the header
and summary as `run.header.json` and `run.summary.json`.

A few `jq` recipes:

```bash
tail -n 1 run.jsonl | jq .                                               # the summary
jq -s 'map(select(.type == "sample")) | max_by(.cpu_percent) | {t, cpu_percent}' run.jsonl
jq -r 'select(.type == "sample") | [.t, .cpu_percent, .pss_bytes] | @csv' run.jsonl
```

## What the workload printed

`treehawk run` also keeps the command's own stdout and stderr, together and in
order, in `<log>.out` — so `run.jsonl` is accompanied by `run.out`. It is the
raw stream, escape codes included, so replaying it shows what the command would
have shown:

```bash
cat run.out                    # or less -R, to render the colours
grep -n "Traceback" run.out    # what went wrong, next to what it cost
```

Only `run` writes it, and only when it captured the output — see
[Usage](usage.md#what-the-command-prints) for when that is. `watch` never does:
the process was already running and its output was never treehawk's to read.

## The PDF report

`treehawk pdf run.jsonl` renders eight pages, each answering one question:
overview, CPU over time, memory over time, process lifetimes, CPU by process,
memory by process, biggest consumers, and sampling quality.
[Download a sample report](assets/output/sample-run.pdf).

![Overview page](assets/output/pdf-page-1.png)

![CPU over time page](assets/output/pdf-page-2.png)

![Process lifetimes page](assets/output/pdf-page-4.png)
