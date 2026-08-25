"""Pure deterministic scheduling policy for Clan Games collection scans."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from .clan_games_events import ClanGamesEvent, ClanGamesEventRegistry
from .clan_games_history import SCAN_KINDS, event_definition_fingerprint


CADENCE_HOURS = 6
CADENCE = timedelta(hours=CADENCE_HOURS)
_SCAN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_DUE_ACTIONS = frozenset({"baseline_due", "periodic_due", "final_due"})


class ClanGamesScheduleError(ValueError):
    """Bounded schedule failure without private player data."""

    def __init__(self, result_code: str, safe_message: str) -> None:
        self.result_code = result_code
        self.safe_message = safe_message
        super().__init__(safe_message)


@dataclass(frozen=True)
class ClanGamesScheduleDecision:
    """Identity-free immutable output of one scheduling evaluation."""

    action: str
    result_code: str
    event_id: str | None = None
    scan_kind: str | None = None
    scan_id: str | None = None
    scheduled_for_utc: str | None = None
    operator_hint_code: str | None = None
    baseline_available: bool | None = None
    baseline_missed: bool = False

    def __post_init__(self) -> None:
        due = self.action in _DUE_ACTIONS
        if due != all(
            value is not None
            for value in (
                self.event_id,
                self.scan_kind,
                self.scan_id,
                self.scheduled_for_utc,
            )
        ):
            raise ClanGamesScheduleError(
                "schedule_conflict", "schedule decision shape is inconsistent"
            )
        if self.scan_kind is not None and self.scan_kind not in SCAN_KINDS:
            raise ClanGamesScheduleError(
                "schedule_conflict", "schedule decision scan kind is invalid"
            )
        if self.scan_id is not None and not _SCAN_ID_PATTERN.fullmatch(self.scan_id):
            raise ClanGamesScheduleError(
                "schedule_conflict", "schedule decision scan identity is invalid"
            )

    @property
    def collector_due(self) -> bool:
        return self.action in _DUE_ACTIONS

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "result_code": self.result_code,
            "event_id": self.event_id,
            "scan_kind": self.scan_kind,
            "scan_id": self.scan_id,
            "scheduled_for_utc": self.scheduled_for_utc,
            "operator_hint_code": self.operator_hint_code,
            "baseline_available": self.baseline_available,
            "baseline_missed": self.baseline_missed,
            "collector_due": self.collector_due,
        }


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ClanGamesScheduleError(
            "schedule_conflict", "schedule clock must be timezone-aware"
        )
    try:
        offset = value.utcoffset()
    except (OverflowError, ValueError):
        offset = None
    if offset is None:
        raise ClanGamesScheduleError(
            "schedule_conflict", "schedule clock must be timezone-aware"
        )
    return value.astimezone(timezone.utc)


def _parse(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        raise ClanGamesScheduleError(
            "schedule_conflict", "schedule timestamp is invalid"
        ) from None


def _canonical(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def deterministic_scan_id(
    event_id: str, scan_kind: str, scheduled_for: datetime
) -> str:
    """Build a stable bounded scan ID from event, kind and planned slot."""

    if scan_kind not in SCAN_KINDS:
        raise ClanGamesScheduleError("schedule_conflict", "scan kind is invalid")
    slot = _aware_utc(scheduled_for)
    compact = slot.strftime("%Y%m%dT%H%M%SZ")
    digest_input = f"{event_id}\n{scan_kind}\n{_canonical(slot)}".encode("utf-8")
    digest = hashlib.sha256(digest_input).hexdigest()[:12]
    candidate = f"cg-{event_id[:24]}-{scan_kind}-{compact}-{digest}"
    if not _SCAN_ID_PATTERN.fullmatch(candidate):
        raise ClanGamesScheduleError(
            "schedule_conflict", "deterministic scan identity is invalid"
        )
    return candidate


@dataclass(frozen=True)
class _StoredScans:
    ids: frozenset[str]
    kinds_by_event: Mapping[str, frozenset[str]]


def _stored_scans(
    registry: ClanGamesEventRegistry,
    summaries: Iterable[Mapping[str, Any]],
) -> _StoredScans:
    events = {event.event_id: event for event in registry.events}
    ids: set[str] = set()
    kinds: dict[str, set[str]] = {}
    identities: dict[str, tuple[str, str]] = {}
    for summary in summaries:
        if not isinstance(summary, Mapping):
            raise ClanGamesScheduleError(
                "schedule_conflict", "stored scan summary is invalid"
            )
        scan_id = summary.get("scan_id")
        event_id = summary.get("event_id")
        scan_kind = summary.get("scan_kind")
        if (
            not isinstance(scan_id, str)
            or not _SCAN_ID_PATTERN.fullmatch(scan_id)
            or not isinstance(event_id, str)
            or scan_kind not in SCAN_KINDS
        ):
            raise ClanGamesScheduleError(
                "schedule_conflict", "stored scan summary is invalid"
            )
        existing = identities.get(scan_id)
        identity = (event_id, scan_kind)
        if existing is not None and existing != identity:
            raise ClanGamesScheduleError(
                "schedule_conflict", "stored scan identity is ambiguous"
            )
        identities[scan_id] = identity
        if event_id not in events:
            continue
        definition_id = summary.get("definition_id")
        if definition_id is not None and definition_id != event_definition_fingerprint(
            events[event_id]
        ):
            raise ClanGamesScheduleError(
                "schedule_conflict", "stored scan uses a different event definition"
            )
        ids.add(scan_id)
        kinds.setdefault(event_id, set()).add(scan_kind)
    return _StoredScans(
        frozenset(ids),
        {event_id: frozenset(values) for event_id, values in kinds.items()},
    )


def _due(
    action: str,
    event: ClanGamesEvent,
    kind: str,
    scheduled_for: datetime,
    *,
    baseline_available: bool,
    baseline_missed: bool = False,
) -> ClanGamesScheduleDecision:
    return ClanGamesScheduleDecision(
        action=action,
        result_code=action,
        event_id=event.event_id,
        scan_kind=kind,
        scan_id=deterministic_scan_id(event.event_id, kind, scheduled_for),
        scheduled_for_utc=_canonical(scheduled_for),
        baseline_available=baseline_available,
        baseline_missed=baseline_missed,
    )


def no_event_registry_decision() -> ClanGamesScheduleDecision:
    return ClanGamesScheduleDecision("no_event_registry", "no_event_registry")


def plan_clan_games_scan(
    registry: ClanGamesEventRegistry,
    scan_summaries: Iterable[Mapping[str, Any]],
    *,
    as_of: datetime,
    cadence: timedelta = CADENCE,
) -> ClanGamesScheduleDecision:
    """Return at most one deterministic action without performing any I/O."""

    if not isinstance(registry, ClanGamesEventRegistry):
        raise ClanGamesScheduleError(
            "schedule_conflict", "validated event registry is required"
        )
    if cadence != CADENCE:
        raise ClanGamesScheduleError(
            "schedule_conflict", "unsupported Clan Games cadence"
        )
    now = _aware_utc(as_of)
    stored = _stored_scans(registry, scan_summaries)
    events = tuple(registry.events)
    if not events:
        return ClanGamesScheduleDecision("no_scan_due", "no_scan_due")

    active = [
        event
        for event in events
        if _parse(event.start_at_utc) <= now < _parse(event.end_at_utc)
    ]
    if len(active) > 1:
        raise ClanGamesScheduleError(
            "schedule_conflict", "multiple active events are actionable"
        )
    active_idle: ClanGamesScheduleDecision | None = None
    if active:
        event = active[0]
        start = _parse(event.start_at_utc)
        kinds = stored.kinds_by_event.get(event.event_id, frozenset())
        baseline_available = "baseline" in kinds
        if now == start and not baseline_available:
            baseline_slot = start - cadence
            return _due(
                "baseline_due",
                event,
                "baseline",
                baseline_slot,
                baseline_available=False,
            )
        elapsed = now - start
        slot_number = int(elapsed.total_seconds() // cadence.total_seconds())
        periodic_slot = start + slot_number * cadence
        periodic_id = deterministic_scan_id(
            event.event_id, "periodic", periodic_slot
        )
        missed = not baseline_available and now > start
        if periodic_id not in stored.ids:
            return _due(
                "periodic_due",
                event,
                "periodic",
                periodic_slot,
                baseline_available=baseline_available,
                baseline_missed=missed,
            )
        if missed:
            active_idle = ClanGamesScheduleDecision(
                "baseline_missed",
                "baseline_missed",
                event_id=event.event_id,
                baseline_available=False,
                baseline_missed=True,
            )
        else:
            active_idle = ClanGamesScheduleDecision(
                "no_scan_due",
                "no_scan_due",
                event_id=event.event_id,
                baseline_available=True,
            )

    upcoming = sorted(
        (event for event in events if _parse(event.start_at_utc) > now),
        key=lambda event: event.start_at_utc,
    )
    if upcoming:
        event = upcoming[0]
        start = _parse(event.start_at_utc)
        baseline_slot = start - cadence
        baseline_available = "baseline" in stored.kinds_by_event.get(
            event.event_id, frozenset()
        )
        if baseline_slot <= now <= start and not baseline_available:
            return _due(
                "baseline_due",
                event,
                "baseline",
                baseline_slot,
                baseline_available=False,
            )

    if active_idle is not None:
        return active_idle

    started = sorted(
        (event for event in events if _parse(event.start_at_utc) <= now),
        key=lambda event: event.start_at_utc,
    )
    if started:
        latest = started[-1]
        end = _parse(latest.end_at_utc)
        if now >= end:
            kinds = stored.kinds_by_event.get(latest.event_id, frozenset())
            baseline_available = "baseline" in kinds
            if "final" not in kinds:
                return _due(
                    "final_due",
                    latest,
                    "final",
                    end,
                    baseline_available=baseline_available,
                    baseline_missed=not baseline_available,
                )
            return ClanGamesScheduleDecision(
                "event_complete",
                "event_complete",
                event_id=latest.event_id,
                baseline_available=baseline_available,
                baseline_missed=not baseline_available,
            )
    return ClanGamesScheduleDecision("no_scan_due", "no_scan_due")
