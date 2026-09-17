# Usage

treehawk has four commands: `watch` and `run` record a workload, `report` and
`pdf` read a finished log back. Every command explains itself with `--help`.

## watch: attach to a running process

```bash
treehawk watch train.py                      # substring of the command line
treehawk watch --exact "python train.py"     # the whole command line
treehawk watch --regex 'worker-\d+'          # a regular expression
treehawk watch --pid 4213                    # one process id
```

Give exactly one of them. Keyword and regex matches are case-insensitive, and
every matching process joins the workload. treehawk never matches itself or the
shell you typed the command in.

If nothing matches, `watch` exits with code `2`. Add `--wait` to keep looking
until the process appears.

A watch ends when the workload's last process exits, when `--duration` has
passed, or on `Ctrl-C` / `SIGTERM`. The log always ends with a summary, and the
workload is left running.

## run: start a command

```bash
treehawk run -- python train.py --epochs 10
```

The command goes after `--`. On Linux treehawk starts it in a transient systemd
scope, a cgroup of its own, so every descendant is counted by the kernel,
including processes that live and die between two samples.

- Without systemd or cgroup v2 (common in containers and CI), treehawk falls back
  to tracking through `/proc` and records why in the log header's `notes`.
  `--no-isolate` chooses that mode on purpose.
- On macOS there is no boundary to create — see [Platforms](platforms.md) — so
  `run` starts the command in its own session and infers membership, which the
  header's `notes` also say.
- `Ctrl-C` and `SIGTERM` are forwarded to the command.
- If the command fails, `treehawk run` exits with its status.

See [How it works](how-it-works.md#run-and-watch) for how `run` and `watch` differ.

### What the command prints

The command's own output does not go straight to the terminal, because the
dashboard is repainting a region of it — left to themselves the two overwrite
each other and neither is readable. Instead treehawk gives the command a pty of
its own and reads it:

- the last few lines appear in an `output` panel at the bottom of the dashboard;
- the whole stream, escape codes and all, is written to `<log>.out` — so
  `treehawk-20260913-100000.jsonl` is accompanied by
  `treehawk-20260913-100000.out`.

A pty rather than a pipe, so the command still sees a terminal: it keeps its
colours and its line buffering, and treehawk does not change how the thing it is
measuring behaves.

```bash
treehawk run -- python train.py          # logs in the dashboard, kept in .out
tail -f treehawk-*.out                   # the full stream, from another shell
treehawk run --no-capture -- htop        # hand the terminal over instead
```

`--no-capture` is the escape hatch for a command that needs the terminal
itself — one that prompts for input, or draws its own full-screen view. Nothing
is drawn while it runs and no `.out` file is written. `--quiet` has the same
effect on the command's output, since it draws no dashboard to protect, and so
does redirecting treehawk's own output to a pipe or a file.

## Options

Shared by `watch` and `run`:

| Option | Default | |
|---|---|---|
| `-i`, `--interval SECONDS` | `1.0` | time between samples |
| `-o`, `--output PATH` | `treehawk-<timestamp>.jsonl` | the log; `-` writes to stdout |
| `--csv` | | write CSV instead of JSON Lines |
| `-q`, `--quiet` | | no dashboard, just the log |
| `-d`, `--duration SECONDS` | | stop early |
| `--expand RULE` | all four | limit the membership rules; repeat for several |
| `--no-pss` | | skip PSS; cheaper at short intervals |
| `--aggregate-only` | | log workload totals only, not a row per process |

`run` only:

| Option | |
|---|---|
| `--no-isolate` | do not ask for a cgroup; track through the process table |
| `--no-capture` | let the command write to this terminal instead of the dashboard |

Without a terminal (a pipe, CI), the dashboard is replaced by plain lines, and
the command writes to the same stream rather than being captured.

## report and pdf

```bash
treehawk report run.jsonl          # summary in the terminal
treehawk report run.jsonl --json   # the header and summary as JSON
treehawk pdf run.jsonl             # writes run.pdf; -o to choose
```

![treehawk report output](assets/output/report.svg)

Both work on the log of an interrupted run: the summary is recomputed from the
samples that were written.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | finished, or stopped with `Ctrl-C` / `SIGTERM` |
| `1` | invalid option, unreadable log, or a command that could not start |
| `2` | no process matched, or a usage error |
| other | `run`: the command's exit status |

## In CI

Fail a job when a test suite uses too much memory:

```bash
treehawk run --quiet -o pytest.jsonl -- uv run pytest
treehawk report pytest.jsonl --json | jq -e '.summary.peak_pss_bytes < 2 * 1024 * 1024 * 1024'
```
