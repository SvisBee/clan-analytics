# Clan war-log probe

Status: live validation passed on 2026-07-20; one official request completed successfully.

## Purpose

This probe checks the verified official endpoint:

```text
GET https://api.clashofclans.com/v1/clans/{encoded_clan_tag}/warlog
```

It performs exactly one HTTPS GET, follows no redirects, retries nothing, and writes only to a new run-specific directory inside:

```text
D:\coc\runs\api_probe\clan_war_log
```

The API token remains in the existing DPAPI-protected file outside the workspace. The PowerShell wrapper reuses the same host-local `Microsoft.PowerShell.Security` pinning and short-lived child-process environment pattern as the validated roster runner.

## Scope

The first version intentionally calls the war-log endpoint without query parameters. Pagination parameter names, defaults, cursor semantics, limits, rate limits, enum values, requiredness, and nullability are not claimed beyond the authenticated Swagger evidence already stored in the repository.

The probe stores the exact response internally and creates a separate safe aggregate preview.

## Output contract

A successful run creates exactly four JSON files:

```text
raw_war_log_response.json
probe_metadata.json
normalized_war_log.json
public_war_log_preview.json
```

Privacy boundaries:

- `raw_war_log_response.json` is internal and may contain clan tags and opponent identity;
- `normalized_war_log.json` is internal and preserves stable game identifiers for future joins;
- `probe_metadata.json` contains request metadata but no token or Authorization header;
- `public_war_log_preview.json` contains only neutral aggregates:
  - observed war count;
  - oldest and newest parseable war dates;
  - result distribution using exact observed strings;
  - team-size distribution.

The public preview never contains clan names, opponent names, game tags, player tags, the token, or internal leadership fields.

## Live validation

The first live execution completed successfully on 2026-07-20.

Observed request metadata:

- request count: 1;
- HTTP status: 200;
- redirects followed: 0;
- timeout: 15 seconds;
- response size: 1043 bytes.

Observed public aggregate:

- wars observed: 1;
- date: 2026-07-17;
- result: `win`;
- team size: 10.

This confirms the endpoint, authentication path, output transaction,
and normalization contract for the observed response. It does not yet
prove the API default page size or complete historical coverage.

## Important limitation

The official war-log entries confirmed by the project schema do not contain per-player member and attack lists. A war-log probe can provide aggregate history, but it cannot populate player cards with participation, attacks, or stars.

Detailed player history requires collecting and retaining detailed snapshots from the separately verified current-war endpoint over time:

```text
GET /clans/{clanTag}/currentwar
```

Schema v2 also uses a matching war-log entry to confirm lifecycle completion and official aggregates. Reconciliation never invents participants, individual attacks, defender links, destruction or map positions. An unmatched entry is retained as aggregate-only evidence; multiple compatible detailed records produce an ambiguous diagnostic instead of an automatic merge.

Therefore the safe sequence is:

1. run this one-request war-log probe;
2. inspect only `probe_metadata.json` and `public_war_log_preview.json`;
3. confirm the observed response contract;
4. prepare a separate current-war snapshot probe;
5. accumulate detailed snapshots locally without inventing historical player data.

## Offline validation

The implementation includes fictional fixtures and tests for:

- deterministic tolerant normalization;
- integer and float handling for verified Swagger fields;
- empty war-log handling;
- one exact official request URL;
- no environment access in dry-run;
- no retries or redirects;
- fail-closed output directories;
- transactional four-file publication;
- token redaction;
- public-preview exclusion of names and tags.

## Commands

Offline tests, with no network:

```powershell
python -m py_compile `
  D:\coc\repo\src\clan_analytics\api\models.py `
  D:\coc\repo\src\clan_analytics\api\normalization.py `
  D:\coc\repo\src\clan_analytics\api\war_log_probe.py `
  D:\coc\repo\scripts\api\probe_clan_war_log.py

python -m unittest discover `
  -s D:\coc\repo\tests `
  -p 'test_*.py'
```

Dry-run, with no token read and no network:

```powershell
powershell.exe `
  -NoLogo `
  -NoProfile `
  -NonInteractive `
  -ExecutionPolicy Bypass `
  -File 'D:\coc\repo\scripts\api\run_clan_war_log_probe.ps1' `
  -ClanTag '#2U2Y88QJQ' `
  -DryRun
```

A live run requires separate explicit permission because it reads the DPAPI secret and performs one external HTTPS GET.
