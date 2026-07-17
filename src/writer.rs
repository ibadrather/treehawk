//! Writer thread: bounded-interval flushing, crash-safe active chunks, Parquet
//! rotation (FR-15, FR-17, FR-18; AT-4).
//!
//! Design (plan §Key designs 6): Parquet needs its footer, so a killed process
//! would lose the whole open chunk. Instead the active chunk is an Arrow IPC
//! *stream* file (`<table>-active.arrows`) appended and fsynced every flush
//! window (≤ 5 s); it is converted into a numbered `.parquet` chunk at rotation
//! boundaries and at finalize. A truncated `.arrows` tail is readable up to the
//! last complete batch.

use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::PathBuf;
use std::sync::mpsc::{Receiver, RecvTimeoutError, Sender, channel};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use anyhow::{Context, Result};
use arrow::datatypes::SchemaRef;
use arrow::ipc::reader::StreamReader;
use arrow::ipc::writer::StreamWriter;
use arrow::record_batch::RecordBatch;
use parquet::arrow::ArrowWriter;
use parquet::basic::{Compression, ZstdLevel};
use parquet::file::properties::WriterProperties;

use crate::model::{
    HostRow, ProcessRow, SampleRow, host_batch, host_schema, processes_batch, processes_schema,
    samples_batch, samples_schema,
};

/// Messages from the sampler thread.
pub enum WriterMsg {
    Tick {
        samples: Vec<SampleRow>,
        host: Option<HostRow>,
        /// Identity rows for processes first seen this tick — appended to a
        /// crash-safe WAL immediately, corrected at finalize.
        new_processes: Vec<ProcessRow>,
    },
    /// Clean shutdown: the full, corrected process table.
    Finalize { processes: Vec<ProcessRow> },
}

pub struct WriterOptions {
    pub dir: PathBuf,
    /// Bounded data-loss window (FR-17). Default 5 s.
    pub flush_interval: Duration,
    /// Active chunk size that triggers rotation to Parquet (FR-18).
    pub rotate_bytes: u64,
}

impl WriterOptions {
    pub fn new(dir: PathBuf) -> Self {
        Self {
            dir,
            flush_interval: Duration::from_secs(5),
            rotate_bytes: 64 * 1024 * 1024,
        }
    }
}

pub fn spawn_writer(opts: WriterOptions) -> (Sender<WriterMsg>, JoinHandle<Result<()>>) {
    let (tx, rx) = channel();
    let handle = std::thread::Builder::new()
        .name("treehawk-writer".into())
        .spawn(move || writer_thread(opts, rx))
        .expect("spawning the writer thread cannot fail");
    (tx, handle)
}

/// One logical table: an active IPC-stream WAL plus rotated Parquet chunks.
struct Table {
    dir: PathBuf,
    prefix: &'static str,
    schema: SchemaRef,
    wal: Option<StreamWriter<BufWriter<File>>>,
    wal_batches: u64,
    chunk_index: u32,
}

impl Table {
    fn new(dir: &std::path::Path, prefix: &'static str, schema: SchemaRef) -> Self {
        Self {
            dir: dir.to_path_buf(),
            prefix,
            schema,
            wal: None,
            wal_batches: 0,
            chunk_index: 0,
        }
    }

    fn wal_path(&self) -> PathBuf {
        self.dir.join(format!("{}-active.arrows", self.prefix))
    }

    fn chunk_path(&self, index: u32) -> PathBuf {
        self.dir.join(format!("{}-{index:05}.parquet", self.prefix))
    }

    /// Appends one batch to the WAL and pushes it to disk (flush + fdatasync).
    fn append(&mut self, batch: &RecordBatch) -> Result<()> {
        if self.wal.is_none() {
            let file = File::create(self.wal_path())
                .with_context(|| format!("create {}", self.wal_path().display()))?;
            self.wal = Some(StreamWriter::try_new(BufWriter::new(file), &self.schema)?);
            self.wal_batches = 0;
        }
        let wal = self.wal.as_mut().expect("just ensured above");
        wal.write(batch)?;
        wal.get_mut().flush()?;
        wal.get_mut().get_ref().sync_data()?;
        self.wal_batches += 1;
        Ok(())
    }

    fn wal_len(&self) -> u64 {
        std::fs::metadata(self.wal_path())
            .map(|m| m.len())
            .unwrap_or(0)
    }

    /// Converts the active WAL into the next numbered Parquet chunk.
    fn rotate(&mut self) -> Result<()> {
        let Some(mut wal) = self.wal.take() else {
            return Ok(());
        };
        wal.finish()?;
        drop(wal);
        let path = self.wal_path();
        let chunk = self.chunk_path(self.chunk_index);
        convert_arrows_to_parquet(&path, &chunk, &self.schema)?;
        std::fs::remove_file(&path)?;
        self.chunk_index += 1;
        Ok(())
    }

    fn rotate_if_large(&mut self, rotate_bytes: u64) -> Result<()> {
        if self.wal.is_some() && self.wal_len() >= rotate_bytes {
            self.rotate()?;
        }
        Ok(())
    }
}

/// Reads every complete batch of an IPC stream file and writes a Parquet file.
/// Tolerates a truncated tail (reads up to the last complete batch).
pub fn convert_arrows_to_parquet(
    arrows: &std::path::Path,
    parquet: &std::path::Path,
    schema: &SchemaRef,
) -> Result<()> {
    let reader = StreamReader::try_new(File::open(arrows)?, None)?;
    let props = WriterProperties::builder()
        .set_compression(Compression::ZSTD(ZstdLevel::default()))
        .build();
    let mut writer = ArrowWriter::try_new(File::create(parquet)?, schema.clone(), Some(props))?;
    for batch in reader.flatten() {
        // .flatten() drops the Err of a truncated tail — exactly the AT-4 contract.
        writer.write(&batch)?;
    }
    writer.close()?;
    Ok(())
}

fn writer_thread(opts: WriterOptions, rx: Receiver<WriterMsg>) -> Result<()> {
    let mut samples_table = Table::new(&opts.dir, "samples", samples_schema());
    let mut host_table = Table::new(&opts.dir, "host", host_schema());
    let mut processes_wal = Table::new(&opts.dir, "processes", processes_schema());

    let mut pending_samples: Vec<SampleRow> = Vec::new();
    let mut pending_host: Vec<HostRow> = Vec::new();
    let mut pending_procs: Vec<ProcessRow> = Vec::new();
    let mut next_flush = Instant::now() + opts.flush_interval;

    let flush = |samples: &mut Vec<SampleRow>,
                 host: &mut Vec<HostRow>,
                 procs: &mut Vec<ProcessRow>,
                 samples_table: &mut Table,
                 host_table: &mut Table,
                 processes_wal: &mut Table|
     -> Result<()> {
        if !samples.is_empty() {
            samples_table.append(&samples_batch(samples)?)?;
            samples.clear();
        }
        if !host.is_empty() {
            host_table.append(&host_batch(host)?)?;
            host.clear();
        }
        if !procs.is_empty() {
            processes_wal.append(&processes_batch(procs)?)?;
            procs.clear();
        }
        Ok(())
    };

    let mut final_processes: Option<Vec<ProcessRow>> = None;
    loop {
        let timeout = next_flush.saturating_duration_since(Instant::now());
        match rx.recv_timeout(timeout) {
            Ok(WriterMsg::Tick {
                samples,
                host,
                new_processes,
            }) => {
                pending_samples.extend(samples);
                pending_host.extend(host);
                pending_procs.extend(new_processes);
                // Backstop so huge trees at high rates don't balloon memory.
                if pending_samples.len() >= 100_000 {
                    flush(
                        &mut pending_samples,
                        &mut pending_host,
                        &mut pending_procs,
                        &mut samples_table,
                        &mut host_table,
                        &mut processes_wal,
                    )?;
                    samples_table.rotate_if_large(opts.rotate_bytes)?;
                    host_table.rotate_if_large(opts.rotate_bytes)?;
                }
            }
            Ok(WriterMsg::Finalize { processes }) => {
                final_processes = Some(processes);
                break;
            }
            Err(RecvTimeoutError::Timeout) => {
                flush(
                    &mut pending_samples,
                    &mut pending_host,
                    &mut pending_procs,
                    &mut samples_table,
                    &mut host_table,
                    &mut processes_wal,
                )?;
                samples_table.rotate_if_large(opts.rotate_bytes)?;
                host_table.rotate_if_large(opts.rotate_bytes)?;
                next_flush = Instant::now() + opts.flush_interval;
            }
            // Sampler died without Finalize: preserve whatever we have.
            Err(RecvTimeoutError::Disconnected) => break,
        }
    }

    flush(
        &mut pending_samples,
        &mut pending_host,
        &mut pending_procs,
        &mut samples_table,
        &mut host_table,
        &mut processes_wal,
    )?;
    samples_table.rotate()?;
    host_table.rotate()?;
    if let Some(processes) = final_processes {
        // The corrected full table replaces the identity-only WAL.
        let props = WriterProperties::builder()
            .set_compression(Compression::ZSTD(ZstdLevel::default()))
            .build();
        let file = File::create(opts.dir.join("processes.parquet"))?;
        let mut writer = ArrowWriter::try_new(file, processes_schema(), Some(props))?;
        if !processes.is_empty() {
            writer.write(&processes_batch(&processes)?)?;
        }
        writer.close()?;
        if let Some(mut wal) = processes_wal.wal.take() {
            wal.finish()?;
        }
        std::fs::remove_file(processes_wal.wal_path()).ok();
    } else if let Some(mut wal) = processes_wal.wal.take() {
        wal.finish()?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::SampleRow;

    fn sample_row(t: u64) -> SampleRow {
        SampleRow {
            t_mono_ns: t,
            proc_id: 0,
            pid: 1,
            dt_ns: Some(1_000_000),
            cpu_utime_ticks: Some(2),
            cpu_stime_ticks: Some(1),
            num_threads: 1,
            vm_rss_kb: 100,
            vm_swap_kb: 0,
            vm_size_kb: 200,
            pss_kb: None,
            voluntary_ctxt_switches: None,
            nonvoluntary_ctxt_switches: None,
            gpu_util_pct: None,
            gpu_mem_bytes: None,
        }
    }

    #[test]
    fn wal_survives_truncation_and_converts_to_parquet() {
        let dir = std::env::temp_dir().join(format!("treehawk-writer-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("temp dir");
        let mut table = Table::new(&dir, "samples", samples_schema());
        table
            .append(&samples_batch(&[sample_row(1), sample_row(2)]).expect("batch"))
            .expect("append");
        table
            .append(&samples_batch(&[sample_row(3)]).expect("batch"))
            .expect("append");

        // Simulate SIGKILL: truncate mid-way through the last batch, no finish().
        let wal_path = table.wal_path();
        let full_len = std::fs::metadata(&wal_path).expect("meta").len();
        drop(table);
        let file = std::fs::OpenOptions::new()
            .write(true)
            .open(&wal_path)
            .expect("reopen wal");
        file.set_len(full_len - 8).expect("truncate");

        let out = dir.join("samples-00000.parquet");
        convert_arrows_to_parquet(&wal_path, &out, &samples_schema()).expect("convert");
        let reader = parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder::try_new(
            File::open(&out).expect("open parquet"),
        )
        .expect("builder")
        .build()
        .expect("reader");
        let rows: usize = reader.flatten().map(|b| b.num_rows()).sum();
        // First complete batch survives; the truncated one is dropped.
        assert_eq!(rows, 2);
        std::fs::remove_dir_all(&dir).ok();
    }
}
