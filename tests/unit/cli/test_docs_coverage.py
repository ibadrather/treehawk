"""Every command and option the CLI offers is documented somewhere in ``docs/``.

The help text lives next to each option, but the docs are written by hand, so
a new flag can go undocumented without anyone noticing. This fails when it
does.
"""

from __future__ import annotations

import pathlib
import re
from collections.abc import Iterator

import pytest
import typer.main
from typer.core import TyperCommand, TyperGroup, TyperOption

from treehawk.cli import app

DOCS = pathlib.Path(__file__).resolve().parents[3] / "docs"


Command = TyperCommand | TyperGroup


def _commands(command: Command, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Command]]:
    """``command`` and every subcommand beneath it, with the words that reach each."""
    yield path, command
    if isinstance(command, TyperGroup):
        for name, sub in command.commands.items():
            assert isinstance(sub, (TyperCommand, TyperGroup))
            yield from _commands(sub, (*path, name))


def _root() -> Command:
    root = typer.main.get_command(app)
    assert isinstance(root, TyperGroup)
    return root


def _long_options(command: Command) -> Iterator[str]:
    for param in command.params:
        if isinstance(param, TyperOption):
            yield from (flag for flag in param.opts if flag.startswith("--"))


def _docs_text() -> str:
    return "\n".join(page.read_text(encoding="utf-8") for page in sorted(DOCS.rglob("*.md")))


CLI_COMMANDS = list(_commands(_root()))
OPTIONS = sorted(
    {(" ".join(path) or "treehawk", flag) for path, command in CLI_COMMANDS for flag in _long_options(command)}
    - {(" ".join(path) or "treehawk", "--help") for path, _ in CLI_COMMANDS}
)


def test_the_cli_is_walked() -> None:
    names = {" ".join(path) for path, _ in CLI_COMMANDS}
    assert {"watch", "run", "top", "report", "pdf", "service install", "service status"} <= names


@pytest.mark.parametrize(("command", "flag"), OPTIONS)
def test_every_option_is_documented(command: str, flag: str) -> None:
    # In code, as it appears in an options table: `--flag` or `--flag VALUE`.
    assert re.search(rf"`{re.escape(flag)}(?![\w-])", _docs_text()), f"{command} {flag} is not in docs/"


@pytest.mark.parametrize("path", [path for path, _ in CLI_COMMANDS if path])
def test_every_command_is_documented(path: tuple[str, ...]) -> None:
    name = " ".join(path)
    assert re.search(rf"(`|treehawk ){re.escape(name)}(?![\w-])", _docs_text()), f"{name} is not in docs/"
