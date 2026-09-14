#!/bin/sh
# Re-record the sample run the docs are illustrated with, and render its outputs.
#
#   docs/scripts/render-output.sh
#
# Runs docs/scripts/demo/train.py under `treehawk run`, then writes, into
# docs/assets/output/:
#
#   sample-run.jsonl        the log itself, offered as a download
#   sample-run.pdf          `treehawk pdf` of it
#   pdf-page-N.png          each PDF page, for the report guide
#   report.svg              `treehawk report` of it
#   dashboard.svg           the live dashboard, part-way through
#
# Needs Linux with a systemd user session (for the cgroup) and pdftoppm
# (poppler-utils). Takes about half a minute.

set -eu

root="$(cd "$(dirname "$0")/../.." && pwd)"
out="$root/docs/assets/output"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

uv sync --project "$root" --quiet
treehawk="$root/.venv/bin/treehawk"

# Run from a scratch directory with the system python, so the command lines in
# the log read `python3 train.py` rather than a path into this checkout.
cp "$root/docs/scripts/demo/train.py" "$work/train.py"
(cd "$work" && "$treehawk" run --quiet --interval 0.5 --output sample-run.jsonl -- python3 train.py)

mkdir -p "$out"
# The log records the host name; keep the machine's own out of a published file.
sed 's/"hostname":"[^"]*"/"hostname":"devbox"/' "$work/sample-run.jsonl" > "$out/sample-run.jsonl"

"$treehawk" pdf "$out/sample-run.jsonl" --output "$out/sample-run.pdf"
rm -f "$out"/pdf-page-*.png
pdftoppm -r 110 -png "$out/sample-run.pdf" "$out/pdf-page"

uv run --project "$root" python "$root/docs/scripts/render_terminal.py" "$out/sample-run.jsonl" "$out"
