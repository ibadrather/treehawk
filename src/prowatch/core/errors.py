"""Every failure prowatch decides to stop for.

One hierarchy, so the command line can handle all of them in one place instead
of knowing which module raises what. Submodules raise; only the CLI catches.

Exit codes live on the class rather than at the catch site, because the code a
failure deserves is a property of the failure, not of who noticed it.
"""

from __future__ import annotations


class ProwatchError(RuntimeError):
    """Base of every deliberate failure. Anything else is a bug."""

    exit_code = 1


class ConfigError(ProwatchError, ValueError):
    """The run was described in a way prowatch cannot carry out.

    Also a ``ValueError``: it is what a caller outside the CLI would expect
    from ``WatchConfig.validate()`` or a factory handed an unknown name.
    """


class UnsupportedPlatform(ProwatchError):
    """prowatch has no implementation for this operating system yet."""


class WorkloadNotFound(ProwatchError):
    """Nothing matched the user's request (within ``--wait``, if given)."""

    exit_code = 2


class LaunchFailed(ProwatchError):
    """The workload could not be started."""


class ReportError(ProwatchError):
    """A log could not be read, or contained no samples."""
