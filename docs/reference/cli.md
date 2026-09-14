# Commands

```text
treehawk [--version] [--help] COMMAND [ARGS]...
```

| Command | Does |
|---|---|
| [`watch`](#treehawk-watch) | Watch a process that is already running, until it ends |
| [`run`](#treehawk-run) | Start a command and watch it, until it ends |
| [`report`](#treehawk-report) | Summarise a finished run |
| [`pdf`](#treehawk-pdf) | Render a finished run as a multi-page PDF report |

## treehawk watch

```text
treehawk watch [OPTIONS] [KEYWORD]
```

Watch a process that is already running, and everything it spawns, until it ends.
Name the process with exactly one of `KEYWORD`, `--pid`, `--exact` or `--regex`.
See [Watching a running process](../guides/watch.md).

`KEYWORD`
:   Text to look for in the command line of a running process. Case-insensitive.

`-p`, `--pid` *INTEGER*
:   Watch this process id.

`-e`, `--exact` *TEXT*
:   The full command line, matched whole.

`-r`, `--regex` *TEXT*
:   A regular expression, searched in the command line. Case-insensitive.

`--wait`
:   Keep looking until the process appears. Without it, a watch that matches
    nothing exits with code `2`.

Also takes every [common option](#common-options) and
[advanced option](#advanced-options).

## treehawk run

```text
treehawk run [OPTIONS] -- COMMAND [ARGS]...
```

Start a command and watch it until it ends. The command goes after `--` and runs
inside its own cgroup, so every descendant is accounted for exactly. See
[Starting a command](../guides/run.md).

`--no-isolate`
:   Do not create a cgroup for the workload; track it through `/proc` only.

Also takes every [common option](#common-options) and
[advanced option](#advanced-options). If the command exits with a non-zero status,
`treehawk run` exits with the same status.

## Common options

These are shared by `watch` and `run`.

`-i`, `--interval` *SECONDS*
:   Seconds between samples. At least `0.01`. Default `1.0`.

`-o`, `--output` *PATH*
:   Where to write the log. `-` writes JSON Lines to stdout. Default:
    `treehawk-<timestamp>.jsonl` (or `.csv`) in the current directory.

`--csv`
:   Write CSV instead of JSON Lines: `PATH`, `<base>.procs.csv`,
    `<base>.header.json` and `<base>.summary.json`.

`-q`, `--quiet`
:   No output on screen; just write the log.

## Advanced options

`-d`, `--duration` *SECONDS*
:   Stop after this long. By default treehawk runs until the workload ends. Under
    `run`, the command is stopped too.

`--expand` *RULE*
:   Which rules may adopt processes into the workload: `tree`, `cgroup`,
    `session` or `orphan`. Repeat it for several, as in
    `--expand tree --expand cgroup`. All four are used by default. See
    [Membership](../concepts/membership.md).

`--no-pss`
:   Skip the shared-memory correction. Cheaper per sample, but summed RSS
    over-counts pages shared between children, and `pss_bytes` is `null`.

`--aggregate-only`
:   Log only the workload total, not a row per process. Much smaller logs for a
    run that lasts days.

## treehawk report

```text
treehawk report [OPTIONS] PATH
```

Summarise a finished run: what was watched, peak and mean CPU, CPU time, the
memory peaks, and the top processes by CPU time and by peak RSS. Works on a log
from an interrupted run by recomputing the summary from its samples. See
[Reading a log](../guides/reading-logs.md).

`PATH`
:   A JSON Lines log written by a previous run.

`--json`
:   Print `{"header": ..., "summary": ...}` as JSON instead.

## treehawk pdf

```text
treehawk pdf [OPTIONS] PATH
```

Render a finished run as a multi-page PDF report. See
[The PDF report](../guides/pdf-report.md).

`PATH`
:   A JSON Lines log written by a previous run.

`-o`, `--output` *PATH*
:   Where to write the PDF. Default: next to the log, with a `.pdf` suffix.

## Global options

`--version`
:   Print `treehawk <version>` and exit.

`--help`
:   Show help for treehawk or for a command, and exit.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success, including a run stopped with `Ctrl-C` or `SIGTERM` |
| `1` | An invalid option value, an unreadable or empty log, a command that could not be started, or an unsupported platform |
| `2` | No process matched (`watch` without `--wait`), or a command-line usage error |
| other | `run` only: the exit status of the command |
