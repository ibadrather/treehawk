//! `session.json`: the self-describing session manifest (FR-16).

use std::io;
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::model::SCHEMA_VERSION;
use crate::util::{monotonic_ns, realtime_ns};

pub const MANIFEST_FILE: &str = "session.json";

#[derive(Debug, Serialize, Deserialize)]
pub struct Manifest {
    pub schema_version: u32,
    pub treehawk_version: String,
    pub session_id: String,
    /// The exact command as given, argv-style (FR-16). Environment is never
    /// recorded (FR-30).
    pub command: Vec<String>,
    pub labels: Vec<String>,
    pub host: HostInfo,
    pub sampling: SamplingConfig,
    /// "cgroup" or "pid-tree" (FR-3).
    pub tracking_mode: String,
    pub cgroup_path: Option<String>,
    /// One matched (monotonic, realtime) pair for wall-clock alignment (FR-12).
    pub clock_anchor: ClockAnchor,
    /// Present only after a clean finalize; its absence marks a crashed or
    /// still-running session (AT-4).
    pub finished: Option<Finished>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct HostInfo {
    pub hostname: String,
    pub kernel: String,
    pub cpu_model: String,
    pub n_cpus: u32,
    /// Jiffies per second (`_SC_CLK_TCK`); needed to turn tick deltas into time.
    pub clk_tck: u32,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct SamplingConfig {
    pub interval_ns: u64,
    /// PSS is read every N ticks (decimation, plan §Key designs 4); 0 = never.
    pub pss_every_ticks: u32,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ClockAnchor {
    pub monotonic_ns: u64,
    pub realtime_ns: u64,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Finished {
    pub ended_mono_ns: u64,
    /// Exit code treehawk propagated (128+signal when signal-killed);
    /// None when the wait was abandoned by a second Ctrl-C.
    pub target_exit_code: Option<i32>,
    pub ticks: u64,
    pub overruns: u64,
    pub achieved_rate_hz: f64,
    /// Tracked processes still alive when the target exited and the session closed.
    pub descendants_alive_at_exit: u32,
}

impl Manifest {
    pub fn new(
        session_id: String,
        command: Vec<String>,
        labels: Vec<String>,
        interval_ns: u64,
        pss_every_ticks: u32,
        tracking_mode: &str,
        cgroup_path: Option<String>,
    ) -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            treehawk_version: env!("CARGO_PKG_VERSION").to_string(),
            session_id,
            command,
            labels,
            host: HostInfo::detect(),
            sampling: SamplingConfig {
                interval_ns,
                pss_every_ticks,
            },
            tracking_mode: tracking_mode.to_string(),
            cgroup_path,
            clock_anchor: ClockAnchor {
                monotonic_ns: monotonic_ns(),
                realtime_ns: realtime_ns(),
            },
            finished: None,
        }
    }

    /// Writes atomically (tmp + rename) so a crash never leaves a torn manifest.
    pub fn write(&self, session_dir: &Path) -> io::Result<()> {
        let json = serde_json::to_string_pretty(self)?;
        let tmp = session_dir.join(".session.json.tmp");
        std::fs::write(&tmp, json)?;
        std::fs::rename(&tmp, session_dir.join(MANIFEST_FILE))
    }

    pub fn load(session_dir: &Path) -> io::Result<Self> {
        let content = std::fs::read_to_string(session_dir.join(MANIFEST_FILE))?;
        serde_json::from_str(&content).map_err(io::Error::other)
    }
}

impl HostInfo {
    pub fn detect() -> Self {
        let uname = rustix::system::uname();
        Self {
            hostname: uname.nodename().to_string_lossy().into_owned(),
            kernel: uname.release().to_string_lossy().into_owned(),
            cpu_model: cpu_model().unwrap_or_else(|| "unknown".to_string()),
            n_cpus: n_cpus(),
            clk_tck: clk_tck(),
        }
    }
}

fn cpu_model() -> Option<String> {
    let cpuinfo = std::fs::read_to_string("/proc/cpuinfo").ok()?;
    cpuinfo.lines().find_map(|l| {
        let rest = l.strip_prefix("model name")?;
        Some(rest.trim_start().strip_prefix(':')?.trim().to_string())
    })
}

pub fn n_cpus() -> u32 {
    // SAFETY: plain sysconf query.
    let n = unsafe { libc::sysconf(libc::_SC_NPROCESSORS_ONLN) };
    n.max(1) as u32
}

pub fn clk_tck() -> u32 {
    // SAFETY: plain sysconf query.
    let n = unsafe { libc::sysconf(libc::_SC_CLK_TCK) };
    if n > 0 { n as u32 } else { 100 }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn manifest_round_trips() {
        let dir =
            std::env::temp_dir().join(format!("treehawk-manifest-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("create temp dir");
        let mut m = Manifest::new(
            "test".into(),
            vec!["true".into()],
            vec![],
            100_000_000,
            10,
            "cgroup",
            None,
        );
        m.finished = Some(Finished {
            ended_mono_ns: 123,
            target_exit_code: Some(0),
            ticks: 10,
            overruns: 0,
            achieved_rate_hz: 10.0,
            descendants_alive_at_exit: 0,
        });
        m.write(&dir).expect("write manifest");
        let loaded = Manifest::load(&dir).expect("load manifest");
        assert_eq!(loaded.schema_version, SCHEMA_VERSION);
        assert_eq!(loaded.command, ["true"]);
        assert_eq!(loaded.finished.expect("finished").ticks, 10);
        assert!(loaded.host.clk_tck > 0);
        std::fs::remove_dir_all(&dir).ok();
    }
}
