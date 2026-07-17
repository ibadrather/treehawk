//! Descendant containment: cgroup v2 tracking with PID-tree fallback (FR-2, FR-3).

use std::collections::{HashMap, HashSet, VecDeque};
use std::fs::File;
use std::io;
use std::os::fd::OwnedFd;
use std::path::{Path, PathBuf};

use rustix::fs::{Mode, OFlags};

use crate::proc::read_fd_to_string;

const CGROUP_ROOT: &str = "/sys/fs/cgroup";

/// How descendants are being tracked; recorded in the manifest (FR-3).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrackingMode {
    Cgroup,
    PidTree,
}

impl TrackingMode {
    pub fn as_str(self) -> &'static str {
        match self {
            TrackingMode::Cgroup => "cgroup",
            TrackingMode::PidTree => "pid-tree",
        }
    }
}

/// A per-session cgroup created under Treehawk's own (delegated) cgroup.
///
/// The target moves *itself* into it pre-exec (see [`crate::spawn`]), so even
/// grandchildren spawned in the first milliseconds cannot escape.
pub struct CgroupTracker {
    dir: PathBuf,
    procs: File,
}

impl CgroupTracker {
    /// Creates `treehawk-<session-id>` under the cgroup we are running in.
    ///
    /// Fails when the subtree is not delegated to us (e.g. non-systemd session);
    /// the caller then falls back to [`PidTreeTracker`] with a warning.
    pub fn create(session_id: &str) -> io::Result<Self> {
        let own = own_cgroup_path()?;
        let dir = Path::new(CGROUP_ROOT)
            .join(own.trim_start_matches('/'))
            .join(format!("treehawk-{session_id}"));
        std::fs::create_dir(&dir)?;
        let procs = match File::open(dir.join("cgroup.procs")) {
            Ok(f) => f,
            Err(e) => {
                let _ = std::fs::remove_dir(&dir);
                return Err(e);
            }
        };
        Ok(Self { dir, procs })
    }

    /// Opens `cgroup.procs` for writing; handed to the child's pre-exec hook.
    pub fn procs_write_fd(&self) -> io::Result<OwnedFd> {
        let fd = rustix::fs::open(self.dir.join("cgroup.procs"), OFlags::WRONLY, Mode::empty())?;
        Ok(fd)
    }

    pub fn path(&self) -> &Path {
        &self.dir
    }

    /// Current member PIDs, straight from the kernel (no inference).
    pub fn members(&mut self, scratch: &mut Vec<u8>) -> io::Result<Vec<i32>> {
        let content = read_fd_to_string(&self.procs, scratch)?;
        Ok(content
            .split_ascii_whitespace()
            .filter_map(|t| t.parse().ok())
            .collect())
    }

    /// Removes the cgroup; harmless to fail while stragglers are still inside.
    pub fn cleanup(self) {
        drop(self.procs);
        if let Err(e) = std::fs::remove_dir(&self.dir) {
            eprintln!(
                "treehawk: warning: could not remove cgroup {} ({e}); \
                 processes may still be running in it",
                self.dir.display()
            );
        }
    }
}

/// Cgroup path of the current process from `/proc/self/cgroup` (v2: `0::<path>`).
fn own_cgroup_path() -> io::Result<String> {
    let content = std::fs::read_to_string("/proc/self/cgroup")?;
    content
        .lines()
        .find_map(|l| l.strip_prefix("0::"))
        .map(|p| p.trim().to_string())
        .ok_or_else(|| io::Error::other("no cgroup v2 entry in /proc/self/cgroup"))
}

/// Fallback tracker: walks `/proc` PPID chains from the target each tick.
///
/// Reduced guarantee (documented): double-forked processes reparent to init
/// and are lost from the walk once their intermediate parent exits.
pub struct PidTreeTracker {
    root: i32,
}

impl PidTreeTracker {
    pub fn new(root: i32) -> Self {
        Self { root }
    }

    pub fn members(&self) -> io::Result<Vec<i32>> {
        let mut ppid_of: HashMap<i32, i32> = HashMap::new();
        for entry in std::fs::read_dir("/proc")? {
            let entry = entry?;
            let Ok(pid) = entry.file_name().to_string_lossy().parse::<i32>() else {
                continue;
            };
            // Process may die between readdir and read: skip, never fail (NFR-4).
            let Ok(stat) = std::fs::read_to_string(entry.path().join("stat")) else {
                continue;
            };
            if let Some(ppid) = ppid_from_stat(&stat) {
                ppid_of.insert(pid, ppid);
            }
        }
        let mut children: HashMap<i32, Vec<i32>> = HashMap::new();
        for (&pid, &ppid) in &ppid_of {
            children.entry(ppid).or_default().push(pid);
        }
        let mut members = Vec::new();
        let mut seen = HashSet::new();
        let mut queue = VecDeque::from([self.root]);
        while let Some(pid) = queue.pop_front() {
            if !seen.insert(pid) {
                continue;
            }
            if pid == self.root && !ppid_of.contains_key(&pid) {
                continue; // target already gone
            }
            members.push(pid);
            if let Some(kids) = children.get(&pid) {
                queue.extend(kids);
            }
        }
        Ok(members)
    }
}

/// Extracts PPID from `/proc/<pid>/stat` (field after the parenthesized comm).
fn ppid_from_stat(stat: &str) -> Option<i32> {
    let rest = &stat[stat.rfind(')')? + 1..];
    rest.split_ascii_whitespace().nth(1)?.parse().ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ppid_parses_even_with_hostile_comm() {
        let stat = "123 (weird ) name)) R 77 123 123 0 -1 4194304 100 0 0 0 5 3 0 0 20 0 1 0 999";
        assert_eq!(ppid_from_stat(stat), Some(77));
    }

    #[test]
    fn pid_tree_includes_self_tree() {
        let me = std::process::id() as i32;
        let members = PidTreeTracker::new(me).members().expect("walk /proc");
        assert!(members.contains(&me));
    }

    #[test]
    fn own_cgroup_path_resolves() {
        // Any modern systemd distro puts us in a v2 cgroup.
        let path = own_cgroup_path().expect("cgroup v2 entry");
        assert!(path.starts_with('/'));
    }
}
