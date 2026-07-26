# Codebase Memory

## Принятая архитектура

Локальные файлы — source of truth. Codebase Memory является дополнительным индексом и не заменяет проверку актуального содержимого файлов.

| Назначение | Project | Root | Script |
| --- | --- | --- | --- |
| Daily repo index | `D-coc-repo` | `D:/coc/repo` | `scripts/workspace/refresh_codebase_memory_repo.ps1` |
| Broad workspace maintenance | `D-coc` | `D:/coc` | `scripts/workspace/refresh_codebase_memory.ps1` |

Daily project охватывает `repo/docs`, `repo/src`, `repo/site`, `repo/tests`, `repo/scripts` и repo configuration. Broad `D-coc` сохраняется для workspace-wide discovery материалов вне Git root; он не удаляется и не переименовывается. Private `obsidian/` leadership notes исключены из broad index вместе с `data/`, `runs/`, `local/` и `repo/site/data/`.

`D:/coc` не является Git root. Поэтому `detect_changes` на broad project может вернуть пустой набор после Git status 128 и не является доказательством отсутствия изменений. Broad rebuild выполняется только как отдельная ручная maintenance-операция и не использует Git HEAD как no-change gate.

## Safety contract

Оба скрипта используют только canonical binary:

`C:\Users\nshhi\AppData\Local\Programs\codebase-memory-mcp\codebase-memory-mcp.exe`

Перед реальным broad rebuild пользователь полностью закрывает Codex и передаёт `-ConfirmCodexClosed -ConfirmStopProcesses`. Script fail-closed проверяет известные Codex process names; он никогда не останавливает Codex. Скрипт останавливает только `codebase-memory-mcp.exe` и не использует wildcard для cache. Для каждого target разрешены только три точных файла: `<project>.db`, `<project>.db-wal`, `<project>.db-shm`.

Перед удалением старого graph скрипт копирует существующие target DB/WAL/SHM в run-local `backup`, сохраняет metadata и SHA-256. При failure после удаления target graph восстанавливается только из этого backup. Backup не удаляется автоматически. Initial build без старого graph явно фиксируется как `backup_present=false` и `rollback=unavailable-initial-build`.

PASS требует не только exit code: `status=indexed`, `skipped_count=0`, positive nodes/edges/File nodes, exact `nodes=expected_nodes`, exact `edges=expected_edges`, clean stderr, и persisted `list_projects` validation с точными project/root/counts. Run artifacts сохраняются вне Git.

## Outputs and exit codes

Daily runs: `D:\coc\runs\codebase_memory_repo_refresh\<YYYY-MM-DD>\<timestamp>_clean_full`.

Broad runs: `D:\coc\runs\codebase_memory_broad_rebuild\<YYYY-MM-DD>\<timestamp>_clean_full`.

Каждый run содержит backup, index stdout/stderr, `projects_after.json`, `refresh.status.txt`, `refresh.manifest.json` и `postflight_controls.json`.

| Code | Meaning |
| --- | --- |
| 0 | PASS |
| 10 | NO_CHANGE (daily Git HEAD only) |
| 20 | Invalid arguments or safety gate |
| 21 | Process quiescence failure |
| 22 | Backup failure |
| 23 | Index process failure |
| 24 | Actual/expected or worker validation failure |
| 25 | Persisted-project validation failure |
| 26 | Rollback failure |

If actual and expected counts differ, treat the run as failed, retain the logs and backup, and inspect the persisted project only after rollback status is known. Do not retry automatically.

## Broad exclusion remediation and external rebuild

`collection_reliability_v1` broad-memory remediation is complete. The external
clean rebuild of the existing `D-coc` project at `D:/coc` passed with 1320 nodes,
5791 edges and 96 File nodes. Official read-only graph queries verified all 12
required File nodes, zero File nodes for `obsidian` and every other excluded
category, and nonzero indexed files in `repo/scripts`, `repo/tests` and
`repo/docs`. Private notes are not indexed or read. `D-coc-repo` remains the
separate daily repository index, not a replacement for broad `D-coc`.

`refresh_codebase_memory.ps1` remains the supported clean full rebuild for the existing `D-coc` project only. It validates the exact project and root, backs up and removes only that project's graph files, uses `mode=full`, validates the persisted project, and reports nodes, edges and File nodes. It neither creates nor substitutes `D-coc-repo`. Any future refresh follows this documented external process and requires a new read-only validation.

Do not run it from an active Codex session. Fully close Codex, run the external command once with both confirmations, inspect the run report, then open a new Codex session for post-rebuild validation. Do not repeat the rebuild without inspecting that result.

## Manual daily initial test

After closing Codex, run this command once with explicit authorization:

    & 'D:\coc\repo\scripts\workspace\refresh_codebase_memory_repo.ps1' -ConfirmStopProcesses -ControlPath 'docs/tasks/clan_war_history_v1_closeout.md','src/clan_analytics/site_update.py','site/index.html' -ControlPhrase 'Definition of Done','build_site_update','current-war-v5-20260720'

The daily state file is `D:\coc\runs\codebase_memory_repo_refresh\state\D-coc-repo.last_success.json`. A matching daily Git HEAD returns exit code 10 unless `-Force` is supplied.

## Phase 2 after reopening Codex

Perform Phase 2 against `D-coc-repo`, not broad `D-coc`:

- `docs/tasks/clan_war_history_v1_closeout.md`: File node, Section nodes, and phrase `Definition of Done`.
- `src/clan_analytics/site_update.py`: File/Module, symbol `build_site_update`, and its own content.
- `site/index.html`: File/Module and asset contract `current-war-v5-20260720`.

The script writes the same project, root, Git HEAD, control paths and phrases into `postflight_controls.json` with `postflight_required_after_codex_restart=true`.

## Historical worker evidence

Historical docs record worker/index failures and earlier successful broad refreshes. They are evidence about previous runs, not claims about the internal mechanism of the closed CBM binary. Current rebuild diagnostics are the run-local stdout, stderr, manifest and rollback record.

Never run an external rebuild while Codex is open. Indexing, stopping CBM processes and graph-file deletion each require explicit authorization.
