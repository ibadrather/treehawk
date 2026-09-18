#!/bin/sh
# treehawk installer
#
#   curl -LsSf https://github.com/ibadrather/treehawk/releases/latest/download/install.sh | sh
#
# Installs the `treehawk` command for the current user from a GitHub release,
# into a private virtual environment under ~/.local/share/treehawk, and links
# the command into ~/.local/bin. Nothing else is required: a Python 3.10+
# already on the system is used as is (uv is used instead when installed). With
# neither, a throwaway copy of uv is downloaded just to fetch a Python.

set -eu

REPO="${TREEHAWK_REPO:-ibadrather/treehawk}"
VERSION="${TREEHAWK_VERSION:-}"
WHEEL="${TREEHAWK_WHEEL:-}"
PYTHON="${TREEHAWK_PYTHON:-}"
DATA_DIR="${TREEHAWK_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/treehawk}"
BIN_DIR="${TREEHAWK_INSTALL_DIR:-${XDG_BIN_HOME:-$HOME/.local/bin}}"
MODIFY_PATH=1
[ "${TREEHAWK_NO_MODIFY_PATH:-0}" = 1 ] && MODIFY_PATH=0

usage() {
    cat << EOF
treehawk installer

Usage: install.sh [OPTIONS]
       curl -LsSf https://github.com/$REPO/releases/latest/download/install.sh | sh -s -- [OPTIONS]

Options:
  --version VERSION     install this release instead of the latest
  --python PATH         build the environment with this Python (3.10 or newer)
  --install-dir DIR     where to link the treehawk command (default: ~/.local/bin)
  --no-modify-path      do not add the install dir to PATH in shell profiles
  -h, --help            show this help

Environment:
  TREEHAWK_VERSION          same as --version
  TREEHAWK_PYTHON           same as --python
  TREEHAWK_INSTALL_DIR      same as --install-dir
  TREEHAWK_NO_MODIFY_PATH   set to 1, same as --no-modify-path
  TREEHAWK_HOME             where the environment lives (default: ~/.local/share/treehawk)
  TREEHAWK_WHEEL            install this wheel (path or URL) instead of a release
  TREEHAWK_REPO             GitHub repository to install from (default: $REPO)
  TREEHAWK_NO_BOOTSTRAP     set to 1 to stop instead of downloading uv when no Python is found

Uninstall:
  rm -rf "$DATA_DIR" "$BIN_DIR/treehawk"
EOF
}

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

download() {
    if has curl; then
        curl -fsSL "$1"
    else
        wget -qO- "$1"
    fi
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

# True when $1 is Python 3.10+ able to create a virtual environment with pip.
python_ok() {
    "$1" -c 'import sys, venv, ensurepip; sys.exit(sys.version_info < (3, 10))' > /dev/null 2>&1
}

find_python() {
    for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
        if has "$candidate" && python_ok "$candidate"; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

# Download uv into a temporary directory without touching shell profiles.
fetch_uv() {
    if [ "${TREEHAWK_NO_BOOTSTRAP:-0}" = 1 ]; then
        err "no Python 3.10+ found (and TREEHAWK_NO_BOOTSTRAP=1); install Python 3.10+ or pass --python"
    fi
    say "no Python 3.10+ found; downloading uv to fetch one (https://docs.astral.sh/uv/)"
    UV_DIR="$TMP_DIR/uv"
    download https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL="$UV_DIR" sh > /dev/null \
        || err "could not download uv"
    UV="$UV_DIR/uv"
    [ -x "$UV" ] || err "uv was downloaded but cannot be found in $UV_DIR"
}

# Earlier installers used `uv tool install` or `pipx install`; drop those
# copies so they do not shadow or fight over the new link.
remove_old_installs() {
    if has uv && uv tool list 2> /dev/null | grep -q '^treehawk '; then
        say "removing the previous 'uv tool' install of treehawk"
        uv tool uninstall treehawk > /dev/null 2>&1 || true
    fi
    if has pipx && pipx list --short 2> /dev/null | grep -q '^treehawk '; then
        say "removing the previous pipx install of treehawk"
        pipx uninstall treehawk > /dev/null 2>&1 || true
    fi
}

# Print $1 with a leading $HOME written as "$HOME", for shell profiles.
home_relative() {
    case "$1" in
        "$HOME"/*) printf '$HOME/%s\n' "${1#"$HOME"/}" ;;
        *) printf '%s\n' "$1" ;;
    esac
}

add_line() {
    # $1: file, $2: line to append unless already present
    if [ -f "$1" ] && grep -qxF "$2" "$1"; then
        return 1
    fi
    mkdir -p "$(dirname "$1")"
    printf '\n%s\n' "$2" >> "$1"
}

modify_path() {
    dir=$(home_relative "$BIN_DIR")
    env_file="$DATA_DIR/env"
    cat > "$env_file" << EOF
#!/bin/sh
# Added by the treehawk installer: put treehawk on PATH.
case ":\${PATH}:" in
    *:"$dir":*) ;;
    *) export PATH="$dir:\$PATH" ;;
esac
EOF
    source_line=". \"$(home_relative "$env_file")\""

    changed=""
    profiles="$HOME/.profile $HOME/.bashrc $HOME/.bash_profile $HOME/.bash_login"
    zshrc="${ZDOTDIR:-$HOME}/.zshrc"
    for rc in $profiles "$zshrc"; do
        # .profile is always written; others only when they already exist, or
        # when zsh is the login shell and .zshrc does not exist yet.
        if [ -f "$rc" ] || [ "$rc" = "$HOME/.profile" ] \
            || { [ "$rc" = "$zshrc" ] && [ "${SHELL##*/}" = zsh ]; }; then
            add_line "$rc" "$source_line" && changed="$changed $rc"
        fi
    done
    if has fish || [ -d "$HOME/.config/fish" ]; then
        fish_file="${XDG_CONFIG_HOME:-$HOME/.config}/fish/conf.d/treehawk.fish"
        add_line "$fish_file" "fish_add_path -g \"$dir\"" && changed="$changed $fish_file"
    fi
    if [ -n "$changed" ]; then
        say "added $BIN_DIR to PATH in:$changed"
    fi
}

while [ $# -gt 0 ]; do
    case "$1" in
        --version | --python | --install-dir)
            [ $# -ge 2 ] || err "$1 needs a value"
            case "$1" in
                --version) VERSION=$2 ;;
                --python) PYTHON=$2 ;;
                --install-dir) BIN_DIR=$2 ;;
            esac
            shift 2
            ;;
        --version=*)
            VERSION=${1#--version=}
            shift
            ;;
        --python=*)
            PYTHON=${1#--python=}
            shift
            ;;
        --install-dir=*)
            BIN_DIR=${1#--install-dir=}
            shift
            ;;
        --no-modify-path)
            MODIFY_PATH=0
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

case "$(uname -s)" in
    Linux | Darwin) ;;
    *) err "treehawk supports Linux and macOS (this is $(uname -s))" ;;
esac
has curl || has wget || err "need curl or wget to download treehawk"

if [ -z "$WHEEL" ]; then
    VERSION=${VERSION#v}
    [ -n "$VERSION" ] || VERSION=$(latest_version)
    WHEEL="https://github.com/$REPO/releases/download/v$VERSION/treehawk-$VERSION-py3-none-any.whl"
    label="treehawk $VERSION"
else
    label="$WHEEL"
fi

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

# Pick how to build the environment: an explicit Python, then uv if it is
# installed, then any Python 3.10+ on PATH, then a throwaway uv.
UV=""
if [ -n "$PYTHON" ]; then
    python_ok "$PYTHON" || err "$PYTHON is not Python 3.10+ with venv and ensurepip"
elif has uv; then
    UV=$(command -v uv)
elif ! PYTHON=$(find_python); then
    fetch_uv
fi

mkdir -p "$DATA_DIR" "$BIN_DIR"
VENV="$DATA_DIR/venv"
OLD_VENV="$DATA_DIR/venv.old"

# A virtual environment cannot be moved once built, so build it in place and
# keep the previous one aside, to be put back if the install fails.
rm -rf "$OLD_VENV"
[ -d "$VENV" ] && mv "$VENV" "$OLD_VENV"
fail() {
    rm -rf "$VENV"
    [ -d "$OLD_VENV" ] && mv "$OLD_VENV" "$VENV"
    err "$*"
}

if [ -n "$UV" ]; then
    say "installing $label with uv"
    "$UV" venv --quiet --python '>=3.10' "$VENV" \
        || fail "uv could not create a Python 3.10+ environment"
    "$UV" pip install --quiet --python "$VENV/bin/python" "$WHEEL" \
        || fail "could not install $label"
else
    say "installing $label with $("$PYTHON" -c 'import sys; print("Python %d.%d" % sys.version_info[:2])')"
    "$PYTHON" -m venv "$VENV" || fail "$PYTHON could not create a virtual environment"
    "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check "$WHEEL" \
        || fail "could not install $label"
fi
installed=$("$VENV/bin/treehawk" --version) || fail "treehawk was installed but does not run"
rm -rf "$OLD_VENV"

remove_old_installs
ln -sf "$VENV/bin/treehawk" "$BIN_DIR/treehawk"
say "installed $installed to $BIN_DIR/treehawk"

case ":$PATH:" in
    *:"$BIN_DIR":*) ;;
    *)
        if [ "$MODIFY_PATH" = 1 ]; then
            modify_path
            say "open a new shell, or run: . \"$DATA_DIR/env\""
        else
            say "$BIN_DIR is not on your PATH; add it to use 'treehawk'"
        fi
        ;;
esac
