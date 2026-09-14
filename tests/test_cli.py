"""The command line surface.

These check the contract a user sees - which flags exist, what the exit codes
mean, what a mistake says - not the monitoring behaviour underneath.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from conftest import write_log
from typer.testing import CliRunner

from treehawk.cli import app

runner = CliRunner()

# Typer forces a terminal under CI (GITHUB_ACTIONS, FORCE_COLOR), and Rich then
# styles "--interval" as two spans, so help text is compared with styling gone.
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    return ANSI_ESCAPE.sub("", text)


def test_version_is_reported() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "treehawk" in result.stdout


def test_bare_invocation_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "watch" in result.stdout and "run" in result.stdout


@pytest.mark.parametrize("command", ["watch", "run"])
def test_the_everyday_options_are_the_short_list(command: str) -> None:
    """Anything rarely needed lives under Advanced, so --help stays readable."""
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0
    text = plain(result.stdout)
    head = text.split("Advanced")[0]
    for flag in ("--interval", "--output", "--csv", "--quiet"):
        assert flag in head
    assert "Advanced" in text


def test_watch_without_a_target_says_what_to_give() -> None:
    result = runner.invoke(app, ["watch"])
    assert result.exit_code == 1
    assert "keyword" in result.output


def test_watch_rejects_two_ways_of_naming_the_same_thing() -> None:
    result = runner.invoke(app, ["watch", "train.py", "--pid", "42"])
    assert result.exit_code == 1
    assert "just one" in result.output


def test_watch_reports_a_process_that_is_not_running(tmp_path: pathlib.Path) -> None:
    result = runner.invoke(app, ["watch", "definitely-not-running-xyzzy", "-q", "-o", str(tmp_path / "x.jsonl")])
    assert result.exit_code == 2
    assert "no process matched" in result.output


def test_run_without_a_command_says_so() -> None:
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 1
    assert "after --" in result.output


def test_an_impossible_interval_is_refused() -> None:
    result = runner.invoke(app, ["watch", "x", "-i", "0"])
    assert result.exit_code != 0


def test_report_renders_a_log(log: str) -> None:
    result = runner.invoke(app, ["report", log])
    assert result.exit_code == 0
    assert "peak" in result.stdout
    assert "train.py" in result.stdout


def test_report_can_emit_json(log: str) -> None:
    result = runner.invoke(app, ["report", log, "--json"])
    assert result.exit_code == 0
    assert '"summary"' in result.stdout


def test_report_on_a_missing_file_fails_clearly(tmp_path: pathlib.Path) -> None:
    result = runner.invoke(app, ["report", str(tmp_path / "nope.jsonl")])
    assert result.exit_code == 1
    assert "cannot read" in result.output


def test_pdf_writes_next_to_the_log(tmp_path: pathlib.Path) -> None:
    log_path = write_log(tmp_path / "run.jsonl")
    result = runner.invoke(app, ["pdf", log_path])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "run.pdf").exists()


def test_pdf_on_an_empty_log_fails_clearly(tmp_path: pathlib.Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    result = runner.invoke(app, ["pdf", str(empty)])
    assert result.exit_code == 1
