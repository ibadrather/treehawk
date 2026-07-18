# Security policy

Treehawk launches the command you give it and records process metadata to local files. By
design it executes arbitrary commands — that is its job, not a vulnerability.

Reports we consider in scope include: recorded sessions leaking data they should not (treehawk
records executable names and user-provided labels, never command lines or environment
variables), escapes from the per-run cgroup containment, and unsafe handling of hostile
`/proc` or cgroup file contents. The internal threat model lives at
[`agents/references/threat-model.md`](agents/references/threat-model.md).

If you believe you have found a vulnerability that is in scope, please report it privately via
[GitHub security advisories](https://github.com/ibadrather/treehawk/security/advisories/new) —
do not open a public issue.
