# Track a workload

`watch` and `run` follow one workload, meaning a process and everything it
spawns, and write a log you read back with [`report` and `pdf`](results.md).
Both sample until the workload ends. `Ctrl-C` or a `SIGTERM` stops them early,
and the log still ends with a complete summary.

Use `run` when you can: it starts the command itself, so nothing is missed. Use
`watch` for something that is already running. [How it
works](how-it-works.md#run-and-watch) explains the difference.

## watch: attach to a running process

```bash
treehawk watch train.py                      # substring of the command line
treehawk watch --exact "python train.py"     # the whole command line
treehawk watch --regex 'worker-\d+'          # a regular expression
treehawk watch --pid 4213                    # one process id
```

| Option | |
|---|---|
| `KEYWORD` | text to look for in the command line of a running process |
| `-e`, `--exact TEXT` | the full command line, matched whole |
| `-r`, `--regex PATTERN` | a regular expression over the command line |
| `-p`, `--pid PID` | one process id |
| `--wait` | if nothing matches yet, keep looking until something does |

Give exactly one of the first four. Keyword and regex matches are
case-insensitive, and every matching process joins the workload. treehawk never
matches itself or its own ancestors, such as the shell you typed the command in.

If nothing matches, `watch` exits with code `2` unless you gave `--wait`. The
workload is left running when the watch ends.

## run: start a command

```bash
treehawk run -- python train.py --epochs 10
```

The command goes after `--`.

- On Linux, `run` starts the command in a transient systemd scope, which is a
  cgroup of its own. The kernel then counts every descendant, including
  processes that live and die between two samples.
- Without systemd or cgroup v2 (common in containers and CI), `run` falls back
  to tracking through `/proc` and records why in the log header's `notes`.
  `--no-isolate` chooses that mode on purpose.
- On macOS there is no boundary to create, so `run` starts the command in its
  own session and infers membership. See [Platforms](platforms.md).
- `Ctrl-C` and `SIGTERM` are forwarded to the command. If the command fails,
  `treehawk run` exits with its status.

### What the command prints

The dashboard repaints part of the terminal, so the command's output would
overwrite it. Instead, treehawk gives the command a pty of its own and reads
from it:

- the last few lines appear in an `output` panel at the bottom of the dashboard;
- the whole stream, escape codes included, is kept beside the log as `.out`
  (`treehawk-20260913-100000.jsonl` gets `treehawk-20260913-100000.out`).

treehawk uses a pty rather than a pipe so the command still sees a terminal. It
keeps its colours and its line buffering, and behaves as it would if you ran it
directly.

```bash
treehawk run -- python train.py          # output in the dashboard, kept in .out
tail -f treehawk-*.out                   # the full stream, from another shell
treehawk run --no-capture -- htop        # hand the terminal over instead
```

Output is not captured, and no `.out` is written, in these cases:

- with `--no-capture`, which you need for a command that prompts for input or
  draws its own full-screen view;
- with `--quiet`, since there is no dashboard to protect;
- when treehawk's own output goes to a pipe or a file.

## Options

These are shared by `watch` and `run`, except the two marked `run` only.

| Option | Default | |
|---|---|---|
| `-i`, `--interval SECONDS` | `1.0` | time between samples, at least `0.01` |
| `-o`, `--output PATH` | `treehawk-<timestamp>.jsonl` | the log; `-` writes it to stdout |
| `--csv` | | write [CSV](results.md#csv) instead of JSON Lines |
| `-q`, `--quiet` | | no dashboard, just the log |

Advanced options, for awkward situations:

| Option | Default | |
|---|---|---|
| `-d`, `--duration SECONDS` | until the workload ends | stop after this long |
| `--expand RULE` | all four | only let these [membership rules](how-it-works.md#membership) adopt processes: `tree`, `cgroup`, `session`, `orphan`; repeat for several |
| `--no-pss` | | skip the fair-memory read (PSS on Linux, phys footprint on macOS); cheaper per sample, but summed RSS over-counts shared pages |
| `--aggregate-only` | | log only the workload total, not a row per process; much smaller logs for runs lasting days |
| `--no-isolate` | | `run` only: do not ask for a cgroup, track through the process table |
| `--no-capture` | | `run` only: let the command write to this terminal instead of the dashboard |

Without a terminal (a pipe, CI), the dashboard is replaced by plain lines.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | finished, or stopped with `Ctrl-C` / `SIGTERM` |
| `1` | treehawk could not do what was asked: an invalid option value, an unreadable log, a command that could not start |
| `2` | `watch` found no matching process, or the command line itself was malformed (unknown option, missing argument) |
| other | `run`: the command's own exit status |

## In CI

Fail a job when a test suite uses too much memory:

```bash
treehawk run --quiet -o pytest.jsonl -- uv run pytest
treehawk report pytest.jsonl --json | jq -e '.summary.peak_pss_bytes < 2 * 1024 * 1024 * 1024'
```
