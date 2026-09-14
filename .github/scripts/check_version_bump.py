"""Fail when the package changed but its version did not go up.

Usage: check_version_bump.py <base-sha>

Compares the version in pyproject.toml at <base-sha> with the one in the
working tree. Only changes that end up in the published package count - files
under src/ and pyproject.toml itself. When there is no usable base (the first
push of a branch, or a force push whose old tip is gone) the latest v* tag is
used instead.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import tomllib
from packaging.version import InvalidVersion, Version

PACKAGE_PATHS = ("src/", "pyproject.toml")
NULL_SHA = "0" * 40


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def commit_exists(ref: str) -> bool:
    probe = subprocess.run(["git", "cat-file", "-e", f"{ref}^{{commit}}"], capture_output=True)
    return probe.returncode == 0


def latest_release_tag() -> str | None:
    tags = git("tag", "--list", "v*", "--sort=-v:refname").splitlines()
    return tags[0] if tags else None


def version_at(ref: str) -> str | None:
    try:
        text = git("show", f"{ref}:pyproject.toml")
    except subprocess.CalledProcessError:
        return None
    return str(tomllib.loads(text)["project"]["version"])


def summary(line: str) -> None:
    print(line)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with pathlib.Path(path).open("a") as handle:
            handle.write(line + "\n")


def fail(message: str) -> int:
    print(f"::error file=pyproject.toml::{message}")
    summary(f"**Version check failed:** {message}")
    return 1


def main(argv: list[str]) -> int:
    with pathlib.Path("pyproject.toml").open("rb") as handle:
        raw = str(tomllib.load(handle)["project"]["version"])
    try:
        current = Version(raw)
    except InvalidVersion:
        return fail(f"version {raw!r} is not a valid PEP 440 version")
    if str(current) != raw:
        return fail(f"write the version as {str(current)!r}, not {raw!r}, so tags and wheel names agree")

    base = argv[1] if len(argv) > 1 else ""
    if not base or base == NULL_SHA or not commit_exists(base):
        base = latest_release_tag() or ""
        if not base:
            summary(f"No base commit and no release tag to compare with; accepting {current}.")
            return 0
        print(f"No usable base commit; comparing with the latest release tag {base}.")

    changed = [path for path in git("diff", "--name-only", base, "HEAD").splitlines() if path.startswith(PACKAGE_PATHS)]
    if not changed:
        summary(f"No package files changed since {base[:12]}; no version bump needed.")
        return 0

    previous_raw = version_at(base)
    if previous_raw is None:
        summary(f"pyproject.toml did not exist at {base[:12]}; accepting {current}.")
        return 0
    previous = Version(previous_raw)

    if current <= previous:
        listed = "\n".join(f"  {path}" for path in changed[:20])
        print(f"Package files changed since {base[:12]}:\n{listed}")
        return fail(
            f"the package changed but the version did not go up ({previous} -> {current}). "
            "Bump it, e.g. `uv version --bump patch`."
        )

    summary(f"Version bumped: {previous} -> {current}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
