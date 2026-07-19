//! End-to-end integration tests for M1 run mode, driving the real binary
//! (spec AT-1, AT-4, FR-22).

use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::time::Duration;

use treehawk::manifest::Manifest;

fn treehawk() -> Command {
    Command::new(env!("CARGO_BIN_EXE_treehawk"))
}

fn temp_session(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("treehawk-it-{name}-{}", std::process::id()));
    std::fs::remove_dir_all(&dir).ok();
    dir
}

fn run_target(out: &Path, interval: &str, target: &[&str]) -> Output {
    let mut cmd = treehawk();
    cmd.args(["run", "--interval", interval, "--out"])
        .arg(out)
        .arg("--");
    cmd.args(target);
    cmd.stdin(Stdio::null());
    cmd.output().expect("treehawk binary should launch")
}

fn report_text(session: &Path) -> String {
    let output = treehawk()
        .arg("report")
        .arg(session)
        .output()
        .expect("treehawk report should launch");
    assert!(
        output.status.success(),
        "report failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8_lossy(&output.stdout).into_owned()
}

/// FR-22: the target's exit code passes through untouched.
#[test]
fn exit_code_passthrough() {
    let dir = temp_session("exitcode");
    let output = run_target(&dir, "50ms", &["sh", "-c", "exit 42"]);
    assert_eq!(output.status.code(), Some(42));
    std::fs::remove_dir_all(&dir).ok();
}

/// The session is complete and self-describing after a clean run.
#[test]
fn clean_session_finalizes() {
    let dir = temp_session("finalize");
    let output = run_target(&dir, "20ms", &["sleep", "0.4"]);
    assert_eq!(output.status.code(), Some(0));

    let manifest = Manifest::load(&dir).expect("manifest present and parseable");
    let finished = manifest.finished.expect("session must be finalized");
    assert_eq!(finished.target_exit_code, Some(0));
    assert!(
        finished.ticks >= 10,
        "expected >=10 ticks, got {}",
        finished.ticks
    );

    assert!(dir.join("processes.parquet").exists());
    assert!(dir.join("samples-00000.parquet").exists());
    assert!(dir.join("host-00000.parquet").exists());
    // Clean finalize leaves no active WAL behind.
    assert!(!dir.join("samples-active.arrows").exists());

    let report = report_text(&dir);
    assert!(
        report.contains("sleep"),
        "report should list the target:\n{report}"
    );
    std::fs::remove_dir_all(&dir).ok();
}

/// AT-1: a disowned (reparented) child is still recorded.
#[test]
fn disowned_child_recorded() {
    let dir = temp_session("disown");
    let output = run_target(&dir, "20ms", &["bash", "-c", "sleep 1 & disown; sleep 2"]);
    assert_eq!(output.status.code(), Some(0));

    let manifest = Manifest::load(&dir).expect("manifest");
    let report = report_text(&dir);
    // The tree is bash + its two sleeps; the disowned sleep must appear even
    // though it reparented away from bash.
    let sleep_rows = report
        .lines()
        .filter(|l| l.trim_start().starts_with("sleep"))
        .count();
    assert!(
        sleep_rows >= 2,
        "expected both sleeps (tracking mode {}):\n{report}",
        manifest.tracking_mode
    );
    assert!(report.contains("bash"), "target itself missing:\n{report}");
    std::fs::remove_dir_all(&dir).ok();
}

/// A process first sighted before its exec is re-recorded under the image it
/// exec'd into, not the fork parent's (the `bash`-caught-pre-exec race that
/// made `disowned_child_recorded` flake on slow CI runners).
#[test]
fn exec_after_first_sight_updates_identity() {
    let dir = temp_session("exec-rename");
    // First sight at ~20 ms is guaranteed to see bash; the exec at ~500 ms
    // must then rename the recorded process.
    let output = run_target(&dir, "20ms", &["bash", "-c", "sleep 0.5; exec sleep 0.5"]);
    assert_eq!(output.status.code(), Some(0));

    let report = report_text(&dir);
    // Only the target row carries an exit code; it must be named sleep.
    let target_row = report
        .lines()
        .find(|l| l.trim_start().starts_with("sleep") && l.split_whitespace().last() == Some("0"));
    assert!(
        target_row.is_some(),
        "target should be recorded under its post-exec name:\n{report}"
    );
    std::fs::remove_dir_all(&dir).ok();
}

/// AT-4: `SIGKILLing` treehawk mid-run leaves a readable dataset.
#[test]
fn sigkill_leaves_readable_dataset() {
    let dir = temp_session("sigkill");
    let mut child = treehawk()
        .args(["run", "--interval", "20ms", "--out"])
        .arg(&dir)
        .args(["--", "sleep", "30"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .expect("spawn treehawk");
    // Let it sample past at least one flush... flushes happen every 1 s.
    std::thread::sleep(Duration::from_millis(2500));
    // SAFETY: plain SIGKILL of the child we just spawned.
    unsafe { libc::kill(child.id() as i32, libc::SIGKILL) };
    child.wait().expect("reap treehawk");

    let manifest = Manifest::load(&dir).expect("manifest survives the kill");
    assert!(manifest.finished.is_none(), "kill must not look finalized");
    assert!(
        dir.join("samples-active.arrows").exists(),
        "active WAL should be left behind"
    );

    let report = report_text(&dir);
    assert!(
        report.contains("not finalized"),
        "report should flag the crash:\n{report}"
    );
    assert!(
        report.contains("sleep"),
        "recovered data should show the target:\n{report}"
    );

    // The orphaned `sleep 30` keeps running in its own process group; clean it up.
    let _ = Command::new("pkill").args(["-f", "^sleep 30$"]).status();
    std::fs::remove_dir_all(&dir).ok();
}

/// A non-empty `--out` directory is refused instead of silently overwritten.
#[test]
fn non_empty_out_dir_is_refused() {
    let dir = temp_session("nooverwrite");
    let output = run_target(&dir, "50ms", &["sleep", "0.2"]);
    assert_eq!(output.status.code(), Some(0));
    let first = Manifest::load(&dir).expect("first session");

    let output = run_target(&dir, "50ms", &["sleep", "0.2"]);
    assert_ne!(output.status.code(), Some(0), "second run must be refused");
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("not empty"),
        "expected the refusal message:\n{stderr}"
    );
    // The first session survives untouched (session ids are unique UUIDs).
    let survivor = Manifest::load(&dir).expect("first session still readable");
    assert_eq!(survivor.session_id, first.session_id);
    std::fs::remove_dir_all(&dir).ok();
}

/// Session ids are UUIDs, so runs can never collide on a directory name.
#[test]
fn session_id_is_a_uuid() {
    let dir = temp_session("uuid");
    let output = run_target(&dir, "50ms", &["true"]);
    assert_eq!(output.status.code(), Some(0));
    let manifest = Manifest::load(&dir).expect("manifest");
    assert_eq!(manifest.session_id.len(), 36, "{}", manifest.session_id);
    assert_eq!(manifest.session_id.matches('-').count(), 4);
    std::fs::remove_dir_all(&dir).ok();
}

/// `--cmdline` records argv, so same-named processes stay distinguishable.
#[test]
fn cmdline_flag_records_argv() {
    let dir = temp_session("cmdline");
    let output = treehawk()
        .args(["run", "--interval", "20ms", "--cmdline", "--out"])
        .arg(&dir)
        .args(["--", "sh", "-c", "sleep 0.3"])
        .stdin(Stdio::null())
        .output()
        .expect("treehawk binary should launch");
    assert_eq!(output.status.code(), Some(0));

    let report = report_text(&dir);
    // The shell's exe basename may be dash/bash, but its recorded arguments
    // must appear in the process column.
    assert!(
        report.contains("-c sleep 0.3"),
        "report should show recorded argv:\n{report}"
    );
    std::fs::remove_dir_all(&dir).ok();
}

/// Whole-tree CPU from the cgroup's `cpu.stat` lands in the manifest and the
/// report, covering processes too short-lived to be sampled.
#[test]
fn cgroup_cpu_totals_reported() {
    let dir = temp_session("cputotal");
    let output = run_target(
        &dir,
        "20ms",
        &[
            "sh",
            "-c",
            "i=0; while [ \"$i\" -lt 50000 ]; do i=$((i+1)); done",
        ],
    );
    assert_eq!(output.status.code(), Some(0));

    let manifest = Manifest::load(&dir).expect("manifest");
    if manifest.tracking_mode != "cgroup" {
        // Cgroup v2 delegation unavailable (e.g. non-systemd session): the
        // total is only defined for cgroup tracking.
        eprintln!("skipping: cgroup tracking unavailable on this host");
        std::fs::remove_dir_all(&dir).ok();
        return;
    }
    let finished = manifest.finished.expect("finalized");
    assert!(
        finished.cgroup_cpu_usage_usec.is_some_and(|us| us > 0),
        "expected cpu.stat usage in the manifest: {:?}",
        finished.cgroup_cpu_usage_usec
    );
    let report = report_text(&dir);
    assert!(
        report.contains("total tree CPU"),
        "report should show the tree CPU total:\n{report}"
    );
    std::fs::remove_dir_all(&dir).ok();
}

/// `-v` prints info-level progress to stderr while stdout stays pipeable.
#[test]
fn verbose_flag_logs_to_stderr() {
    let dir = temp_session("verbose");
    let output = treehawk()
        .args(["-v", "run", "--interval", "50ms", "--out"])
        .arg(&dir)
        .args(["--", "sleep", "0.2"])
        .stdin(Stdio::null())
        .output()
        .expect("treehawk binary should launch");
    assert_eq!(output.status.code(), Some(0));

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("treehawk: info: tracking mode:"),
        "expected the tracking-mode info line on stderr:\n{stderr}"
    );
    assert!(
        stderr.contains("treehawk: info: session finalized:"),
        "expected the finalize summary on stderr:\n{stderr}"
    );
    std::fs::remove_dir_all(&dir).ok();
}

/// Stub subcommands fail loudly instead of pretending to work.
#[test]
fn stub_subcommands_error() {
    let output = treehawk().arg("watch").output().expect("launch");
    assert_eq!(output.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&output.stderr).contains("not yet implemented"),);
}
