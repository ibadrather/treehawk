# Log format

A log is [JSON Lines](https://jsonlines.org/): one JSON object per line. It holds
a `header`, one `sample` per interval, and a `summary` at the end. Each line is
flushed as it is written, so a killed run still leaves a readable log.

<figure class="diagram" markdown="span">
  ![A JSON Lines log: a header line, one sample per interval, and a summary; and the CSV variant's four files](../assets/diagrams/log-format.light.svg#only-light)
  ![A JSON Lines log: a header line, one sample per interval, and a summary; and the CSV variant's four files](../assets/diagrams/log-format.dark.svg#only-dark)
</figure>

Download the [sample log](../assets/output/sample-run.jsonl) to see a real one.

## Conventions

- Every record has a `type`. Header and summary records also carry `schema`, the
  format version, currently `1`. New fields may be added within a version, so
  readers should ignore keys they do not know.
- Sizes are bytes, times are seconds, and CPU percentages count one core as `100`.
- `null` means the value could not be read. treehawk never writes a guess.

## header

The first line: what was watched, how, and on what machine.

```json
{
  "type": "header",
  "schema": 1,
  "treehawk_version": "0.3.1",
  "started_at": "2026-09-14T08:00:12.764+00:00",
  "mode": "run",
  "matcher": {"kind": "pid", "value": 65398},
  "argv": ["python3", "train.py"],
  "interval": 0.5,
  "expand": ["tree", "cgroup", "session", "orphan"],
  "per_process": true,
  "group_path": "/user.slice/user-1000.slice/user@1000.service/app.slice/treehawk-65397-1789372812.scope",
  "host": {"platform": "linux", "hostname": "devbox", "ncpu": 32, "clk_tck": 100,
           "page_size": 4096, "mem_total_bytes": 33250807808},
  "notes": []
}
```

| Field | |
|---|---|
| `treehawk_version` | the version that wrote the log |
| `started_at` | ISO 8601 time the run started |
| `mode` | `run` or `watch` |
| `matcher` | how the workload was named: `kind` is `pid`, `keyword`, `exact` or `regex` |
| `argv` | the command, under `run`; `null` under `watch` |
| `interval` | requested seconds between samples |
| `expand` | the membership rules in use |
| `per_process` | `false` for an `--aggregate-only` log, which has no `procs` |
| `group_path` | the cgroup `run` created; `null` otherwise |
| `host` | platform, host name, CPU count, clock ticks per second, page size, total RAM |
| `notes` | anything that limits the log, such as a missing cgroup v2 |

## sample

One per interval.

| Field | |
|---|---|
| `seq` | sample number, from `0` |
| `t` | seconds since the first sample |
| `ts` | ISO 8601 time of the sample |
| `n_procs` | live processes in the workload |
| `cpu_percent` | workload CPU over the last interval; `null` in the first sample |
| `cpu_percent_norm` | the same, as a share of the whole machine |
| `cpu_seconds_total` | lifetime CPU time of the workload, including members that exited |
| `cpu_seconds_used` | CPU time used since treehawk attached |
| `rss_bytes` | summed RSS; counts shared pages once per process |
| `pss_bytes` | summed PSS; `null` if unreadable or with `--no-pss` |
| `swap_bytes` | summed swap |
| `group_memory_bytes` | the cgroup's `memory.current`; `null` without a cgroup |
| `group_memory_peak_bytes` | the cgroup's `memory.peak`; `null` without a cgroup |
| `overrun` | `true` if this sample came more than 1.5 intervals after the last |
| `procs` | one object per process, below; absent with `--aggregate-only` |

Keys added by [metric collectors](../development/extending.md#add-a-metric) appear
as objects under the collector's namespace.

### procs

| Field | |
|---|---|
| `pid`, `ppid` | process and parent id |
| `starttime` | start time in clock ticks since boot; with `pid`, identifies the process |
| `name` | the kernel's process name (`comm`) |
| `cmdline` | the full command line, or `""` if unreadable |
| `state` | the kernel's state letter, such as `R` or `S` |
| `threads` | thread count |
| `cpu_percent` | this process' CPU over the last interval; `null` in its first sample |
| `cpu_seconds` | this process' lifetime CPU time |
| `rss_bytes`, `pss_bytes`, `swap_bytes` | as for the sample, for this process |
| `via` | how it joined the workload: `match`, `tree`, `cgroup`, `session` or `orphan` |

## summary

The last line, written when the run ends however it ends, unless treehawk itself
is killed with `SIGKILL`.

| Field | |
|---|---|
| `samples`, `duration_s` | how many samples, over how long |
| `peak_cpu_percent`, `mean_cpu_percent` | CPU peak and mean |
| `peak_n_procs`, `total_procs_seen` | most processes at once, and in total |
| `peak_rss_bytes`, `peak_pss_bytes`, `peak_group_memory_bytes` | the three memory peaks |
| `cpu_seconds_total`, `cpu_seconds_used` | as in the last sample |
| `overruns` | samples flagged `overrun` |
| `exit_code` | the command's exit status under `run`; `null` under `watch` |
| `top_by_cpu`, `top_by_memory` | up to five processes each, with `pid`, `name`, `cmdline`, `via`, `cpu_seconds` and `peak_rss_bytes` |

## CSV

With `--csv`, the same data is split by shape:

| File | Content |
|---|---|
| `<base>.csv` | one row per sample, with the sample fields above except `procs` |
| `<base>.procs.csv` | one row per process per sample: `seq`, `t`, `ts`, then the `procs` fields |
| `<base>.header.json` | the header record |
| `<base>.summary.json` | the summary record |

`<base>` is `--output` without its suffix. Join the two tables on `seq`.
