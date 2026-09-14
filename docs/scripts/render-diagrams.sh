#!/bin/sh
# Export the draw.io diagrams the docs and the README show.
#
#   docs/scripts/render-diagrams.sh
#   DRAWIO=/path/to/drawio docs/scripts/render-diagrams.sh
#
# The .drawio files are the source; open one in draw.io (desktop, the VS Code
# extension, or app.diagrams.net), edit it, and run this to refresh the SVGs.
# Every diagram is exported twice, for light and for dark pages, and the pages
# pick the one that matches the reader's theme. The exports are committed so
# the site build and GitHub's README view need no draw.io of their own.

set -eu

DRAWIO="${DRAWIO:-drawio}"
command -v "$DRAWIO" > /dev/null 2>&1 || {
    echo "render-diagrams: draw.io desktop not found; install it or set DRAWIO" >&2
    exit 1
}

assets="$(cd "$(dirname "$0")/../assets" && pwd)"

export_svg() {
    # --no-sandbox and --disable-gpu let the Electron CLI run headless.
    "$DRAWIO" --no-sandbox --disable-gpu --export --format svg "$@" 2> /dev/null
}

strip_fallbacks() {
    # draw.io puts a PNG copy of every text label behind the HTML one, for
    # viewers without foreignObject support. Every browser (and so GitHub) has
    # it, and the copies make each file half a megabyte.
    python3 - "$@" << 'EOF'
import pathlib, re, sys
for name in sys.argv[1:]:
    path = pathlib.Path(name)
    path.write_text(re.sub(r"<image [^>]*?data:image/png;base64,[^\"]*\"[^>]*/>", "", path.read_text()))
EOF
}

for source in "$assets"/diagrams/*.drawio; do
    base="${source%.drawio}"
    for theme in light dark; do
        export_svg --theme "$theme" --border 12 --output "$base.$theme.svg" "$source"
        strip_fallbacks "$base.$theme.svg"
    done
done

# The logo is a tile, so one export reads on light and dark headers alike; it
# doubles as the favicon.
export_svg --theme light --transparent --output "$assets/brand/logo.svg" "$assets/brand/logo.drawio"
