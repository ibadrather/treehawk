# prowatch

Log the CPU and RAM a process uses — **and every process it spawns**, including
children that daemonize and detach themselves.

Point it at something already running, by keyword or by the exact command line:

```bash
prowatch watch train.py                       # substring of the command line
prowatch watch --exact "python train.py"      # the whole command line
prowatch watch --regex 'worker-\d+'
prowatch watch --pid 4213
```

…or let prowatch start the command, which is more accurate (see below):

```bash
prowatch run -- python train.py --epochs 10
```

Then read the log back:

```bash
prowatch report prowatch-20260913-100000.jsonl
```

Linux only for now. No third-party dependencies: everything comes from `/proc`
and cgroup v2 directly, not from `ps`, `top` or `pidstat`.

## Install

```bash
uv sync            # or: pip install -e .
uv run prowatch --help
```

## The problem it solves

Monitoring "a process and its children" by walking parent-child links works
right up until a child daemonizes — `fork`, `setsid`, `fork` again, original
parent exits. The survivor is re-parented to PID 1 (or to a subreaper such as
`systemd --user`) and has left its parent's session. A moment later there is no
link left to follow, and a naive monitor reports that the workload finished
while it is still burning a core.

prowatch keeps membership by four independent rules and *never evicts* a
process once admitted — it stays tracked until that exact process exits, no
matter what its parent becomes:

| rule | catches |
|---|---|
| `tree` | ordinary children and grandchildren |
| `cgroup` | anything inside a control group the workload owns — the one boundary a fork cannot escape |
| `session` | children re-parented away that kept the session |
| `orphan` | a process that appeared during the watch, lost its parent, and sits in a tracked process' cgroup |

Choose them with `--expand tree,cgroup,orphan` (all four are on by default).

The `orphan` rule is the one that closes the daemonization gap in attach mode.
It recognises re-parenting by noticing that the adopting reaper lives in a
*different* cgroup, which is what separates a detached grandchild from an
unrelated process started in the same terminal.

### `run` is exact; `watch` is very good

`prowatch run` starts the command inside its own transient cgroup
(`systemd-run --user --scope`). Membership is then a kernel fact rather than an
inference, CPU and memory totals come from `cpu.stat` and `memory.current`, and
nothing is missed before the first sample — including processes that are born
and die between two samples. If systemd is unavailable prowatch falls back to
`/proc` tracking and says so in the log header.

`prowatch watch` has to infer membership. It is reliable in practice, but a
process that detaches *and* moves itself to an unrelated cgroup in the gap
between two samples can be missed. Use `run` when exactness matters.

## Output

JSON Lines by default: a `header` record, one `sample` per interval, and a
`summary` at the end. Records are flushed as they are written, so a killed run
still leaves a readable log.

```jsonc
{"type":"sample","seq":12,"t":6.0,"ts":"...","n_procs":3,
 "cpu_percent":287.4,          // sum across processes; 100% = one core
 "cpu_percent_norm":9.0,       // of the whole machine
 "cpu_seconds_total":18.4,     // lifetime cpu time, incl. exited children
 "cpu_seconds_used":17.9,      // ...since prowatch attached
 "rss_bytes":1379926016,       // sum of RSS: double-counts shared pages
 "pss_bytes":1104150528,       // shared pages divided fairly - the honest number
 "swap_bytes":0,
 "group_memory_bytes":1107296256,      // the kernel's own figure, when available
 "group_memory_peak_bytes":1342177280,
 "overrun":false,
 "procs":[{"pid":9001,"ppid":1,"name":"python","cmdline":"python train.py",
           "cpu_percent":99.8,"rss_bytes":...,"pss_bytes":...,"via":"orphan"}]}
```

`via` says which rule found each process — useful when a result surprises you.

`--format csv` writes `run.csv` (one row per sample) and `run.procs.csv` (one
row per process per sample, joinable on `seq`), with the header and summary
alongside as JSON.

### Which memory number to trust

Three are recorded because each is wrong in a different way:

* **`rss_bytes`** — always available, but summing RSS over a fork tree counts
  every shared page once per child. Overstates real use, sometimes by a lot.
* **`pss_bytes`** — each shared page divided among the processes mapping it, so
  the sum is what the workload actually costs the machine. Needs permission to
  read `smaps_rollup` (same user). Disable with `--no-pss` if sampling fast.
* **`group_memory_bytes`** — the kernel's own charge for the cgroup. Exact when
  a boundary exists, `null` otherwise.

## Options worth knowing

```
-i, --interval SECONDS   sampling period (default 1.0)
-o, --output PATH        log file; '-' for stdout
-f, --format jsonl|csv
-d, --duration SECONDS   stop after this long
-n, --max-samples N      stop after N samples
    --wait [SECONDS]     wait for the process to appear (watch)
    --rescan             re-run the matcher every sample, catching restarts
    --expand RULES       membership rules, or 'none'
    --no-pss             skip smaps_rollup (cheaper at short intervals)
    --no-per-process     log only the aggregate
    --keep-going         keep sampling after the last process exits
    --show-procs N       print the top N processes live
    --no-isolate         (run) do not create a cgroup
    --leave-running      (run) do not stop the workload when monitoring ends
-q, --quiet
```

Sampling uses absolute deadlines, so intervals do not drift. A sample that
takes longer than the interval is flagged `overrun` and counted in the summary.

## Limitations

* **Processes that live and die between two samples** are invisible to polling.
  Their CPU still lands in the totals under `run` mode (the cgroup counter sees
  them); under `watch` it is lost. Shorten `--interval` or use `run`.
* **`watch` can miss a process that detaches and changes cgroup** in the gap
  between samples.
* **Another user's processes** expose `stat` but not `smaps_rollup`, so `pss`
  will be `null`. prowatch never fakes a value it could not read.
* **`--rescan` with a keyword** will pick up any new process matching it, which
  is what you want for a restarting service and not what you want otherwise.
* prowatch never adopts its own ancestors (your shell, `uv`, `timeout`), since
  they carry the keyword you typed.

## Design

```
core/         platform-agnostic policy - models, interfaces, membership, sampling
  interfaces.py   the narrow protocols everything else depends on
  tracker.py      membership: sticky admission, exit accounting
  strategies.py   one class per membership rule
  matchers.py     one class per way of naming the workload
  monitor.py      the sampling loop; knows nothing about Linux
  aggregate.py    counters -> rates; samples -> summary
platforms/    concrete OS implementations of those protocols
  linux/          procfs.py, cgroup2.py, source.py, launcher.py
gpu/          the seam for GPU metrics (protocol defined, nothing registered yet)
sinks/        jsonl, csv, console, and a composite
cli.py        the composition root: the only module that wires it all together
```

`core` never imports `platforms`; the CLI injects the implementations. Adding a
membership rule, an output format, a matcher or a metric collector means adding
a class and a registry entry — not editing the loop.

**Adding GPU metrics** later: implement `MetricCollector` (`namespace`,
`collect(snapshot)`, `close()`), register it in `gpu/__init__.py`, and its keys
appear in every sample under its namespace. The loop, the schema and the sinks
are untouched.

**Adding another OS**: implement `ProcessSource`, `HostInfoSource` and
optionally `GroupMetricSource`/`ProcessLauncher` in `platforms/<os>/`, then
register a builder in `platforms/registry.py`.

## Tests

```bash
uv run pytest                     # everything
uv run pytest -m "not integration"   # fast: fixture /proc trees only
```

The unit tests run against fake `/proc` and cgroup trees (every reader takes its
root as an argument), so they need no privileges and no real workload. The
integration tests spawn `tests/workload.py`, which deliberately double-forks a
detached child, and assert it is still in the log after its parent is gone.
