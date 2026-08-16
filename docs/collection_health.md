# Collection health

## Status

completed; natural scheduled-run and legacy reader compatibility validated

## Scope and privacy

Collection health is local operator telemetry. It is written below
`D:\coc\local\health\site_update` and into the corresponding local updater
run directory. It is excluded from Git, `site/data`, GitHub Pages and Codebase
Memory. It contains neither token material, Authorization, raw API payloads,
game tags, DPAPI data nor absolute blocking Git paths.

`latest-run.json` records every normal and PreviewOnly attempt.
`last-success.json` changes only after a successful normal collection or a
normal collection with no public change. `latest-failure.json` changes only
after a failed normal run; a later normal success retains it and adds
`resolved_at_utc`. PreviewOnly never replaces either state file.

Each run has a versioned `health.json` and a JSON-lines `bootstrap.log` before
mutex, history and Git preflight exits. The health contract records the run,
stage timings, safe result code and `process_exit_code`, Git counts, probe
outcomes, builder, validation, local snapshot-history status, publication and freshness facts. Timestamps use
UTC `DateTimeOffset`; run and stage durations use a monotonic Stopwatch and
are never negative.

Stages are appended only when they begin. Every begun stage is finalized as
`success`, `no_change`, `failed` or `skipped`; stages not reached are absent.
Every final run has a `complete` stage. Controlled failures use process exit
code `1`, while successful, no-change, preview and mutex-skipped runs use `0`.

## Result semantics

The updater remains fail-closed. `api_http_403` means that the API rejected the
observed request with HTTP 403. In this installation collection usually needs
the approved VPN because the API key uses an IP allowlist. The updater does not
enable VPN, inspect the external IP, change the key or change the allowlist.
The operator hint is `enable_approved_vpn`: enable the configured approved VPN
and wait for the next scheduled run. It is not a claim that every 403 has the
same cause, and it is never labelled `token_invalid` without separate proof.

`git_dirty`, `git_branch_ahead`, `git_branch_behind` and
`git_branch_diverged` are intentional fail-closed gates. The updater never
stashes, commits unrelated work, resets, cleans or deletes files to bypass
them. Health reports counts and up to 20 repository-relative paths only.

Failed collection leaves the published site on the last confirmed data.
Freshness is measured from the last successful normal collection, not from a
failed attempt or an unchanged `site-config.json` heartbeat.

## Operator command

Run locally:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File D:\coc\repo\scripts\update\show_clan_site_health.ps1
```

Use `-Json` for a machine-readable read-only result. Both formats expose only
the run ID and a logical `runs/site_update/<run-id>` location, never an
absolute workspace, repository, health or transcript path. The command performs no
network, GitHub, API, updater or secret operation. For `api_http_403` it tells
the operator that the latest published data did not change and to verify the
approved VPN before the next scheduled run.

The reader is backward-compatible with preserved health records that predate
optional fields such as `process_exit_code`, `publication` or `freshness`.
Such a record is marked `legacy_record=true`; unavailable values remain null
and never prevent valid newer summaries from being displayed.

On builder failure, health may record only whether an exact local input bundle
was captured, its artifact count, capture status and a logical
`local/diagnostics/builder_failure/<run-id>` reference. It never contains the
bundle contents or an absolute path. Older health records without these fields
remain valid.

For normal runs that pass tests, `snapshot_history` is appended before
`atomic_apply`. Its safe result distinguishes successful/idempotent recording
from initialization, validation, schema, conflict, ordering, lock and write
failures. The health reader projects this optional object when present and
returns null for preserved records that predate it. PreviewOnly does not append
the stage and does not touch the SQLite store.

## Completed validation

Natural normal run `20260726-210001-439c3f15` completed successfully with
`process_exit_code=0` and duration `38.195` seconds. Its probes, builder,
validation, tests, apply, commit and push stages completed; a non-fatal Python
warning on native stderr did not change the successful result. The Task
Scheduler recorded result `0`.

The operator reader was then validated against that successful summary and a
preserved legacy failure. Human and JSON modes return success, retain the
legacy record as `legacy_record=true`, leave its unknown process exit as null,
and expose only a logical run path. No health state is published.
