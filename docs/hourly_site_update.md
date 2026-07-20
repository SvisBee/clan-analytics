# Hourly clan site updater

Status: implementation prepared and validated offline; no scheduled task has been registered and no unified live update has been executed.

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

The same war is updated in place using a deterministic internal identifier. Hourly snapshots do not create duplicate wars. The public export is:

```text
site/data/war-history.json
```

It contains no player tags, clan tag, opponent identity, raw API data, token material, or leadership notes.

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
