# Install

treehawk runs on Linux and macOS, including Apple Silicon, with Python 3.10 or
newer.

```bash
curl -LsSf https://github.com/ibadrather/treehawk/releases/latest/download/install.sh | sh
```

The script needs no uv, pipx or other tooling. It installs the latest release
into its own virtual environment under `~/.local/share/treehawk`, links the
`treehawk` command into `~/.local/bin`, and adds that directory to your `PATH`
in your shell profiles. It builds the environment with any Python 3.10+ already
on the system, or with uv if you have it. If there is neither, it downloads a
temporary copy of uv just to fetch a Python.

## Installer options

Pass options after `sh -s --`, for example
`curl -LsSf …/install.sh | sh -s -- --version 0.7.0`. Each one can also be set
through an environment variable.

| Option | Environment variable | Default | |
|---|---|---|---|
| `--version VERSION` | `TREEHAWK_VERSION` | latest | install this release instead |
| `--python PATH` | `TREEHAWK_PYTHON` | first Python 3.10+ found | build the environment with this Python |
| `--install-dir DIR` | `TREEHAWK_INSTALL_DIR` | `~/.local/bin` | where to link the `treehawk` command |
| `--no-modify-path` | `TREEHAWK_NO_MODIFY_PATH=1` | | leave shell profiles alone |
| | `TREEHAWK_HOME` | `~/.local/share/treehawk` | where the environment lives |
| | `TREEHAWK_WHEEL` | | install this wheel (path or URL) instead of a release |
| | `TREEHAWK_REPO` | `ibadrather/treehawk` | GitHub repository to install from |
| | `TREEHAWK_NO_BOOTSTRAP=1` | | stop instead of downloading uv when no Python is found |
| `-h`, `--help` | | | list these |

## Upgrade and uninstall

To upgrade, run the script again. To uninstall:

```bash
rm -rf ~/.local/share/treehawk ~/.local/bin/treehawk
```

## Other ways

With uv, from a release or from `main`:

```bash
uv tool install https://github.com/ibadrather/treehawk/releases/download/v<version>/treehawk-<version>-py3-none-any.whl
uv tool install git+https://github.com/ibadrather/treehawk
```

From a checkout (see [Development](development.md)):

```bash
uv sync
uv run treehawk --help
uv tool install --editable . --force    # make the checkout your global treehawk
```

The editable install picks up source changes the next time you run
`treehawk`. A change to dependencies or entry points in `pyproject.toml` needs
the command run again. To go back to a release, rerun the install script.

To run treehawk as a service from boot, install it as above and then see
[Track the whole machine](machine.md#run-it-as-a-service).
