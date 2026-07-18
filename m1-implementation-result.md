
M1 is done and verified. The whole plan.md Deliverable 1 is implemented, and every check in TESTING.md passed on this machine.

Verification results

- cargo fmt --check, clippy --all-targets -- -D warnings, cargo test — all green: 19 unit tests + 5 integration tests (the suite drives the real binary).
- AT-1 (no child escapes) — live run of bash -c 'sleep 1 & disown; sleep 2' in cgroup mode recorded the disowned sleep with its own 1s lifetime. (Only two rows appear because bash execs its final command — the target PID is the 2s sleep; that's correct, not a miss.)
- FR-22 — exit codes pass through (exit 42 → 42, plus live exit-0 run).
- AT-4 — integration test SIGKILLs treehawk mid-run; the session stays readable, report flags "not finalized" and recovers data from the .arrows WAL tail.
- pandas promise — pd.read_parquet opens samples/host/processes directly (pandas 3.0.3): correct nulls on first samples, PSS decimated to 1 Hz, GPU columns reserved as null for M2, labels and target exit code in the processes table.
- NFR-1 overhead smoke — 51-process tree at 10 Hz: treehawk averaged 0.9% of one core (pidstat), and the manifest shows 451 ticks, 0 overruns, exactly 10.0 Hz achieved.

Also done this session

- README status bumped to "M1 usable" with M1 checked off in the roadmap (plan step 9).
- Two clippy-strict fixes and the lib+bin restructure so tests (and later PyO3) link the crate.
- Saved memory notes: M1 status/next steps (M2 = NVML GPU backend), and this box's quirks (sudo needs an external terminal; the pip bootstrap path).

Everything is uncommitted in the working tree — say the word and I'll commit it (happy to split into logical commits, e.g. scaffold / core / tests / docs). Natural next step after that is M2: the GpuBackend trait with NVML first, since your RTX card is the reference hardware.