# Collection health

## Status

implemented, awaiting natural scheduled-run validation

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
stage timings, safe result code, Git counts, probe outcomes, builder,
validation, publication and freshness facts.

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

Use `-Json` for a machine-readable read-only result. The command performs no
network, GitHub, API, updater or secret operation. For `api_http_403` it tells
the operator that the latest published data did not change and to verify the
approved VPN before the next scheduled run.
