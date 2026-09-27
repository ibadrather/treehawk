# Whole machine: `top` and the service

!!! note "Linux only"
    The service needs **Linux with systemd**, and the machine-wide CPU and
    memory line needs Linux too. `treehawk top` runs by hand on macOS, but
    it tracks processes only there.

`treehawk top` watches **every** process on the machine and logs the top N by
CPU and the top N by memory. It keeps going until it is stopped. Two kinds of
trouble also get logged, for any process, whether it ranks or not:

- **spike**: a reading far above that process' own recent baseline, such as
  a process idling at 5% that jumps to 300%;
- **creep**: resident memory that has risen steadily for half an hour. This is
  a leak suspect.

```bash
treehawk top                         # every 0.5 s, top 10, logs in ./treehawk-top
treehawk top --interval auto         # as often as the machine allows (0.1-5 s)
treehawk top --top 20 --interval 1
```

## Run it as a service (Linux)

To run it from boot to shutdown, install treehawk first (see
[Install](index.md)). Then:

```bash
sudo "$(command -v treehawk)" service install
```

`sudo` is needed because a system service is written to `/etc/systemd/system`,
and only root can read every process' memory. `$(command -v treehawk)` passes
sudo the full path, which it would not otherwise find in `~/.local/bin`.

The command writes `/etc/systemd/system/treehawk.service`, enables it, and
starts it. It takes the same options as `top`:

```bash
sudo "$(command -v treehawk)" service install --interval auto --top 15 --keep 2G
```

Check on it:

```bash
systemctl status treehawk
journalctl -u treehawk -f        # spikes and leak suspects, as they happen
```

Remove it (the logs stay):

```bash
sudo "$(command -v treehawk)" service uninstall
```

### Or copy the unit file yourself

The unit file is [`packaging/treehawk.service`](https://github.com/ibadrather/treehawk/blob/main/packaging/treehawk.service):

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

Set `ExecStart` to the path `command -v treehawk` prints, then:

```bash
sudo cp packaging/treehawk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now treehawk
```

## Reading it back

```bash
treehawk report /var/lib/treehawk                  # everything kept
treehawk report /var/lib/treehawk --since 2h       # the last two hours
treehawk report /var/lib/treehawk --since "2026-09-27 14:00" --until "2026-09-27 15:00"
treehawk pdf    /var/lib/treehawk -o machine.pdf   # charts
```

The report lists:

- the top consumers;
- the largest CPU and memory spikes (when, who, how high, and what was normal
  for that process);
- the leak suspects, with how fast each one grows.

The PDF has five pages:

1. overview;
2. machine CPU and memory, with spikes marked;
3. CPU by process;
4. memory by process;
5. the spike and leak table.

Charts keep the peak of each time bucket, so a spike still shows in a month
of data.

## Where the logs go and how big they get

```
/var/lib/treehawk/<boot>/top-20260927-140000.jsonl.gz
```

- **One directory per boot**, so a reboot starts a new one.
- **One file per hour** (`--segment`). Each file is compressed once it is
  closed and is complete on its own, so `report` and `pdf` work on one file,
  one boot or everything.
- **A disk budget** (`--keep`, default 1G). The oldest files are deleted first.
  At 0.5 s and top 10, an hour takes roughly 1–2 MB compressed.

Each sample records:

- the machine's CPU %, memory in use and swap;
- for each top process: CPU %, RSS, PSS, swap, and why it is there
  (`c` for CPU, `m` for memory).

A process' command line is written once per file, not in every sample.
Collecting a sample costs one `stat` read per process. Only the top N are read
in depth.

## Limits

- A spike needs about 15 s of history for that process before it can fire, and
  a leak suspect needs 30 minutes of steady growth.
- GPU usage is not collected yet. The log has a column ready for it.
- `top` samples by polling, so a process that lives for less than one interval
  can be missed.
