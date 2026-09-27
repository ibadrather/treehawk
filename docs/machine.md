# Track the whole machine

`treehawk top` looks at **every** process on the machine and logs the top N by
CPU and the top N by memory. It runs until it is stopped. It also logs two kinds
of trouble for any process, whether or not it ranks:

- **spike**: a reading far above that process' own recent baseline, such as
  a process idling at 5% that jumps to 300%;
- **creep**: resident memory that has risen steadily for half an hour. This is
  a leak suspect.

[How it works](how-it-works.md#whole-machine-tracking) gives the exact rules.

!!! note "Linux for the full picture"
    `top` runs by hand on macOS but tracks processes only: there is no
    machine-wide CPU and memory line there, and no service.

```bash
treehawk top                         # every 0.5 s, top 10, logs in ./treehawk-top
treehawk top --interval auto         # as often as the machine allows (0.1-5 s)
treehawk top --top 20 --interval 1
```

## Options

| Option | Default | |
|---|---|---|
| `-n`, `--top N` | `10` | how many processes to follow, by CPU and by memory each |
| `-i`, `--interval SECONDS|auto` | `0.5` | time between samples, or `auto` to sample as often as the machine allows |
| `--dir PATH` | `./treehawk-top` | where the logs go, one directory per boot |
| `-q`, `--quiet` | | no live view, just the log |
| `--keep SIZE` | `1G` | disk the log directory may use; the oldest files go first (`500M`, `2G`, ...) |
| `--segment SPAN` | `1h` | time one log file covers before it is compressed and the next begins (`30m`, `1h`, `1d`, ...) |
| `--no-pss` | | skip the fair-memory read for the top N; cheaper per sample |
| `-d`, `--duration SECONDS` | until stopped | stop after this long |

## Run it as a service

On Linux with systemd, `top` can run from boot to shutdown.
[Install](install.md) treehawk first, then:

```bash
sudo "$(command -v treehawk)" service install
```

`sudo` is needed because the unit is written to `/etc/systemd/system` and only
root can read every process' memory. `$(command -v treehawk)` gives sudo the
full path, which it would not otherwise find in `~/.local/bin`.

| Command | |
|---|---|
| `service install [OPTIONS]` | write `/etc/systemd/system/treehawk.service`, enable it and (re)start it |
| `service status` | show whether the service is running (`systemctl status treehawk`) |
| `service uninstall` | stop the service and remove the unit; the logs are kept |

`service install` takes the same options as `top`, with two differences:
`--dir` defaults to `/var/lib/treehawk`, and there is no `--quiet` or
`--duration`. The options are checked when you install, not at the next boot.
Run it again to change them.

```bash
sudo "$(command -v treehawk)" service install --interval auto --top 15 --keep 2G
treehawk service status
journalctl -u treehawk -f        # spikes and leak suspects, as they happen
sudo "$(command -v treehawk)" service uninstall
```

The unit runs at `Nice=10` with idle I/O priority, so the monitor yields to real
work. It may write only to its log directory.

If you would rather manage the unit yourself, copy
[`packaging/treehawk.service`](https://github.com/ibadrather/treehawk/blob/main/packaging/treehawk.service).
It is what `service install` writes for the default options:

```ini
[Unit]
Description=treehawk: track the top processes on this machine
Documentation=https://ibadrather.github.io/treehawk/
After=local-fs.target

[Service]
Type=simple
ExecStart=/usr/local/bin/treehawk top --dir /var/lib/treehawk
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=30
Nice=10
IOSchedulingClass=idle
StateDirectory=treehawk
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=yes
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
```

Set `ExecStart` to the path `command -v treehawk` prints, plus any `top`
options, then:

```bash
sudo cp packaging/treehawk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now treehawk
```

## Reading it back

Point [`report` or `pdf`](results.md) at the log directory, a boot directory or
a single file. `--since` and `--until` select a time window:

```bash
treehawk report /var/lib/treehawk                  # everything kept
treehawk report /var/lib/treehawk --since 2h       # the last two hours
treehawk report /var/lib/treehawk --since "2026-09-27 14:00" --until "2026-09-27 15:00"
treehawk pdf    /var/lib/treehawk -o machine.pdf   # charts
```

The report lists the top consumers, the largest CPU and memory spikes (when,
who, how high, and what was normal for that process), and the leak suspects with
how fast each one grows.

## Where the logs go

```
/var/lib/treehawk/<boot>/top-20260927-140000.jsonl.gz
```

- **One directory per boot**, so a reboot starts a new one.
- **One file per `--segment`**, an hour by default. A file is compressed once it
  is closed and is complete on its own.
- **A disk budget** (`--keep`). The oldest files are deleted first. At 0.5 s and
  top 10, an hour takes roughly 1–2 MB compressed.

[Read the results](results.md#the-top-log) describes what is inside.
