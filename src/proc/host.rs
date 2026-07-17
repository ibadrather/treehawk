//! Host-context sampling: whole-machine CPU, memory, and PSI pressure (FR-9).

use std::fs::File;
use std::io;

use super::read_fd_to_string;
use crate::model::HostRow;

/// Aggregate CPU jiffies from the `cpu ` summary line of `/proc/stat`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CpuTotals {
    pub busy_ticks: u64,
    pub total_ticks: u64,
}

/// Parses the first (`cpu `) line: user nice system idle iowait irq softirq steal.
pub fn parse_cpu_totals(proc_stat: &str) -> Option<CpuTotals> {
    let line = proc_stat.lines().next()?.strip_prefix("cpu ")?;
    let mut fields = line.split_ascii_whitespace().map(|f| f.parse::<u64>());
    let mut take = |_: &str| fields.next().and_then(Result::ok);
    let user = take("user")?;
    let nice = take("nice")?;
    let system = take("system")?;
    let idle = take("idle")?;
    let iowait = take("iowait").unwrap_or(0);
    let irq = take("irq").unwrap_or(0);
    let softirq = take("softirq").unwrap_or(0);
    let steal = take("steal").unwrap_or(0);
    let busy = user + nice + system + irq + softirq + steal;
    Some(CpuTotals {
        busy_ticks: busy,
        total_ticks: busy + idle + iowait,
    })
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct MemInfo {
    pub mem_total_kb: u64,
    pub mem_available_kb: u64,
    pub swap_total_kb: u64,
    pub swap_free_kb: u64,
}

pub fn parse_meminfo(content: &str) -> MemInfo {
    let mut out = MemInfo::default();
    for line in content.lines() {
        let Some((key, rest)) = line.split_once(':') else {
            continue;
        };
        let value = rest
            .split_ascii_whitespace()
            .next()
            .and_then(|v| v.parse().ok())
            .unwrap_or(0);
        match key {
            "MemTotal" => out.mem_total_kb = value,
            "MemAvailable" => out.mem_available_kb = value,
            "SwapTotal" => out.swap_total_kb = value,
            "SwapFree" => out.swap_free_kb = value,
            _ => {}
        }
    }
    out
}

/// Parses `avg10` from a `/proc/pressure/*` file: `(some, full)`.
pub fn parse_psi_avg10(content: &str) -> (Option<f32>, Option<f32>) {
    let field = |kind: &str| {
        content
            .lines()
            .find(|l| l.starts_with(kind))?
            .split_ascii_whitespace()
            .find_map(|tok| tok.strip_prefix("avg10=")?.parse().ok())
    };
    (field("some"), field("full"))
}

/// Cached-fd reader for the per-tick host row.
pub struct HostReader {
    stat: File,
    meminfo: File,
    /// PSI needs kernel ≥ 4.20 with CONFIG_PSI; absent files stay None.
    psi_cpu: Option<File>,
    psi_mem: Option<File>,
    psi_io: Option<File>,
    prev: Option<(CpuTotals, u64)>,
}

impl HostReader {
    pub fn open() -> io::Result<Self> {
        Ok(Self {
            stat: File::open("/proc/stat")?,
            meminfo: File::open("/proc/meminfo")?,
            psi_cpu: File::open("/proc/pressure/cpu").ok(),
            psi_mem: File::open("/proc/pressure/memory").ok(),
            psi_io: File::open("/proc/pressure/io").ok(),
            prev: None,
        })
    }

    /// Reads one host row; recorded every tick even when idle (FR-9).
    pub fn sample(&mut self, t_mono_ns: u64, scratch: &mut Vec<u8>) -> io::Result<HostRow> {
        let totals = parse_cpu_totals(read_fd_to_string(&self.stat, scratch)?)
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "unparsable /proc/stat"))?;
        let mem = parse_meminfo(read_fd_to_string(&self.meminfo, scratch)?);
        let psi = |file: &Option<File>, scratch: &mut Vec<u8>| match file {
            Some(f) => read_fd_to_string(f, scratch)
                .map(parse_psi_avg10)
                .unwrap_or((None, None)),
            None => (None, None),
        };
        let (psi_cpu_some, _) = psi(&self.psi_cpu, scratch);
        let (psi_mem_some, psi_mem_full) = psi(&self.psi_mem, scratch);
        let (psi_io_some, psi_io_full) = psi(&self.psi_io, scratch);

        let (dt_ns, busy, total) = match self.prev {
            Some((p, pt)) => (
                Some(t_mono_ns.saturating_sub(pt)),
                Some(totals.busy_ticks.saturating_sub(p.busy_ticks)),
                Some(totals.total_ticks.saturating_sub(p.total_ticks)),
            ),
            None => (None, None, None),
        };
        self.prev = Some((totals, t_mono_ns));
        Ok(HostRow {
            t_mono_ns,
            dt_ns,
            cpu_busy_ticks: busy,
            cpu_total_ticks: total,
            mem_total_kb: mem.mem_total_kb,
            mem_available_kb: mem.mem_available_kb,
            swap_total_kb: mem.swap_total_kb,
            swap_free_kb: mem.swap_free_kb,
            psi_cpu_some_avg10: psi_cpu_some,
            psi_mem_some_avg10: psi_mem_some,
            psi_mem_full_avg10: psi_mem_full,
            psi_io_some_avg10: psi_io_some,
            psi_io_full_avg10: psi_io_full,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cpu_totals_parse() {
        let stat = "cpu  100 5 50 900 20 3 7 15 0 0\ncpu0 10 1 5 90 2 0 0 1 0 0\n";
        let totals = parse_cpu_totals(stat).expect("parses");
        assert_eq!(totals.busy_ticks, 100 + 5 + 50 + 3 + 7 + 15);
        assert_eq!(totals.total_ticks, totals.busy_ticks + 900 + 20);
    }

    #[test]
    fn meminfo_parses() {
        let content = "MemTotal:  32000000 kB\nMemFree: 1 kB\nMemAvailable: 16000000 kB\n\
                       SwapTotal: 2000000 kB\nSwapFree: 1500000 kB\n";
        let mem = parse_meminfo(content);
        assert_eq!(mem.mem_total_kb, 32_000_000);
        assert_eq!(mem.mem_available_kb, 16_000_000);
        assert_eq!(mem.swap_free_kb, 1_500_000);
    }

    #[test]
    fn psi_parses() {
        let content = "some avg10=1.55 avg60=0.90 avg300=0.30 total=123\n\
                       full avg10=0.10 avg60=0.05 avg300=0.01 total=45\n";
        assert_eq!(parse_psi_avg10(content), (Some(1.55), Some(0.10)));
    }

    #[test]
    fn live_host_sample() {
        let mut scratch = Vec::new();
        let mut reader = HostReader::open().expect("open host files");
        let first = reader.sample(1_000, &mut scratch).expect("first sample");
        assert!(first.mem_total_kb > 0);
        assert_eq!(first.dt_ns, None);
        let second = reader.sample(2_000, &mut scratch).expect("second sample");
        assert_eq!(second.dt_ns, Some(1_000));
        assert!(second.cpu_total_ticks.is_some());
    }
}
