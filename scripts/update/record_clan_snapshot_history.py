"""Record one confirmed roster observation without making network requests.

This is the sole updater-facing adapter for clan_snapshot_history_v1.  Its
machine-readable result deliberately contains operational facts only; roster
payloads, member names, tags, and filesystem paths are never emitted.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from clan_analytics.api.normalization import NormalizationError, normalize_clan
from clan_analytics.clan_snapshot_history import (
    NORMALIZATION_VERSION,
    ObservationConflictError,
    OutOfOrderObservationError,
    SnapshotValidationError,
    UnsupportedSchemaVersionError,
    _parse_utc,
    initialize_snapshot_store,
    list_observations,
    record_confirmed_observation,
    validate_snapshot_store,
)


LOGICAL_DATABASE_PATH = "data/clan_snapshot_history/clan_snapshot_history.v1.sqlite3"
VALIDATION_VERSION = "clan_snapshot_history_updater_v1"


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".snapshot-history-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        os.replace(temporary_name, path)
    except Exception:
        try:
            Path(temporary_name).unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _read_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise SnapshotValidationError("probe input must be an object")
    return value


def _validated_observed_at(metadata: dict[str, Any]) -> str:
    if metadata.get("request_count") != 1:
        raise SnapshotValidationError("roster probe request count is invalid")
    if metadata.get("response_status") != 200:
        raise SnapshotValidationError("roster probe response status is invalid")
    if metadata.get("redirects_followed") != 0:
        raise SnapshotValidationError("roster probe redirects are invalid")
    return _parse_utc(metadata.get("collected_at"))


def _database_path(arguments: argparse.Namespace) -> Path:
    database = Path(arguments.database).resolve()
    if arguments.allow_test_database:
        return database
    expected = (Path(arguments.workspace_root).resolve() / LOGICAL_DATABASE_PATH).resolve()
    if database != expected:
        raise SnapshotValidationError("snapshot database path is not the approved logical location")
    return database


def _result(
    *, status: str, result_code: str, source_run_id: str, observed_at_utc: str | None = None,
    initialized_store: bool | None = None, inserted_payload: bool | None = None,
    inserted_observation: bool | None = None, observation_id: str | None = None,
    recorded_at_utc: str | None = None,
) -> dict[str, Any]:
    safe_message = "Confirmed roster observation recorded." if status == "success" else "Confirmed roster observation was not recorded."
    return {
        "schema_version": 1,
        "status": status,
        "result_code": result_code,
        "mode": "normal",
        "source_run_id": source_run_id,
        "logical_database_path": LOGICAL_DATABASE_PATH,
        "observed_at_utc": observed_at_utc,
        "recorded_at_utc": recorded_at_utc,
        "storage_schema_version": 1,
        "normalization_version": NORMALIZATION_VERSION,
        "initialized_store": initialized_store,
        "inserted_payload": inserted_payload,
        "inserted_observation": inserted_observation,
        "observation_id": observation_id,
        "safe_message": safe_message,
    }


def _failure_code(error: Exception, phase: str) -> str:
    if isinstance(error, UnsupportedSchemaVersionError):
        return "snapshot_history_schema_unsupported"
    if isinstance(error, ObservationConflictError):
        return "snapshot_history_conflict"
    if isinstance(error, OutOfOrderObservationError):
        return "snapshot_history_out_of_order"
    if isinstance(error, sqlite3.OperationalError) and any(word in str(error).lower() for word in ("locked", "busy")):
        return "snapshot_history_locked"
    if isinstance(error, sqlite3.DatabaseError):
        return "snapshot_history_write_failure"
    if isinstance(error, (SnapshotValidationError, NormalizationError, json.JSONDecodeError, OSError)):
        return "snapshot_history_initialization_failure" if phase == "initialization" else "snapshot_history_validation_failure"
    return "snapshot_history_unexpected_failure"


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    source_run_id = arguments.source_run_id
    initialized = False
    observed_at_utc: str | None = None
    try:
        raw = _read_object(Path(arguments.roster_json))
        metadata = _read_object(Path(arguments.roster_metadata))
        observed_at_utc = _validated_observed_at(metadata)
        snapshot = normalize_clan(raw, collected_at=observed_at_utc, raw_source_reference="raw_clan_response.json")
        database = _database_path(arguments)
        phase = "initialization"
        if not database.exists():
            initialize_snapshot_store(database)
            initialized = True
        phase = "validation"
        validate_snapshot_store(database)
        observation = record_confirmed_observation(
            database, snapshot, observed_at_utc, source_run_id, arguments.validation_version
        )
        validate_snapshot_store(database)
        recorded_at_utc = next(
            item["recorded_at_utc"]
            for item in list_observations(database)
            if item["observation_id"] == observation.observation_id
        )
        code = "snapshot_history_success" if observation.inserted_observation else "snapshot_history_idempotent"
        return _result(
            status="success", result_code=code, source_run_id=source_run_id,
            observed_at_utc=observed_at_utc, initialized_store=initialized,
            inserted_payload=observation.inserted_payload,
            inserted_observation=observation.inserted_observation,
            observation_id=observation.observation_id, recorded_at_utc=recorded_at_utc,
        )
    except Exception as error:
        return _result(
            status="failed", result_code=_failure_code(error, locals().get("phase", "validation")),
            source_run_id=source_run_id, observed_at_utc=observed_at_utc,
            initialized_store=initialized,
        )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roster-json", required=True)
    parser.add_argument("--roster-metadata", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--validation-version", default=VALIDATION_VERSION)
    parser.add_argument("--result-json", required=True)
    parser.add_argument("--allow-test-database", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    result = run(arguments)
    try:
        _write_json_atomic(Path(arguments.result_json), result)
    except Exception:
        print("snapshot_history_result_write_failure", file=sys.stderr)
        return 1
    if result["status"] != "success":
        print(result["result_code"], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
