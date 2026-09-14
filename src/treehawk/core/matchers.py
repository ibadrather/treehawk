"""Ways of naming the workload to watch.

Each matcher is one rule. Adding another (match on executable path, on user,
on environment) means adding a class and a registry entry, not editing the
seeding logic.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from treehawk.core.errors import ConfigError
from treehawk.core.interfaces import ProcessMatcher, Record
from treehawk.core.models import ProcInfo


class PidMatcher:
    """Match one explicit process id."""

    def __init__(self, pid: int) -> None:
        self._pid = pid

    @property
    def needs_cmdline(self) -> bool:
        return False

    @property
    def names_one_process(self) -> bool:
        return True

    def matches(self, *, info: ProcInfo, cmdline: str) -> bool:
        del cmdline  # a pid names the process outright
        return info.pid == self._pid

    def describe(self) -> Record:
        return {"kind": "pid", "value": self._pid}


class KeywordMatcher:
    """Case-insensitive substring of the command line (or the process name)."""

    def __init__(self, keyword: str) -> None:
        self._raw = keyword
        self._needle = keyword.lower()

    @property
    def needs_cmdline(self) -> bool:
        return True

    @property
    def names_one_process(self) -> bool:
        return False

    def matches(self, *, info: ProcInfo, cmdline: str) -> bool:
        return self._needle in (cmdline or info.comm).lower()

    def describe(self) -> Record:
        return {"kind": "keyword", "value": self._raw}


class ExactMatcher:
    """The whole reconstructed command line must equal the given string."""

    def __init__(self, command: str) -> None:
        self._command = command.strip()

    @property
    def needs_cmdline(self) -> bool:
        return True

    @property
    def names_one_process(self) -> bool:
        return False

    def matches(self, *, info: ProcInfo, cmdline: str) -> bool:
        del info  # only the command line counts
        return cmdline.strip() == self._command

    def describe(self) -> Record:
        return {"kind": "exact", "value": self._command}


class RegexMatcher:
    """Regular expression searched against the command line."""

    def __init__(self, pattern: str) -> None:
        self._pattern = pattern
        self._regex = re.compile(pattern, re.IGNORECASE)

    @property
    def needs_cmdline(self) -> bool:
        return True

    @property
    def names_one_process(self) -> bool:
        return False

    def matches(self, *, info: ProcInfo, cmdline: str) -> bool:
        return self._regex.search(cmdline or info.comm) is not None

    def describe(self) -> Record:
        return {"kind": "regex", "value": self._pattern}


MatcherFactory = Callable[[object], ProcessMatcher]

MATCHER_KINDS: dict[str, MatcherFactory] = {
    "pid": lambda value: PidMatcher(int(str(value))),
    "keyword": lambda value: KeywordMatcher(str(value)),
    "exact": lambda value: ExactMatcher(str(value)),
    "regex": lambda value: RegexMatcher(str(value)),
}


def build_matcher(*, kind: str, value: object) -> ProcessMatcher:
    """Create a matcher by name. Unknown names raise :class:`ConfigError`."""
    try:
        factory = MATCHER_KINDS[kind]
    except KeyError:
        raise ConfigError(f"unknown matcher {kind!r}; known: {', '.join(sorted(MATCHER_KINDS))}") from None
    return factory(value)
