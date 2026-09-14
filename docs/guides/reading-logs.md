# Reading a log

Every run leaves a log. It is plain data, written as the run goes, so you can
summarise it with treehawk, or query it with the tools you already use.

<figure class="diagram" markdown="span">
  ![A JSON Lines log: a header line, one sample per interval, and a summary; and the CSV variant's four files](../assets/diagrams/log-format.light.svg#only-light)
  ![A JSON Lines log: a header line, one sample per interval, and a summary; and the CSV variant's four files](../assets/diagrams/log-format.dark.svg#only-dark)
</figure>

The [log format reference](../reference/log-format.md) lists every field.

!!! example "Follow along"

    Every command on this page works on the [sample log](../assets/output/sample-run.jsonl).

## treehawk report

```console
$ treehawk report sample-run.jsonl
```

<figure class="terminal" markdown="span">
  ![treehawk report output](../assets/output/report.svg)
</figure>

The first block is what was watched, and how. The panel holds the figures people
ask for: peak and mean CPU, total CPU time, the three memory peaks, and the
processes responsible for most of the CPU time and memory. The colour of each
`via` label is its [discovery group](../concepts/membership.md#via).

For a script, ask for JSON. It is an object with the log's `header` and `summary`:

```console
$ treehawk report sample-run.jsonl --json | jq '.summary | {peak_cpu_percent, peak_pss_bytes, exit_code}'
{
  "peak_cpu_percent": 319.64,
  "peak_pss_bytes": 614628352,
  "exit_code": 0
}
```

!!! info "Interrupted runs still report"

    A run that was killed has no summary line, and possibly a half-written last
    line. `report` skips the torn line and recomputes the summary from the
    samples, so you still get an answer.

## jq

A JSON Lines log is one JSON object per line, so `jq` reads it as a stream.

```bash
# The summary (the last line of a run that finished)
tail -n 1 run.jsonl | jq .

# When was CPU highest?
jq -s 'map(select(.type == "sample")) | max_by(.cpu_percent) | {t, cpu_percent}' run.jsonl

# A time series, as CSV
jq -r 'select(.type == "sample") | [.t, .cpu_percent, .pss_bytes] | @csv' run.jsonl

# Every process that had to be recognised after it detached
jq -r 'select(.type == "sample") | .procs[] | select(.via != "match" and .via != "tree")
       | "\(.pid) \(.via) \(.cmdline)"' run.jsonl | sort -u
```

## pandas

```python
import pandas as pd

records = pd.read_json("run.jsonl", lines=True)
samples = records[records["type"] == "sample"].dropna(axis="columns", how="all")

# One row per process per sample, with the sample's time alongside
procs = pd.json_normalize(
    samples.to_dict("records"),
    record_path="procs",
    meta=["seq", "t"],
)

cpu_by_process = procs.groupby(["pid", "via"])["cpu_seconds"].max().sort_values(ascending=False)
print(cpu_by_process)
```

`cpu_seconds` is a process' lifetime CPU time at that sample, so its maximum is
the total. `cpu_percent` is the rate over the last interval.

## CSV

`--csv` writes the same data as four files, named after `--output`:

| File | Holds |
|---|---|
| `run.csv` | one row per sample |
| `run.procs.csv` | one row per process per sample, joinable to `run.csv` on `seq` |
| `run.header.json` | the header record |
| `run.summary.json` | the summary record, written when the run ends |

```console
$ treehawk run --csv --output run.csv -- ./job.sh
```

Rows are appended as each sample arrives, so a killed run leaves every row it
recorded. With `--aggregate-only` there is no `run.procs.csv`.

```python
import pandas as pd

samples = pd.read_csv("run.csv")
procs = pd.read_csv("run.procs.csv")

busiest = procs.loc[procs.groupby("seq")["cpu_percent"].idxmax(), ["seq", "pid", "name", "cpu_percent"]]
timeline = samples.merge(busiest, on="seq", suffixes=("", "_busiest"))
```

## Streaming

`--output -` writes JSON Lines to stdout as it is recorded, which lets another
program consume a run live. Add `--quiet` so the dashboard stays out of the way:

```console
$ treehawk watch --pid 4213 --quiet --output - | jq --unbuffered 'select(.type == "sample") | .cpu_percent'
```
