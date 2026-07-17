//! Target launch and lifecycle: exact-argument spawn, cgroup self-join,
//! signal forwarding, and exit-code transparency (FR-1, FR-22).

use std::io;
use std::os::fd::{AsRawFd, OwnedFd};
use std::os::unix::process::CommandExt;
use std::process::{Child, Command};
use std::sync::atomic::{AtomicU32, Ordering};

/// Counts of signals received by treehawk itself, set from the handlers.
static SIGINT_COUNT: AtomicU32 = AtomicU32::new(0);
static SIGTERM_COUNT: AtomicU32 = AtomicU32::new(0);

extern "C" fn on_signal(sig: libc::c_int) {
    match sig {
        libc::SIGINT => SIGINT_COUNT.fetch_add(1, Ordering::Relaxed),
        libc::SIGTERM => SIGTERM_COUNT.fetch_add(1, Ordering::Relaxed),
        _ => 0,
    };
}

/// Installs SIGINT/SIGTERM handlers *without* SA_RESTART so the wait loop's
/// `waitpid` is interrupted (EINTR) and can forward promptly.
pub fn install_signal_handlers() -> io::Result<()> {
    // SAFETY: the handler only touches atomics (async-signal-safe).
    unsafe {
        let mut action: libc::sigaction = std::mem::zeroed();
        action.sa_sigaction = on_signal as extern "C" fn(libc::c_int) as usize;
        libc::sigemptyset(&mut action.sa_mask);
        action.sa_flags = 0;
        for sig in [libc::SIGINT, libc::SIGTERM] {
            if libc::sigaction(sig, &action, std::ptr::null_mut()) != 0 {
                return Err(io::Error::last_os_error());
            }
        }
    }
    Ok(())
}

/// Launches the target with stdio inherited and arguments untouched (FR-1).
///
/// In the child, pre-exec: become leader of a new process group (so signals
/// can be forwarded to the whole tree), then move *itself* into the session
/// cgroup by writing `0` to the inherited `cgroup.procs` fd — closing the
/// race where an early grandchild could be spawned outside the cgroup.
pub fn spawn_target(argv: &[String], cgroup_procs: Option<OwnedFd>) -> io::Result<Child> {
    let (program, args) = argv.split_first().ok_or(io::ErrorKind::InvalidInput)?;
    let mut command = Command::new(program);
    command.args(args);
    let raw_procs_fd = cgroup_procs.as_ref().map(|fd| fd.as_raw_fd());
    // SAFETY: the hook runs post-fork/pre-exec and only calls async-signal-safe
    // libc functions on data captured by value.
    unsafe {
        command.pre_exec(move || {
            if libc::setpgid(0, 0) != 0 {
                return Err(io::Error::last_os_error());
            }
            if let Some(fd) = raw_procs_fd {
                // "0" migrates the writing process itself (cgroup v2 semantics).
                if libc::write(fd, c"0".as_ptr().cast(), 1) != 1 {
                    return Err(io::Error::last_os_error());
                }
            }
            Ok(())
        });
    }
    let child = command.spawn()?;
    drop(cgroup_procs); // parent's copy; the child wrote through its inherited fd
    Ok(child)
}

/// How the target ended, mapped to treehawk's own exit code (FR-22).
#[derive(Debug, Clone, Copy)]
pub enum TargetExit {
    Code(i32),
    Signal(i32),
    /// User pressed Ctrl-C twice: stop waiting, finalize, get out.
    Abandoned,
}

impl TargetExit {
    /// Exit code treehawk itself should exit with: the target's code, or the
    /// shell convention `128 + signal` when signal-killed.
    pub fn exit_code(self) -> u8 {
        match self {
            TargetExit::Code(c) => c as u8,
            TargetExit::Signal(s) => (128 + s) as u8,
            TargetExit::Abandoned => 130,
        }
    }

    pub fn code_for_manifest(self) -> Option<i32> {
        match self {
            TargetExit::Code(c) => Some(c),
            TargetExit::Signal(s) => Some(128 + s),
            TargetExit::Abandoned => None,
        }
    }
}

/// Waits for the target, forwarding SIGINT/SIGTERM to its process group.
///
/// A second SIGINT abandons the wait so the session can still be finalized
/// (flush + manifest) before exiting.
pub fn wait_target(child: &mut Child) -> io::Result<TargetExit> {
    let pid = child.id() as i32;
    let mut forwarded_int = 0;
    let mut forwarded_term = 0;
    loop {
        let mut status: libc::c_int = 0;
        // SAFETY: plain waitpid on our own direct child.
        let ret = unsafe { libc::waitpid(pid, &mut status, 0) };
        if ret == pid {
            if libc::WIFEXITED(status) {
                return Ok(TargetExit::Code(libc::WEXITSTATUS(status)));
            }
            if libc::WIFSIGNALED(status) {
                return Ok(TargetExit::Signal(libc::WTERMSIG(status)));
            }
            continue; // stopped/continued: keep waiting
        }
        let err = io::Error::last_os_error();
        if err.raw_os_error() != Some(libc::EINTR) {
            return Err(err);
        }
        let ints = SIGINT_COUNT.load(Ordering::Relaxed);
        let terms = SIGTERM_COUNT.load(Ordering::Relaxed);
        if ints >= 2 {
            eprintln!("treehawk: second interrupt: abandoning target, finalizing session");
            return Ok(TargetExit::Abandoned);
        }
        if ints > forwarded_int {
            forwarded_int = ints;
            forward_to_group(pid, libc::SIGINT);
        }
        if terms > forwarded_term {
            forwarded_term = terms;
            forward_to_group(pid, libc::SIGTERM);
        }
    }
}

fn forward_to_group(pgid: i32, sig: libc::c_int) {
    // SAFETY: negative pid targets the process group the child leads.
    unsafe {
        libc::kill(-pgid, sig);
    }
}
