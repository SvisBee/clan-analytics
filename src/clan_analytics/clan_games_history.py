"""Local-only SQLite authority for Clan Games player observations.

The store is deliberately separate from the API source and event registry.
Importing this module performs no filesystem or network operation. A database
is created only by an explicit call to :func:`initialize_clan_games_store`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .api.clan_games import (
    GAMES_CHAMPION_NORMALIZATION_VERSION,
    GAMES_CHAMPION_SOURCE_KIND,
    GamesChampionSnapshot,
)
from .clan_games_events import ClanGamesEvent


SCHEMA_VERSION = 1
STORAGE_KIND = "clan_games"
MIGRATION_STATE = "stable"
LOGICAL_DATABASE_PATH = "data/clan_games/clan_games.v1.sqlite3"
CANONICAL_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
BUSY_TIMEOUT_MILLISECONDS = 5_000

SCAN_KINDS = frozenset({"baseline", "periodic", "final"})
SCAN_STATUSES = frozenset({"success", "partial_success", "failed"})
PLAYER_RESULT_STATUSES = frozenset({"success", "failed", "skipped"})
_PLAYER_TAG_PATTERN = re.compile(r"#[A-Z0-9]{3,20}")
_SCAN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_SAFE_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")
_STORAGE_RESULT_CODES = frozenset(
    {
        "success",
        "no_change",
        "store_created",
        "store_not_found",
        "invalid_store",
        "unsupported_schema",
        "invalid_event_definition",
        "invalid_scan",
        "scan_conflict",
        "out_of_order_scan",
        "invalid_player_result",
        "locked",
        "write_failure",
        "backup_failure",
    }
)


class ClanGamesStoreError(RuntimeError):
    """Bounded storage failure whose message never contains player identity."""

    def __init__(self, result_code: str, safe_message: str) -> None:
        if result_code not in _STORAGE_RESULT_CODES:
            raise ValueError("invalid Clan Games storage result code")
        self.result_code = result_code
        self.safe_message = safe_message
        super().__init__(safe_message)


class UnsupportedClanGamesSchemaError(ClanGamesStoreError):
    def __init__(self) -> None:
        super().__init__("unsupported_schema", "Clan Games store schema is unsupported")


@dataclass(frozen=True)
class PlayerScanResult:
    """One requested private identity result in a future bounded scan."""

    player_tag: str = field(repr=False)
    result_status: str
    result_code: str
    attempted_at_utc: str | datetime | None
    observed_at_utc: str | datetime | None = None
    cumulative_value: int | None = None
    achievement_target: int | None = None
    source_kind: str | None = None
    normalization_version: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.player_tag, str) or not _PLAYER_TAG_PATTERN.fullmatch(
            self.player_tag
        ):
            raise ClanGamesStoreError(
                "invalid_player_result", "player result identity is invalid"
            )
        if self.result_status not in PLAYER_RESULT_STATUSES:
            raise ClanGamesStoreError(
                "invalid_player_result", "player result status is invalid"
            )
        _validate_safe_code(
            self.result_code, "invalid_player_result", "player result code is invalid"
        )
        attempted = _optional_timestamp(
            self.attempted_at_utc, "attempted_at", "invalid_player_result"
        )
        observed = _optional_timestamp(
            self.observed_at_utc, "observed_at", "invalid_player_result"
        )
        object.__setattr__(self, "attempted_at_utc", attempted)
        object.__setattr__(self, "observed_at_utc", observed)
        if self.result_status == "success":
            if attempted is None or observed is None:
                raise ClanGamesStoreError(
                    "invalid_player_result",
                    "successful player result requires attempt and observation timestamps",
                )
            if self.result_code != "success":
                raise ClanGamesStoreError(
                    "invalid_player_result",
                    "successful player result requires success result code",
                )
            _validate_nonnegative_integer(self.cumulative_value)
            _validate_nonnegative_integer(self.achievement_target)
            if (
                self.source_kind != GAMES_CHAMPION_SOURCE_KIND
                or self.normalization_version
                != GAMES_CHAMPION_NORMALIZATION_VERSION
            ):
                raise ClanGamesStoreError(
                    "invalid_player_result",
                    "successful player result requires normalized source metadata",
                )
            if attempted > observed:
                raise ClanGamesStoreError(
                    "invalid_player_result",
                    "player result timestamps are out of order",
                )
        else:
            if self.result_status == "failed" and attempted is None:
                raise ClanGamesStoreError(
                    "invalid_player_result",
                    "failed player result requires an attempt timestamp",
                )
            if observed is not None:
                raise ClanGamesStoreError(
                    "invalid_player_result",
                    "unsuccessful player result cannot contain an observation timestamp",
                )
            if any(
                value is not None
                for value in (
                    self.cumulative_value,
                    self.achievement_target,
                    self.source_kind,
                    self.normalization_version,
                )
            ):
                raise ClanGamesStoreError(
                    "invalid_player_result",
                    "unsuccessful player result cannot contain observation values",
                )
            if self.result_code == "success":
                raise ClanGamesStoreError(
                    "invalid_player_result",
                    "unsuccessful player result requires a failure or skip code",
                )

    @classmethod
    def success(
        cls, snapshot: GamesChampionSnapshot, *, attempted_at: str | datetime
    ) -> "PlayerScanResult":
        if not isinstance(snapshot, GamesChampionSnapshot):
            raise ClanGamesStoreError(
                "invalid_player_result", "normalized player snapshot is invalid"
            )
        return cls(
            player_tag=snapshot.player_tag_internal,
            result_status="success",
            result_code="success",
            attempted_at_utc=attempted_at,
            observed_at_utc=snapshot.observed_at_utc,
            cumulative_value=snapshot.value,
            achievement_target=snapshot.target,
            source_kind=snapshot.source_kind,
            normalization_version=snapshot.normalization_version,
        )

    @classmethod
    def failed(
        cls,
        player_tag: str,
        *,
        result_code: str,
        attempted_at: str | datetime,
    ) -> "PlayerScanResult":
        return cls(player_tag, "failed", result_code, attempted_at)

    @classmethod
    def skipped(
        cls,
        player_tag: str,
        *,
        result_code: str,
        attempted_at: str | datetime | None = None,
    ) -> "PlayerScanResult":
        return cls(player_tag, "skipped", result_code, attempted_at)


@dataclass(frozen=True)
class ClanGamesScan:
    """Fully formed immutable scan candidate written in one transaction."""

    scan_id: str
    event_id: str
    scan_kind: str
    started_at_utc: str
    finished_at_utc: str
    player_results: tuple[PlayerScanResult, ...]
    status: str
    result_code: str
    requested_count: int
    successful_count: int
    failed_count: int
    skipped_count: int

    @classmethod
    def create(
        cls,
        *,
        scan_id: str,
        event_id: str,
        scan_kind: str,
        started_at: str | datetime,
        finished_at: str | datetime,
        player_results: Iterable[PlayerScanResult],
        result_code: str | None = None,
    ) -> "ClanGamesScan":
        started = _canonical_timestamp(started_at, "started_at", "invalid_scan")
        finished = _canonical_timestamp(finished_at, "finished_at", "invalid_scan")
        results = tuple(player_results)
        success_count = sum(item.result_status == "success" for item in results)
        failed_count = sum(item.result_status == "failed" for item in results)
        skipped_count = sum(item.result_status == "skipped" for item in results)
        requested_count = len(results)
        if success_count == requested_count and requested_count > 0:
            status = "success"
        elif 0 < success_count < requested_count:
            status = "partial_success"
        else:
            status = "failed"
        return cls(
            scan_id=scan_id,
            event_id=event_id,
            scan_kind=scan_kind,
            started_at_utc=started,
            finished_at_utc=finished,
            player_results=results,
            status=status,
            result_code=result_code or status,
            requested_count=requested_count,
            successful_count=success_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
        )

    def __post_init__(self) -> None:
        if not isinstance(self.scan_id, str) or not _SCAN_ID_PATTERN.fullmatch(
            self.scan_id
        ):
            raise ClanGamesStoreError("invalid_scan", "scan identity is invalid")
        if not _nonempty_text(self.event_id) or len(self.event_id) > 64:
            raise ClanGamesStoreError("invalid_scan", "scan event identity is invalid")
        if self.scan_kind not in SCAN_KINDS:
            raise ClanGamesStoreError("invalid_scan", "scan kind is invalid")
        started = _canonical_timestamp(
            self.started_at_utc, "started_at", "invalid_scan"
        )
        finished = _canonical_timestamp(
            self.finished_at_utc, "finished_at", "invalid_scan"
        )
        object.__setattr__(self, "started_at_utc", started)
        object.__setattr__(self, "finished_at_utc", finished)
        if started > finished:
            raise ClanGamesStoreError("invalid_scan", "scan timestamps are out of order")
        if not isinstance(self.player_results, tuple) or not all(
            isinstance(item, PlayerScanResult) for item in self.player_results
        ):
            raise ClanGamesStoreError("invalid_scan", "scan player results are invalid")
        if not self.player_results:
            raise ClanGamesStoreError("invalid_scan", "scan requires requested players")
        tags = [item.player_tag for item in self.player_results]
        if len(tags) != len(set(tags)):
            raise ClanGamesStoreError(
                "invalid_player_result", "scan contains duplicate player identities"
            )
        counts = _counts(self.player_results)
        supplied = (
            self.requested_count,
            self.successful_count,
            self.failed_count,
            self.skipped_count,
        )
        if supplied != counts:
            raise ClanGamesStoreError("invalid_scan", "scan counts are inconsistent")
        _validate_status_counts(self.status, *counts)
        _validate_safe_code(self.result_code, "invalid_scan", "scan result code is invalid")
        if (self.status == "success") != (self.result_code == "success"):
            raise ClanGamesStoreError(
                "invalid_scan", "scan status and result code are inconsistent"
            )
        for item in self.player_results:
            attempted = item.attempted_at_utc
            observed = item.observed_at_utc
            if attempted is not None and not (started <= attempted <= finished):
                raise ClanGamesStoreError(
                    "invalid_player_result", "player attempt is outside the scan interval"
                )
            if observed is not None and not (started <= observed <= finished):
                raise ClanGamesStoreError(
                    "invalid_player_result",
                    "player observation is outside the scan interval",
                )


@dataclass(frozen=True)
class StoreOperationResult:
    status: str
    result_code: str
    scan_id: str | None = None
    definition_fingerprint: str | None = None
    scan_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if self.result_code not in _STORAGE_RESULT_CODES:
            raise ValueError("invalid Clan Games storage result code")


_SCHEMA = """
CREATE TABLE schema_metadata (
  schema_version INTEGER NOT NULL,
  storage_kind TEXT NOT NULL,
  created_at_utc TEXT NOT NULL,
  migration_state TEXT NOT NULL
);
CREATE TABLE event_definition_snapshot (
  definition_id TEXT NOT NULL PRIMARY KEY,
  event_id TEXT NOT NULL,
  start_at_utc TEXT NOT NULL,
  end_at_utc TEXT NOT NULL,
  official_source_url TEXT NOT NULL,
  confirmed_at_utc TEXT NOT NULL,
  definition_fingerprint TEXT NOT NULL UNIQUE,
  recorded_at_utc TEXT NOT NULL,
  CHECK(definition_id = definition_fingerprint),
  CHECK(length(event_id) > 0),
  CHECK(start_at_utc < end_at_utc)
);
CREATE TABLE collection_scan (
  scan_id TEXT NOT NULL PRIMARY KEY,
  event_id TEXT NOT NULL,
  definition_id TEXT NOT NULL REFERENCES event_definition_snapshot(definition_id) ON DELETE RESTRICT,
  scan_kind TEXT NOT NULL CHECK(scan_kind IN ('baseline', 'periodic', 'final')),
  started_at_utc TEXT NOT NULL,
  finished_at_utc TEXT NOT NULL,
  requested_count INTEGER NOT NULL CHECK(requested_count > 0),
  successful_count INTEGER NOT NULL CHECK(successful_count >= 0),
  failed_count INTEGER NOT NULL CHECK(failed_count >= 0),
  skipped_count INTEGER NOT NULL CHECK(skipped_count >= 0),
  status TEXT NOT NULL CHECK(status IN ('success', 'partial_success', 'failed')),
  result_code TEXT NOT NULL,
  scan_fingerprint TEXT NOT NULL UNIQUE,
  recorded_at_utc TEXT NOT NULL,
  CHECK(started_at_utc <= finished_at_utc),
  CHECK(requested_count = successful_count + failed_count + skipped_count),
  CHECK(
    (status = 'success' AND successful_count = requested_count AND failed_count = 0 AND skipped_count = 0) OR
    (status = 'partial_success' AND successful_count > 0 AND successful_count < requested_count) OR
    (status = 'failed' AND successful_count = 0)
  ),
  UNIQUE(event_id, started_at_utc)
);
CREATE TABLE player_scan_result (
  scan_id TEXT NOT NULL REFERENCES collection_scan(scan_id) ON DELETE RESTRICT,
  player_tag TEXT NOT NULL,
  result_status TEXT NOT NULL CHECK(result_status IN ('success', 'failed', 'skipped')),
  result_code TEXT NOT NULL,
  attempted_at_utc TEXT,
  observed_at_utc TEXT,
  cumulative_value INTEGER,
  achievement_target INTEGER,
  source_kind TEXT,
  normalization_version TEXT,
  PRIMARY KEY (scan_id, player_tag),
  CHECK(length(player_tag) > 0),
  CHECK(
    (result_status = 'success' AND result_code = 'success' AND attempted_at_utc IS NOT NULL AND observed_at_utc IS NOT NULL AND cumulative_value >= 0 AND achievement_target >= 0 AND source_kind IS NOT NULL AND normalization_version IS NOT NULL) OR
    (result_status = 'failed' AND result_code != 'success' AND attempted_at_utc IS NOT NULL AND observed_at_utc IS NULL AND cumulative_value IS NULL AND achievement_target IS NULL AND source_kind IS NULL AND normalization_version IS NULL) OR
    (result_status = 'skipped' AND result_code != 'success' AND observed_at_utc IS NULL AND cumulative_value IS NULL AND achievement_target IS NULL AND source_kind IS NULL AND normalization_version IS NULL)
  )
);
CREATE INDEX event_definition_snapshot_event_id ON event_definition_snapshot(event_id);
CREATE INDEX collection_scan_definition_id ON collection_scan(definition_id);
CREATE INDEX collection_scan_event_started ON collection_scan(event_id, started_at_utc);
CREATE INDEX player_scan_result_status ON player_scan_result(scan_id, result_status);
"""

_EXPECTED_COLUMNS = {
    "schema_metadata": (
        ("schema_version", "INTEGER", 1, 0),
        ("storage_kind", "TEXT", 1, 0),
        ("created_at_utc", "TEXT", 1, 0),
        ("migration_state", "TEXT", 1, 0),
    ),
    "event_definition_snapshot": (
        ("definition_id", "TEXT", 1, 1),
        ("event_id", "TEXT", 1, 0),
        ("start_at_utc", "TEXT", 1, 0),
        ("end_at_utc", "TEXT", 1, 0),
        ("official_source_url", "TEXT", 1, 0),
        ("confirmed_at_utc", "TEXT", 1, 0),
        ("definition_fingerprint", "TEXT", 1, 0),
        ("recorded_at_utc", "TEXT", 1, 0),
    ),
    "collection_scan": (
        ("scan_id", "TEXT", 1, 1),
        ("event_id", "TEXT", 1, 0),
        ("definition_id", "TEXT", 1, 0),
        ("scan_kind", "TEXT", 1, 0),
        ("started_at_utc", "TEXT", 1, 0),
        ("finished_at_utc", "TEXT", 1, 0),
        ("requested_count", "INTEGER", 1, 0),
        ("successful_count", "INTEGER", 1, 0),
        ("failed_count", "INTEGER", 1, 0),
        ("skipped_count", "INTEGER", 1, 0),
        ("status", "TEXT", 1, 0),
        ("result_code", "TEXT", 1, 0),
        ("scan_fingerprint", "TEXT", 1, 0),
        ("recorded_at_utc", "TEXT", 1, 0),
    ),
    "player_scan_result": (
        ("scan_id", "TEXT", 1, 1),
        ("player_tag", "TEXT", 1, 2),
        ("result_status", "TEXT", 1, 0),
        ("result_code", "TEXT", 1, 0),
        ("attempted_at_utc", "TEXT", 0, 0),
        ("observed_at_utc", "TEXT", 0, 0),
        ("cumulative_value", "INTEGER", 0, 0),
        ("achievement_target", "INTEGER", 0, 0),
        ("source_kind", "TEXT", 0, 0),
        ("normalization_version", "TEXT", 0, 0),
    ),
}

_REQUIRED_INDEXES = {
    "event_definition_snapshot_event_id",
    "collection_scan_definition_id",
    "collection_scan_event_started",
    "player_scan_result_status",
}


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value) and not value.isspace()


def _validate_safe_code(value: object, code: str, message: str) -> str:
    if not isinstance(value, str) or not _SAFE_CODE_PATTERN.fullmatch(value):
        raise ClanGamesStoreError(code, message)
    return value


def _validate_nonnegative_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ClanGamesStoreError(
            "invalid_player_result", "player observation value is invalid"
        )
    return value


def _canonical_timestamp(
    value: str | datetime, field_name: str, result_code: str
) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and "T" in value and (
        value.endswith("Z") or re.search(r"[+-]\d{2}:\d{2}$", value)
    ):
        try:
            parsed = datetime.fromisoformat(
                value[:-1] + "+00:00" if value.endswith("Z") else value
            )
        except ValueError:
            raise ClanGamesStoreError(result_code, f"{field_name} is invalid") from None
    else:
        raise ClanGamesStoreError(
            result_code, f"{field_name} must be timezone-aware"
        )
    try:
        offset = parsed.utcoffset() if parsed.tzinfo is not None else None
    except (OverflowError, ValueError):
        offset = None
    if offset is None:
        raise ClanGamesStoreError(
            result_code, f"{field_name} must be timezone-aware"
        )
    try:
        return parsed.astimezone(timezone.utc).strftime(CANONICAL_TIMESTAMP_FORMAT)
    except (OverflowError, OSError, ValueError):
        raise ClanGamesStoreError(result_code, f"{field_name} is invalid") from None


def _optional_timestamp(
    value: str | datetime | None, field_name: str, result_code: str
) -> str | None:
    if value is None:
        return None
    return _canonical_timestamp(value, field_name, result_code)


def _is_canonical_timestamp(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 27:
        return False
    try:
        return datetime.strptime(value, CANONICAL_TIMESTAMP_FORMAT).strftime(
            CANONICAL_TIMESTAMP_FORMAT
        ) == value
    except ValueError:
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime(CANONICAL_TIMESTAMP_FORMAT)


def _counts(results: Iterable[PlayerScanResult]) -> tuple[int, int, int, int]:
    values = tuple(results)
    return (
        len(values),
        sum(item.result_status == "success" for item in values),
        sum(item.result_status == "failed" for item in values),
        sum(item.result_status == "skipped" for item in values),
    )


def _validate_status_counts(
    status: str, requested: int, successful: int, failed: int, skipped: int
) -> None:
    if status not in SCAN_STATUSES or requested <= 0:
        raise ClanGamesStoreError("invalid_scan", "scan status or coverage is invalid")
    if requested != successful + failed + skipped:
        raise ClanGamesStoreError("invalid_scan", "scan counts are inconsistent")
    if status == "success" and not (
        successful == requested and failed == 0 and skipped == 0
    ):
        raise ClanGamesStoreError("invalid_scan", "successful scan counts are invalid")
    if status == "partial_success" and not (0 < successful < requested):
        raise ClanGamesStoreError("invalid_scan", "partial scan counts are invalid")
    if status == "failed" and successful != 0:
        raise ClanGamesStoreError("invalid_scan", "failed scan counts are invalid")


def _safe_path(path: Path) -> Path:
    resolved = Path(path).resolve()
    parts = {part.casefold() for part in resolved.parts}
    if "site" in parts or resolved.suffix.casefold() not in {
        ".sqlite",
        ".sqlite3",
        ".db",
    }:
        raise ClanGamesStoreError("invalid_store", "Clan Games store path is invalid")
    return resolved


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MILLISECONDS / 1000)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MILLISECONDS}")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    return connection


def _readonly_connect(path: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro",
            uri=True,
            timeout=BUSY_TIMEOUT_MILLISECONDS / 1000,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MILLISECONDS}")
        connection.execute("PRAGMA query_only = ON")
        return connection
    except sqlite3.Error:
        raise ClanGamesStoreError("invalid_store", "Clan Games store could not be opened") from None


def _standalone_destination(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MILLISECONDS / 1000)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.execute("PRAGMA synchronous = FULL")
    return connection


def _sidecars(path: Path) -> tuple[Path, Path]:
    return Path(str(path) + "-wal"), Path(str(path) + "-shm")


def _remove_database_files(path: Path) -> None:
    for candidate in (path, *_sidecars(path)):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


def _make_standalone(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
    finally:
        connection.close()
    for candidate in _sidecars(path):
        candidate.unlink(missing_ok=True)


def _canonical_event_payload(event: ClanGamesEvent) -> dict[str, str]:
    if not isinstance(event, ClanGamesEvent):
        raise ClanGamesStoreError(
            "invalid_event_definition", "event definition is invalid"
        )
    return {
        "event_id": event.event_id,
        "start_at_utc": event.start_at_utc,
        "end_at_utc": event.end_at_utc,
        "official_source_url": event.official_source_url,
        "confirmed_at_utc": event.confirmed_at_utc,
    }


def event_definition_fingerprint(event: ClanGamesEvent) -> str:
    serialized = json.dumps(
        _canonical_event_payload(event),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _player_payload(result: PlayerScanResult) -> dict[str, Any]:
    return {
        "player_tag": result.player_tag,
        "result_status": result.result_status,
        "result_code": result.result_code,
        "attempted_at_utc": result.attempted_at_utc,
        "observed_at_utc": result.observed_at_utc,
        "cumulative_value": result.cumulative_value,
        "achievement_target": result.achievement_target,
        "source_kind": result.source_kind,
        "normalization_version": result.normalization_version,
    }


def scan_content_fingerprint(event: ClanGamesEvent, scan: ClanGamesScan) -> str:
    definition = event_definition_fingerprint(event)
    if scan.event_id != event.event_id:
        raise ClanGamesStoreError(
            "invalid_event_definition", "scan and event definition do not match"
        )
    payload = {
        "definition_fingerprint": definition,
        "scan_id": scan.scan_id,
        "event_id": scan.event_id,
        "scan_kind": scan.scan_kind,
        "started_at_utc": scan.started_at_utc,
        "finished_at_utc": scan.finished_at_utc,
        "status": scan.status,
        "result_code": scan.result_code,
        "requested_count": scan.requested_count,
        "successful_count": scan.successful_count,
        "failed_count": scan.failed_count,
        "skipped_count": scan.skipped_count,
        "player_results": [
            _player_payload(item)
            for item in sorted(scan.player_results, key=lambda item: item.player_tag)
        ],
    }
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _unique_columns(connection: sqlite3.Connection, table: str) -> set[tuple[str, ...]]:
    values: set[tuple[str, ...]] = set()
    for index in connection.execute(f"PRAGMA index_list({table})"):
        if index[2]:
            values.add(
                tuple(
                    row[2]
                    for row in connection.execute(f"PRAGMA index_info({index[1]})")
                )
            )
    return values


def _metadata(connection: sqlite3.Connection) -> sqlite3.Row:
    try:
        rows = connection.execute("SELECT * FROM schema_metadata").fetchall()
    except sqlite3.DatabaseError:
        raise ClanGamesStoreError("invalid_store", "Clan Games metadata is missing") from None
    if len(rows) != 1:
        raise ClanGamesStoreError("invalid_store", "Clan Games metadata is invalid")
    row = rows[0]
    try:
        schema_version = row["schema_version"]
        storage_kind = row["storage_kind"]
        migration_state = row["migration_state"]
        created_at_utc = row["created_at_utc"]
    except (IndexError, KeyError):
        raise ClanGamesStoreError("invalid_store", "Clan Games metadata is invalid") from None
    if schema_version != SCHEMA_VERSION:
        raise UnsupportedClanGamesSchemaError()
    if storage_kind != STORAGE_KIND or migration_state != MIGRATION_STATE:
        raise ClanGamesStoreError("invalid_store", "Clan Games metadata is invalid")
    if not _is_canonical_timestamp(created_at_utc):
        raise ClanGamesStoreError("invalid_store", "Clan Games metadata is invalid")
    return row


def _validate_schema_shape(connection: sqlite3.Connection) -> None:
    objects = connection.execute(
        "SELECT name, type, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
    ).fetchall()
    if any(row["type"] not in {"table", "index"} for row in objects):
        raise ClanGamesStoreError(
            "invalid_store", "Clan Games schema objects are invalid"
        )
    tables = {row["name"] for row in objects if row["type"] == "table"}
    if tables != set(_EXPECTED_COLUMNS):
        raise ClanGamesStoreError("invalid_store", "Clan Games tables are invalid")
    for table, expected in _EXPECTED_COLUMNS.items():
        actual = tuple(
            (row["name"], row["type"].upper(), row["notnull"], row["pk"])
            for row in connection.execute(f"PRAGMA table_info({table})")
        )
        if actual != expected:
            raise ClanGamesStoreError("invalid_store", "Clan Games columns are invalid")
    indexes = {row["name"] for row in objects if row["type"] == "index"}
    if indexes != _REQUIRED_INDEXES:
        raise ClanGamesStoreError("invalid_store", "Clan Games indexes are invalid")
    if ("definition_fingerprint",) not in _unique_columns(
        connection, "event_definition_snapshot"
    ):
        raise ClanGamesStoreError("invalid_store", "Clan Games unique indexes are invalid")
    scan_unique = _unique_columns(connection, "collection_scan")
    if ("scan_fingerprint",) not in scan_unique or (
        "event_id",
        "started_at_utc",
    ) not in scan_unique:
        raise ClanGamesStoreError("invalid_store", "Clan Games unique indexes are invalid")
    expected_foreign = {
        ("definition_id", "event_definition_snapshot", "definition_id")
    }
    actual_foreign = {
        (row["from"], row["table"], row["to"])
        for row in connection.execute("PRAGMA foreign_key_list(collection_scan)")
    }
    player_foreign = {
        (row["from"], row["table"], row["to"])
        for row in connection.execute("PRAGMA foreign_key_list(player_scan_result)")
    }
    if actual_foreign != expected_foreign or player_foreign != {
        ("scan_id", "collection_scan", "scan_id")
    }:
        raise ClanGamesStoreError("invalid_store", "Clan Games foreign keys are invalid")
    sql = " ".join((row["sql"] or "") for row in objects if row["type"] == "table")
    compact = re.sub(r"\s+", " ", sql)
    for required in (
        "CHECK(definition_id = definition_fingerprint)",
        "CHECK(scan_kind IN ('baseline', 'periodic', 'final'))",
        "CHECK(status IN ('success', 'partial_success', 'failed'))",
        "CHECK(result_status IN ('success', 'failed', 'skipped'))",
        "CHECK(requested_count = successful_count + failed_count + skipped_count)",
    ):
        if required not in compact:
            raise ClanGamesStoreError("invalid_store", "Clan Games constraints are invalid")


def _event_from_row(row: sqlite3.Row) -> ClanGamesEvent:
    try:
        return ClanGamesEvent(
            event_id=row["event_id"],
            start_at_utc=row["start_at_utc"],
            end_at_utc=row["end_at_utc"],
            official_source_url=row["official_source_url"],
            confirmed_at_utc=row["confirmed_at_utc"],
        )
    except Exception:
        raise ClanGamesStoreError(
            "invalid_store", "Clan Games event provenance is invalid"
        ) from None


def _result_from_row(row: sqlite3.Row) -> PlayerScanResult:
    try:
        return PlayerScanResult(
            player_tag=row["player_tag"],
            result_status=row["result_status"],
            result_code=row["result_code"],
            attempted_at_utc=row["attempted_at_utc"],
            observed_at_utc=row["observed_at_utc"],
            cumulative_value=row["cumulative_value"],
            achievement_target=row["achievement_target"],
            source_kind=row["source_kind"],
            normalization_version=row["normalization_version"],
        )
    except ClanGamesStoreError:
        raise ClanGamesStoreError(
            "invalid_store", "Clan Games player result is invalid"
        ) from None


def _scan_from_rows(
    row: sqlite3.Row, player_rows: Iterable[sqlite3.Row]
) -> ClanGamesScan:
    try:
        return ClanGamesScan(
            scan_id=row["scan_id"],
            event_id=row["event_id"],
            scan_kind=row["scan_kind"],
            started_at_utc=row["started_at_utc"],
            finished_at_utc=row["finished_at_utc"],
            player_results=tuple(_result_from_row(item) for item in player_rows),
            status=row["status"],
            result_code=row["result_code"],
            requested_count=row["requested_count"],
            successful_count=row["successful_count"],
            failed_count=row["failed_count"],
            skipped_count=row["skipped_count"],
        )
    except ClanGamesStoreError:
        raise ClanGamesStoreError("invalid_store", "Clan Games scan is invalid") from None


def _validate_logical_content(connection: sqlite3.Connection) -> None:
    definitions = {
        row["definition_id"]: row
        for row in connection.execute(
            "SELECT * FROM event_definition_snapshot ORDER BY definition_id"
        )
    }
    referenced = {
        row[0] for row in connection.execute("SELECT DISTINCT definition_id FROM collection_scan")
    }
    if set(definitions) != referenced:
        raise ClanGamesStoreError(
            "invalid_store", "Clan Games event provenance contains unused definitions"
        )
    events: dict[str, ClanGamesEvent] = {}
    for definition_id, row in definitions.items():
        if not _is_canonical_timestamp(row["recorded_at_utc"]):
            raise ClanGamesStoreError(
                "invalid_store", "Clan Games event provenance is invalid"
            )
        event = _event_from_row(row)
        fingerprint = event_definition_fingerprint(event)
        if definition_id != fingerprint or row["definition_fingerprint"] != fingerprint:
            raise ClanGamesStoreError(
                "invalid_store", "Clan Games event fingerprint is invalid"
            )
        events[definition_id] = event
    previous_by_event: dict[str, str] = {}
    scans = connection.execute(
        "SELECT * FROM collection_scan ORDER BY event_id, started_at_utc, scan_id"
    ).fetchall()
    for row in scans:
        if not _is_canonical_timestamp(row["recorded_at_utc"]):
            raise ClanGamesStoreError("invalid_store", "Clan Games scan is invalid")
        event = events.get(row["definition_id"])
        if event is None or event.event_id != row["event_id"]:
            raise ClanGamesStoreError(
                "invalid_store", "Clan Games scan provenance is invalid"
            )
        previous = previous_by_event.get(row["event_id"])
        if previous is not None and row["started_at_utc"] <= previous:
            raise ClanGamesStoreError(
                "invalid_store", "Clan Games scan chronology is invalid"
            )
        previous_by_event[row["event_id"]] = row["started_at_utc"]
        players = connection.execute(
            "SELECT * FROM player_scan_result WHERE scan_id = ? ORDER BY player_tag",
            (row["scan_id"],),
        ).fetchall()
        scan = _scan_from_rows(row, players)
        if scan_content_fingerprint(event, scan) != row["scan_fingerprint"]:
            raise ClanGamesStoreError(
                "invalid_store", "Clan Games scan fingerprint is invalid"
            )


def initialize_clan_games_store(path: str | Path) -> StoreOperationResult:
    """Atomically and explicitly initialize an empty schema-v1 store."""

    target = _safe_path(Path(path))
    if target.exists():
        validate_clan_games_store(target)
        return StoreOperationResult("no_change", "no_change")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.init{target.suffix}")
    _remove_database_files(temporary)
    connection: sqlite3.Connection | None = None
    published = False
    try:
        connection = _connect(temporary)
        with connection:
            connection.executescript(_SCHEMA)
            connection.execute(
                "INSERT INTO schema_metadata VALUES (?, ?, ?, ?)",
                (SCHEMA_VERSION, STORAGE_KIND, _utc_now(), MIGRATION_STATE),
            )
        connection.close()
        connection = None
        _make_standalone(temporary)
        validate_clan_games_store(temporary)
        try:
            os.link(temporary, target)
            published = True
        except FileExistsError:
            validate_clan_games_store(target)
            return StoreOperationResult("no_change", "no_change")
        except OSError:
            raise ClanGamesStoreError(
                "write_failure", "atomic Clan Games store publication failed"
            ) from None
        validate_clan_games_store(target)
        return StoreOperationResult("success", "store_created")
    except ClanGamesStoreError:
        _remove_database_files(temporary)
        if published:
            _remove_database_files(target)
        raise
    except (OSError, sqlite3.Error):
        _remove_database_files(temporary)
        if published:
            _remove_database_files(target)
        raise ClanGamesStoreError(
            "write_failure", "Clan Games store initialization failed"
        ) from None
    finally:
        if connection is not None:
            connection.close()
        _remove_database_files(temporary)


def validate_clan_games_store(path: str | Path) -> None:
    """Fail-closed validation of schema shape, metadata and all stored content."""

    target = _safe_path(Path(path))
    if not target.is_file():
        raise ClanGamesStoreError("store_not_found", "Clan Games store does not exist")
    connection = _readonly_connect(target)
    try:
        _metadata(connection)
        _validate_schema_shape(connection)
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ClanGamesStoreError(
                "invalid_store", "Clan Games store integrity check failed"
            )
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ClanGamesStoreError(
                "invalid_store", "Clan Games store foreign keys are invalid"
            )
        _validate_logical_content(connection)
    except UnsupportedClanGamesSchemaError:
        raise
    except ClanGamesStoreError:
        raise
    except sqlite3.DatabaseError:
        raise ClanGamesStoreError("invalid_store", "Clan Games store is invalid") from None
    finally:
        connection.close()


def _insert_definition(
    connection: sqlite3.Connection, event: ClanGamesEvent, fingerprint: str
) -> None:
    existing = connection.execute(
        "SELECT * FROM event_definition_snapshot WHERE definition_id = ?",
        (fingerprint,),
    ).fetchone()
    if existing is not None:
        if event_definition_fingerprint(_event_from_row(existing)) != fingerprint:
            raise ClanGamesStoreError(
                "invalid_event_definition", "event definition conflicts with provenance"
            )
        return
    payload = _canonical_event_payload(event)
    connection.execute(
        "INSERT INTO event_definition_snapshot VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            fingerprint,
            payload["event_id"],
            payload["start_at_utc"],
            payload["end_at_utc"],
            payload["official_source_url"],
            payload["confirmed_at_utc"],
            fingerprint,
            _utc_now(),
        ),
    )


def record_clan_games_scan(
    path: str | Path, event: ClanGamesEvent, scan: ClanGamesScan
) -> StoreOperationResult:
    """Record one complete scan atomically or reject the entire candidate."""

    if not isinstance(scan, ClanGamesScan):
        raise ClanGamesStoreError("invalid_scan", "scan model is invalid")
    definition_fingerprint = event_definition_fingerprint(event)
    scan_fingerprint = scan_content_fingerprint(event, scan)
    target = _safe_path(Path(path))
    validate_clan_games_store(target)
    try:
        connection = _connect(target)
    except sqlite3.OperationalError as error:
        code = "locked" if "locked" in str(error).casefold() else "write_failure"
        raise ClanGamesStoreError(code, "Clan Games scan transaction failed") from None
    except sqlite3.Error:
        raise ClanGamesStoreError(
            "write_failure", "Clan Games scan transaction failed"
        ) from None
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT definition_id, scan_fingerprint FROM collection_scan WHERE scan_id = ?",
            (scan.scan_id,),
        ).fetchone()
        if existing is not None:
            if (
                existing["definition_id"] == definition_fingerprint
                and existing["scan_fingerprint"] == scan_fingerprint
            ):
                connection.rollback()
                return StoreOperationResult(
                    "no_change",
                    "no_change",
                    scan.scan_id,
                    definition_fingerprint,
                    scan_fingerprint,
                )
            raise ClanGamesStoreError(
                "scan_conflict", "scan identity conflicts with stored content"
            )
        latest = connection.execute(
            "SELECT started_at_utc FROM collection_scan WHERE event_id = ? "
            "ORDER BY started_at_utc DESC LIMIT 1",
            (scan.event_id,),
        ).fetchone()
        if latest is not None and scan.started_at_utc <= latest["started_at_utc"]:
            raise ClanGamesStoreError(
                "out_of_order_scan", "scan is not chronologically later for the event"
            )
        _insert_definition(connection, event, definition_fingerprint)
        connection.execute(
            "INSERT INTO collection_scan VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scan.scan_id,
                scan.event_id,
                definition_fingerprint,
                scan.scan_kind,
                scan.started_at_utc,
                scan.finished_at_utc,
                scan.requested_count,
                scan.successful_count,
                scan.failed_count,
                scan.skipped_count,
                scan.status,
                scan.result_code,
                scan_fingerprint,
                _utc_now(),
            ),
        )
        connection.executemany(
            "INSERT INTO player_scan_result VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    scan.scan_id,
                    item.player_tag,
                    item.result_status,
                    item.result_code,
                    item.attempted_at_utc,
                    item.observed_at_utc,
                    item.cumulative_value,
                    item.achievement_target,
                    item.source_kind,
                    item.normalization_version,
                )
                for item in sorted(scan.player_results, key=lambda item: item.player_tag)
            ],
        )
        row = connection.execute(
            "SELECT requested_count, successful_count, failed_count, skipped_count "
            "FROM collection_scan WHERE scan_id = ?",
            (scan.scan_id,),
        ).fetchone()
        actual = connection.execute(
            "SELECT COUNT(*), SUM(result_status = 'success'), "
            "SUM(result_status = 'failed'), SUM(result_status = 'skipped') "
            "FROM player_scan_result WHERE scan_id = ?",
            (scan.scan_id,),
        ).fetchone()
        if tuple(row) != tuple(actual):
            raise ClanGamesStoreError("invalid_scan", "scan row counts are inconsistent")
        connection.commit()
        return StoreOperationResult(
            "success",
            "success",
            scan.scan_id,
            definition_fingerprint,
            scan_fingerprint,
        )
    except ClanGamesStoreError:
        if connection.in_transaction:
            connection.rollback()
        raise
    except sqlite3.OperationalError as error:
        if connection.in_transaction:
            connection.rollback()
        code = "locked" if "locked" in str(error).casefold() else "write_failure"
        raise ClanGamesStoreError(code, "Clan Games scan transaction failed") from None
    except sqlite3.Error:
        if connection.in_transaction:
            connection.rollback()
        raise ClanGamesStoreError("write_failure", "Clan Games scan transaction failed") from None
    finally:
        connection.close()


def list_scan_summaries(
    path: str | Path, event_id: str | None = None
) -> list[dict[str, Any]]:
    validate_clan_games_store(path)
    connection = _readonly_connect(_safe_path(Path(path)))
    try:
        sql = (
            "SELECT scan_id, event_id, definition_id, scan_kind, started_at_utc, "
            "finished_at_utc, requested_count, successful_count, failed_count, "
            "skipped_count, status, result_code, scan_fingerprint, recorded_at_utc "
            "FROM collection_scan"
        )
        parameters: tuple[Any, ...] = ()
        if event_id is not None:
            sql += " WHERE event_id = ?"
            parameters = (event_id,)
        sql += " ORDER BY event_id, started_at_utc, scan_id"
        return [dict(row) for row in connection.execute(sql, parameters)]
    finally:
        connection.close()


def get_scan_by_id(path: str | Path, scan_id: str) -> dict[str, Any] | None:
    """Return one identity-free scan summary for an operational retry gate."""

    if not isinstance(scan_id, str) or not _SCAN_ID_PATTERN.fullmatch(scan_id):
        raise ClanGamesStoreError("invalid_scan", "scan identity is invalid")
    validate_clan_games_store(path)
    connection = _readonly_connect(_safe_path(Path(path)))
    try:
        row = connection.execute(
            "SELECT s.scan_id, s.event_id, s.scan_kind, s.started_at_utc, "
            "s.finished_at_utc, s.requested_count, s.successful_count, "
            "s.failed_count, s.skipped_count, s.status, s.result_code, "
            "s.recorded_at_utc, (SELECT COUNT(*) FROM player_scan_result p "
            "WHERE p.scan_id = s.scan_id AND p.attempted_at_utc IS NOT NULL) "
            "AS attempted_count FROM collection_scan s WHERE s.scan_id = ?",
            (scan_id,),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        connection.close()


def load_event_player_observations(
    path: str | Path, event_id: str
) -> list[dict[str, Any]]:
    validate_clan_games_store(path)
    connection = _readonly_connect(_safe_path(Path(path)))
    try:
        rows = connection.execute(
            "SELECT s.scan_id, s.event_id, s.definition_id, s.scan_kind, "
            "s.started_at_utc, s.finished_at_utc, s.status AS scan_status, "
            "p.player_tag, p.result_status, p.result_code, p.attempted_at_utc, "
            "p.observed_at_utc, p.cumulative_value, p.achievement_target, "
            "p.source_kind, p.normalization_version "
            "FROM collection_scan s JOIN player_scan_result p ON p.scan_id = s.scan_id "
            "WHERE s.event_id = ? ORDER BY s.started_at_utc, s.scan_id, p.player_tag",
            (event_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def get_latest_scan(path: str | Path, event_id: str) -> dict[str, Any] | None:
    values = list_scan_summaries(path, event_id)
    return values[-1] if values else None


def get_scans_by_kind(
    path: str | Path, event_id: str, scan_kind: str
) -> list[dict[str, Any]]:
    if scan_kind not in SCAN_KINDS:
        raise ClanGamesStoreError("invalid_scan", "scan kind is invalid")
    return [
        item
        for item in list_scan_summaries(path, event_id)
        if item["scan_kind"] == scan_kind
    ]


def summarize_clan_games_store(path: str | Path) -> dict[str, Any]:
    """Return identity-free aggregate health useful to a future collector."""

    validate_clan_games_store(path)
    connection = _readonly_connect(_safe_path(Path(path)))
    try:
        scans = connection.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(status = 'success') AS successful, "
            "SUM(status = 'partial_success') AS partial, "
            "SUM(status = 'failed') AS failed, "
            "MIN(started_at_utc) AS earliest, MAX(started_at_utc) AS latest "
            "FROM collection_scan"
        ).fetchone()
        players = connection.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(result_status = 'success') AS successful, "
            "SUM(result_status = 'failed') AS failed, "
            "SUM(result_status = 'skipped') AS skipped FROM player_scan_result"
        ).fetchone()
        return {
            "event_count": connection.execute(
                "SELECT COUNT(DISTINCT event_id) FROM event_definition_snapshot"
            ).fetchone()[0],
            "definition_count": connection.execute(
                "SELECT COUNT(*) FROM event_definition_snapshot"
            ).fetchone()[0],
            "scan_count": scans["total"],
            "successful_scan_count": scans["successful"] or 0,
            "partial_scan_count": scans["partial"] or 0,
            "failed_scan_count": scans["failed"] or 0,
            "player_result_count": players["total"],
            "successful_player_result_count": players["successful"] or 0,
            "failed_player_result_count": players["failed"] or 0,
            "skipped_player_result_count": players["skipped"] or 0,
            "earliest_scan_at_utc": scans["earliest"],
            "latest_scan_at_utc": scans["latest"],
        }
    finally:
        connection.close()


def create_validated_clan_games_backup(
    path: str | Path, backup_path: str | Path, *, overwrite: bool = False
) -> StoreOperationResult:
    """Create a validated standalone backup without automatic restore."""

    source = _safe_path(Path(path))
    destination = _safe_path(Path(backup_path))
    validate_clan_games_store(source)
    if destination.exists() and not overwrite:
        raise ClanGamesStoreError("backup_failure", "backup destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.stem}.{uuid.uuid4().hex}.backup{destination.suffix}"
    )
    preserved = destination.with_name(
        f".{destination.stem}.{uuid.uuid4().hex}.preserved{destination.suffix}"
    )
    _remove_database_files(temporary)
    _remove_database_files(preserved)
    destination_preserved = False
    try:
        input_db = _readonly_connect(source)
        output_db = _standalone_destination(temporary)
        try:
            input_db.backup(output_db)
        finally:
            output_db.close()
            input_db.close()
        _make_standalone(temporary)
        validate_clan_games_store(temporary)
        if destination.exists():
            try:
                os.link(destination, preserved)
                destination_preserved = True
            except OSError:
                raise ClanGamesStoreError(
                    "backup_failure", "existing backup could not be preserved"
                ) from None
        os.replace(temporary, destination)
        _make_standalone(destination)
        validate_clan_games_store(destination)
        _remove_database_files(preserved)
        destination_preserved = False
        return StoreOperationResult("success", "success")
    except ClanGamesStoreError as error:
        _remove_database_files(temporary)
        if destination_preserved:
            try:
                os.replace(preserved, destination)
                destination_preserved = False
            except OSError:
                raise ClanGamesStoreError(
                    "backup_failure",
                    "Clan Games backup failed and the prior destination could not be restored",
                ) from None
        if error.result_code == "backup_failure":
            raise
        raise ClanGamesStoreError("backup_failure", "Clan Games backup failed") from None
    except (OSError, sqlite3.Error):
        _remove_database_files(temporary)
        if destination_preserved:
            try:
                os.replace(preserved, destination)
                destination_preserved = False
            except OSError:
                pass
        raise ClanGamesStoreError("backup_failure", "Clan Games backup failed") from None
    finally:
        _remove_database_files(temporary)
        if not destination_preserved:
            _remove_database_files(preserved)
