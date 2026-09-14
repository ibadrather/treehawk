# Watching a running process

`treehawk watch` attaches to a process that is already running and follows it,
and everything it spawns, until the last of them exits.

```console
$ treehawk watch train.py
```

## Naming the process

Give exactly one of these:

| You give | Matches | Example |
|---|---|---|
| `KEYWORD` | a case-insensitive substring of the command line (or of the process name, when the command line is unreadable) | `treehawk watch train.py` |
| `--exact`, `-e` | the whole command line, compared after trimming spaces | `treehawk watch --exact "python train.py --epochs 10"` |
| `--regex`, `-r` | a case-insensitive regular expression, searched in the command line | `treehawk watch --regex 'gunicorn: worker'` |
| `--pid`, `-p` | one process id | `treehawk watch --pid 4213` |

Every process that matches becomes part of the workload, so a keyword that fits
three processes watches all three. Use `--exact`, a tighter `--regex` or `--pid`
when that is not what you want.

!!! note "Your own shell never matches"

    The command line you typed contains the keyword, and so do the shell, `uv`,
    `timeout` or anything else between you and treehawk. treehawk never adopts
    itself or its own ancestors. A `--pid` is taken at its word, so it may name
    one of them if you really mean it.

Some patterns that come up often:

```bash
treehawk watch --pid "$(pgrep -o postgres)"     # the oldest postgres process
treehawk watch --regex '^celery .* worker'      # a worker pool, however many there are
treehawk watch --exact "$(tr '\0' ' ' < /proc/4213/cmdline)"
```

## When it has not started yet

Without `--wait`, a watch that matches nothing fails straight away with exit
code `2`:

```console
$ treehawk watch nightly-backup
treehawk: no process matched
```

With `--wait`, treehawk keeps looking until the process appears, with no timeout,
so you can start the watch first and the job later:

```console
$ treehawk watch --wait nightly-backup
```

## How long it runs

A watch ends when:

- every process of the workload has exited,
- `--duration SECONDS` has passed, if you gave one, or
- you press `Ctrl-C`, or something sends treehawk `SIGTERM`.

In every case the log ends with a complete summary. Unlike `run`, stopping a
watch leaves the workload alone.

## Common options

```console
$ treehawk watch train.py --interval 0.25 --output train.jsonl
$ treehawk watch train.py --csv --output train.csv
$ treehawk watch train.py --quiet
```

| Option | Default | |
|---|---|---|
| `-i`, `--interval` | `1.0` | Seconds between samples, at least `0.01` |
| `-o`, `--output` | `treehawk-<timestamp>.jsonl` | Where the log goes; `-` writes JSON Lines to stdout |
| `--csv` | off | [CSV files](reading-logs.md#csv) instead of JSON Lines |
| `-q`, `--quiet` | off | No dashboard; only the log |

The full list, including the advanced options, is in the
[command reference](../reference/cli.md#treehawk-watch).

## Limiting the membership rules

By default four rules may add processes to the workload: `tree`, `cgroup`,
`session` and `orphan` ([what each one catches](../concepts/membership.md)). To
allow only some of them, repeat `--expand`:

```console
$ treehawk watch train.py --expand tree --expand cgroup
```

With `--expand tree` alone treehawk behaves like a classic parent-child monitor,
which is a useful comparison when a result surprises you. The `via` field of each
process in the log says which rule admitted it.

## What watch can miss

`watch` infers membership from `/proc`. That is reliable in practice, but a
process that detaches *and* moves itself into an unrelated cgroup in the gap
between two samples can be missed, and CPU burnt by a process that is born and
dies between two samples is invisible. When exactness matters, let treehawk
[start the command](run.md) instead.

<figure class="diagram" markdown="span">
  ![run gives the command its own cgroup; watch infers membership inside a shared one](../assets/diagrams/run-vs-watch.light.svg#only-light)
  ![run gives the command its own cgroup; watch infers membership inside a shared one](../assets/diagrams/run-vs-watch.dark.svg#only-dark)
</figure>
