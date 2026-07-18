//! Command-line interface definitions (FR-21, FR-23).

use std::path::PathBuf;
use std::time::Duration;

use clap::{Args, Parser, Subcommand};

#[derive(Parser)]
#[command(
    name = "treehawk",
    version,
    about = "Watches your process tree like a hawk: run a command and log CPU & RAM \
             usage of it and every process it spawns"
)]
pub struct Cli {
    #[command(subcommand)]
    pub command: Command,
}

#[derive(Subcommand)]
pub enum Command {
    /// Run a command and record it plus all descendant processes
    Run(RunArgs),
    /// Print a human-readable summary of a recorded session
    Report(ReportArgs),
    /// Watch the whole system for heavy processes (planned: M3)
    Watch,
    /// Export a session to other formats (planned: M3)
    Export,
    /// List recorded sessions (planned: M3)
    Ls,
    /// Manage the configuration file (planned: M3)
    Config,
    /// Manage the always-on systemd service (planned: M5)
    Service,
}

#[derive(Args)]
pub struct RunArgs {
    /// Sampling interval, e.g. 10ms, 100ms, 1s (range: 1ms..60s)
    #[arg(long, default_value = "100ms", value_parser = parse_interval)]
    pub interval: Duration,

    /// Output directory for the session (default: ./treehawk/<timestamp>)
    #[arg(long)]
    pub out: Option<PathBuf>,

    /// Label attached to every recorded process (repeatable)
    #[arg(long)]
    pub label: Vec<String>,

    /// The command to run and everything after it as its arguments
    #[arg(required = true, trailing_var_arg = true, allow_hyphen_values = true)]
    pub command: Vec<String>,
}

#[derive(Args)]
pub struct ReportArgs {
    /// Path to a session directory
    pub session: PathBuf,
}

/// Parses `10ms` / `1s` style intervals and enforces the FR-11 range (1 ms – 60 s).
fn parse_interval(s: &str) -> Result<Duration, String> {
    let s = s.trim();
    let (number, unit) = match s.find(|c: char| !c.is_ascii_digit() && c != '.') {
        Some(idx) => s.split_at(idx),
        None => (s, "ms"),
    };
    let value: f64 = number
        .parse()
        .map_err(|_| format!("invalid interval: {s:?}"))?;
    let nanos = match unit.trim() {
        "ns" => value,
        "us" | "µs" => value * 1e3,
        "ms" => value * 1e6,
        "s" => value * 1e9,
        "m" | "min" => value * 60e9,
        other => return Err(format!("unknown time unit {other:?} (use us, ms, s)")),
    };
    let d = Duration::from_nanos(nanos as u64);
    if d < Duration::from_millis(1) || d > Duration::from_secs(60) {
        return Err(format!("interval {s} out of range (1ms..60s)"));
    }
    Ok(d)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn interval_parsing() {
        assert_eq!(parse_interval("100ms"), Ok(Duration::from_millis(100)));
        assert_eq!(parse_interval("1s"), Ok(Duration::from_secs(1)));
        assert_eq!(parse_interval("250"), Ok(Duration::from_millis(250)));
        assert_eq!(parse_interval("1500us"), Ok(Duration::from_micros(1500)));
        assert!(parse_interval("0ms").is_err());
        assert!(parse_interval("61s").is_err());
        assert!(parse_interval("fast").is_err());
    }

    #[test]
    fn cli_parses_run_with_trailing_command() {
        let cli = Cli::try_parse_from([
            "treehawk", "run", "--label", "cam", "--", "python3", "-c", "pass",
        ])
        .expect("cli should parse");
        match cli.command {
            Command::Run(args) => {
                assert_eq!(args.command, ["python3", "-c", "pass"]);
                assert_eq!(args.label, ["cam"]);
                assert_eq!(args.interval, Duration::from_millis(100));
            }
            _ => panic!("expected run subcommand"),
        }
    }
}
