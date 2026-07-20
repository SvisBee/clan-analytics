# Clan current-war probe

Status: live validation passed on 2026-07-20; one official current-war request completed successfully.

Public summary uses official `clan.stars` as `clan_stars`. `attack_stars_total` is the sum of individual attack results and may be larger after repeat attacks. Per-player `stars_earned` is labelled «Звёзды в атаках». `new_stars_contributed` is calculated by global attack order only when order and defender links are complete and unambiguous; otherwise it is `null` with a diagnostic status.

During the current frontend transition, top-level `stars_earned` is a deprecated compatibility alias for official `clan_stars`. In legacy JSON the same name meant the sum of attack results, so new consumers must never use it as a clan score: without `clan_stars` they display a neutral unavailable value. They may use legacy `stars_earned` only as the attack-result-total fallback.

## Purpose

The probe performs one authenticated request to the official current-war endpoint and stores a local snapshot that can later contribute player-level attacks and stars to the clan analytics project.

Verified endpoint contract used by this implementation:

- method: `GET`;
- endpoint: `/clans/{clanTag}/currentwar`;
- requests per invocation: exactly one;
- redirects: rejected;
- retries: none;
- timeout: 15 seconds by default;
- response size limit: 2 MiB.

The official Clash of Clans API portal was rechecked before preparing this package. The implementation remains fail-closed and accepts only the exact endpoint template embedded in the code.

## Outputs

A successful run creates exactly four files below a run-specific directory under `D:\coc\runs\api_probe`:

- `raw_current_war_response.json`;
- `probe_metadata.json`;
- `normalized_current_war.json`;
- `public_current_war_preview.json`.

Raw and normalized files are local/internal. The public preview contains only:

- war state and end date;
- participant and attack aggregates;
- player nickname, Town Hall level, attacks used, attacks available, stars, and average stars.

The public preview excludes player tags, clan tags, opponent identity, token material, and internal leadership fields.

## No-current-war response

A successful API response with `state = notInWar` is valid. It produces a public preview with `data_status = not_in_war`, zero participants, and an empty member list. It is not treated as an error and must not trigger a retry.

## Live validation

The first live execution completed successfully on 2026-07-20.

Observed request metadata:

- request count: 1;
- HTTP status: 200;
- redirects followed: 0;
- timeout: 15 seconds;
- response size: 11656 bytes.

Observed current-war snapshot:

- state: `inWar`;
- end date: 2026-07-20;
- participants: 15;
- attacks available: 30;
- attacks used: 15;
- stars earned: 34.

The observed snapshot contained tag-free public player metrics.
Because the war was still active, these values are provisional and
must not be treated as the final war result. Official active `clan_stars`
may grow between snapshots; a stale snapshot cannot lower a detailed final
score. The separate offline lifecycle regression is 38 -> 41 -> 45, while
future live validation must compare against the snapshot actually received.

## Important limitation

The endpoint provides only the current war object available at request time. It does not reconstruct detailed player attacks for older wars. To build history, successful detailed snapshots must be stored over time and later merged by internal player tag before producing a tag-free public projection.

## Execution policy

- Store the API token only in the existing DPAPI secret outside the workspace.
- Never pass or print the token directly.
- Run no more than one live request per explicit operator permission.
- Do not retry on `notInWar`, HTTP errors, normalization errors, or output validation failures.
- Do not publish raw or normalized output files.
