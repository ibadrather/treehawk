//! Per-PID metric readers over cached file descriptors (FR-7, NFR-1).
//!
//! Each tracked process gets one [`PidHandle`] holding open fds to its /proc
//! files; every tick we `pread` from offset 0 into a reused scratch buffer —
//! no per-tick path lookups or allocations on the hot path (plan §7).

pub mod host;

use std::io;
use std::os::fd::{AsFd, OwnedFd};

use rustix::fs::{Mode, OFlags, openat};

/// Reads a whole (small) /proc-style file via `pread` into a reused buffer.
///
/// Returns a `&str` borrowing from `scratch`, valid until its next reuse.
pub fn read_fd_to_string(fd: impl AsFd, scratch: &mut Vec<u8>) -> io::Result<&str> {
    let mut len = 0usize;
    loop {
        if scratch.len() < len + 1024 {
            scratch.resize(len + 4096, 0);
        }
        let n = rustix::io::pread(&fd, &mut scratch[len..], len as u64)?;
        if n == 0 {
            break;
        }
        len += n;
    }
    std::str::from_utf8(&scratch[..len])
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "non-UTF-8 proc data"))
}

/// Fields we use from `/proc/<pid>/stat`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Stat {
    pub comm: String,
    pub ppid: i32,
    pub utime_ticks: u64,
    pub stime_ticks: u64,
    pub num_threads: u32,
    /// Process start, in clock ticks since boot: the PID-reuse-safe identity half.
    pub starttime_ticks: u64,
}

/// Parses `/proc/<pid>/stat`, robust to `comm` containing spaces/parens.
pub fn parse_stat(content: &str) -> Option<Stat> {
    let open = content.find('(')?;
    let close = content.rfind(')')?;
    let comm = content.get(open + 1..close)?.to_string();
    let mut fields = content.get(close + 1..)?.split_ascii_whitespace();
    // Fields after comm, 0-indexed: 0=state 1=ppid ... 11=utime 12=stime ... 17=num_threads ... 19=starttime
    let ppid = fields.nth(1)?.parse().ok()?;
    let utime_ticks = fields.nth(9)?.parse().ok()?;
    let stime_ticks = fields.next()?.parse().ok()?;
    let num_threads = fields.nth(4)?.parse().ok()?;
    let starttime_ticks = fields.nth(1)?.parse().ok()?;
    Some(Stat {
        comm,
        ppid,
        utime_ticks,
        stime_ticks,
        num_threads,
        starttime_ticks,
    })
}

/// Fields we use from `/proc/<pid>/status`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Status {
    pub uid: u32,
    /// Vm* are absent for kernel threads and zombies; they read as 0.
    pub vm_rss_kb: u64,
    pub vm_swap_kb: u64,
    pub vm_size_kb: u64,
    pub voluntary_ctxt_switches: Option<u64>,
    pub nonvoluntary_ctxt_switches: Option<u64>,
}

pub fn parse_status(content: &str) -> Status {
    let mut out = Status::default();
    for line in content.lines() {
        let Some((key, rest)) = line.split_once(':') else {
            continue;
        };
        let value = || rest.split_ascii_whitespace().next()?.parse::<u64>().ok();
        match key {
            "Uid" => out.uid = value().unwrap_or(0) as u32,
            "VmRSS" => out.vm_rss_kb = value().unwrap_or(0),
            "VmSwap" => out.vm_swap_kb = value().unwrap_or(0),
            "VmSize" => out.vm_size_kb = value().unwrap_or(0),
            "voluntary_ctxt_switches" => out.voluntary_ctxt_switches = value(),
            "nonvoluntary_ctxt_switches" => out.nonvoluntary_ctxt_switches = value(),
            _ => {}
        }
    }
    out
}

/// Extracts `Pss:` (kB) from `/proc/<pid>/smaps_rollup`.
pub fn parse_pss_kb(content: &str) -> Option<u64> {
    content.lines().find_map(|l| {
        l.strip_prefix("Pss:")?
            .split_ascii_whitespace()
            .next()?
            .parse()
            .ok()
    })
}

/// Whether PSS is readable for this process; tri-state so we probe exactly once.
enum PssState {
    Unprobed,
    Open(OwnedFd),
    Unavailable,
}

/// Open fds and previous-tick CPU counters for one tracked process.
pub struct PidHandle {
    pub pid: i32,
    stat_fd: OwnedFd,
    status_fd: OwnedFd,
    smaps: PssState,
    pub identity: Identity,
    /// Previous tick's (utime, stime, t_mono_ns) for delta computation.
    pub prev: Option<(u64, u64, u64)>,
}

/// Immutable facts captured when the process is first seen (FR-30 subset:
/// no cmdline, no environment).
#[derive(Debug, Clone)]
pub struct Identity {
    pub comm: String,
    pub ppid: i32,
    pub uid: u32,
    pub exe_path: Option<String>,
    pub starttime_ticks: u64,
}

/// One tick's raw readings for one process.
pub struct PidSample {
    pub stat: Stat,
    pub status: Status,
    pub pss_kb: Option<u64>,
}

impl PidHandle {
    /// Opens all per-tick fds. Any error means "this PID is gone or off-limits":
    /// the caller skips it this tick (NFR-4).
    pub fn open(pid: i32, scratch: &mut Vec<u8>) -> io::Result<Self> {
        let dir = rustix::fs::open(
            format!("/proc/{pid}"),
            OFlags::PATH | OFlags::DIRECTORY | OFlags::CLOEXEC,
            Mode::empty(),
        )?;
        let stat_fd = openat(
            &dir,
            "stat",
            OFlags::RDONLY | OFlags::CLOEXEC,
            Mode::empty(),
        )?;
        let status_fd = openat(
            &dir,
            "status",
            OFlags::RDONLY | OFlags::CLOEXEC,
            Mode::empty(),
        )?;
        let exe_path = rustix::fs::readlinkat(&dir, "exe", Vec::new())
            .ok()
            .map(|c| c.to_string_lossy().into_owned());

        let stat = parse_stat(read_fd_to_string(&stat_fd, scratch)?)
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "unparsable stat"))?;
        let status = parse_status(read_fd_to_string(&status_fd, scratch)?);
        let identity = Identity {
            comm: stat.comm.clone(),
            ppid: stat.ppid,
            uid: status.uid,
            exe_path,
            starttime_ticks: stat.starttime_ticks,
        };
        Ok(Self {
            pid,
            stat_fd,
            status_fd,
            smaps: PssState::Unprobed,
            identity,
            prev: None,
        })
    }

    /// Reads this tick's metrics. `want_pss` gates the (pricier) smaps_rollup
    /// read so it can be decimated independently of the sampling rate.
    pub fn sample(&mut self, want_pss: bool, scratch: &mut Vec<u8>) -> io::Result<PidSample> {
        let stat = parse_stat(read_fd_to_string(&self.stat_fd, scratch)?)
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "unparsable stat"))?;
        let status = parse_status(read_fd_to_string(&self.status_fd, scratch)?);
        let pss_kb = if want_pss {
            self.read_pss(scratch)
        } else {
            None
        };
        Ok(PidSample {
            stat,
            status,
            pss_kb,
        })
    }

    fn read_pss(&mut self, scratch: &mut Vec<u8>) -> Option<u64> {
        if let PssState::Unprobed = self.smaps {
            let path = format!("/proc/{}/smaps_rollup", self.pid);
            self.smaps =
                match rustix::fs::open(path, OFlags::RDONLY | OFlags::CLOEXEC, Mode::empty()) {
                    Ok(fd) => PssState::Open(fd),
                    Err(_) => PssState::Unavailable,
                };
        }
        match &self.smaps {
            PssState::Open(fd) => parse_pss_kb(read_fd_to_string(fd, scratch).ok()?),
            _ => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const STAT_FIXTURE: &str = "1234 (fire (fox)) S 1000 1234 1234 0 -1 4194560 \
        12345 678 90 12 4500 1500 30 40 20 0 17 0 98765 1073741824 25000 \
        18446744073709551615 1 1 0 0 0 0 0 4096 0 0 0 0 17 3 0 0 0 0 0";

    const STATUS_FIXTURE: &str = "Name:\tfirefox\nUmask:\t0022\nState:\tS (sleeping)\n\
        Tgid:\t1234\nPid:\t1234\nPPid:\t1000\nUid:\t1000\t1000\t1000\t1000\n\
        Gid:\t1000\t1000\t1000\t1000\nVmPeak:\t  200000 kB\nVmSize:\t  150000 kB\n\
        VmRSS:\t   98304 kB\nVmSwap:\t     512 kB\nThreads:\t17\n\
        voluntary_ctxt_switches:\t4321\nnonvoluntary_ctxt_switches:\t99\n";

    #[test]
    fn stat_parses_fixture() {
        let stat = parse_stat(STAT_FIXTURE).expect("fixture parses");
        assert_eq!(
            stat,
            Stat {
                comm: "fire (fox)".into(),
                ppid: 1000,
                utime_ticks: 4500,
                stime_ticks: 1500,
                num_threads: 17,
                starttime_ticks: 98765,
            }
        );
    }

    #[test]
    fn status_parses_fixture() {
        let status = parse_status(STATUS_FIXTURE);
        assert_eq!(status.uid, 1000);
        assert_eq!(status.vm_rss_kb, 98304);
        assert_eq!(status.vm_swap_kb, 512);
        assert_eq!(status.vm_size_kb, 150000);
        assert_eq!(status.voluntary_ctxt_switches, Some(4321));
        assert_eq!(status.nonvoluntary_ctxt_switches, Some(99));
    }

    #[test]
    fn status_of_kernel_thread_defaults_to_zero_memory() {
        let status = parse_status("Name:\tkthreadd\nUid:\t0\t0\t0\t0\n");
        assert_eq!(status.vm_rss_kb, 0);
        assert_eq!(status.voluntary_ctxt_switches, None);
    }

    #[test]
    fn pss_parses() {
        let content = "00400000-7fff:\nRss:  100 kB\nPss:     4242 kB\nPss_Anon: 1 kB\n";
        assert_eq!(parse_pss_kb(content), Some(4242));
    }

    #[test]
    fn live_self_inspection() {
        let mut scratch = Vec::new();
        let me = std::process::id() as i32;
        let mut handle = PidHandle::open(me, &mut scratch).expect("open self");
        let sample = handle.sample(true, &mut scratch).expect("sample self");
        assert!(sample.status.vm_rss_kb > 0);
        assert!(sample.stat.num_threads >= 1);
        assert_eq!(handle.identity.uid, rustix::process::getuid().as_raw());
        // Repeated reads through the same cached fds must keep working.
        let again = handle.sample(false, &mut scratch).expect("resample self");
        assert!(again.stat.utime_ticks >= sample.stat.utime_ticks);
    }
}
