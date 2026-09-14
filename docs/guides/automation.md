# Scripts, CI and long watches

treehawk is a good citizen in a script: it has a quiet mode, exit codes you can
test, and output that stays readable when nobody is watching the terminal.

## Without a terminal

The dashboard is drawn only when standard error is a terminal. Piped into a file
or captured by CI, treehawk prints plain lines instead, since cursor control in a
log is noise. `--quiet` turns screen output off entirely; the log is still
written.

```bash
treehawk run --quiet --output job.jsonl -- ./job.sh
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | The run finished, or was stopped with `Ctrl-C` or `SIGTERM` |
| `1` | treehawk could not do what was asked: an invalid option, a log it cannot read, a command it could not start, or an unsupported platform |
| `2` | Nothing matched the process you named (`watch` without `--wait`), or the command line itself was invalid |
| *the command's* | Under `run`, a command that fails passes its exit status through |

Every error is a single line on standard error, starting with `treehawk:`.

## In CI

Measure a test suite, keep the report, and fail the job if it used too much
memory:

```yaml
- name: Test, measured
  run: |
    uvx --from git+https://github.com/ibadrather/treehawk \
      treehawk run --quiet --interval 0.5 --output pytest.jsonl -- uv run pytest

- name: Report
  if: always()
  run: |
    uvx --from git+https://github.com/ibadrather/treehawk treehawk report pytest.jsonl
    uvx --from git+https://github.com/ibadrather/treehawk treehawk pdf pytest.jsonl

- name: Stay under 2 GiB
  run: |
    uvx --from git+https://github.com/ibadrather/treehawk treehawk report pytest.jsonl --json \
      | jq -e '.summary.peak_pss_bytes < 2 * 1024 * 1024 * 1024'

- uses: actions/upload-artifact@v4
  if: always()
  with:
    name: resource-usage
    path: pytest.*
```

`jq -e` exits non-zero when the expression is false, which fails the step.

!!! note "CI runners rarely have a systemd user session"

    Without one, `run` tracks the command through `/proc` instead of a cgroup,
    and says so in the log header's `notes`. The numbers are still good; see
    [run and watch](../concepts/run-vs-watch.md) for what changes.

## Leaving a watch running

A watch is meant to be left alone for as long as the workload lives, even for
days. Its memory use does not grow: the dashboard history, the set of processes
it has seen and the summary tables are all capped.

The log does grow, at roughly 1 KB per sample per five processes: about 90 MB a
day at the default one-second interval. For a long run, keep it small:

```bash
treehawk watch --wait --quiet --interval 5 --aggregate-only \
  --output ~/logs/backup.jsonl nightly-backup
```

- `--interval 5` samples a fifth as often.
- `--aggregate-only` keeps the workload totals and drops the row per process.
  The PDF then leaves out the per-process pages.
- `--no-pss` skips reading `smaps_rollup`, which matters most at short intervals
  and with many processes; PSS is then `null` in the log.

To keep a watch running after you log out, start it as a transient user service:

```console
$ systemd-run --user --unit=watch-backup \
    treehawk watch --wait --quiet --interval 5 --output %h/logs/backup.jsonl nightly-backup
$ journalctl --user -u watch-backup -f
```

When the backup finishes, the watch writes its summary and exits, and the unit
goes with it.
