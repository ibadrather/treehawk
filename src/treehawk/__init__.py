"""treehawk - log CPU and RAM usage of a process and everything it spawns."""

import importlib.metadata

# pyproject.toml is the one place the version is written; CI refuses a package
# change that does not raise it.
try:
    __version__ = importlib.metadata.version("treehawk")
except importlib.metadata.PackageNotFoundError:  # a source tree never installed
    __version__ = "0.0.0+unknown"

SCHEMA_VERSION = 1
