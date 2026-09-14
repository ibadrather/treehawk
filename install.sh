#!/bin/sh
# treehawk installer
#
#   curl -LsSf https://github.com/ibadrather/treehawk/releases/latest/download/install.sh | sh
#
# Installs the `treehawk` command for the current user from a GitHub release,
# using `uv tool install` (or `pipx install` when that is what you have). When
# neither is present, uv is installed first; uv then fetches a suitable Python
# by itself, so no system Python is needed.
#
# Options (pass through the pipe with `| sh -s -- --version 0.2.0`):
#   --version VERSION   install this release instead of the latest
#   -h, --help          show this help
#
# Environment:
#   TREEHAWK_VERSION        same as --version
#   TREEHAWK_WHEEL          install this wheel (path or URL) instead of a release
#   TREEHAWK_REPO           GitHub repository to install from (default: ibadrather/treehawk)
#   TREEHAWK_NO_BOOTSTRAP   set to 1 to stop instead of installing uv

set -eu

REPO="${TREEHAWK_REPO:-ibadrather/treehawk}"
VERSION="${TREEHAWK_VERSION:-}"
WHEEL="${TREEHAWK_WHEEL:-}"

say() {
    printf 'treehawk-installer: %s\n' "$*" >&2
}

err() {
    say "error: $*"
    exit 1
}

has() {
    command -v "$1" > /dev/null 2>&1
}

usage() {
    sed -n '2,20s/^# \{0,1\}//p' "$0" 2> /dev/null || true
    echo "See https://github.com/$REPO"
}

# Print the URL a GitHub "latest" link redirects to.
resolve_redirect() {
    if has curl; then
        curl -fsSLI -o /dev/null -w '%{url_effective}' "$1"
    else
        wget -q -S --spider "$1" 2>&1 | sed -n 's/^ *[Ll]ocation: *//p' | tail -n 1 | tr -d '\r'
    fi
}

latest_version() {
    final=$(resolve_redirect "https://github.com/$REPO/releases/latest") \
        || err "could not reach github.com/$REPO"
    tag=${final##*/tag/}
    [ "$tag" != "$final" ] || err "no release of $REPO found"
    printf '%s\n' "${tag#v}"
}

add_uv_to_path() {
    for dir in "${XDG_BIN_HOME:-}" "$HOME/.local/bin" "${CARGO_HOME:-$HOME/.cargo}/bin"; do
        if [ -n "$dir" ] && [ -x "$dir/uv" ]; then
            PATH="$dir:$PATH"
            return 0
        fi
    done
    return 1
}

bootstrap_uv() {
    if [ "${TREEHAWK_NO_BOOTSTRAP:-0}" = 1 ]; then
        err "neither uv nor pipx is installed (and TREEHAWK_NO_BOOTSTRAP=1); install uv from https://docs.astral.sh/uv/"
    fi
    say "neither uv nor pipx found; installing uv first (https://docs.astral.sh/uv/)"
    if has curl; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    else
        wget -qO- https://astral.sh/uv/install.sh | sh
    fi
    # The uv installer edits shell profiles, but this shell's PATH predates that.
    has uv || add_uv_to_path || err "uv was installed but cannot be found; open a new shell and run this again"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --version)
            [ $# -ge 2 ] || err "--version needs a value"
            VERSION=$2
            shift 2
            ;;
        --version=*)
            VERSION=${1#--version=}
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            err "unknown argument: $1 (see --help)"
            ;;
    esac
done

[ "$(uname -s)" = Linux ] || err "treehawk supports Linux only for now (this is $(uname -s))"
has curl || has wget || err "need curl or wget to download treehawk"

if [ -z "$WHEEL" ]; then
    VERSION=${VERSION#v}
    [ -n "$VERSION" ] || VERSION=$(latest_version)
    WHEEL="https://github.com/$REPO/releases/download/v$VERSION/treehawk-$VERSION-py3-none-any.whl"
    label="treehawk $VERSION"
else
    label="$WHEEL"
fi

if ! has uv && ! has pipx; then
    bootstrap_uv
fi

if has uv; then
    say "installing $label with uv"
    uv tool install --reinstall --python '>=3.12' "$WHEEL"
    hint="uv tool update-shell"
else
    say "installing $label with pipx"
    pipx install --force "$WHEEL" \
        || err "pipx could not install treehawk (it needs Python 3.12 or newer); installing uv and re-running is the simplest fix"
    hint="pipx ensurepath"
fi

if has treehawk; then
    say "done: $(treehawk --version)"
else
    say "done, but 'treehawk' is not on your PATH yet: run '$hint' and open a new shell"
fi
