//! The sampling thread: absolute-deadline ticker, overrun accounting, and
//! per-tick batch assembly (FR-11, FR-12, FR-13).

use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::Sender;
use std::thread::JoinHandle;
use std::time::Duration;

use rustix::time::{ClockId, Timespec};

use crate::cgroup::{CgroupTracker, PidTreeTracker};
use crate::model::{ProcessRow, SampleRow};
use crate::proc::PidHandle;
use crate::proc::host::HostReader;
use crate::util::monotonic_ns;
use crate::writer::WriterMsg;

/// How members of the tracked set are enumerated each tick (FR-3).
pub enum Tracker {
    Cgroup(CgroupTracker),
    PidTree(PidTreeTracker),
}

impl Tracker {
    fn members(&mut self, scratch: &mut Vec<u8>) -> std::io::Result<Vec<i32>> {
        match self {
            Self::Cgroup(c) => c.members(scratch),
            Self::PidTree(p) => p.members(),
        }
    }
}

/// Set by the main thread when the target has exited.
#[derive(Default)]
pub struct StopSignal {
    stop: AtomicBool,
    /// The target's exit code (128+signal form), for the process table.
    target_exit_code: Mutex<Option<i32>>,
}

impl StopSignal {
    pub fn request_stop(&self, target_exit_code: Option<i32>) {
        if let Ok(mut guard) = self.target_exit_code.lock() {
            *guard = target_exit_code;
        }
        self.stop.store(true, Ordering::Release);
    }

    fn stopping(&self) -> bool {
        self.stop.load(Ordering::Acquire)
    }
}

pub struct SamplerOptions {
    pub interval: Duration,
    /// Read PSS every N ticks (0 = never); keeps `smaps_rollup` cost off the
    /// hot path at high rates.
    pub pss_every_ticks: u32,
    /// Comma-joined `--label` values, attached to every process row in M1.
    pub labels: Option<String>,
}

/// Sampler outcome for the manifest (FR-13, FR-11b groundwork).
pub struct SamplerStats {
    pub ticks: u64,
    pub overruns: u64,
    pub descendants_alive_at_exit: u32,
}

pub fn spawn_sampler(
    opts: SamplerOptions,
    tracker: Tracker,
    target_pid: i32,
    tx: Sender<WriterMsg>,
    stop: Arc<StopSignal>,
) -> JoinHandle<SamplerStats> {
    std::thread::Builder::new()
        .name("treehawk-sampler".into())
        .spawn(move || sampler_thread(&opts, tracker, target_pid, &tx, &stop))
        .expect("spawning the sampler thread cannot fail")
}

fn sampler_thread(
    opts: &SamplerOptions,
    mut tracker: Tracker,
    target_pid: i32,
    tx: &Sender<WriterMsg>,
    stop: &StopSignal,
) -> SamplerStats {
    let interval_ns = opts.interval.as_nanos() as u64;
    let mut scratch: Vec<u8> = Vec::with_capacity(4096);
    let mut host = HostReader::open().ok();
    let mut handles: HashMap<i32, PidHandle> = HashMap::new();
    let mut proc_ids: HashMap<i32, u32> = HashMap::new();
    let mut registry: HashMap<u32, ProcessRow> = HashMap::new();
    let mut next_proc_id: u32 = 0;
    let mut ticks: u64 = 0;
    let mut overruns: u64 = 0;
    let mut alive_at_exit: u32 = 0;

    let mut next = monotonic_ns() + interval_ns;
    loop {
        sleep_until(next, stop);
        let stopping = stop.stopping();
        let t = monotonic_ns();

        // --- one tick ---
        let members = tracker.members(&mut scratch).unwrap_or_default();
        let member_set: HashSet<i32> = members.iter().copied().collect();
        let want_pss = opts.pss_every_ticks != 0 && ticks % u64::from(opts.pss_every_ticks) == 0;
        let mut samples = Vec::with_capacity(members.len());
        let mut new_processes = Vec::new();

        for pid in members {
            if let std::collections::hash_map::Entry::Vacant(vacant) = handles.entry(pid) {
                // First sight of this PID: open fds and record identity. Any
                // failure means it died already or is off-limits — skip (NFR-4).
                let Ok(handle) = PidHandle::open(pid, &mut scratch) else {
                    continue;
                };
                let proc_id = next_proc_id;
                next_proc_id += 1;
                let id = &handle.identity;
                let exe_name = id.exe_name();
                let row = ProcessRow {
                    proc_id,
                    pid,
                    ppid: id.ppid,
                    uid: id.uid,
                    exe_path: id.exe_path.clone(),
                    exe_name,
                    starttime_ticks: id.starttime_ticks,
                    first_seen_ns: t,
                    last_seen_ns: t,
                    exit_code: None,
                    labels: opts.labels.clone(),
                };
                registry.insert(proc_id, row.clone());
                new_processes.push(row);
                vacant.insert(handle);
                proc_ids.insert(pid, proc_id);
            }
            let Some(handle) = handles.get_mut(&pid) else {
                continue;
            };
            let Some(&proc_id) = proc_ids.get(&pid) else {
                continue;
            };
            if let Ok(s) = handle.sample(want_pss, &mut scratch) {
                if s.stat.comm != handle.identity.comm {
                    // Exec after first sight: refresh the identity and re-emit
                    // the row so the WAL carries the corrected name too (the
                    // reader keeps the last row per proc_id).
                    handle.refresh_identity(&s.stat.comm);
                    if let Some(row) = registry.get_mut(&proc_id) {
                        row.exe_path.clone_from(&handle.identity.exe_path);
                        row.exe_name = handle.identity.exe_name();
                        new_processes.push(row.clone());
                    }
                }
                let (dt_ns, du, ds) = match handle.prev {
                    Some((pu, ps, pt)) => (
                        Some(t.saturating_sub(pt)),
                        Some(s.stat.utime_ticks.saturating_sub(pu)),
                        Some(s.stat.stime_ticks.saturating_sub(ps)),
                    ),
                    None => (None, None, None),
                };
                handle.prev = Some((s.stat.utime_ticks, s.stat.stime_ticks, t));
                if let Some(row) = registry.get_mut(&proc_id) {
                    row.last_seen_ns = t;
                }
                samples.push(SampleRow {
                    t_mono_ns: t,
                    proc_id,
                    pid,
                    dt_ns,
                    cpu_utime_ticks: du,
                    cpu_stime_ticks: ds,
                    num_threads: s.stat.num_threads,
                    vm_rss_kb: s.status.vm_rss_kb,
                    vm_swap_kb: s.status.vm_swap_kb,
                    vm_size_kb: s.status.vm_size_kb,
                    pss_kb: s.pss_kb,
                    voluntary_ctxt_switches: s.status.voluntary_ctxt_switches,
                    nonvoluntary_ctxt_switches: s.status.nonvoluntary_ctxt_switches,
                    gpu_util_pct: None,
                    gpu_mem_bytes: None,
                });
            } else {
                // Died mid-tick: drop the handle, keep the identity row.
                handles.remove(&pid);
                proc_ids.remove(&pid);
            }
        }
        handles.retain(|pid, _| member_set.contains(pid));
        proc_ids.retain(|pid, _| member_set.contains(pid));

        let host_row = host.as_mut().and_then(|h| h.sample(t, &mut scratch).ok());
        ticks += 1;
        let msg = WriterMsg::Tick {
            samples,
            host: host_row,
            new_processes,
        };
        if tx.send(msg).is_err() {
            break; // writer gone (it logs its own error); stop sampling
        }
        // --- end tick ---

        if stopping {
            alive_at_exit = handles.len() as u32;
            break;
        }
        next += interval_ns;
        let now = monotonic_ns();
        if now >= next {
            // Overrun: skip to the next aligned tick, never queue up (FR-13).
            let missed = (now - next) / interval_ns + 1;
            overruns += missed;
            next += missed * interval_ns;
        }
    }

    // Finalize: correct the process table with end times and the target's exit code.
    let target_exit = stop.target_exit_code.lock().map(|g| *g).unwrap_or(None);
    let mut processes: Vec<ProcessRow> = registry.into_values().collect();
    processes.sort_by_key(|r| r.proc_id);
    if let Some(code) = target_exit
        && let Some(row) = processes.iter_mut().find(|r| r.pid == target_pid)
    {
        row.exit_code = Some(code);
    }
    let _ = tx.send(WriterMsg::Finalize { processes });
    if let Tracker::Cgroup(cgroup) = tracker {
        cgroup.cleanup();
    }
    SamplerStats {
        ticks,
        overruns,
        descendants_alive_at_exit: alive_at_exit,
    }
}

/// Sleeps until `deadline_ns` on `CLOCK_MONOTONIC` using absolute-deadline
/// `clock_nanosleep` (no drift accumulation), in ≤ 50 ms slices so a stop
/// request is honored promptly even at long intervals.
fn sleep_until(deadline_ns: u64, stop: &StopSignal) {
    const SLICE_NS: u64 = 50_000_000;
    loop {
        if stop.stopping() {
            return;
        }
        let now = monotonic_ns();
        if now >= deadline_ns {
            return;
        }
        let target = deadline_ns.min(now + SLICE_NS);
        let request = Timespec {
            tv_sec: (target / 1_000_000_000) as i64,
            tv_nsec: (target % 1_000_000_000) as i64,
        };
        // EINTR just re-enters the loop with a fresh deadline check.
        let _ = rustix::thread::clock_nanosleep_absolute(ClockId::Monotonic, &request);
    }
}
