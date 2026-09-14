# Membership

The hard question for a process monitor is not "how much CPU is this process
using" but "which processes are *this workload*". treehawk answers it with one
idea, stickiness, and four rules.

## Why a parent-child walk is not enough

Following parent links works until a child daemonizes: `fork`, `setsid`, `fork`
again, and the original parent exits. The survivor is re-parented to PID 1, or to
a subreaper such as `systemd --user`, and it has left its parent's session. A
moment later there is no link left to follow.

<figure class="diagram" markdown="span">
  ![How a daemonized child escapes a parent-child walk](../assets/diagrams/daemonize.light.svg#only-light)
  ![How a daemonized child escapes a parent-child walk](../assets/diagrams/daemonize.dark.svg#only-dark)
</figure>

The whole dance takes microseconds, so it routinely happens between two samples.

## Admitted once, never evicted

A process joins the workload in one of two ways: the [matcher](../guides/watch.md#naming-the-process)
selects it when the watch starts, or one of the rules below adopts it later. From
then on it stays a member until **that exact process exits**. Losing its parent,
leaving its session, or being re-parented to PID 1 changes nothing.

"That exact process" means the pair of PID and start time, so a PID the kernel
reuses for an unrelated process is never mistaken for a member.

When a member exits, its final CPU time is folded into the running total, so the
workload's CPU time never goes backwards when a child finishes.

<figure class="diagram" markdown="span">
  ![The matcher seeds the tracker; the tree, cgroup, session and orphan rules adopt more processes into it](../assets/diagrams/membership-rules.light.svg#only-light)
  ![The matcher seeds the tracker; the tree, cgroup, session and orphan rules adopt more processes into it](../assets/diagrams/membership-rules.dark.svg#only-dark)
</figure>

## The four rules

Each sample, every enabled rule looks for processes to adopt, and they repeat
until nothing new turns up, since one adoption can reveal more (a cgroup adoption
can uncover a whole subtree). Choose which rules run with
[`--expand`](../guides/watch.md#limiting-the-membership-rules); all four are on by
default.

### `tree`

Adopts the children and grandchildren of every member, following parent links.
It catches everything a classic monitor catches, and nothing more.

### `cgroup`

Adopts everything inside a control group the workload owns. A cgroup is the one
boundary a process cannot leave by forking: `fork` and `setsid` change the parent
and the session, never the cgroup.

The danger is adopting a cgroup that is *not* the workload's. Your login session
is a cgroup too, and adopting it would drag in every process in the terminal. So a
cgroup is accepted only when both guards pass:

- treehawk itself is not inside it, and
- every process currently in it is already a member.

Once accepted, the cgroup is remembered, and processes that appear in it later are
adopted at once. The scope `treehawk run` creates skips the guards: there is no
doubt whose it is.

### `session`

Adopts processes that share a session led by a member. It catches children that
were re-parented away but kept the session, including ones started after the
session leader itself exited. treehawk's own session is never adopted.

### `orphan`

The rule aimed at the gap polling leaves open. It adopts a process that:

1. appeared since the last sample,
2. has been re-parented, and
3. sits in the same cgroup as a member.

The subtle part is (2). "Its parent is PID 1" is not enough, because
`systemd --user`, container init systems and anything else that sets
`PR_SET_CHILD_SUBREAPER` collect orphans themselves, so a detached child usually
reports a very much alive parent. What gives it away is that the adopting reaper
lives in a *different* cgroup, whereas a process spawned normally always starts in
its parent's. That one comparison separates a detached grandchild from an
unrelated process started in the same terminal.

This is the rule that closes the daemonization gap under `watch`, where no cgroup
belongs to the workload alone.

## What is never adopted

treehawk never adopts itself or any of its ancestors: the shell you typed the
command in, `uv`, `timeout`, `sudo`. They carry the keyword you searched for,
because you typed it there, and adopting the shell would track the whole terminal
rather than the work. `--pid` may *name* one of them, because then you meant it,
but no rule will adopt one.

## via

Every process in the log carries a `via` field: `match` for a process the matcher
selected, or the name of the rule that adopted it. It is the first thing to check
when a result surprises you.

Views group the values by what a reader wants to know: was treehawk told about the
process, did it follow a parent link, or did it have to recognise it after it
detached?

| Group | `via` | Colour |
|---|---|---|
| matched | `match` | <span class="th-chip th-chip--matched"></span> |
| child | `tree` | <span class="th-chip th-chip--child"></span> |
| detached | `cgroup`, `session`, `orphan` | <span class="th-chip th-chip--detached"></span> |

The same three colours are used by the dashboard, `treehawk report` and every page
of the PDF.
