//! Run-mode orchestration: session setup, launch, sample, finalize (plan §D1).

use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::Arc;
use std::time::Duration;

use anyhow::{Context, Result};

use crate::cgroup::{CgroupTracker, PidTreeTracker, TrackingMode};
use crate::cli::RunArgs;
use crate::manifest::{Finished, Manifest};
use crate::sampler::{SamplerOptions, SamplerStats, StopSignal, Tracker, spawn_sampler};
use crate::spawn::{TargetExit, install_signal_handlers, spawn_target, wait_target};
use crate::util::{monotonic_ns, utc_timestamp_compact};
use crate::writer::{WriterMsg, WriterOptions, spawn_writer};

/// PSS decimation: aim for ~1 Hz regardless of the sampling rate.
fn pss_every_ticks(interval: Duration) -> u32 {
    let per_second = (1.0 / interval.as_secs_f64()).round() as u32;
    per_second.max(1)
}

pub fn run(args: &RunArgs) -> ExitCode {
    match run_inner(args) {
        Ok(code) => code,
        Err(err) => {
            eprintln!("treehawk: error: {err:#}");
            ExitCode::FAILURE
        }
    }
}

fn run_inner(args: &RunArgs) -> Result<ExitCode> {
    install_signal_handlers().context("installing signal handlers")?;

    let session_id = utc_timestamp_compact();
    let session_dir: PathBuf = args
        .out
        .clone()
        .unwrap_or_else(|| PathBuf::from("treehawk").join(&session_id));
    std::fs::create_dir_all(&session_dir)
        .with_context(|| format!("creating session dir {}", session_dir.display()))?;

    // Containment: cgroup v2, or PID-tree fallback with the FR-3 warning.
    let cgroup = match CgroupTracker::create(&session_id) {
        Ok(c) => Some(c),
        Err(err) => {
            eprintln!(
                "treehawk: warning: cgroup tracking unavailable ({err}); falling back to \
                 PID-tree tracking — daemonizing (double-forking) descendants may be missed"
            );
            None
        }
    };
    let mode = if cgroup.is_some() {
        TrackingMode::Cgroup
    } else {
        TrackingMode::PidTree
    };

    let interval_ns = args.interval.as_nanos() as u64;
    let pss_ticks = pss_every_ticks(args.interval);
    let manifest = Manifest::new(
        session_id,
        args.command.clone(),
        args.label.clone(),
        interval_ns,
        pss_ticks,
        mode.as_str(),
        cgroup
            .as_ref()
            .map(|c| c.path().to_string_lossy().into_owned()),
    );
    manifest
        .write(&session_dir)
        .context("writing session.json")?;

    let (tx, writer_handle) = spawn_writer(WriterOptions::new(session_dir.clone()));

    let procs_fd = cgroup
        .as_ref()
        .map(super::super::cgroup::CgroupTracker::procs_write_fd)
        .transpose()
        .context("opening cgroup.procs for the child")?;
    let mut child = match spawn_target(&args.command, procs_fd) {
        Ok(child) => child,
        Err(err) => {
            // Nothing was sampled: shut the writer down cleanly, then report
            // like a shell would (127 not-found / 126 not-executable).
            let _ = tx.send(WriterMsg::Finalize { processes: vec![] });
            drop(tx);
            let _ = writer_handle.join();
            if let Some(c) = cgroup {
                c.cleanup();
            }
            eprintln!("treehawk: failed to run {:?}: {err}", args.command[0]);
            let code = match err.kind() {
                std::io::ErrorKind::NotFound => 127,
                std::io::ErrorKind::PermissionDenied => 126,
                _ => 125,
            };
            return Ok(ExitCode::from(code));
        }
    };
    let target_pid = child.id() as i32;

    let tracker = match cgroup {
        Some(c) => Tracker::Cgroup(c),
        None => Tracker::PidTree(PidTreeTracker::new(target_pid)),
    };
    let stop = Arc::new(StopSignal::default());
    let sampler_handle = spawn_sampler(
        SamplerOptions {
            interval: args.interval,
            pss_every_ticks: pss_ticks,
            labels: (!args.label.is_empty()).then(|| args.label.join(",")),
        },
        tracker,
        target_pid,
        tx,
        Arc::clone(&stop),
    );

    let exit = wait_target(&mut child).context("waiting for target")?;
    stop.request_stop(exit.code_for_manifest());

    let stats: SamplerStats = sampler_handle
        .join()
        .map_err(|_| anyhow::anyhow!("sampler thread panicked"))?;
    writer_handle
        .join()
        .map_err(|_| anyhow::anyhow!("writer thread panicked"))?
        .context("writer failed")?;

    finalize_manifest(&session_dir, manifest, &stats, exit, interval_ns)?;
    Ok(ExitCode::from(exit.exit_code()))
}

fn finalize_manifest(
    session_dir: &std::path::Path,
    mut manifest: Manifest,
    stats: &SamplerStats,
    exit: TargetExit,
    interval_ns: u64,
) -> Result<()> {
    let ended = monotonic_ns();
    let duration_s = (ended.saturating_sub(manifest.clock_anchor.monotonic_ns)) as f64 / 1e9;
    let expected_rate = 1e9 / interval_ns as f64;
    manifest.finished = Some(Finished {
        ended_mono_ns: ended,
        target_exit_code: exit.code_for_manifest(),
        ticks: stats.ticks,
        overruns: stats.overruns,
        achieved_rate_hz: if duration_s > 0.0 {
            (stats.ticks as f64 / duration_s).min(expected_rate)
        } else {
            0.0
        },
        descendants_alive_at_exit: stats.descendants_alive_at_exit,
    });
    manifest
        .write(session_dir)
        .context("finalizing session.json")?;
    Ok(())
}
