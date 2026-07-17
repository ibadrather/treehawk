//! Sample row types and their Arrow schemas (NFR-8: versioned, additive evolution).
//!
//! GPU columns are already reserved as nullable so the M2 backends extend data
//! without a schema break (plan §Key designs 8).

use std::sync::Arc;

use arrow::array::{
    ArrayRef, Float32Builder, Int32Builder, StringBuilder, UInt32Builder, UInt64Builder,
};
use arrow::datatypes::{DataType, Field, Schema};
use arrow::error::ArrowError;
use arrow::record_batch::RecordBatch;

/// Bumped on any non-additive change to the tables below (NFR-8).
pub const SCHEMA_VERSION: u32 = 1;

/// One per-process sample at one tick (FR-7, M1 subset: CPU + RAM).
///
/// CPU time is stored as raw jiffy deltas plus the elapsed nanoseconds they
/// cover; percentages are derived at presentation time (NFR-7).
#[derive(Debug, Clone)]
pub struct SampleRow {
    pub t_mono_ns: u64,
    pub proc_id: u32,
    pub pid: i32,
    /// Nanoseconds since this process's previous sample; None on its first sample.
    pub dt_ns: Option<u64>,
    pub cpu_utime_ticks: Option<u64>,
    pub cpu_stime_ticks: Option<u64>,
    pub num_threads: u32,
    pub vm_rss_kb: u64,
    pub vm_swap_kb: u64,
    pub vm_size_kb: u64,
    /// PSS from smaps_rollup; None when unreadable or decimated (FR-7 "when readable").
    pub pss_kb: Option<u64>,
    pub voluntary_ctxt_switches: Option<u64>,
    pub nonvoluntary_ctxt_switches: Option<u64>,
    /// Reserved for M2 (always None in M1).
    pub gpu_util_pct: Option<f32>,
    /// Reserved for M2 (always None in M1).
    pub gpu_mem_bytes: Option<u64>,
}

/// One host-context row per tick, recorded even when idle (FR-9).
#[derive(Debug, Clone)]
pub struct HostRow {
    pub t_mono_ns: u64,
    pub dt_ns: Option<u64>,
    /// Non-idle jiffies across all CPUs since the previous tick.
    pub cpu_busy_ticks: Option<u64>,
    /// All jiffies across all CPUs since the previous tick.
    pub cpu_total_ticks: Option<u64>,
    pub mem_total_kb: u64,
    pub mem_available_kb: u64,
    pub swap_total_kb: u64,
    pub swap_free_kb: u64,
    /// PSI `some avg10` values; None when /proc/pressure is unavailable.
    pub psi_cpu_some_avg10: Option<f32>,
    pub psi_mem_some_avg10: Option<f32>,
    pub psi_mem_full_avg10: Option<f32>,
    pub psi_io_some_avg10: Option<f32>,
    pub psi_io_full_avg10: Option<f32>,
}

/// Per-process identity, one row per process seen in the session (FR-30).
///
/// Deliberately no cmdline and no environment in M1; identity is executable
/// path/basename plus user labels.
#[derive(Debug, Clone)]
pub struct ProcessRow {
    pub proc_id: u32,
    pub pid: i32,
    pub ppid: i32,
    pub uid: u32,
    pub exe_path: Option<String>,
    pub exe_name: String,
    /// Kernel starttime in clock ticks since boot — with pid this is the reuse-safe identity.
    pub starttime_ticks: u64,
    pub first_seen_ns: u64,
    pub last_seen_ns: u64,
    /// Only known for the direct target in M1 (descendant exits arrive with FR-4 events in M4).
    pub exit_code: Option<i32>,
    pub labels: Option<String>,
}

pub fn samples_schema() -> Arc<Schema> {
    Arc::new(Schema::new(vec![
        Field::new("t_mono_ns", DataType::UInt64, false),
        Field::new("proc_id", DataType::UInt32, false),
        Field::new("pid", DataType::Int32, false),
        Field::new("dt_ns", DataType::UInt64, true),
        Field::new("cpu_utime_ticks", DataType::UInt64, true),
        Field::new("cpu_stime_ticks", DataType::UInt64, true),
        Field::new("num_threads", DataType::UInt32, false),
        Field::new("vm_rss_kb", DataType::UInt64, false),
        Field::new("vm_swap_kb", DataType::UInt64, false),
        Field::new("vm_size_kb", DataType::UInt64, false),
        Field::new("pss_kb", DataType::UInt64, true),
        Field::new("voluntary_ctxt_switches", DataType::UInt64, true),
        Field::new("nonvoluntary_ctxt_switches", DataType::UInt64, true),
        Field::new("gpu_util_pct", DataType::Float32, true),
        Field::new("gpu_mem_bytes", DataType::UInt64, true),
    ]))
}

pub fn host_schema() -> Arc<Schema> {
    Arc::new(Schema::new(vec![
        Field::new("t_mono_ns", DataType::UInt64, false),
        Field::new("dt_ns", DataType::UInt64, true),
        Field::new("cpu_busy_ticks", DataType::UInt64, true),
        Field::new("cpu_total_ticks", DataType::UInt64, true),
        Field::new("mem_total_kb", DataType::UInt64, false),
        Field::new("mem_available_kb", DataType::UInt64, false),
        Field::new("swap_total_kb", DataType::UInt64, false),
        Field::new("swap_free_kb", DataType::UInt64, false),
        Field::new("psi_cpu_some_avg10", DataType::Float32, true),
        Field::new("psi_mem_some_avg10", DataType::Float32, true),
        Field::new("psi_mem_full_avg10", DataType::Float32, true),
        Field::new("psi_io_some_avg10", DataType::Float32, true),
        Field::new("psi_io_full_avg10", DataType::Float32, true),
    ]))
}

pub fn processes_schema() -> Arc<Schema> {
    Arc::new(Schema::new(vec![
        Field::new("proc_id", DataType::UInt32, false),
        Field::new("pid", DataType::Int32, false),
        Field::new("ppid", DataType::Int32, false),
        Field::new("uid", DataType::UInt32, false),
        Field::new("exe_path", DataType::Utf8, true),
        Field::new("exe_name", DataType::Utf8, false),
        Field::new("starttime_ticks", DataType::UInt64, false),
        Field::new("first_seen_ns", DataType::UInt64, false),
        Field::new("last_seen_ns", DataType::UInt64, false),
        Field::new("exit_code", DataType::Int32, true),
        Field::new("labels", DataType::Utf8, true),
    ]))
}

pub fn samples_batch(rows: &[SampleRow]) -> Result<RecordBatch, ArrowError> {
    let mut t = UInt64Builder::with_capacity(rows.len());
    let mut proc_id = UInt32Builder::with_capacity(rows.len());
    let mut pid = Int32Builder::with_capacity(rows.len());
    let mut dt = UInt64Builder::with_capacity(rows.len());
    let mut utime = UInt64Builder::with_capacity(rows.len());
    let mut stime = UInt64Builder::with_capacity(rows.len());
    let mut threads = UInt32Builder::with_capacity(rows.len());
    let mut rss = UInt64Builder::with_capacity(rows.len());
    let mut swap = UInt64Builder::with_capacity(rows.len());
    let mut vsize = UInt64Builder::with_capacity(rows.len());
    let mut pss = UInt64Builder::with_capacity(rows.len());
    let mut vctx = UInt64Builder::with_capacity(rows.len());
    let mut nvctx = UInt64Builder::with_capacity(rows.len());
    let mut gpu_util = Float32Builder::with_capacity(rows.len());
    let mut gpu_mem = UInt64Builder::with_capacity(rows.len());
    for r in rows {
        t.append_value(r.t_mono_ns);
        proc_id.append_value(r.proc_id);
        pid.append_value(r.pid);
        dt.append_option(r.dt_ns);
        utime.append_option(r.cpu_utime_ticks);
        stime.append_option(r.cpu_stime_ticks);
        threads.append_value(r.num_threads);
        rss.append_value(r.vm_rss_kb);
        swap.append_value(r.vm_swap_kb);
        vsize.append_value(r.vm_size_kb);
        pss.append_option(r.pss_kb);
        vctx.append_option(r.voluntary_ctxt_switches);
        nvctx.append_option(r.nonvoluntary_ctxt_switches);
        gpu_util.append_option(r.gpu_util_pct);
        gpu_mem.append_option(r.gpu_mem_bytes);
    }
    let arrays: Vec<ArrayRef> = vec![
        Arc::new(t.finish()),
        Arc::new(proc_id.finish()),
        Arc::new(pid.finish()),
        Arc::new(dt.finish()),
        Arc::new(utime.finish()),
        Arc::new(stime.finish()),
        Arc::new(threads.finish()),
        Arc::new(rss.finish()),
        Arc::new(swap.finish()),
        Arc::new(vsize.finish()),
        Arc::new(pss.finish()),
        Arc::new(vctx.finish()),
        Arc::new(nvctx.finish()),
        Arc::new(gpu_util.finish()),
        Arc::new(gpu_mem.finish()),
    ];
    RecordBatch::try_new(samples_schema(), arrays)
}

pub fn host_batch(rows: &[HostRow]) -> Result<RecordBatch, ArrowError> {
    let mut t = UInt64Builder::with_capacity(rows.len());
    let mut dt = UInt64Builder::with_capacity(rows.len());
    let mut busy = UInt64Builder::with_capacity(rows.len());
    let mut total = UInt64Builder::with_capacity(rows.len());
    let mut mem_total = UInt64Builder::with_capacity(rows.len());
    let mut mem_avail = UInt64Builder::with_capacity(rows.len());
    let mut swap_total = UInt64Builder::with_capacity(rows.len());
    let mut swap_free = UInt64Builder::with_capacity(rows.len());
    let mut psi = [
        Float32Builder::with_capacity(rows.len()),
        Float32Builder::with_capacity(rows.len()),
        Float32Builder::with_capacity(rows.len()),
        Float32Builder::with_capacity(rows.len()),
        Float32Builder::with_capacity(rows.len()),
    ];
    for r in rows {
        t.append_value(r.t_mono_ns);
        dt.append_option(r.dt_ns);
        busy.append_option(r.cpu_busy_ticks);
        total.append_option(r.cpu_total_ticks);
        mem_total.append_value(r.mem_total_kb);
        mem_avail.append_value(r.mem_available_kb);
        swap_total.append_value(r.swap_total_kb);
        swap_free.append_value(r.swap_free_kb);
        psi[0].append_option(r.psi_cpu_some_avg10);
        psi[1].append_option(r.psi_mem_some_avg10);
        psi[2].append_option(r.psi_mem_full_avg10);
        psi[3].append_option(r.psi_io_some_avg10);
        psi[4].append_option(r.psi_io_full_avg10);
    }
    let mut arrays: Vec<ArrayRef> = vec![
        Arc::new(t.finish()),
        Arc::new(dt.finish()),
        Arc::new(busy.finish()),
        Arc::new(total.finish()),
        Arc::new(mem_total.finish()),
        Arc::new(mem_avail.finish()),
        Arc::new(swap_total.finish()),
        Arc::new(swap_free.finish()),
    ];
    for mut b in psi {
        arrays.push(Arc::new(b.finish()));
    }
    RecordBatch::try_new(host_schema(), arrays)
}

pub fn processes_batch(rows: &[ProcessRow]) -> Result<RecordBatch, ArrowError> {
    let mut proc_id = UInt32Builder::with_capacity(rows.len());
    let mut pid = Int32Builder::with_capacity(rows.len());
    let mut ppid = Int32Builder::with_capacity(rows.len());
    let mut uid = UInt32Builder::with_capacity(rows.len());
    let mut exe_path = StringBuilder::new();
    let mut exe_name = StringBuilder::new();
    let mut starttime = UInt64Builder::with_capacity(rows.len());
    let mut first_seen = UInt64Builder::with_capacity(rows.len());
    let mut last_seen = UInt64Builder::with_capacity(rows.len());
    let mut exit_code = Int32Builder::with_capacity(rows.len());
    let mut labels = StringBuilder::new();
    for r in rows {
        proc_id.append_value(r.proc_id);
        pid.append_value(r.pid);
        ppid.append_value(r.ppid);
        uid.append_value(r.uid);
        exe_path.append_option(r.exe_path.as_deref());
        exe_name.append_value(&r.exe_name);
        starttime.append_value(r.starttime_ticks);
        first_seen.append_value(r.first_seen_ns);
        last_seen.append_value(r.last_seen_ns);
        exit_code.append_option(r.exit_code);
        labels.append_option(r.labels.as_deref());
    }
    let arrays: Vec<ArrayRef> = vec![
        Arc::new(proc_id.finish()),
        Arc::new(pid.finish()),
        Arc::new(ppid.finish()),
        Arc::new(uid.finish()),
        Arc::new(exe_path.finish()),
        Arc::new(exe_name.finish()),
        Arc::new(starttime.finish()),
        Arc::new(first_seen.finish()),
        Arc::new(last_seen.finish()),
        Arc::new(exit_code.finish()),
        Arc::new(labels.finish()),
    ];
    RecordBatch::try_new(processes_schema(), arrays)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn batches_round_trip_row_counts() {
        let sample = SampleRow {
            t_mono_ns: 1,
            proc_id: 0,
            pid: 42,
            dt_ns: None,
            cpu_utime_ticks: None,
            cpu_stime_ticks: None,
            num_threads: 1,
            vm_rss_kb: 1024,
            vm_swap_kb: 0,
            vm_size_kb: 2048,
            pss_kb: Some(900),
            voluntary_ctxt_switches: Some(1),
            nonvoluntary_ctxt_switches: Some(2),
            gpu_util_pct: None,
            gpu_mem_bytes: None,
        };
        let batch = samples_batch(&[sample.clone(), sample]).expect("valid batch");
        assert_eq!(batch.num_rows(), 2);
        assert_eq!(batch.schema(), samples_schema());
    }
}
