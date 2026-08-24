# Clan snapshot history v1 closeout

## Status

completed

## Architecture

- Local SQLite schema version 1 is the internal-only authority.
- Every confirmed successful normal collection records one immutable hourly observation.
- Repeated normalized roster state reuses a deduplicated payload while preserving each observation.
- Exact private player tags are local-only stable identities.
- Membership events are derived deterministically from consecutive observations.
- This stage has no public snapshot projection and performs no backfill.

## Production validation

The production database was validated read-only from its first observation on 2026-07-26 through the latest audited observation on 2026-08-24. The updater-run reliability audit covered the controlled recovery run `20260817-013037-23933231` through `20260824-110003-0bc32c7c`.

- 145 observations map one-to-one to 145 successful normal source runs from the snapshot integration boundary.
- 119 distinct payloads contain 2,679 member-state rows.
- 26 observations reuse an existing payload.
- 54 derived membership events were observed: 6 joined, 18 left, 2 rejoined, 3 role changes and 25 Town Hall changes; no name changes were present.
- The baseline observation creates no joined events.
- All source-run health records and roster timestamps matched; missing and orphan observations were both zero.
- Failed-run observation violations were zero. Both preserved builder failures named in the recovery audit are excluded.
- Database schema metadata, logical validation, `integrity_check` and foreign keys passed.
- The database size at audit time was 774,144 bytes. WAL was empty and SHM existed as a 32,768-byte sidecar.

After the 2026-08-17 recovery, 77 completed successful or no-change normal runs recorded observations. Thirty-eight failed attempts stopped before the snapshot boundary, of which 35 were HTTP 403 and 3 were transport failures. Three interrupted historical attempts remained incomplete at the tests stage and recorded no observation. There were no recurring builder, war-history or snapshot-history failures after the recovery.

Two larger derived-event transitions followed collection gaps of approximately 46 and 354 hours. Their Town Hall changes were strictly increasing; the full production sequence contained 25 increases and zero decreases. This evidence does not indicate an artificial leave/rejoin cycle.

## Failure semantics

- Failed probes, builder, public validation or tests do not record an observation.
- PreviewOnly does not initialize, open or write the snapshot store.
- A successful normal run records an observation even when public files have no change.
- A confirmed observation is not rolled back if a later public apply, commit or push fails.
- The snapshot stage consumes the existing roster probe and makes no additional API request.

## Privacy

- The authoritative database remains under the documented logical `data/clan_snapshot_history` location outside Git root.
- SQLite and its sidecars are absent from Git history, Git index, Pages and Codebase Memory.
- Public JSON contains no snapshot-history identities or internal storage fields.
- No public API or frontend contract changed in this closeout.

## Deferred work

- Git-public backfill;
- public snapshot projection;
- retention or compaction;
- weekly donation analytics.

## Definition of Done

- [x] Schema v1 production database valid.
- [x] Natural production observations accumulated.
- [x] First observation validated as a baseline with no bulk joined events.
- [x] Successful normal runs map one-to-one to observations.
- [x] Failed and PreviewOnly runs produce no observations.
- [x] Production payload deduplication validated.
- [x] Canonical chronology and source-run uniqueness validated.
- [x] Derived membership events are deterministic and sane.
- [x] Privacy boundary validated.
- [x] SQLite excluded from Git, Codebase Memory and public files.
- [x] Hourly updater stable after the ordinary-war history repair.
- [x] Snapshot-history health stage stable.
- [x] Snapshot recording makes no additional API request.
- [x] Public contract unchanged.
- [x] Focused and full offline test suites pass.
- [x] Git and documentation current at closeout.

The next roadmap stage is `donations_weekly_v1`. It is recorded only and was not started by this closeout.
