# Security policy

## Supported versions

Only the latest release is supported with security fixes.

## Reporting a vulnerability

Report vulnerabilities privately through
[GitHub security advisories](https://github.com/ibadrather/treehawk/security/advisories/new) —
please do not open a public issue for anything security-sensitive.

You should receive an initial response within a week. Once a fix is available, the advisory is
published and credited.

## Scope notes

Treehawk runs unprivileged and records process metadata to local files. Areas of particular
interest for reports:

- Recorded sessions leaking data they should not (treehawk deliberately records executable
  names and user-provided labels, never command lines or environment variables, so secrets in
  arguments stay out of logs).
- Escapes from the per-run cgroup containment or the PID-tree fallback being tricked into
  sampling unrelated processes.
- Unsafe handling of hostile `/proc` or cgroup file contents (these parsers are fuzz-adjacent
  attack surface).

The internal threat model lives at
[`agents/references/threat-model.md`](agents/references/threat-model.md).
