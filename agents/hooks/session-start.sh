#!/bin/bash
# Adapted from uv's agents/hooks/session-start.sh (https://github.com/astral-sh/uv),
# MIT OR Apache-2.0. Local sessions need no setup; remote (web) sessions get
# their environment prepared by the web variant.
set -euo pipefail

# Dispatch to web hook if running remotely
if [ "${CLAUDE_CODE_REMOTE:-}" = "true" ]; then
  exec bash "$(dirname "$0")/session-start-web.sh"
fi
