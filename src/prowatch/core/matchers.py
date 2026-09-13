"""Ways of naming the workload to watch.

Each matcher is one rule. Adding another (match on executable path, on user,
on environment) means adding a class and a registry entry, not editing the
seeding logic.
"""

from __future__ import annotations

import re
from typing import Callable

from .interfaces import ProcessMatcher, Record
from .models import ProcInfo


class PidMatcher:
    """Match one explicit process id."""

    def __init__(self, pid: int) -> None:
        self._pid = pid

    @property
    def needs_cmdline(self) -> bool:
        return False

    def matches(self, info: ProcInfo, cmdline: str) -> bool:
        return info.pid == self._pid

    def describe(self) -> Record:
        return {"kind": "pid", "value": self._pid}


class KeywordMatcher:
    """Case-insensitive substring of the command line (or the process name)."""

    def __init__(self, keyword: str, *, case_sensitive: bool = False) -> None:
        self._raw = keyword
        self._case_sensitive = case_sensitive
        self._needle = keyword if case_sensitive else keyword.lower()

    @property
    def needs_cmdline(self) -> bool:
        return True

    def matches(self, info: ProcInfo, cmdline: str) -> bool:
        haystack = cmdline or info.comm
        if not self._case_sensitive:
            haystack = haystack.lower()
        return self._needle in haystack

    def describe(self) -> Record:
        return {
            "kind": "keyword",
            "value": self._raw,
            "case_sensitive": self._case_sensitive,
        }


class ExactMatcher:
    """The whole reconstructed command line must equal the given string."""

    def __init__(self, command: str) -> None:
        self._command = command.strip()

    @property
    def needs_cmdline(self) -> bool:
        return True

    def matches(self, info: ProcInfo, cmdline: str) -> bool:
        return cmdline.strip() == self._command

    def describe(self) -> Record:
        return {"kind": "exact", "value": self._command}


class RegexMatcher:
    """Regular expression searched against the command line."""

    def __init__(self, pattern: str, *, case_sensitive: bool = False) -> None:
        self._pattern = pattern
        flags = 0 if case_sensitive else re.IGNORECASE
        self._regex = re.compile(pattern, flags)

    @property
    def needs_cmdline(self) -> bool:
        return True

    def matches(self, info: ProcInfo, cmdline: str) -> bool:
        return self._regex.search(cmdline or info.comm) is not None

    def describe(self) -> Record:
        return {"kind": "regex", "value": self._pattern}


MatcherFactory = Callable[..., ProcessMatcher]

MATCHER_KINDS: dict[str, MatcherFactory] = {
    "pid": lambda value, **kw: PidMatcher(int(value)),
    "keyword": lambda value, **kw: KeywordMatcher(str(value), **kw),
    "exact": lambda value, **kw: ExactMatcher(str(value)),
    "regex": lambda value, **kw: RegexMatcher(str(value), **kw),
}


def build_matcher(kind: str, value: object, **kwargs: bool) -> ProcessMatcher:
    """Create a matcher by name. Unknown names raise ``ValueError``."""
    try:
        factory = MATCHER_KINDS[kind]
    except KeyError:
        raise ValueError(
            f"unknown matcher {kind!r}; known: {', '.join(sorted(MATCHER_KINDS))}"
        ) from None
    return factory(value, **kwargs)
