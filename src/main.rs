use std::process::ExitCode;

use clap::Parser;

use treehawk::cli::{Cli, Command};
use treehawk::{cmd, logging};

fn main() -> ExitCode {
    let cli = Cli::parse();
    logging::init(cli.verbose);
    let result = match cli.command {
        Command::Run(args) => return cmd::run::run(&args),
        Command::Report(args) => cmd::report::report(&args),
        Command::Watch | Command::Export | Command::Ls | Command::Config | Command::Service => {
            eprintln!("treehawk: this subcommand is not yet implemented (planned milestone)");
            return ExitCode::from(2);
        }
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            eprintln!("treehawk: error: {err:#}");
            ExitCode::FAILURE
        }
    }
}
