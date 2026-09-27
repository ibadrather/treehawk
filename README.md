# treehawk

<table>
  <tr>
    <th align="left">Build</th>
    <td><a href="https://github.com/ibadrather/treehawk/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/ibadrather/treehawk/actions/workflows/ci.yml/badge.svg?branch=main"></a> <a href="https://github.com/ibadrather/treehawk/releases/latest"><img alt="Release" src="https://img.shields.io/github/v/release/ibadrather/treehawk?sort=semver"></a></td>
  </tr>
  <tr>
    <th align="left">Python</th>
    <td><a href="https://github.com/ibadrather/treehawk/actions/workflows/ci.yml"><img alt="Python 3.10" src="https://img.shields.io/badge/python-3.10-3776AB?logo=python&amp;logoColor=white"></a> <a href="https://github.com/ibadrather/treehawk/actions/workflows/ci.yml"><img alt="Python 3.11" src="https://img.shields.io/badge/python-3.11-3776AB?logo=python&amp;logoColor=white"></a> <a href="https://github.com/ibadrather/treehawk/actions/workflows/ci.yml"><img alt="Python 3.12" src="https://img.shields.io/badge/python-3.12-3776AB?logo=python&amp;logoColor=white"></a> <a href="https://github.com/ibadrather/treehawk/actions/workflows/ci.yml"><img alt="Python 3.13" src="https://img.shields.io/badge/python-3.13-3776AB?logo=python&amp;logoColor=white"></a> <a href="https://github.com/ibadrather/treehawk/actions/workflows/ci.yml"><img alt="Python 3.14" src="https://img.shields.io/badge/python-3.14-3776AB?logo=python&amp;logoColor=white"></a></td>
  </tr>
  <tr>
    <th align="left">Platforms</th>
    <td><a href="https://ibadrather.github.io/treehawk/platforms/"><img alt="Linux" src="https://img.shields.io/badge/platform-linux-FCC624?logo=linux&amp;logoColor=black"></a> <a href="https://ibadrather.github.io/treehawk/platforms/"><img alt="macOS" src="https://img.shields.io/badge/platform-macOS-000000?logo=apple&amp;logoColor=white"></a></td>
  </tr>
  <tr>
    <th align="left">Project</th>
    <td><a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue"></a> <a href="https://ibadrather.github.io/treehawk/"><img alt="Docs" src="https://img.shields.io/badge/docs-ibadrather.github.io%2Ftreehawk-4531cc"></a></td>
  </tr>
</table>

Log the CPU and RAM a process uses — **and every process it spawns**, including
children that daemonize and detach themselves. Or follow the busiest processes
of the whole machine, as a service from boot to shutdown.

**Documentation: <https://ibadrather.github.io/treehawk/>**

## Install

```bash
curl -LsSf https://github.com/ibadrather/treehawk/releases/latest/download/install.sh | sh
```

Linux and macOS, including Apple Silicon, with Python 3.10+. The script needs no
uv or pipx. For its options, upgrading, uninstalling and other ways to install,
see [Install](https://ibadrather.github.io/treehawk/install/).

## Quick tour

```bash
treehawk run -- python train.py --epochs 10   # start a command and follow it; exact
treehawk watch train.py                       # attach to one already running
treehawk report treehawk-*.jsonl              # summary in the terminal
treehawk pdf    treehawk-*.jsonl              # an 8-page PDF report

treehawk top                                  # the top processes of the whole machine
sudo "$(command -v treehawk)" service install # ... as a systemd service (Linux)
treehawk report /var/lib/treehawk --since 2h
```

Both modes sample until stopped or until the workload ends. While they run you
get a live dashboard, and afterwards a log you can summarise, chart, or query
with `jq`.

| | |
|---|---|
| [Track a workload](https://ibadrather.github.io/treehawk/workload/) | `watch` and `run`, and their options |
| [Track the whole machine](https://ibadrather.github.io/treehawk/machine/) | `top`, the service, spikes and leak suspects |
| [Read the results](https://ibadrather.github.io/treehawk/results/) | `report`, `pdf`, and the log formats |
| [How it works](https://ibadrather.github.io/treehawk/how-it-works/) | membership rules, memory measures, limitations |
| [Platforms](https://ibadrather.github.io/treehawk/platforms/) | what Linux and macOS can and cannot report |

Metrics come from the kernel directly (`/proc` and cgroup v2 on Linux,
`libproc` and `sysctl` on macOS), not from `ps`, `top` or a third-party library.

## Development

```bash
uv sync
uv run ruff format && uv run ruff check && uv run mypy && uv run pytest
```

Read [`AGENTS.md`](AGENTS.md) before changing anything, and
[Development](https://ibadrather.github.io/treehawk/development/) for the
architecture, tests and releases.
