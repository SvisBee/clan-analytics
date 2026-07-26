"""Local-only authoritative storage for confirmed clan roster observations.

This module has no updater integration and never creates a store until its
explicit initialization API is called.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .api.models import ClanSnapshot

SCHEMA_VERSION = 1
NORMALIZATION_VERSION = "clan_snapshot_v1"


class SnapshotStoreError(RuntimeError):
    """Base error for safe local snapshot-store operations."""


class UnsupportedSchemaVersionError(SnapshotStoreError):
    pass


class SnapshotValidationError(SnapshotStoreError):
    pass


class ObservationConflictError(SnapshotStoreError):
    pass


class OutOfOrderObservationError(SnapshotStoreError):
    pass


class BackupValidationError(SnapshotStoreError):
    pass


@dataclass(frozen=True)
class CanonicalSnapshot:
    payload: dict[str, Any]
    serialized: bytes
    fingerprint: str


@dataclass(frozen=True)
class ObservationResult:
    observation_id: str
    payload_id: str
    inserted_payload: bool
    inserted_observation: bool


@dataclass(frozen=True)
class MembershipEvent:
    event_type: str
    player_tag: str
    previous_observation_id: str
    observation_id: str
    previous_observed_at_utc: str
    observed_at_utc: str
    event_detected_at_utc: str
    exact_event_time_known: bool = False
    before_value: Any | None = None
    after_value: Any | None = None


_SCHEMA = """
CREATE TABLE schema_metadata (
  schema_version INTEGER NOT NULL,
  created_at_utc TEXT NOT NULL,
  storage_kind TEXT NOT NULL,
  migration_state TEXT NOT NULL
);
CREATE TABLE snapshot_payload (
  payload_id TEXT PRIMARY KEY,
  payload_fingerprint TEXT NOT NULL UNIQUE,
  clan_tag TEXT NOT NULL,
  clan_name TEXT NOT NULL,
  clan_level INTEGER,
  member_count INTEGER NOT NULL,
  created_at_utc TEXT NOT NULL,
  normalization_version TEXT NOT NULL
);
CREATE TABLE member_state (
  payload_id TEXT NOT NULL REFERENCES snapshot_payload(payload_id) ON DELETE RESTRICT,
  player_tag TEXT NOT NULL,
  display_name TEXT NOT NULL,
  clan_role TEXT,
  town_hall_level INTEGER,
  exp_level INTEGER,
  trophies INTEGER,
  builder_base_trophies INTEGER,
  donations INTEGER,
  donations_received INTEGER,
  clan_rank INTEGER,
  previous_clan_rank INTEGER,
  PRIMARY KEY (payload_id, player_tag)
);
CREATE TABLE snapshot_observation (
  observation_id TEXT PRIMARY KEY,
  source_run_id TEXT NOT NULL UNIQUE,
  payload_id TEXT NOT NULL REFERENCES snapshot_payload(payload_id) ON DELETE RESTRICT,
  observed_at_utc TEXT NOT NULL,
  recorded_at_utc TEXT NOT NULL,
  validation_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status = 'confirmed'),
  UNIQUE(observed_at_utc)
);
CREATE INDEX snapshot_observation_observed_at ON snapshot_observation(observed_at_utc);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> str:
    if not isinstance(value, str):
        raise SnapshotValidationError("observed_at must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SnapshotValidationError("observed_at is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SnapshotValidationError("observed_at must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_path(path: Path) -> Path:
    resolved = path.resolve()
    parts = {part.casefold() for part in resolved.parts}
    if "site" in parts or resolved.suffix.casefold() not in {".sqlite", ".sqlite3", ".db"}:
        raise SnapshotValidationError("snapshot store path is not permitted")
    return resolved


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    return connection


def _readonly_connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _metadata(connection: sqlite3.Connection) -> sqlite3.Row:
    try:
        rows = connection.execute("SELECT * FROM schema_metadata").fetchall()
    except sqlite3.DatabaseError as error:
        raise SnapshotValidationError("snapshot store metadata is missing") from error
    if len(rows) != 1:
        raise SnapshotValidationError("snapshot store metadata is invalid")
    row = rows[0]
    version = row["schema_version"]
    if version != SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError("snapshot store schema version is unsupported")
    if row["storage_kind"] != "clan_snapshot_history" or row["migration_state"] != "stable":
        raise SnapshotValidationError("snapshot store metadata is invalid")
    return row


def initialize_snapshot_store(path: str | Path) -> None:
    """Create schema v1 only through this explicit call; initialization is idempotent."""
    target = _safe_path(Path(path))
    existed = target.exists()
    if existed:
        validate_snapshot_store(target)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = _connect(target)
    try:
        with connection:
            connection.executescript(_SCHEMA)
            connection.execute(
                "INSERT INTO schema_metadata VALUES (?, ?, ?, ?)",
                (SCHEMA_VERSION, _utc_now(), "clan_snapshot_history", "stable"),
            )
    except Exception:
        connection.close()
        if not existed:
            target.unlink(missing_ok=True)
        raise
    finally:
        if connection:
            connection.close()


def validate_snapshot_store(path: str | Path) -> None:
    target = _safe_path(Path(path))
    if not target.is_file():
        raise SnapshotValidationError("snapshot store does not exist")
    connection = _readonly_connect(target)
    try:
        _metadata(connection)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise SnapshotValidationError("snapshot store integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise SnapshotValidationError("snapshot store foreign keys are invalid")
    finally:
        connection.close()


def build_canonical_snapshot(clan_snapshot: ClanSnapshot) -> CanonicalSnapshot:
    """Return a stable, tag-sorted representation containing confirmed fields only."""
    if not isinstance(clan_snapshot.clan_tag, str) or not clan_snapshot.clan_tag:
        raise SnapshotValidationError("clan identity is invalid")
    members = []
    seen: set[str] = set()
    for member in clan_snapshot.members:
        if not isinstance(member.player_tag, str) or not member.player_tag or member.player_tag in seen:
            raise SnapshotValidationError("member identity is invalid")
        seen.add(member.player_tag)
        members.append({
            "player_tag": member.player_tag, "display_name": member.display_name,
            "clan_role": member.clan_role, "town_hall_level": member.town_hall_level,
            "exp_level": member.exp_level, "trophies": member.trophies,
            "builder_base_trophies": member.builder_base_trophies,
            "donations": member.donations, "donations_received": member.donations_received,
            "clan_rank": member.clan_rank, "previous_clan_rank": member.previous_clan_rank,
        })
    payload = {
        "normalization_version": NORMALIZATION_VERSION,
        "clan": {"clan_tag": clan_snapshot.clan_tag, "clan_name": clan_snapshot.name,
                 "clan_level": clan_snapshot.level, "member_count": len(members)},
        "members": sorted(members, key=lambda item: item["player_tag"]),
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return CanonicalSnapshot(payload, serialized, hashlib.sha256(serialized).hexdigest())


def record_confirmed_observation(path: str | Path, clan_snapshot: ClanSnapshot, observed_at_utc: str, source_run_id: str, validation_version: str) -> ObservationResult:
    """Atomically store one confirmed observation, with source-run idempotency."""
    if not isinstance(source_run_id, str) or not source_run_id or not isinstance(validation_version, str) or not validation_version:
        raise SnapshotValidationError("source run or validation version is invalid")
    observed = _parse_utc(observed_at_utc)
    canonical = build_canonical_snapshot(clan_snapshot)
    target = _safe_path(Path(path))
    if not target.is_file():
        raise SnapshotValidationError("snapshot store does not exist")
    validate_snapshot_store(target)
    connection = _connect(target)
    try:
        _metadata(connection)
        connection.execute("BEGIN IMMEDIATE")
        prior = connection.execute("SELECT observation_id, payload_id, observed_at_utc FROM snapshot_observation WHERE source_run_id = ?", (source_run_id,)).fetchone()
        if prior is not None:
            if prior["payload_id"] == canonical.fingerprint and prior["observed_at_utc"] == observed:
                connection.rollback()
                return ObservationResult(prior["observation_id"], canonical.fingerprint, False, False)
            raise ObservationConflictError("source run conflicts with an existing confirmed observation")
        latest = connection.execute("SELECT observed_at_utc FROM snapshot_observation ORDER BY observed_at_utc DESC LIMIT 1").fetchone()
        if latest is not None and observed <= latest["observed_at_utc"]:
            raise OutOfOrderObservationError("confirmed observation is not chronologically later")
        clan = canonical.payload["clan"]
        inserted_payload = connection.execute("SELECT 1 FROM snapshot_payload WHERE payload_id = ?", (canonical.fingerprint,)).fetchone() is None
        if inserted_payload:
            connection.execute("INSERT INTO snapshot_payload VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (canonical.fingerprint, canonical.fingerprint, clan["clan_tag"], clan["clan_name"], clan["clan_level"], clan["member_count"], _utc_now(), NORMALIZATION_VERSION))
            connection.executemany("INSERT INTO member_state VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [(canonical.fingerprint, member["player_tag"], member["display_name"], member["clan_role"], member["town_hall_level"], member["exp_level"], member["trophies"], member["builder_base_trophies"], member["donations"], member["donations_received"], member["clan_rank"], member["previous_clan_rank"]) for member in canonical.payload["members"]])
        observation_id = hashlib.sha256(f"{source_run_id}|{observed}|{canonical.fingerprint}".encode("utf-8")).hexdigest()
        connection.execute("INSERT INTO snapshot_observation VALUES (?, ?, ?, ?, ?, ?, 'confirmed')", (observation_id, source_run_id, canonical.fingerprint, observed, _utc_now(), validation_version))
        connection.commit()
        return ObservationResult(observation_id, canonical.fingerprint, inserted_payload, True)
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def list_observations(path: str | Path) -> list[dict[str, str]]:
    validate_snapshot_store(path)
    connection = _readonly_connect(_safe_path(Path(path)))
    try:
        return [dict(row) for row in connection.execute("SELECT observation_id, source_run_id, payload_id, observed_at_utc, recorded_at_utc, validation_version, status FROM snapshot_observation ORDER BY observed_at_utc")]
    finally:
        connection.close()


def derive_membership_events(path: str | Path) -> list[MembershipEvent]:
    validate_snapshot_store(path)
    connection = _readonly_connect(_safe_path(Path(path)))
    try:
        observations = connection.execute("SELECT observation_id, payload_id, observed_at_utc FROM snapshot_observation ORDER BY observed_at_utc").fetchall()
        events: list[MembershipEvent] = []; previous: dict[str, sqlite3.Row] = {}; seen: set[str] = set(); prior_observation: sqlite3.Row | None = None
        for observation in observations:
            current = {row["player_tag"]: row for row in connection.execute("SELECT * FROM member_state WHERE payload_id = ? ORDER BY player_tag", (observation["payload_id"],))}
            if prior_observation is not None:
                for tag in sorted(set(previous) - set(current)):
                    events.append(MembershipEvent("left", tag, prior_observation["observation_id"], observation["observation_id"], prior_observation["observed_at_utc"], observation["observed_at_utc"], observation["observed_at_utc"]))
                for tag in sorted(set(current) - set(previous)):
                    event_type = "rejoined" if tag in seen else "joined"
                    events.append(MembershipEvent(event_type, tag, prior_observation["observation_id"], observation["observation_id"], prior_observation["observed_at_utc"], observation["observed_at_utc"], observation["observed_at_utc"]))
                for tag in sorted(set(current) & set(previous)):
                    for field, kind in (("display_name", "name_changed"), ("clan_role", "role_changed"), ("town_hall_level", "town_hall_changed")):
                        if current[tag][field] != previous[tag][field]:
                            events.append(MembershipEvent(kind, tag, prior_observation["observation_id"], observation["observation_id"], prior_observation["observed_at_utc"], observation["observed_at_utc"], observation["observed_at_utc"], False, previous[tag][field], current[tag][field]))
            seen.update(current); previous = current; prior_observation = observation
        return events
    finally:
        connection.close()


def classify_donation_delta(before: int | None, after: int | None) -> str:
    if before is None or after is None:
        return "unavailable"
    if after > before:
        return "increase"
    if after == before:
        return "unchanged"
    return "reset_or_unknown"


def create_validated_backup(path: str | Path, backup_path: str | Path, *, overwrite: bool = False) -> None:
    source = _safe_path(Path(path)); destination = _safe_path(Path(backup_path))
    validate_snapshot_store(source)
    if destination.exists() and not overwrite:
        raise BackupValidationError("backup destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.stem + ".tmp" + destination.suffix)
    temporary.unlink(missing_ok=True)
    try:
        input_db = _connect(source)
        output_db = _connect(temporary)
        try:
            input_db.backup(output_db)
        finally:
            output_db.close()
            input_db.close()
        validate_snapshot_store(temporary)
        os.replace(temporary, destination)
    except Exception as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(error, SnapshotStoreError):
            raise
        raise BackupValidationError("snapshot backup failed validation") from error
