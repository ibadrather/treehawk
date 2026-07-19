# treehawk v0.1.2 — Test Report

**Date:** 2026-07-19 · **Host:** proart, i9-13980HX (32 CPUs), Linux 7.0.0-27-generic · **Install:** release installer → `~/.cargo/bin/treehawk` (9.27 MB) · **Baseline:** [v0.1.1 report](treehawk-v0.1.1-test-report.md), protocol per [agent test instructions](treehawk-agent-test-instructions.md)

## Verdict

**All five open issues from v0.1.1 are fixed, and every fix was verified — not just observed in the changelog.** Core accuracy, the exit-code contract, and performance all still hold. Three new minor findings, all cosmetic or acceptable trade-offs; nothing blocks release.

## Fixes from v0.1.1 — verified

| v0.1.1 issue | v0.1.2 behavior | Verified how |
|---|---|---|
| CPU% machine-normalized (core-saturating proc showed 3.1%) | **Per-core convention**: burner displays 100.0% mean | Recomputed from raw ticks: 100.0% of one core — display now matches raw data |
| `--out` silently overwrote existing sessions | **Refuses non-empty dir**: "output directory is not empty … use a new or empty directory", exit 1 | Ran `--out` at an existing session; old data intact |
| Short-lived processes' CPU unattributed | New report line: `cpu: 4.2s total tree CPU · 0.2s unattributed to sampled processes` from cgroup `cpu.stat` (`cgroup_cpu_usage_usec` in session.json) | 200 × `/bin/true` run shows `0.1s total · 0.1s unattributed` — the invisible work is now visible in aggregate |
| SIGKILL lost the entire active chunk | **Streaming `-active.arrows` files**: after kill -9 at 1.5 s, report recovered 9 samples (v0.1.1: zero) and the "recovered from the active chunk" note is now accurate | kill -9 mid-run, inspected surviving files, report readable |
| No argv capture | New **`--cmdline`** flag adds a `cmdline` column to `processes.parquet`; **off by default** with a stated rationale (command lines can contain secrets), column null without the flag | Checked column contents with and without the flag |

The opt-in-with-rationale design for `--cmdline` is the right call.

## Regression suite — all pass

- Smoke, full-tree capture (6 processes, 3 levels, labels applied), stub subcommands exit 2 with "planned milestone" message.
- Memory accuracy: 300 MiB allocation → 309.8 MiB RSS peak.
- CPU accuracy: burner = 100.0% of one core from raw ticks; 2-core fork test shows 2 × 100.0%.
- Exit codes: 42 → 42, missing command → 127, bad report path → 1, `--interval 0ms` → 2, SIGINT → 130 with clean finalization.
- Orphan detection: busy-cgroup warning printed, treehawk exits promptly with target's code.
- 1 ms sampling: 996.5 Hz achieved, 0 overruns. stdin/stdout passthrough intact.
- Data integrity: finalized sessions contain only parquet + session.json (arrows files converted, no leftovers); timestamps monotonic; host rows = tick count; PSS cadence matches `pss_every_ticks`; ppid links form a tree; labels present.
- Overhead at default 100 ms: 0.02 s CPU over 5 s (~0.4% of one core), ~9.5 MB RSS — unchanged.

## New findings (ranked)

1. **Newlines in cmdline break the report table.** With `--cmdline`, a target like `python3 -c $'\nimport time…'` renders its embedded newlines literally, splitting one table row across lines and misaligning columns. Sanitize whitespace (replace `\n`/`\t` with spaces) for display. Repro: `treehawk run --cmdline --out X -- python3 -c $'\nprint(1)'` then `report X`.
2. **CPU%peak/p95 can exceed 100% for single-threaded processes.** gzip (one thread) showed peak 120.1% and p95 120.0% at 50 ms sampling. Cause: `clk_tck=100` quantization — a 50 ms window holds 5 ticks nominally, but 6 ticks are sometimes observed (6/5 = 120%). Mean is unaffected (96.0% matches raw). Cosmetic but will confuse users; consider smoothing over ≥2 ticks or noting the granularity floor.
3. **1 ms-interval overhead roughly doubled**: 1.26 s CPU per 5 s run (~25% of one core) vs 0.67 s in v0.1.1 — presumably the price of streaming durability (finding 4's fix). Default-interval overhead is unchanged, so this is an acceptable trade-off; noting it as the baseline for future runs.
4. **Default session dir is now `./treehawk/<uuid>`** (was `<timestamp>Z`). UUIDs don't sort chronologically and are unreadable at a glance; with `ls` still unimplemented there's no way to browse sessions. Consider `<timestamp>-<short-suffix>` to keep collision safety and sortability.

## Still open / untested

- `watch`, `export`, `ls`, `config`, `service` remain stubs (M3/M5) — with sessions accumulating, `ls` and `export` are the next ergonomic gaps.
- GPU sampling columns still untested (no NVIDIA GPU on this host).
- `schema_version` stayed 1 despite the additive `cmdline` column — fine for parquet consumers, just noting the choice.
- Feature wishes from v0.1.1 still relevant: tree-indented report view (ppid is recorded), whole-tree summary row (total tree CPU line is a good start — peak aggregate RSS would complete it), `report --json`.

## Reproduction artifacts

All v0.1.2 sessions under the scratchpad `v012/` directory (`smoke`, `tree-test`, `cpu2`, `shortlived`, `sigint`, `crashed`, `orphan2`, `fast`, `oh1`, `oh2`, `stdin`, `nocmdline`).
