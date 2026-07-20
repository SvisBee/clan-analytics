# Reliable history foundation

Status: implemented and validated offline; real local history v1 is not migrated.

## Purpose

Schema v2 preserves detailed ordinary-war observations without publishing stable game identifiers. It separates detailed current-war facts, official war-log aggregates, inferred lifecycle state and tag-free public projections. This stage does not add player ratings or selection recommendations.

## Stable war identity

A record receives one deterministic `war_id` when first observed. The ID is not recalculated when later fields appear.

Strong identifiers are exact known preparation, start and end timestamps. Shared strong fields must agree. Team size and attacks per member are supporting evidence. They can reject a timestamp-free match, but a compatible shared strong timestamp is stronger than a late or regressing scalar: the record is merged, its first canonical scalar is retained and a diagnostic is recorded. Member-tag overlap is supporting evidence, not a global natural key, because the same roster can participate in multiple wars.

An early snapshot with missing timestamps may match only one recent active record with an exact participant set and compatible known war settings. A simple roster overlap is insufficient. When the next snapshot introduces a distinct known lifecycle timestamp, the short collection window, exact roster and compatible settings provide progressive identity evidence; this refines the same ID instead of creating a second war. Map position, nickname, Town Hall and attacks never define war identity.

Conflicting shared timestamps prohibit merge. Multiple equally compatible records produce `ambiguous` diagnostics and are not automatically collapsed. Insufficient evidence starts a separate record instead of mutating an unrelated completed war.

## Schema v2

Top level contains `schema_version: 2`, `wars` and `diagnostics`. Each war record contains:

- stable `war_id` and identity evidence;
- first and last collection timestamps;
- `lifecycle_status` and `reconciliation_status`;
- ordered `states_seen`;
- immutable `observations` with fingerprint, observation time and full internal snapshot;
- monotonic `canonical` detailed snapshot;
- separate `war_log` aggregate or `null`;
- diagnostics and migration metadata.

Aggregate-only war-log records have no detailed observations or canonical player snapshot.

## Observations and canonical merge

The observation fingerprint covers game facts but excludes collection time, local raw-source path and source timestamp. These source fields describe collection provenance rather than game facts. Identical API facts collected repeatedly create one observation. A changed state, participant fact, map position, attack or official aggregate creates a new observation.

Canonical merge is monotonic:

- a missing later scalar does not erase a known value;
- during `preparation` or `inWar`, official `clan_stars` can increase but never decreases; a lower observation records `regressing_clan_stars_observed`;
- the first detailed `warEnded` score finalizes the canonical official score; stale active snapshots cannot lower it, while a different later `warEnded` records `conflicting_final_clan_stars_observed`;
- a temporarily missing player is retained;
- nickname, Town Hall and map position can be updated while old observations remain immutable;
- known attacks are retained when a later response is incomplete;
- an exact repeated attack is not duplicated;
- conflicting facts for the same positive attack order keep the earlier canonical fact and add a diagnostic.

Immutable observations remain authoritative for reconstruction. Canonical is a latest-known convenience projection.

## Lifecycle

- `active` covers preparation and in-war detailed observations.
- `finalized_detailed` requires an observed detailed `warEnded`.
- `closed_without_final_detailed` means `notInWar` followed one active record.
- `closed_war_log_without_final_detail` means war log later confirmed such closure.
- `closed_war_log_only` is an aggregate with no detailed snapshot.
- `incomplete` means the detailed state is not understood.
- `ambiguous` means automatic identity or reconciliation is unsafe.

`notInWar` closes exactly one active record but does not rewrite its canonical detailed facts as a fictional final snapshot. Multiple active records are marked ambiguous.

The offline lifecycle regression uses `38 -> 41 -> 45`: 38/43/18 remains the fixed accounting fixture, 41/19 is a later active observation, and 45 is the observed final official score. These are test/documentation facts only. Future live validation must compare the public result with the `clan.stars` actually returned by its separately approved API snapshot, never a hard-coded 45.

## War-log reconciliation

War log supplies official result, clan/opponent aggregates, end time and team size when present. Automatic matching requires an exact end time plus at least one compatible supporting fact such as team size, attacks per member or an internal clan/opponent tag. Team size alone and absent timestamps never match. One match attaches the aggregate idempotently; a later detailed snapshot can promote an aggregate-only record without changing its stable war ID. No match creates an aggregate-only record. Multiple matches produce an ambiguity diagnostic.

War log never creates player membership, attacks, defender links, individual stars, destruction or map positions.

## Star accounting v1

`clan_stars` is the official score from `current war -> clan.stars` and is displayed as «Звёзды клана».

For one transition public schema also emits deprecated top-level `stars_earned = clan_stars` so already cached legacy JavaScript does not render `undefined` or `NaN`. New JavaScript uses `clan_stars` for the official score and `attack_stars_total` for per-attack averages; the alias will be removed only in a separately reviewed compatibility-retirement stage.

Older public JSON used `stars_earned` for the attack-result sum. It cannot reconstruct the official score, so current JavaScript shows `–` when that legacy payload lacks `clan_stars`; it may still use the value for the attack average.

`attack_stars_total` is the sum of `stars` across attacks. It is used for the per-attack average but can exceed the official score after repeated attacks against one base.

`stars_earned` is the sum of results in one player's attacks and is displayed as «Звёзды в атаках».

`new_stars_contributed` processes all clan attacks by unique positive global `attack.order`. For each defender it adds `max(0, attack.stars - previous_best)`. Missing, duplicate or invalid order, or a missing defender link, makes the contribution unavailable (`null`) rather than zero.

The consistency diagnostic sums the best observed stars for every attacked defender and compares the result with official `clan_stars`:

- `consistent` when equal;
- `inconsistent` when both values exist and differ;
- `unavailable` when official score or defender evidence is missing.

An inconsistent active snapshot does not fail publication. Official `clan_stars` remains authoritative and the calculated value remains diagnostic.

## Migration v1 to v2

Migration is deterministic and idempotent. Each legacy record preserves its `war_id`, timestamps, states and the only available `latest` snapshot as exactly one observation. Legacy observation count is metadata only because earlier snapshots cannot be reconstructed. Unknown future schema versions fail closed.

The implementation and tests do not migrate `D:\coc\data\war_history\history.json`. Live migration requires a separate reviewed action.

The site-update builder rejects schema v1 by default. The hourly updater checks history schema before loading API configuration or making any probe request and exits with an instruction to use the separate migration command. It never migrates history automatically.

The updater runs `validate_war_history.py` before API configuration, local token access, run-directory creation or probes. It accepts a missing history, rejects v1, invalid JSON, semantically invalid v2 and unknown versions. Semantic validation covers record kind, identity seed/evidence, diagnostics vocabulary, observation fingerprints, source metadata, detailed-to-war-log relations and lifecycle/reconciliation combinations. An isolated PowerShell integration test exercises these failure modes through the actual updater entry point with fake configuration and probe wrappers; no API request or run directory is reached. Its Git preflight also fails closed for any local ahead commit; it never pushes a pre-existing commit and reports its hash and paths for manual recovery.

Chronology alone is insufficient for timestamp-free progressive matching. The three-hour collection window and the seven-day bounded game-time window are project heuristics, not Clash of Clans API rules. Progressive evidence is accepted only with an ordered available timeline (`preparation <= start <= end`), exact roster, compatible settings, one candidate and lifecycle timestamps within that bounded window. A reversed, distant or otherwise incompatible timeline creates a separate diagnostic record rather than changing the existing war.

`scripts/update/migrate_war_history_v1_to_v2.py --preview` validates a source and reports hashes without changing it. Execute mode requires the exact source hash, `--confirm-migration` and an explicit backup directory; it writes a timestamped non-overwriting backup, validates it, atomically replaces the source, reads it back and rolls back from the backup if post-write validation fails. Real history remains v1 and this command has not been run against it.

## Recovery

History parsing and validation fail closed. Invalid JSON, unsupported schema and malformed records are never interpreted as empty history.

The Python recovery boundary writes a same-directory temporary file, flushes and calls `fsync`, creates and flushes a backup before replacement, and uses `os.replace`. Backup, write and replace failures identify their stage and path. A validated backup can be restored through a separate atomic replacement. Tests use only temporary directories.

The PowerShell updater continues to build proposed history under a run directory and keeps its existing backup/rollback boundary. Live integration and migration remain disabled until separately approved.

## Offline acceptance

The acceptance fixture contains 18 used attacks out of 30, attack-star sum 43 and official clan score 38. Public current-war output must contain `clan_stars: 38` and `attack_stars_total: 43`, and the site must display 38 in «Звёзды клана».

The recursive public-data scan normalizes camelCase, snake_case, hyphenated and spaced key spelling. It rejects player, attacker and defender tags; clan and opponent identity; raw payload/response; request headers; token and credential fields; Authorization strings; Windows drive and UNC paths; local source paths; and DPAPI metadata or blobs. These internal fields cannot enter any public JSON export.
