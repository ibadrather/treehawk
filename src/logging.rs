//! Stderr logger behind the [`log`] facade, following the STYLE.md CLI-message
//! rules: warnings and status lines go to stderr so stdout stays pipeable, and
//! every line is prefixed `treehawk: <level>:`.
//!
//! Verbosity is set once at startup from the global `-v` flag: warnings are
//! always shown, `-v` adds info, `-vv` adds debug, `-vvv` adds trace. The
//! sampling thread never logs per tick — a disabled level costs one atomic
//! load, but the observer must never disturb the measurement.

use log::{Level, LevelFilter, Log, Metadata, Record};

struct StderrLogger;

impl Log for StderrLogger {
    fn enabled(&self, metadata: &Metadata) -> bool {
        metadata.level() <= log::max_level()
    }

    fn log(&self, record: &Record) {
        if self.enabled(record.metadata()) {
            let level = match record.level() {
                Level::Error => "error",
                Level::Warn => "warning",
                Level::Info => "info",
                Level::Debug => "debug",
                Level::Trace => "trace",
            };
            eprintln!("treehawk: {level}: {}", record.args());
        }
    }

    fn flush(&self) {}
}

static LOGGER: StderrLogger = StderrLogger;

/// Maps the `-v` count to a level filter: warnings by default, then
/// info / debug / trace.
fn level_filter(verbose: u8) -> LevelFilter {
    match verbose {
        0 => LevelFilter::Warn,
        1 => LevelFilter::Info,
        2 => LevelFilter::Debug,
        _ => LevelFilter::Trace,
    }
}

/// Installs the stderr logger. Called once from `main` before dispatch; a
/// repeated call (e.g., from tests) leaves the existing logger in place.
pub fn init(verbose: u8) {
    if log::set_logger(&LOGGER).is_ok() {
        log::set_max_level(level_filter(verbose));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn verbosity_maps_to_levels() {
        assert_eq!(level_filter(0), LevelFilter::Warn);
        assert_eq!(level_filter(1), LevelFilter::Info);
        assert_eq!(level_filter(2), LevelFilter::Debug);
        assert_eq!(level_filter(9), LevelFilter::Trace);
    }
}
