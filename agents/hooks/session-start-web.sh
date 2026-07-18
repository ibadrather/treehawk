#!/bin/bash
# Adapted from uv's agents/hooks/session-start-web.sh (https://github.com/astral-sh/uv),
# MIT OR Apache-2.0. Prepares a remote (Claude Code web) sandbox for treehawk work.
set -euo pipefail

# Install `gh`
if ! command -v gh &> /dev/null; then
    apt-get update -qq
    apt-get install -y -qq gh
fi

# Install clippy and rustfmt for the active toolchain.
if command -v rustup &> /dev/null; then
    rustup component add clippy rustfmt
fi

# Set GH_REPO so `gh` works even when the git remote points to a local proxy
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo 'export GH_REPO=ibadrather/treehawk' >> "$CLAUDE_ENV_FILE"
fi
