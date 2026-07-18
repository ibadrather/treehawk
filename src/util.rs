//! Small shared helpers: clocks and timestamp formatting.

use std::time::{SystemTime, UNIX_EPOCH};

use rustix::time::{ClockId, clock_gettime};

/// Nanoseconds on `CLOCK_MONOTONIC`. Every sample is stamped with this (FR-12).
pub fn monotonic_ns() -> u64 {
    let ts = clock_gettime(ClockId::Monotonic);
    ts.tv_sec as u64 * 1_000_000_000 + ts.tv_nsec as u64
}

/// Nanoseconds on `CLOCK_REALTIME`, for the once-per-session wall-clock anchor (FR-12).
pub fn realtime_ns() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0)
}

/// `20260717-165412Z`-style UTC stamp used for default session directory names.
pub fn utc_timestamp_compact() -> String {
    let secs = (realtime_ns() / 1_000_000_000) as i64;
    let (y, mo, d, h, mi, s) = civil_from_unix(secs);
    format!("{y:04}{mo:02}{d:02}-{h:02}{mi:02}{s:02}Z")
}

/// Converts Unix seconds to UTC civil time (Howard Hinnant's days-from-civil inverse).
fn civil_from_unix(secs: i64) -> (i64, u32, u32, u32, u32, u32) {
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let (hour, minute, second) = (rem / 3600, (rem % 3600) / 60, rem % 60);

    let shifted_days = days + 719_468;
    let era = shifted_days.div_euclid(146_097);
    let doe = shifted_days.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let year = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = if month <= 2 { year + 1 } else { year };
    (
        year,
        month as u32,
        day as u32,
        hour as u32,
        minute as u32,
        second as u32,
    )
}

/// Renders byte counts for human-readable report output.
pub fn human_bytes(bytes: u64) -> String {
    const UNITS: [&str; 5] = ["B", "KiB", "MiB", "GiB", "TiB"];
    let mut value = bytes as f64;
    let mut unit = 0;
    while value >= 1024.0 && unit < UNITS.len() - 1 {
        value /= 1024.0;
        unit += 1;
    }
    if unit == 0 {
        format!("{bytes} B")
    } else {
        format!("{value:.1} {}", UNITS[unit])
    }
}

/// Renders a duration in seconds as `1h02m03s` / `12.3s` for report output.
pub fn human_duration(secs: f64) -> String {
    if secs < 60.0 {
        return format!("{secs:.1}s");
    }
    let total = secs as u64;
    let (h, m, s) = (total / 3600, (total % 3600) / 60, total % 60);
    if h > 0 {
        format!("{h}h{m:02}m{s:02}s")
    } else {
        format!("{m}m{s:02}s")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn civil_conversion_known_dates() {
        assert_eq!(civil_from_unix(0), (1970, 1, 1, 0, 0, 0));
        // 2026-07-17 12:34:56 UTC
        assert_eq!(civil_from_unix(1_784_291_696), (2026, 7, 17, 12, 34, 56));
        // Leap day 2024-02-29
        assert_eq!(civil_from_unix(1_709_164_800), (2024, 2, 29, 0, 0, 0));
    }

    #[test]
    fn human_formats() {
        assert_eq!(human_bytes(512), "512 B");
        assert_eq!(human_bytes(2 * 1024 * 1024), "2.0 MiB");
        assert_eq!(human_duration(12.34), "12.3s");
        assert_eq!(human_duration(3723.0), "1h02m03s");
    }
}
