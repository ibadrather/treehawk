//! Small shared helpers: clocks, session identifiers, and human formatting.

use std::fmt::Write;
use std::time::{SystemTime, UNIX_EPOCH};

use rustix::rand::{GetRandomFlags, getrandom};
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

/// Random version-4 UUID (RFC 9562) used as the session identifier, so two
/// runs — even concurrent ones — can never collide on a session directory.
pub fn session_uuid() -> String {
    let mut bytes = [0u8; 16];
    let filled = getrandom(&mut bytes[..], GetRandomFlags::empty()).unwrap_or(0);
    if filled < bytes.len() {
        // getrandom(2) practically cannot fail on Linux; mix the clocks and
        // PID as a last resort so a session id is still produced.
        let mut seed =
            realtime_ns() ^ monotonic_ns().rotate_left(32) ^ u64::from(std::process::id());
        for chunk in bytes.chunks_mut(8) {
            seed = splitmix64(seed);
            chunk.copy_from_slice(&seed.to_le_bytes()[..chunk.len()]);
        }
    }
    bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
    bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC variant
    let mut hex = String::with_capacity(32);
    for byte in bytes {
        let _ = write!(hex, "{byte:02x}");
    }
    format!(
        "{}-{}-{}-{}-{}",
        &hex[0..8],
        &hex[8..12],
        &hex[12..16],
        &hex[16..20],
        &hex[20..32]
    )
}

/// One `SplitMix64` step; only the `getrandom` fallback mixer, not for crypto.
fn splitmix64(state: u64) -> u64 {
    let mut z = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
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
    fn session_uuid_is_well_formed_and_unique() {
        let id = session_uuid();
        assert_eq!(id.len(), 36);
        let chars: Vec<char> = id.chars().collect();
        for i in [8, 13, 18, 23] {
            assert_eq!(chars[i], '-');
        }
        assert_eq!(chars[14], '4', "version nibble: {id}");
        assert!(matches!(chars[19], '8' | '9' | 'a' | 'b'), "variant: {id}");
        assert_ne!(session_uuid(), id);
    }

    #[test]
    fn human_formats() {
        assert_eq!(human_bytes(512), "512 B");
        assert_eq!(human_bytes(2 * 1024 * 1024), "2.0 MiB");
        assert_eq!(human_duration(12.34), "12.3s");
        assert_eq!(human_duration(3723.0), "1h02m03s");
    }
}
