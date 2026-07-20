"""Explicit, recoverable first migration for local war-history v1."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .history import HISTORY_SCHEMA_VERSION, HistoryError, migrate_history, load_history


class MigrationError(ValueError):
    """Raised when an explicit migration cannot finish safely."""


def _bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise MigrationError(f"migration read stage failed: {path}") from error


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_source(path: Path) -> tuple[Mapping[str, Any], bytes]:
    data = _bytes(path)
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MigrationError(f"migration source JSON stage failed: {path}") from error
    if not isinstance(payload, Mapping):
        raise MigrationError(f"migration source must be an object: {path}")
    return payload, data


def _serialized(history: Mapping[str, Any]) -> bytes:
    return (json.dumps(history, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_fsynced(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _summary(source: Mapping[str, Any], migrated: Mapping[str, Any], source_hash: str, migrated_hash: str) -> dict[str, Any]:
    wars = migrated.get("wars", [])
    return {
        "source_schema": source.get("schema_version"),
        "target_schema": HISTORY_SCHEMA_VERSION,
        "wars": len(wars) if isinstance(wars, list) else 0,
        "observations": sum(len(record.get("observations", [])) for record in wars if isinstance(record, Mapping)),
        "warnings": ["legacy history retains only its latest snapshot"] if source.get("schema_version") == 1 else [],
        "source_sha256": source_hash,
        "migrated_sha256": migrated_hash,
    }


def preview_migration(source_path: Path, output_path: Path | None = None) -> dict[str, Any]:
    source, source_bytes = _parse_source(source_path)
    try:
        migrated = migrate_history(source)
    except HistoryError as error:
        raise MigrationError(f"migration validation stage failed: {source_path}: {error}") from error
    migrated_bytes = _serialized(migrated)
    if output_path is not None:
        if output_path.exists():
            raise MigrationError(f"preview output already exists: {output_path}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_fsynced(output_path, migrated_bytes)
    return _summary(source, migrated, _sha256(source_bytes), _sha256(migrated_bytes))


def execute_migration(*, source_path: Path, backup_dir: Path, expected_source_sha256: str, confirm: bool) -> dict[str, Any]:
    if not confirm:
        raise MigrationError("migration requires --confirm-migration")
    source, source_bytes = _parse_source(source_path)
    source_hash = _sha256(source_bytes)
    if source_hash != expected_source_sha256.lower():
        raise MigrationError("migration expected source SHA-256 does not match")
    if source.get("schema_version") != 1:
        raise MigrationError("migration execute requires schema v1 source")
    try:
        migrated = migrate_history(source)
    except HistoryError as error:
        raise MigrationError(f"migration validation stage failed: {source_path}: {error}") from error
    migrated_bytes = _serialized(migrated)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{source_path.stem}-v1-{stamp}-{source_hash[:12]}.json"
    if backup_path.exists():
        raise MigrationError(f"migration backup collision: {backup_path}")
    temp = source_path.parent / f".{source_path.name}.{uuid.uuid4().hex}.migration.tmp"
    replaced = False
    try:
        _write_fsynced(backup_path, source_bytes)
        if _sha256(_bytes(backup_path)) != source_hash:
            raise MigrationError(f"migration backup hash verification failed: {backup_path}")
        _write_fsynced(temp, migrated_bytes)
        os.replace(temp, source_path)
        replaced = True
        read_back = load_history(source_path)
        if _sha256(_serialized(read_back)) != _sha256(migrated_bytes):
            raise MigrationError(f"migration post-write hash verification failed: {source_path}")
    except Exception as error:
        if replaced:
            rollback = source_path.parent / f".{source_path.name}.{uuid.uuid4().hex}.rollback.tmp"
            try:
                _write_fsynced(rollback, _bytes(backup_path))
                os.replace(rollback, source_path)
            except OSError as rollback_error:
                raise MigrationError(f"migration rollback failed; backup retained: {backup_path}") from rollback_error
        if isinstance(error, MigrationError):
            raise
        raise MigrationError(f"migration replace stage failed: {source_path}") from error
    finally:
        temp.unlink(missing_ok=True)
    report = _summary(source, migrated, source_hash, _sha256(migrated_bytes))
    report["backup_path"] = str(backup_path)
    return report
