# Clan snapshot history v1

## Status

Storage core and normal-updater integration are implemented. Natural scheduled-run validation, public projection, backfill and retention compaction remain pending separate approval.

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

The normal updater records exactly one confirmed roster observation after all probes, builder, public validation and tests have succeeded, and before backup, atomic apply or Git operations. It consumes the existing roster-probe files and makes no additional API request. `PreviewOnly` returns before this stage and neither creates nor opens the snapshot database.

The updater source-run ID is the actual updater run ID. The observation timestamp comes from the confirmed roster probe metadata, then is canonicalized to fixed-width UTC. The local adapter writes the safe run artifact `snapshot-history-result.json`; it excludes roster payloads, member names, game tags and absolute paths. A storage failure is fail-closed and prevents public apply, commit and push. A successful observation remains authoritative if a later public apply, commit or push fails.

The operator-visible `snapshot_history` health object contains only status, result code, logical database path, initialization/insert facts, opaque observation ID and canonical timestamps. Supported normal outcomes are `snapshot_history_success` and `snapshot_history_idempotent`; failures distinguish initialization, validation, unsupported schema, conflict, out-of-order, lock, write, result-write and unexpected errors. `snapshot_history_skipped_preview` is reserved taxonomy; PreviewOnly omits the stage entirely for backward-compatible history.
