# Hourly clan site updater

Status: live updater validated and Scheduled Task registered; task may be intentionally disabled during maintenance.

## Purpose

The updater keeps both the clan roster and war data current. One normal run performs exactly three official API requests:

1. current clan details and member roster;
2. current clan war;
3. clan war log.

It then updates local detailed-war history, builds public-only JSON, validates the site, and publishes through Git only when public data changed.

## Roster coverage

Every run rebuilds the roster from the current clan endpoint. The public site therefore reflects:

- members joining or leaving;
- nickname changes;
- clan-role changes;
- Town Hall upgrades;
- clan level and badge changes.

Stable player tags are used only in local internal history and never appear in `site/data`.

## War order

Current-war participants use the official `mapPosition` field. The public field is `war_position`, and the website keeps participants in positions `1..N`. Search and filters only hide cards; they do not reorder the remaining cards.

## Local history

Internal detailed history is stored outside Git:

```text
D:\coc\data\war_history\history.json
```

Schema v2 stores distinct immutable observations and a monotonic canonical snapshot. Identical facts are deduplicated. Later incomplete responses cannot delete earlier members or attacks. War log reconciliation can close a lifecycle without inventing missing final personal facts. The public export is:

```text
site/data/war-history.json
```

It contains no player tags, clan tag, opponent identity, raw API data, token material, or leadership notes.

The real local history remains schema v1 until a separately approved migration. Before API configuration or probes, the updater checks the local schema and refuses v1 with an instruction to use `scripts/update/migrate_war_history_v1_to_v2.py`. Offline migration tests do not change `D:\coc\data\war_history\history.json`. The Scheduled Task is disabled during this maintenance stage.

Before a probe can run, the updater also requires both browser scripts, `site/assets/js/app.js` and `site/assets/js/current-war-contract.js`. When Node.js is available it runs `node --check` for both before and after the publish boundary; a missing or syntactically invalid current-war contract therefore fails closed.

The real history is still v1 and the Scheduled Task remains Disabled. No live migration or live API validation occurred in this maintenance pass. The offline preflight integration test uses an isolated temporary workspace only; it proves invalid history stops before local config, probes and run-directory creation.

The updater reports separate persistence, public replacement, commit and push stages. A commit failure leaves the run backup and a dirty working tree for manual recovery; a push failure leaves one local data commit ahead of `origin/main` for manual inspection and push. It never silently creates a second replacement commit.

During maintenance, the updater does not auto-push any ahead commit. `check_update_git_state.py` fails closed, prints the pending HEAD and changed paths, and requires manual inspection and push before a new collection. This prevents Scheduled Task from publishing unrelated code or documentation.

## Local configuration

The clan tag is not committed to the repository. Create the local configuration once:

```powershell
powershell.exe `
  -NoLogo `
  -NoProfile `
  -ExecutionPolicy Bypass `
  -File 'D:\coc\repo\scripts\update\initialize_local_update_config.ps1' `
  -ClanTag '#YOUR_CLAN_TAG' `
  -ConfirmConfig
```

The config is stored outside Git and Codebase Memory:

```text
D:\coc\data\config\clan_site_update.json
```

## Unified command

Preview-only mode performs the three API requests and all validations but changes neither persistent history nor Git:

```powershell
powershell.exe `
  -NoLogo `
  -NoProfile `
  -ExecutionPolicy Bypass `
  -File 'D:\coc\repo\scripts\update\update_clan_site.ps1' `
  -PreviewOnly
```

Normal mode updates history and site data, commits allowed public JSON files, and pushes `main` when data changed:

```powershell
powershell.exe `
  -NoLogo `
  -NoProfile `
  -ExecutionPolicy Bypass `
  -File 'D:\coc\repo\scripts\update\update_clan_site.ps1'
```

The updater fails closed when Git is dirty, the local branch is behind GitHub, a probe fails, generated public data is unsafe, tests fail, or unexpected files change.

## Hourly Windows task

Task registration is a separate explicit action:

```powershell
powershell.exe `
  -NoLogo `
  -NoProfile `
  -ExecutionPolicy Bypass `
  -File 'D:\coc\repo\scripts\update\install_hourly_update_task.ps1' `
  -ConfirmSchedule
```

The task:

- runs every hour;
- also runs when the user logs on;
- starts a missed run when possible;
- ignores a second instance while one run is active;
- may wake the laptop from sleep;
- runs only for the configured interactive Windows user.

It persists across restart and shutdown. It cannot run while the laptop is fully powered off. After the next user logon, the logon trigger performs one current update rather than replaying every missed hour.

Because the DPAPI token and Git credentials belong to the current Windows user, the scheduled task is intentionally configured for that user while logged on. A locked session is still logged on; a pre-login machine state is not.

## Desktop shortcuts

Shortcuts are created separately:

```powershell
powershell.exe `
  -NoLogo `
  -NoProfile `
  -ExecutionPolicy Bypass `
  -File 'D:\coc\repo\scripts\update\install_desktop_shortcuts.ps1' `
  -ConfirmShortcuts
```

They provide:

- `Обновить сайт клана`;
- `Проверить сайт клана без публикации`.

## Logs and runs

Each update writes a run directory below:

```text
D:\coc\runs\site_update
```

Transcripts are stored below:

```text
D:\coc\local\logs\site_update
```

These locations remain outside Git and Codebase Memory.

## Important limitations

Hourly collection greatly reduces the risk of missing attacks. It cannot reconstruct detailed player attacks for a war if the laptop was unavailable for the entire period in which the detailed current-war endpoint exposed that war. The war log may still retain the aggregate result, but not historical per-player attacks.

Codebase Memory is not refreshed hourly. It indexes source structure, not fast-changing local or public data snapshots, and remains a separate explicit maintenance action.

The official clan score comes from `current war -> clan.stars`. The sum of attack results is retained separately and is not used as the clan score. A new-stars contribution is emitted only when global attack order and defender links are complete and unique.
