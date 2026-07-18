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
    // Let it sample past at least one flush... the first flush happens at 5 s.
    std::thread::sleep(Duration::from_secs(6));
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

/// Stub subcommands fail loudly instead of pretending to work.
#[test]
fn stub_subcommands_error() {
    let output = treehawk().arg("watch").output().expect("launch");
    assert_eq!(output.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&output.stderr).contains("not yet implemented"),);
}
