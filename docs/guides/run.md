# Starting a command

`treehawk run` starts a command itself and measures it from its first instant to
its last. It is the exact way to use treehawk.

```console
$ treehawk run -- python train.py --epochs 10
```

Everything after `--` is the command, passed on untouched. Options for treehawk
go before it:

```console
$ treehawk run --interval 0.5 --output train.jsonl -- python train.py --epochs 10
```

## Why it is exact

treehawk starts the command inside a transient systemd scope,
`treehawk-<pid>-<time>.scope`, which gives it a cgroup of its own:

```
systemd-run --user --scope --collect --quiet --unit=treehawk-<pid>-<time> -- <your command>
```

A process can change its parent and its session, but it cannot leave its cgroup
by forking. So under `run`:

- **membership is a kernel fact.** Every descendant, detached or not, is in the
  scope.
- **CPU and memory totals come from the kernel's own counters** (`cpu.stat`,
  `memory.current`, `memory.peak`), so they include processes that were born and
  died between two samples.
- **nothing is missed before the first sample**, because the workload does not
  exist until treehawk creates it.

<figure class="diagram" markdown="span">
  ![run gives the command its own cgroup; watch infers membership inside a shared one](../assets/diagrams/run-vs-watch.light.svg#only-light)
  ![run gives the command its own cgroup; watch infers membership inside a shared one](../assets/diagrams/run-vs-watch.dark.svg#only-dark)
</figure>

## When there is no systemd

If `systemd-run` is missing, there is no user session bus (common in containers
and some CI runners), or cgroup v2 is not mounted, treehawk starts the command
directly and tracks it through `/proc`, as `watch` does. It does not fail, and it
does not hide the downgrade: the reason is written to the `notes` in the
[log header](../reference/log-format.md#header).

To choose that mode on purpose, pass `--no-isolate`:

```console
$ treehawk run --no-isolate -- make -j16
```

## Exit codes and signals

treehawk behaves like a wrapper you can put in front of a command in a script:

- If the command exits with a non-zero status, `treehawk run` exits with that
  status too.
- `Ctrl-C` or `SIGTERM` sent to treehawk is forwarded to the command, and the log
  is closed with a complete summary.
- If monitoring ends first, for example because `--duration` has passed, the
  command is sent `SIGTERM`, given five seconds to exit, and then killed.

```bash
treehawk run --quiet --output build.jsonl -- make -j16 || echo "build failed"
```

The exit code of the command is also recorded in the log, as `exit_code` in the
summary.

## Examples

```bash
# A test suite, sampled every quarter second
treehawk run -i 0.25 -o pytest.jsonl -- uv run pytest

# A build, as CSV for a spreadsheet (writes build.csv, build.procs.csv, ...)
treehawk run --csv -o build.csv -- cargo build --release

# A long job, keeping the log small
treehawk run --aggregate-only -i 5 -o etl.jsonl -- ./etl.sh --full

# Stop measuring after ten minutes (the command is stopped too)
treehawk run --duration 600 -- ./benchmark
```

See the [command reference](../reference/cli.md#treehawk-run) for every option.
