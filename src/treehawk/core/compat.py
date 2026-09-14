"""Standard-library features newer than the oldest Python treehawk supports.

Each name here is the real thing where it exists and a faithful stand-in where
it does not, so the rest of the package imports it from one place and never
checks the interpreter version itself.
"""

from __future__ import annotations

import sys

if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from enum import Enum

    class StrEnum(str, Enum):
        """Python 3.10 stand-in for :class:`enum.StrEnum`.

        ``str()`` and ``format()`` of a member give its value, as they do for
        the real one - records and CLI messages rely on that.
        """

        def __str__(self) -> str:
            return str(self.value)

        def __format__(self, format_spec: str) -> str:
            return str(self.value).__format__(format_spec)


__all__ = ["StrEnum", "override"]
