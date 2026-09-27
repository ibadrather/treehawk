"""``top``, reading its logs back, and the systemd service - on a fake machine."""

from __future__ import annotations

import pathlib
import signal
from collections.abc import Iterator, Sequence

import pytest
from conftest import TEST_HOST, FakeClock, FakeHost, FakeProcessSource, FakeSystemSource
from typer.testing import CliRunner

from treehawk.cli import app
from treehawk.cli.models import Runtime
from treehawk.cli.parsing import parse_interval, parse_moment, parse_size, parse_span
from treehawk.cli.service import unit_text
from treehawk.core.config import Pacing
from treehawk.core.errors import ConfigError
from treehawk.platforms.models import Platform
from treehawk.report import read_records

runner = CliRunner()
MIB = 1024 * 1024
REPO = pathlib.Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def restore_signal_handlers() -> Iterator[None]:
    saved = [(signum, signal.getsignal(signum)) for signum in (signal.SIGINT, signal.SIGTERM)]
    yield
    for signum, handler in saved:
        if handler is not None:
            signal.signal(signum, handler)


class FakeSystemctl:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fail_on = fail_on

    def __call__(self, arguments: Sequence[str]) -> int:
        self.calls.append(list(arguments))
        return 1 if self.fail_on in arguments else 0


def machine() -> Platform:
    source = FakeProcessSource([lambda s: s.bump(1, ticks=40)] * 100)
    source.put(1, comm="busy", rss_bytes=50 * MIB, starttime=1)
    source.put(2, comm="big", rss_bytes=900 * MIB, starttime=2)
    return Platform(
        name="linux",
        process_source=source,
        host_source=FakeHost(TEST_HOST),
        system_source=FakeSystemSource(),
    )


def runtime(
    *,
    platform: Platform | None = None,
    root: bool = True,
    systemctl: FakeSystemctl | None = None,
    unit_dir: str = "/nonexistent",
) -> Runtime:
    chosen = platform or machine()
    return Runtime(
        platform=lambda: chosen,
        clock=FakeClock(),
        self_pid=lambda: 9,
        is_root=lambda: root,
        systemctl=systemctl or FakeSystemctl(),
        unit_dir=unit_dir,
    )


def run_top(directory: pathlib.Path, *extra: str) -> None:
    result = runner.invoke(
        app,
        ["top", "--dir", str(directory), "--duration", "6", "--quiet", *extra],
        obj=runtime(),
    )
    assert result.exit_code == 0, result.output


def test_top_writes_a_readable_log_directory(tmp_path: pathlib.Path) -> None:
    logs = tmp_path / "logs"
    run_top(logs, "--segment", "3")
    files = sorted(logs.rglob("*.jsonl.gz"))
    assert len(files) == 3  # 0-2s, 3-5s, 6s
    kinds = {record.get("type") for record in read_records(str(logs))}
    assert kinds >= {"header", "proc", "sample", "event", "summary"}


def test_report_and_pdf_read_a_top_directory(tmp_path: pathlib.Path) -> None:
    logs = tmp_path / "logs"
    run_top(logs)
    report = runner.invoke(app, ["report", str(logs)], env={"COLUMNS": "140"})
    assert report.exit_code == 0, report.output
    assert "top by cpu time while ranked" in report.output
    assert "/usr/bin/busy" in report.output

    as_json = runner.invoke(app, ["report", str(logs), "--json"])
    assert as_json.exit_code == 0
    assert '"top_by_memory"' in as_json.output

    destination = tmp_path / "machine.pdf"
    pdf = runner.invoke(app, ["pdf", str(logs), "-o", str(destination)])
    assert pdf.exit_code == 0, pdf.output
    assert destination.stat().st_size > 1000


def test_a_window_with_nothing_in_it_says_so(tmp_path: pathlib.Path) -> None:
    logs = tmp_path / "logs"
    run_top(logs)
    result = runner.invoke(app, ["report", str(logs), "--until", "2000-01-01"])
    assert result.exit_code == 1
    assert "no samples" in result.output


def test_a_window_is_refused_for_a_workload_log(log: str) -> None:
    result = runner.invoke(app, ["report", log, "--since", "1h"])
    assert result.exit_code == 1
    assert "top logs only" in result.output


def test_top_rejects_a_bad_interval(tmp_path: pathlib.Path) -> None:
    result = runner.invoke(app, ["top", "--dir", str(tmp_path), "--interval", "fast"], obj=runtime())
    assert result.exit_code == 1
    assert "auto" in result.output
    zero = runner.invoke(app, ["top", "--dir", str(tmp_path), "--interval", "0"], obj=runtime())
    assert zero.exit_code == 1
    assert "greater than 0" in zero.output


def test_service_install_writes_the_unit_and_starts_it(tmp_path: pathlib.Path) -> None:
    systemctl = FakeSystemctl()
    result = runner.invoke(
        app,
        ["service", "install", "--interval", "auto", "--top", "15"],
        obj=runtime(systemctl=systemctl, unit_dir=str(tmp_path)),
    )
    assert result.exit_code == 0, result.output
    unit = (tmp_path / "treehawk.service").read_text()
    assert "-m treehawk top --dir /var/lib/treehawk --top 15 --interval auto" in unit
    assert "StateDirectory=treehawk" in unit
    assert "ReadWritePaths" not in unit
    assert systemctl.calls == [["daemon-reload"], ["enable", "treehawk.service"], ["restart", "treehawk.service"]]


def test_service_install_elsewhere_may_write_there(tmp_path: pathlib.Path) -> None:
    result = runner.invoke(
        app,
        ["service", "install", "--dir", "/data/treehawk"],
        obj=runtime(unit_dir=str(tmp_path)),
    )
    assert result.exit_code == 0, result.output
    assert "ReadWritePaths=/data/treehawk" in (tmp_path / "treehawk.service").read_text()


def test_service_install_needs_root(tmp_path: pathlib.Path) -> None:
    result = runner.invoke(app, ["service", "install"], obj=runtime(root=False, unit_dir=str(tmp_path)))
    assert result.exit_code == 1
    assert "root" in result.output
    assert not (tmp_path / "treehawk.service").exists()


def test_service_install_reports_a_systemctl_failure(tmp_path: pathlib.Path) -> None:
    systemctl = FakeSystemctl(fail_on="enable")
    result = runner.invoke(app, ["service", "install"], obj=runtime(systemctl=systemctl, unit_dir=str(tmp_path)))
    assert result.exit_code == 1
    assert "systemctl enable" in result.output


def test_service_uninstall_stops_and_removes(tmp_path: pathlib.Path) -> None:
    (tmp_path / "treehawk.service").write_text("[Unit]\n")
    systemctl = FakeSystemctl()
    result = runner.invoke(app, ["service", "uninstall"], obj=runtime(systemctl=systemctl, unit_dir=str(tmp_path)))
    assert result.exit_code == 0, result.output
    assert not (tmp_path / "treehawk.service").exists()
    assert systemctl.calls == [["disable", "--now", "treehawk.service"], ["daemon-reload"]]


def test_the_shipped_unit_file_is_what_install_writes() -> None:
    """``packaging/treehawk.service`` is for copying by hand; it must not drift."""
    shipped = (REPO / "packaging" / "treehawk.service").read_text()
    expected = unit_text(
        command=["/usr/local/bin/treehawk", "top", "--dir", "/var/lib/treehawk"],
        log_dir="/var/lib/treehawk",
    )
    assert shipped == expected
    assert shipped in (REPO / "docs" / "machine.md").read_text()


def test_parsing() -> None:
    assert parse_interval("auto") == (Pacing.AUTO, None)
    assert parse_interval("0.25") == (Pacing.FIXED, 0.25)
    assert parse_size("500M") == 500 * MIB
    assert parse_size("1GiB") == 1024 * MIB
    assert parse_span("30m") == pytest.approx(1800.0)
    assert parse_span("2d") == pytest.approx(172800.0)
    assert parse_moment("1h", now=10000.0) == pytest.approx(6400.0)
    assert parse_moment("2026-01-01T00:00:00+00:00", now=0.0) == pytest.approx(1767225600.0)
    for bad in ("fast", "1s"):
        with pytest.raises(ConfigError):
            parse_interval(bad)
    with pytest.raises(ConfigError):
        parse_size("lots")
    with pytest.raises(ConfigError):
        parse_moment("yesterday", now=0.0)
