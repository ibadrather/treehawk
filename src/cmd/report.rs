//! `treehawk report <session>`: human-readable session summary (FR-19).
//!
//! Reads the manifest, all rotated Parquet chunks, and — transparently — any
//! leftover `*-active.arrows` tail from a crashed session (AT-4).

use std::collections::HashMap;
use std::fs::File;
use std::io::IsTerminal;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result, bail};
use arrow::array::{Array, AsArray};
use arrow::datatypes::{Int32Type, UInt32Type, UInt64Type};
use arrow::ipc::reader::StreamReader;
use arrow::record_batch::RecordBatch;
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;

use crate::cli::ReportArgs;
use crate::manifest::Manifest;
use crate::util::{human_bytes, human_duration};

pub fn report(args: ReportArgs) -> Result<()> {
    let dir = &args.session;
    if !dir.join("session.json").exists() {
        bail!(
            "{} is not a treehawk session (no session.json)",
            dir.display()
        );
    }
    let manifest = Manifest::load(dir).context("reading session.json")?;
    let samples = read_table(dir, "samples")?;
    let processes = read_processes(dir)?;

    let clk_tck = manifest.host.clk_tck as f64;
    let n_cpus = manifest.host.n_cpus as f64;

    let mut stats: HashMap<u32, ProcStats> = HashMap::new();
    for batch in &samples {
        accumulate(batch, &mut stats, clk_tck, n_cpus)?;
    }

    print_summary(&manifest, dir, &mut stats, &processes);
    Ok(())
}

#[derive(Default)]
struct ProcStats {
    samples: u64,
    cpu_pcts: Vec<f64>,
    peak_rss_kb: u64,
    first_t_ns: u64,
    last_t_ns: u64,
}

/// Identity fields pulled from the processes table.
struct ProcInfo {
    pid: i32,
    exe_name: String,
    labels: Option<String>,
    exit_code: Option<i32>,
    first_seen_ns: u64,
    last_seen_ns: u64,
}

fn accumulate(
    batch: &RecordBatch,
    stats: &mut HashMap<u32, ProcStats>,
    clk_tck: f64,
    n_cpus: f64,
) -> Result<()> {
    let col = |name: &str| {
        batch
            .column_by_name(name)
            .with_context(|| format!("samples table missing column {name}"))
    };
    let t = col("t_mono_ns")?.as_primitive::<UInt64Type>();
    let proc_id = col("proc_id")?.as_primitive::<UInt32Type>();
    let dt = col("dt_ns")?.as_primitive::<UInt64Type>();
    let utime = col("cpu_utime_ticks")?.as_primitive::<UInt64Type>();
    let stime = col("cpu_stime_ticks")?.as_primitive::<UInt64Type>();
    let rss = col("vm_rss_kb")?.as_primitive::<UInt64Type>();
    for row in 0..batch.num_rows() {
        let entry = stats.entry(proc_id.value(row)).or_default();
        entry.samples += 1;
        entry.peak_rss_kb = entry.peak_rss_kb.max(rss.value(row));
        let t_ns = t.value(row);
        if entry.first_t_ns == 0 {
            entry.first_t_ns = t_ns;
        }
        entry.last_t_ns = entry.last_t_ns.max(t_ns);
        if !dt.is_null(row) && dt.value(row) > 0 && !utime.is_null(row) {
            let cpu_seconds = (utime.value(row) + stime.value(row)) as f64 / clk_tck;
            let wall_seconds = dt.value(row) as f64 / 1e9;
            // Machine-normalized: 100% = every core busy (NFR-7).
            entry
                .cpu_pcts
                .push(cpu_seconds / wall_seconds / n_cpus * 100.0);
        }
    }
    Ok(())
}

/// All chunks of one table, oldest first, plus any readable WAL tail.
fn read_table(dir: &Path, prefix: &str) -> Result<Vec<RecordBatch>> {
    let mut chunk_paths: Vec<PathBuf> = std::fs::read_dir(dir)?
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.starts_with(&format!("{prefix}-")) && n.ends_with(".parquet"))
        })
        .collect();
    chunk_paths.sort();
    let mut batches = Vec::new();
    for path in chunk_paths {
        let reader = ParquetRecordBatchReaderBuilder::try_new(File::open(&path)?)
            .with_context(|| format!("opening {}", path.display()))?
            .build()?;
        for batch in reader {
            batches.push(batch?);
        }
    }
    let wal = dir.join(format!("{prefix}-active.arrows"));
    if wal.exists()
        && let Ok(reader) = StreamReader::try_new(File::open(&wal)?, None)
    {
        // A truncated tail after SIGKILL simply ends the iteration early.
        batches.extend(reader.flatten());
    }
    Ok(batches)
}

fn read_processes(dir: &Path) -> Result<HashMap<u32, ProcInfo>> {
    let batches = {
        let final_table = dir.join("processes.parquet");
        if final_table.exists() {
            let reader =
                ParquetRecordBatchReaderBuilder::try_new(File::open(&final_table)?)?.build()?;
            reader.collect::<std::result::Result<Vec<_>, _>>()?
        } else {
            read_table(dir, "processes")? // WAL only (crashed session)
        }
    };
    let mut out = HashMap::new();
    for batch in &batches {
        let col = |name: &str| {
            batch
                .column_by_name(name)
                .with_context(|| format!("processes table missing column {name}"))
        };
        let proc_id = col("proc_id")?.as_primitive::<UInt32Type>();
        let pid = col("pid")?.as_primitive::<Int32Type>();
        let exe_name = col("exe_name")?.as_string::<i32>();
        let labels = col("labels")?.as_string::<i32>();
        let exit_code = col("exit_code")?.as_primitive::<Int32Type>();
        let first_seen = col("first_seen_ns")?.as_primitive::<UInt64Type>();
        let last_seen = col("last_seen_ns")?.as_primitive::<UInt64Type>();
        for row in 0..batch.num_rows() {
            out.insert(
                proc_id.value(row),
                ProcInfo {
                    pid: pid.value(row),
                    exe_name: exe_name.value(row).to_string(),
                    labels: (!labels.is_null(row)).then(|| labels.value(row).to_string()),
                    exit_code: (!exit_code.is_null(row)).then(|| exit_code.value(row)),
                    first_seen_ns: first_seen.value(row),
                    last_seen_ns: last_seen.value(row),
                },
            );
        }
    }
    Ok(out)
}

fn percentile(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let idx = ((sorted.len() - 1) as f64 * p).round() as usize;
    sorted[idx]
}

fn print_summary(
    manifest: &Manifest,
    dir: &Path,
    stats: &mut HashMap<u32, ProcStats>,
    processes: &HashMap<u32, ProcInfo>,
) {
    // Color only on a TTY (FR-21).
    let tty = std::io::stdout().is_terminal();
    let (bold, dim, reset) = if tty {
        ("\x1b[1m", "\x1b[2m", "\x1b[0m")
    } else {
        ("", "", "")
    };

    println!("{bold}Session{reset} {}", dir.display());
    println!("  command:  {}", manifest.command.join(" "));
    println!(
        "  host:     {} · {} · kernel {} · {} CPUs",
        manifest.host.hostname, manifest.host.cpu_model, manifest.host.kernel, manifest.host.n_cpus
    );
    println!(
        "  tracking: {}{}",
        manifest.tracking_mode,
        manifest
            .cgroup_path
            .as_deref()
            .map(|p| format!(" ({p})"))
            .unwrap_or_default()
    );
    let interval_ms = manifest.sampling.interval_ns as f64 / 1e6;
    match &manifest.finished {
        Some(f) => {
            let duration =
                (f.ended_mono_ns
                    .saturating_sub(manifest.clock_anchor.monotonic_ns)) as f64
                    / 1e9;
            println!(
                "  sampling: {interval_ms} ms interval · {} ticks · {} overruns · {:.1} Hz achieved",
                f.ticks, f.overruns, f.achieved_rate_hz
            );
            println!(
                "  duration: {} · target exit: {}{}",
                human_duration(duration),
                f.target_exit_code
                    .map(|c| c.to_string())
                    .unwrap_or_else(|| "unknown".into()),
                if f.descendants_alive_at_exit > 0 {
                    format!(
                        " · {} descendant(s) still alive at exit",
                        f.descendants_alive_at_exit
                    )
                } else {
                    String::new()
                }
            );
        }
        None => {
            println!("  sampling: {interval_ms} ms interval");
            println!(
                "  {bold}note:{reset} session was not finalized (crashed or still running); \
                 data recovered from the active chunk"
            );
        }
    }
    println!();

    let mut proc_ids: Vec<u32> = stats.keys().copied().collect();
    proc_ids.sort_by(|a, b| {
        let mean = |id: &u32| {
            let s = &stats[id];
            if s.cpu_pcts.is_empty() {
                0.0
            } else {
                s.cpu_pcts.iter().sum::<f64>() / s.cpu_pcts.len() as f64
            }
        };
        mean(b).total_cmp(&mean(a))
    });

    println!(
        "{bold}{:<24} {:>7} {:>8} {:>8} {:>8} {:>8} {:>10} {:>9} {:>5}{reset}",
        "PROCESS",
        "PID",
        "SAMPLES",
        "CPU%mean",
        "CPU%p95",
        "CPU%peak",
        "RSS peak",
        "LIFETIME",
        "EXIT"
    );
    for proc_id in proc_ids {
        let s = stats.get_mut(&proc_id).expect("key from stats");
        s.cpu_pcts.sort_by(f64::total_cmp);
        let mean = if s.cpu_pcts.is_empty() {
            0.0
        } else {
            s.cpu_pcts.iter().sum::<f64>() / s.cpu_pcts.len() as f64
        };
        let p95 = percentile(&s.cpu_pcts, 0.95);
        let peak = s.cpu_pcts.last().copied().unwrap_or(0.0);
        let info = processes.get(&proc_id);
        let name = info.map_or_else(
            || format!("proc#{proc_id}"),
            |i| match &i.labels {
                Some(l) => format!("{} [{}]", i.exe_name, l),
                None => i.exe_name.clone(),
            },
        );
        let lifetime = info
            .map(|i| (i.last_seen_ns.saturating_sub(i.first_seen_ns)) as f64 / 1e9)
            .unwrap_or_else(|| (s.last_t_ns.saturating_sub(s.first_t_ns)) as f64 / 1e9);
        println!(
            "{:<24} {:>7} {:>8} {:>8.1} {:>8.1} {:>8.1} {:>10} {:>9} {:>5}",
            truncate(&name, 24),
            info.map(|i| i.pid).unwrap_or(-1),
            s.samples,
            mean,
            p95,
            peak,
            human_bytes(s.peak_rss_kb * 1024),
            human_duration(lifetime),
            info.and_then(|i| i.exit_code)
                .map(|c| c.to_string())
                .unwrap_or_else(|| "-".into()),
        );
    }
    if stats.is_empty() {
        println!("{dim}(no samples recorded — target likely exited within one interval){reset}");
    }
    // Processes seen (identity) but never sampled, e.g. died between ticks.
    let unsampled: Vec<&ProcInfo> = processes
        .iter()
        .filter(|(id, _)| !stats.contains_key(id))
        .map(|(_, info)| info)
        .collect();
    if !unsampled.is_empty() {
        println!(
            "{dim}+ {} process(es) seen but never sampled: {}{reset}",
            unsampled.len(),
            unsampled
                .iter()
                .map(|i| i.exe_name.as_str())
                .collect::<Vec<_>>()
                .join(", ")
        );
    }
}

fn truncate(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        s.to_string()
    } else {
        let cut: String = s.chars().take(max - 1).collect();
        format!("{cut}…")
    }
}
