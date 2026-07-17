# Style guide

_A style guide for user-facing text: CLI output, error messages, and documentation. Adapted from
[uv's STYLE.md](https://github.com/astral-sh/uv/blob/main/STYLE.md) (MIT OR Apache-2.0), with
uv-specific sections replaced by treehawk equivalents._

## General

1. Use of "e.g." and "i.e." should always be wrapped in commas, e.g., as shown here.
1. Em-dashes are okay, but not recommended when using monospace fonts. Use "—", not "--" or "-".
1. Always wrap em-dashes in spaces, e.g., "hello — world" not "hello—world".
1. Hyphenate compound words, e.g., use "per-process" not "per process".
1. Use backticks to escape: commands, code expressions, package names, and file paths.
1. Use less than and greater than symbols to wrap bare URLs, e.g., `<https://example.com>`.
1. Avoid bare URLs outside of reference documentation; prefer labels, e.g., `[name](url)`.
1. If a message ends with a single relevant value, precede it with a colon, e.g.,
   `Could not read the manifest: <path>`. If the value is a literal, wrap it in backticks.
1. Markdown files should be wrapped at 100 characters.
1. Use a space, not an equals sign, for command-line arguments with a value, e.g.,
   `--interval 10ms`, not `--interval=10ms`.

## Styling treehawk

1. Do not escape with backticks, e.g., treehawk, unless referring specifically to the `treehawk`
   executable.
1. Capitalize as "Treehawk" only at the beginning of a sentence or heading; otherwise "treehawk".

## Terminology

1. Use "cgroup", not "control group" or "cGroup".
1. Use "process tree", not "process-tree" (except as a compound adjective, e.g.,
   "per-process-tree accounting").
1. Use "session" for one recorded run (the Parquet files + manifest), not "log" or "trace".
1. Use "sample" for one measurement of one process; "sampling rate" not "sample rate".

## CLI messages

1. Errors start with `treehawk: error:` followed by a lowercase clause, e.g.,
   `treehawk: error: cgroup v2 is not mounted`.
1. Warnings and status lines go to stderr; only the requested data (reports, listings) goes to
   stdout, so output stays pipeable.
1. When an error has a remedy, state it in a second clause or line, e.g.,
   `try running with sudo or enable user delegation`.

## Documentation

1. Use periods at the end of all sentences, including lists unless they enumerate single items.
1. Avoid language that patronizes the reader, e.g., "simply do this".
1. Avoid "we" in favor of "treehawk" or imperative language.
