# Collection reliability v1 closeout

## Status

completed

## Goal

Make the regular collection updater observable and safely diagnosable without
reading raw payloads or long local transcripts.

## Implemented

- bootstrap log before mutex and Git preflight;
- per-run `health.json` and local latest-run, last-success and latest-failure summaries;
- atomic health writes, stable result taxonomy, structured stages and UTC timestamps;
- Stopwatch durations, `process_exit_code` and native stdout/stderr boundary;
- non-fatal stderr warning at exit `0`, fail-closed native nonzero exit, HTTP 403 classification and VPN operator hint;
- intentional dirty-Git diagnostics and safe repository-relative blocking paths;
- human and JSON operator command with logical path projection;
- legacy health compatibility and privacy/path-redaction tests.

## Operational semantics

The updater does not enable VPN, discover external IP, change the API key or
allowlist. This installation usually requires an approved VPN; HTTP 403 only
suggests checking that VPN and does not prove a particular cause. A failed
collection retains the last successful published data. Dirty Git is an
intentional blocker: the updater never stashes, resets or cleans. A run with
no public changes creates no empty data commit. Health remains local and is
never published.

## Verified natural run

`20260726-210001-439c3f15` was a normal successful run with process exit `0`
and duration `38.195` seconds. The three probes, builder, validation, tests,
apply, commit and push completed. A Python warning on stderr was non-fatal;
Task Scheduler recorded result `0`.

## Fixed defects

- a stderr warning was treated as a terminating PowerShell error;
- duration could become negative;
- stages could remain running and the complete stage could be absent;
- process exit could disagree with the Task result;
- operator output could disclose paths;
- a legacy health record could break the complete projection.

## Limitations

- the task requires an enabled computer and interactive user session;
- IP allowlist configuration can produce expected HTTP 403 responses;
- the updater does not manage VPN;
- Task Scheduler Operational log can be unavailable;
- health is local operator state;
- gaps before health existed are not reconstructed automatically;
- Codebase Memory excludes local health, runs, data and secrets.

## Broad memory remediation

The production stage remains completed. The external clean rebuild of `D-coc`
at `D:/coc` completed successfully, with 1320 nodes, 5791 edges and 96 File
nodes. Official read-only graph queries found all 12 required File nodes exactly
once; `obsidian` and every other excluded category had zero File nodes, while
the allowed scripts, tests and docs areas remained indexed. `D-coc-repo`
remains a separate, unchanged repository-scoped project. No private content was
read during this validation.

The previous raw Scheduled Task XML hash cannot be reproduced because its
historical XML export was not retained. The current semantic Task definition
matches the installer contract and has no detected semantic drift. Future checks
use the semantic fields and canonical semantic hash
`E44D8D73E6D0978EC34C6A3AA372F1545179F7C96FA524A2F145DCBC23E7D406`, not raw
XML hash alone. The Task was not changed during finalization.

## Definition of Done

- [x] implementation committed;
- [x] offline tests green;
- [x] natural normal run validated;
- [x] health lifecycle validated;
- [x] operator command validated;
- [x] legacy compatibility validated;
- [x] Git clean;
- [x] commits pushed;
- [x] Task restored;
- [x] documentation synced;
- [x] broad Codebase Memory externally rebuilt and read-only validated.
