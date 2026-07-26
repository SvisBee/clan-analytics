# Clan snapshot history v1

## Status

Storage core is implemented. Updater integration, public projection, backfill and retention compaction are pending separate approval.

## Authority and boundary

The authoritative store is internal-only SQLite schema version 1 at the logical local path `data/clan_snapshot_history/clan_snapshot_history.v1.sqlite3`. It is created only by an explicit storage API call. It is excluded from Git, public site data and Codebase Memory. No snapshot history is published in this phase.

The exact internal `player_tag` is the sole stable member identity. Display names, rank and map position are not identity keys. No fuzzy matching, manual identity merge or deterministic-hash replacement is used.

## Stored state

Each distinct normalized roster becomes one immutable payload with the confirmed clan fields: tag, name, level and member list. Member state stores only confirmed normalized fields: tag, display name, role, town hall, experience, trophies, builder-base trophies, donations, donations received and ranks. Unknown API fields and raw payloads are not stored.

Each confirmed successful collection run creates one immutable observation reference. Timestamps use fixed-width UTC `YYYY-MM-DDTHH:MM:SS.ffffffZ`, so SQLite lexical ordering equals chronological ordering. Repeated payloads reuse the existing payload but retain the new run observation. A source-run retry is idempotent only when both payload and canonical timestamp match. A conflicting retry, equal timestamp from a different run, naive timestamp or earlier live timestamp fails closed.

The first observation is a baseline and creates no join events. Later events are derived deterministically from consecutive confirmed observations: joined, left, rejoined, name changed, role changed and town-hall changed. Event time is detected at the later observation; the exact change time is unknown.

Donations and donations received are raw counters only. A pure delta helper reports increase, unchanged, reset-or-unknown or unavailable. Weekly analytics is outside v1.

## Safety and recovery

SQLite uses foreign keys, WAL journal mode, FULL synchronous mode and explicit single-writer transactions. Validation fail-closes on unexpected schema objects, incorrect schema shape, metadata, indexes, constraints, foreign keys, canonical timestamps, fingerprints and payload/member consistency. An observation cannot exist without its complete member state. No automatic vacuum, deletion, retention or migration is performed. A validated SQLite backup helper creates a standalone rollback-journal file without dependent WAL/SHM sidecars; it does not run before normal observations and never restores automatically.

The existing updater does not use this store yet. A future integration must record only after its roster probe, normalization and validation boundary succeeds, without another API request or public-contract change.
