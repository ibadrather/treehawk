//! Treehawk: run a command and log CPU/RAM usage of it and every descendant.
//!
//! Library form of the binary so integration tests (and, post-v1, the `PyO3`
//! bindings) can reach the internals. The CLI entry point is `main.rs`.

pub mod cgroup;
pub mod cli;
pub mod cmd;
pub mod manifest;
pub mod model;
pub mod proc;
pub mod sampler;
pub mod spawn;
pub mod util;
pub mod writer;
