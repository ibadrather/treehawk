"""Installing ``treehawk top`` as a systemd service.

Only the unit text and the order of ``systemctl`` calls live here. Running
``systemctl`` goes through :class:`ServiceManager`, so the whole flow is
tested without touching the machine's init system.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from treehawk.cli.constants import CLI
from treehawk.core.errors import TreehawkError


class ServiceError(TreehawkError):
    """systemd refused, or is not there."""


class ServiceManager(Protocol):
    """Runs one ``systemctl`` command and returns its exit status."""

    def __call__(self, arguments: Sequence[str]) -> int: ...


def systemctl(arguments: Sequence[str]) -> int:
    """The real ``systemctl``."""
    try:
        return subprocess.run(["systemctl", *arguments], check=False).returncode
    except FileNotFoundError:
        raise ServiceError("systemctl not found; the service needs systemd (Linux)") from None


def unit_text(*, command: Sequence[str], log_dir: str) -> str:
    """The unit file.

    Hardened as far as a process monitor allows: it must read every process
    in /proc, so it runs as root, but it may write nowhere except its log
    directory, and it yields the CPU and the disk to real work.
    """
    writable = "" if log_dir == CLI.service_state_dir else f"ReadWritePaths={log_dir}\n"
    return f"""[Unit]
Description=treehawk: track the top processes on this machine
Documentation=https://ibadrather.github.io/treehawk/
After=local-fs.target

[Service]
Type=simple
ExecStart={shlex.join(command)}
Restart=always
RestartSec={CLI.service_restart_seconds}
KillSignal=SIGTERM
TimeoutStopSec={CLI.service_stop_timeout}
Nice={CLI.service_nice}
IOSchedulingClass=idle
StateDirectory={CLI.service_name}
{writable}ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=yes
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
"""


def unit_path(unit_dir: str) -> Path:
    return Path(unit_dir) / f"{CLI.service_name}.service"


def install(*, text: str, unit_dir: str, manager: ServiceManager) -> Path:
    """Write the unit, then enable and (re)start it."""
    path = unit_path(unit_dir)
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise ServiceError(f"cannot write {path}: {exc}") from exc
    _run(manager, ["daemon-reload"])
    _run(manager, ["enable", f"{CLI.service_name}.service"])
    _run(manager, ["restart", f"{CLI.service_name}.service"])
    return path


def uninstall(*, unit_dir: str, manager: ServiceManager) -> Path:
    """Stop and disable the service, and remove its unit. Logs are left alone."""
    path = unit_path(unit_dir)
    if path.exists():
        manager(["disable", "--now", f"{CLI.service_name}.service"])
        try:
            path.unlink()
        except OSError as exc:
            raise ServiceError(f"cannot remove {path}: {exc}") from exc
        _run(manager, ["daemon-reload"])
    return path


def status(*, manager: ServiceManager) -> int:
    return manager(["status", "--no-pager", f"{CLI.service_name}.service"])


def _run(manager: ServiceManager, arguments: list[str]) -> None:
    code = manager(arguments)
    if code != 0:
        raise ServiceError(f"systemctl {' '.join(arguments)} failed with exit status {code}")
