"""Operator-confirmed local Clan Games event registry.

The registry is intentionally independent from the player API source. Importing
this module performs no filesystem or network operation.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit, urlunsplit


REGISTRY_SCHEMA_VERSION = 1
REGISTRY_LOGICAL_PATH = "data/clan_games/event_registry.v1.json"
OFFICIAL_SOURCE_HOSTS = frozenset({"supercell.com"})
_EVENT_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_CANONICAL_TIMESTAMP_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z"
)
_EVENT_FIELDS = frozenset(
    {
        "event_id",
        "start_at_utc",
        "end_at_utc",
        "official_source_url",
        "confirmed_at_utc",
    }
)
_REGISTRY_FIELDS = frozenset({"schema_version", "events"})
_RESULT_CODES = frozenset(
    {
        "success",
        "no_change",
        "registry_created",
        "registry_not_found",
        "invalid_registry",
        "invalid_event",
        "duplicate_event_id",
        "event_conflict",
        "event_overlap",
        "invalid_official_source",
        "replace_requires_existing",
        "backup_failure",
        "write_failure",
    }
)


class EventRegistryError(ValueError):
    """A bounded domain failure suitable for operator-facing CLI output."""

    def __init__(self, result_code: str, safe_message: str) -> None:
        if result_code not in _RESULT_CODES:
            raise ValueError("invalid event registry result code")
        self.result_code = result_code
        self.safe_message = safe_message
        super().__init__(safe_message)


def _canonical_timestamp(value: str | datetime, field_name: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        if "T" not in value or not (
            value.endswith("Z") or re.search(r"[+-]\d{2}:\d{2}$", value)
        ):
            raise EventRegistryError(
                "invalid_event", f"{field_name} must include a timezone"
            )
        try:
            parsed = datetime.fromisoformat(
                value[:-1] + "+00:00" if value.endswith("Z") else value
            )
        except ValueError:
            raise EventRegistryError(
                "invalid_event", f"{field_name} is not a valid ISO-8601 timestamp"
            ) from None
    else:
        raise EventRegistryError(
            "invalid_event", f"{field_name} must be a timestamp string"
        )
    try:
        offset = parsed.utcoffset() if parsed.tzinfo is not None else None
    except (OverflowError, ValueError):
        offset = None
    if offset is None:
        raise EventRegistryError(
            "invalid_event", f"{field_name} must include a timezone"
        )
    try:
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    except (OverflowError, OSError, ValueError):
        raise EventRegistryError(
            "invalid_event", f"{field_name} is outside the supported timestamp range"
        ) from None


def _canonical_registry_timestamp(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _CANONICAL_TIMESTAMP_PATTERN.fullmatch(value):
        raise EventRegistryError(
            "invalid_registry", f"{field_name} must use canonical UTC format"
        )
    try:
        canonical = _canonical_timestamp(value, field_name)
    except EventRegistryError:
        raise EventRegistryError(
            "invalid_registry", f"{field_name} must use canonical UTC format"
        ) from None
    if canonical != value:
        raise EventRegistryError(
            "invalid_registry", f"{field_name} must use canonical UTC format"
        )
    return value


def _validate_event_id(value: Any, *, registry_context: bool = False) -> str:
    code = "invalid_registry" if registry_context else "invalid_event"
    if not isinstance(value, str) or not _EVENT_ID_PATTERN.fullmatch(value):
        raise EventRegistryError(
            code,
            "event_id must be 1-64 lowercase ASCII letters, digits, hyphens or underscores and start with a letter or digit",
        )
    return value


def _canonical_official_source_url(
    value: Any, *, registry_context: bool = False
) -> str:
    code = "invalid_registry" if registry_context else "invalid_official_source"
    if not isinstance(value, str) or not value or any(
        character.isspace() or ord(character) < 32 for character in value
    ):
        raise EventRegistryError(code, "official source URL is invalid")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise EventRegistryError(code, "official source URL is invalid") from None
    if (
        parsed.scheme.lower() != "https"
        or hostname not in OFFICIAL_SOURCE_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or "\\" in parsed.path
    ):
        raise EventRegistryError(
            code, "official source URL must be a canonical approved Supercell HTTPS URL"
        )
    return urlunsplit(("https", hostname, parsed.path or "/", "", ""))


def _parse_canonical(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )


@dataclass(frozen=True)
class ClanGamesEvent:
    """Immutable, canonical operator-confirmed event truth and provenance."""

    event_id: str
    start_at_utc: str
    end_at_utc: str
    official_source_url: str
    confirmed_at_utc: str

    def __post_init__(self) -> None:
        _validate_event_id(self.event_id)
        start = _canonical_registry_timestamp(self.start_at_utc, "start_at_utc")
        end = _canonical_registry_timestamp(self.end_at_utc, "end_at_utc")
        _canonical_registry_timestamp(self.confirmed_at_utc, "confirmed_at_utc")
        official_url = _canonical_official_source_url(self.official_source_url)
        if official_url != self.official_source_url:
            raise EventRegistryError(
                "invalid_official_source", "official source URL is not canonical"
            )
        if start >= end:
            raise EventRegistryError(
                "invalid_event", "event start must be earlier than event end"
            )

    @classmethod
    def create(
        cls,
        *,
        event_id: str,
        start_at: str | datetime,
        end_at: str | datetime,
        official_source_url: str,
        confirmed_at: str | datetime,
    ) -> "ClanGamesEvent":
        return cls(
            event_id=_validate_event_id(event_id),
            start_at_utc=_canonical_timestamp(start_at, "start_at"),
            end_at_utc=_canonical_timestamp(end_at, "end_at"),
            official_source_url=_canonical_official_source_url(official_source_url),
            confirmed_at_utc=_canonical_timestamp(confirmed_at, "confirmed_at"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "event_id": self.event_id,
            "start_at_utc": self.start_at_utc,
            "end_at_utc": self.end_at_utc,
            "official_source_url": self.official_source_url,
            "confirmed_at_utc": self.confirmed_at_utc,
        }

    @property
    def duration_seconds(self) -> int:
        return int(
            (_parse_canonical(self.end_at_utc) - _parse_canonical(self.start_at_utc))
            .total_seconds()
        )

    def status(self, as_of: str | datetime) -> str:
        moment = _canonical_timestamp(as_of, "as_of")
        if moment < self.start_at_utc:
            return "upcoming"
        if moment < self.end_at_utc:
            return "active"
        return "ended"


def _events_overlap(first: ClanGamesEvent, second: ClanGamesEvent) -> bool:
    return (
        first.start_at_utc < second.end_at_utc
        and second.start_at_utc < first.end_at_utc
    )


def _event_sort_key(event: ClanGamesEvent) -> tuple[str, str, str]:
    return event.start_at_utc, event.end_at_utc, event.event_id


def _validate_event_sequence(
    events: tuple[ClanGamesEvent, ...], *, require_order: bool
) -> None:
    if require_order and tuple(sorted(events, key=_event_sort_key)) != events:
        raise EventRegistryError(
            "invalid_registry", "registry events are not in deterministic order"
        )
    identifiers: set[str] = set()
    semantic_events: set[tuple[str, str, str]] = set()
    for index, event in enumerate(events):
        if event.event_id in identifiers:
            raise EventRegistryError(
                "invalid_registry", "registry contains duplicate event IDs"
            )
        identifiers.add(event.event_id)
        semantic_key = (
            event.start_at_utc,
            event.end_at_utc,
            event.official_source_url,
        )
        if semantic_key in semantic_events:
            raise EventRegistryError(
                "invalid_registry", "registry contains a duplicate semantic event"
            )
        semantic_events.add(semantic_key)
        for previous in events[:index]:
            if _events_overlap(previous, event):
                raise EventRegistryError(
                    "invalid_registry", "registry contains overlapping event windows"
                )


@dataclass(frozen=True)
class ClanGamesEventRegistry:
    """Immutable validated schema-v1 registry."""

    schema_version: int
    events: tuple[ClanGamesEvent, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or self.schema_version != REGISTRY_SCHEMA_VERSION
        ):
            raise EventRegistryError(
                "invalid_registry", "registry schema_version must equal 1"
            )
        if not isinstance(self.events, tuple) or not all(
            isinstance(event, ClanGamesEvent) for event in self.events
        ):
            raise EventRegistryError(
                "invalid_registry", "registry events must be immutable event records"
            )
        _validate_event_sequence(self.events, require_order=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "events": [event.to_dict() for event in self.events],
        }


@dataclass(frozen=True)
class RegistryOperationResult:
    """Safe result for explicit registry mutation commands."""

    status: str
    result_code: str
    event_id: str | None = None
    backup_logical_path: str | None = None

    def __post_init__(self) -> None:
        if self.result_code not in _RESULT_CODES:
            raise ValueError("invalid event registry result code")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "result_code": self.result_code,
            "event_id": self.event_id,
            "backup_logical_path": self.backup_logical_path,
        }


def _event_from_registry_payload(value: Any) -> ClanGamesEvent:
    if not isinstance(value, Mapping) or frozenset(value) != _EVENT_FIELDS:
        raise EventRegistryError(
            "invalid_registry", "registry event fields do not match schema v1"
        )
    _validate_event_id(value.get("event_id"), registry_context=True)
    start = _canonical_registry_timestamp(value.get("start_at_utc"), "start_at_utc")
    end = _canonical_registry_timestamp(value.get("end_at_utc"), "end_at_utc")
    confirmed = _canonical_registry_timestamp(
        value.get("confirmed_at_utc"), "confirmed_at_utc"
    )
    official_url = _canonical_official_source_url(
        value.get("official_source_url"), registry_context=True
    )
    if official_url != value.get("official_source_url"):
        raise EventRegistryError(
            "invalid_registry", "registry official source URL must be canonical"
        )
    if start >= end:
        raise EventRegistryError(
            "invalid_registry", "registry event start must be earlier than its end"
        )
    try:
        return ClanGamesEvent(
            event_id=value["event_id"],
            start_at_utc=start,
            end_at_utc=end,
            official_source_url=official_url,
            confirmed_at_utc=confirmed,
        )
    except EventRegistryError as error:
        raise EventRegistryError("invalid_registry", error.safe_message) from None


def _registry_from_payload(value: Any) -> ClanGamesEventRegistry:
    if not isinstance(value, Mapping) or frozenset(value) != _REGISTRY_FIELDS:
        raise EventRegistryError(
            "invalid_registry", "registry root fields do not match schema v1"
        )
    schema_version = value.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != REGISTRY_SCHEMA_VERSION:
        raise EventRegistryError(
            "invalid_registry", "registry schema_version must equal 1"
        )
    raw_events = value.get("events")
    if not isinstance(raw_events, list):
        raise EventRegistryError(
            "invalid_registry", "registry events must be an array"
        )
    events = tuple(_event_from_registry_payload(item) for item in raw_events)
    return ClanGamesEventRegistry(schema_version=schema_version, events=events)


def _registry_bytes(registry: ClanGamesEventRegistry) -> bytes:
    return (
        json.dumps(
            registry.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
        )
        + "\n"
    ).encode("utf-8")


def _load_registry_with_bytes(
    path: Path,
) -> tuple[ClanGamesEventRegistry, bytes]:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise EventRegistryError(
            "registry_not_found", "event registry does not exist"
        ) from None
    except OSError:
        raise EventRegistryError(
            "invalid_registry", "event registry could not be read"
        ) from None
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EventRegistryError(
            "invalid_registry", "event registry is not valid UTF-8 JSON"
        ) from None
    return _registry_from_payload(value), data


def load_event_registry(path: Path) -> ClanGamesEventRegistry:
    """Load a strict registry without repairing or rewriting it."""

    return _load_registry_with_bytes(Path(path))[0]


def validate_event_registry(path: Path) -> ClanGamesEventRegistry:
    """Read-only strict validation alias for operator tooling."""

    return load_event_registry(path)


def _write_fsynced_exclusive(path: Path, data: bytes, error_code: str) -> None:
    created = False
    try:
        with path.open("xb") as stream:
            created = True
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        if created:
            path.unlink(missing_ok=True)
        raise EventRegistryError(error_code, "registry file write failed") from None


def _write_new_atomic(
    path: Path, data: bytes, error_code: str = "write_failure"
) -> None:
    temp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_fsynced_exclusive(temp, data, error_code)
        try:
            os.link(temp, path)
        except FileExistsError:
            raise
        except OSError:
            raise EventRegistryError(
                error_code, "atomic new registry file publication failed"
            ) from None
    finally:
        temp.unlink(missing_ok=True)


def _write_atomic_replace(path: Path, data: bytes, expected_current: bytes) -> None:
    temp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        _write_fsynced_exclusive(temp, data, "write_failure")
        try:
            if path.read_bytes() != expected_current:
                raise EventRegistryError(
                    "event_conflict", "event registry changed during the operation"
                )
            os.replace(temp, path)
        except EventRegistryError:
            raise
        except OSError:
            raise EventRegistryError(
                "write_failure", "atomic registry replacement failed"
            ) from None
    finally:
        temp.unlink(missing_ok=True)


def _restore_bytes(path: Path, data: bytes) -> None:
    temp = path.parent / f".{path.name}.{uuid.uuid4().hex}.restore.tmp"
    try:
        _write_fsynced_exclusive(temp, data, "write_failure")
        os.replace(temp, path)
        if path.read_bytes() != data:
            raise OSError("restored registry bytes differ")
    except OSError:
        raise EventRegistryError(
            "write_failure", "registry rollback failed; validated backup retained"
        ) from None
    finally:
        temp.unlink(missing_ok=True)


def initialize_event_registry(path: Path) -> RegistryOperationResult:
    """Explicitly create an empty registry without overwriting existing data."""

    target = Path(path)
    if target.exists():
        load_event_registry(target)
        return RegistryOperationResult(status="no_change", result_code="no_change")
    registry = ClanGamesEventRegistry(
        schema_version=REGISTRY_SCHEMA_VERSION, events=()
    )
    try:
        _write_new_atomic(target, _registry_bytes(registry))
    except FileExistsError:
        load_event_registry(target)
        return RegistryOperationResult(status="no_change", result_code="no_change")
    try:
        loaded = load_event_registry(target)
    except EventRegistryError:
        target.unlink(missing_ok=True)
        raise EventRegistryError(
            "write_failure", "new event registry failed post-write validation"
        ) from None
    if loaded != registry:
        target.unlink(missing_ok=True)
        raise EventRegistryError(
            "write_failure", "new event registry failed post-write validation"
        )
    return RegistryOperationResult(status="success", result_code="registry_created")


def _candidate_registry(
    existing: ClanGamesEventRegistry,
    event: ClanGamesEvent,
    *,
    replace_index: int | None = None,
) -> ClanGamesEventRegistry:
    events = list(existing.events)
    comparison = [
        other for index, other in enumerate(events) if index != replace_index
    ]
    for other in comparison:
        if _events_overlap(other, event):
            raise EventRegistryError(
                "event_overlap", "event window overlaps an existing event"
            )
    if replace_index is None:
        events.append(event)
    else:
        events[replace_index] = event
    events.sort(key=_event_sort_key)
    return ClanGamesEventRegistry(
        schema_version=REGISTRY_SCHEMA_VERSION, events=tuple(events)
    )


def register_event(path: Path, event: ClanGamesEvent) -> RegistryOperationResult:
    """Register one event atomically, with exact retry idempotency."""

    if not isinstance(event, ClanGamesEvent):
        raise EventRegistryError("invalid_event", "event model is invalid")
    target = Path(path)
    registry, original_bytes = _load_registry_with_bytes(target)
    existing = next(
        (item for item in registry.events if item.event_id == event.event_id), None
    )
    if existing is not None:
        if existing == event:
            return RegistryOperationResult(
                status="no_change", result_code="no_change", event_id=event.event_id
            )
        raise EventRegistryError(
            "duplicate_event_id", "event_id already exists with different fields"
        )
    candidate = _candidate_registry(registry, event)
    _write_atomic_replace(target, _registry_bytes(candidate), original_bytes)
    try:
        if load_event_registry(target) != candidate:
            raise EventRegistryError(
                "write_failure", "registered event failed post-write validation"
            )
    except EventRegistryError:
        _restore_bytes(target, original_bytes)
        raise
    return RegistryOperationResult(
        status="success", result_code="success", event_id=event.event_id
    )


def _backup_timestamp(clock: Callable[[], datetime] | None) -> str:
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    canonical = _canonical_timestamp(now, "backup timestamp")
    return datetime.strptime(canonical, "%Y-%m-%dT%H:%M:%S.%fZ").strftime(
        "%Y%m%dT%H%M%S.%fZ"
    )


def replace_event(
    path: Path,
    event: ClanGamesEvent,
    *,
    explicit_replace: bool = False,
    clock: Callable[[], datetime] | None = None,
) -> RegistryOperationResult:
    """Explicitly replace one event after a validated local backup is secured."""

    if not explicit_replace:
        raise EventRegistryError(
            "event_conflict", "event replacement requires explicit operator intent"
        )
    if not isinstance(event, ClanGamesEvent):
        raise EventRegistryError("invalid_event", "event model is invalid")
    target = Path(path)
    registry, original_bytes = _load_registry_with_bytes(target)
    replace_index = next(
        (
            index
            for index, existing in enumerate(registry.events)
            if existing.event_id == event.event_id
        ),
        None,
    )
    if replace_index is None:
        raise EventRegistryError(
            "replace_requires_existing", "replacement requires an existing event_id"
        )
    if registry.events[replace_index] == event:
        return RegistryOperationResult(
            status="no_change", result_code="no_change", event_id=event.event_id
        )
    candidate = _candidate_registry(
        registry, event, replace_index=replace_index
    )
    backup_directory = target.parent / "backups" / "event_registry"
    backup_name = f"{_backup_timestamp(clock)}-event_registry.v1.json"
    backup_path = backup_directory / backup_name
    try:
        backup_directory.mkdir(parents=True, exist_ok=True)
        try:
            _write_new_atomic(backup_path, original_bytes, "backup_failure")
        except FileExistsError:
            raise EventRegistryError(
                "backup_failure", "registry backup already exists"
            ) from None
        backup_registry, backup_bytes = _load_registry_with_bytes(backup_path)
        if backup_registry != registry or backup_bytes != original_bytes:
            raise EventRegistryError(
                "backup_failure", "registry backup validation failed"
            )
    except EventRegistryError as error:
        if error.result_code != "backup_failure":
            backup_path.unlink(missing_ok=True)
            raise EventRegistryError(
                "backup_failure", "registry backup validation failed"
            ) from None
        raise
    except OSError:
        backup_path.unlink(missing_ok=True)
        raise EventRegistryError(
            "backup_failure", "registry backup could not be created"
        ) from None
    _write_atomic_replace(target, _registry_bytes(candidate), original_bytes)
    try:
        if load_event_registry(target) != candidate:
            raise EventRegistryError(
                "write_failure", "replacement failed post-write validation"
            )
    except EventRegistryError:
        _restore_bytes(target, original_bytes)
        raise
    return RegistryOperationResult(
        status="success",
        result_code="success",
        event_id=event.event_id,
        backup_logical_path=f"backups/event_registry/{backup_name}",
    )


def get_event(
    registry: ClanGamesEventRegistry, event_id: str
) -> ClanGamesEvent | None:
    _validate_event_id(event_id)
    return next(
        (event for event in registry.events if event.event_id == event_id), None
    )


def list_events(
    registry: ClanGamesEventRegistry,
) -> tuple[ClanGamesEvent, ...]:
    return registry.events


def get_active_event(
    registry: ClanGamesEventRegistry, as_of: str | datetime
) -> ClanGamesEvent | None:
    moment = _canonical_timestamp(as_of, "as_of")
    return next(
        (
            event
            for event in registry.events
            if event.start_at_utc <= moment < event.end_at_utc
        ),
        None,
    )


def get_upcoming_event(
    registry: ClanGamesEventRegistry, as_of: str | datetime
) -> ClanGamesEvent | None:
    moment = _canonical_timestamp(as_of, "as_of")
    return next(
        (event for event in registry.events if event.start_at_utc > moment), None
    )
