# Installation

treehawk runs on **Linux** with **Python 3.10 or newer**. It needs nothing else to
watch processes. For the exact mode of [`treehawk run`](../guides/run.md), the
machine should also have cgroup v2 and a systemd user session, which every
current desktop and server distribution provides.

## The installer

The quickest route is the install script attached to every release:

```console
$ curl -LsSf https://github.com/ibadrather/treehawk/releases/latest/download/install.sh | sh
```

It installs the `treehawk` command for the current user with `uv tool install`,
or with `pipx` if that is what you have. If neither is present it installs
[uv](https://docs.astral.sh/uv/) first, and uv fetches a suitable Python by
itself, so no system Python is needed.

Options go through the pipe after `sh -s --`:

```console
$ curl -LsSf https://github.com/ibadrather/treehawk/releases/latest/download/install.sh | sh -s -- --version 0.3.1
```

| Option or variable | Effect |
|---|---|
| `--version VERSION`, `TREEHAWK_VERSION` | Install this release instead of the latest |
| `TREEHAWK_WHEEL` | Install this wheel (a path or a URL) instead of a release |
| `TREEHAWK_REPO` | Install from another GitHub repository (default `ibadrather/treehawk`) |
| `TREEHAWK_NO_BOOTSTRAP=1` | Stop instead of installing uv when no installer is found |
| `-h`, `--help` | Show the installer's help |

!!! tip "Read before you pipe"

    The script is short and plain POSIX `sh`. Download it first if you would
    rather read it: `curl -LsSf -o install.sh https://github.com/ibadrather/treehawk/releases/latest/download/install.sh`.

## Installing a release yourself

Each [GitHub release](https://github.com/ibadrather/treehawk/releases) carries a
universal wheel.

=== "uv"

    ```console
    $ uv tool install https://github.com/ibadrather/treehawk/releases/download/v0.3.1/treehawk-0.3.1-py3-none-any.whl
    ```

=== "pipx"

    ```console
    $ pipx install https://github.com/ibadrather/treehawk/releases/download/v0.3.1/treehawk-0.3.1-py3-none-any.whl
    ```

=== "pip"

    ```console
    $ python3 -m venv ~/.venvs/treehawk
    $ ~/.venvs/treehawk/bin/pip install https://github.com/ibadrather/treehawk/releases/download/v0.3.1/treehawk-0.3.1-py3-none-any.whl
    ```

=== "Latest main"

    ```console
    $ uv tool install git+https://github.com/ibadrather/treehawk
    ```

To try it without installing anything permanently, let `uvx` run it in a
throwaway environment:

```console
$ uvx --from git+https://github.com/ibadrather/treehawk treehawk --help
```

## From a checkout

```console
$ git clone https://github.com/ibadrather/treehawk
$ cd treehawk
$ uv sync
$ uv run treehawk --help
```

See [Contributing](../development/contributing.md) for the development workflow.

## Checking the install

```console
$ treehawk --version
treehawk 0.3.1
```

To check that `treehawk run` can give a command a cgroup of its own:

```console
$ stat -fc %T /sys/fs/cgroup
cgroup2fs
$ systemd-run --user --scope --quiet true && echo "scopes work"
scopes work
```

If either check fails, treehawk still works: `run` falls back to tracking through
`/proc` and records why in the log header's `notes`. See
[run and watch](../concepts/run-vs-watch.md) for what that changes.

## Upgrading and uninstalling

Run the installer again to move to the latest release, or reinstall a specific
wheel with `--force`:

```console
$ uv tool install --force https://github.com/ibadrather/treehawk/releases/download/v0.3.1/treehawk-0.3.1-py3-none-any.whl
```

To remove it:

=== "uv"

    ```console
    $ uv tool uninstall treehawk
    ```

=== "pipx"

    ```console
    $ pipx uninstall treehawk
    ```
