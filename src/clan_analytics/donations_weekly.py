"""Pure weekly donation derivation for confirmed member observations.

The values produced here are confirmed lower bounds: only positive counter
increments that can be assigned unambiguously to one Moscow calendar week are
included. This module has no SQLite, API, public projection, or file-writing
integration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Literal
from zoneinfo import TZPATH, ZoneInfo, ZoneInfoNotFoundError


METRIC_VERSION = "donations_weekly_v1"
TIMEZONE_NAME = "Europe/Moscow"
DEFAULT_GAP_THRESHOLD = timedelta(hours=2)

CounterClassification = Literal[
    "increase", "unchanged", "reset_or_unknown", "unavailable"
]
AttributionStatus = Literal["attributed", "excluded_boundary_ambiguous"]
CompletenessStatus = Literal["complete", "partial", "insufficient_data"]


class DonationsWeeklyError(RuntimeError):
    """Base error for safe weekly donation derivation."""


class InvalidObservationError(DonationsWeeklyError):
    """An observation or derivation boundary is invalid."""


class DuplicateObservationTimeError(DonationsWeeklyError):
    """One internal identity has more than one observation at a timestamp."""


class InvalidMembershipSequenceError(DonationsWeeklyError):
    """A membership segment reappears after a later segment has begun."""


@dataclass(frozen=True)
class DonationObservation:
    """One normalized confirmed counter observation for a private identity."""

    player_id_internal: str
    observed_at_utc: datetime
    donations: int | None
    donations_received: int | None
    membership_segment_id: str


@dataclass(frozen=True)
class WeekWindow:
    """A Monday-based user week in Europe/Moscow and its UTC boundaries."""

    week_id: str
    week_start_local: datetime
    week_end_local: datetime
    week_start_utc: datetime
    week_end_utc: datetime
    is_current: bool


@dataclass(frozen=True)
class DonationTransition:
    """Counter evidence for one consecutive pair in a membership segment."""

    player_id_internal: str
    membership_segment_id: str
    previous_observed_at_utc: datetime
    observed_at_utc: datetime
    donations_classification: CounterClassification
    donations_positive_delta: int
    donations_received_classification: CounterClassification
    donations_received_positive_delta: int
    attribution_status: AttributionStatus
    affected_week_ids: tuple[str, ...]
    gap_affected: bool


@dataclass(frozen=True)
class PlayerWeeklyDonations:
    """Confirmed lower-bound donation evidence for one private player/week."""

    player_id_internal: str
    week_id: str
    week_start_local: datetime
    week_end_local: datetime
    week_start_utc: datetime
    week_end_utc: datetime
    donations_confirmed: int
    donations_received_confirmed: int
    positive_donation_transition_count: int
    positive_received_transition_count: int
    reset_count: int
    unavailable_count: int
    gap_count: int
    boundary_ambiguous_count: int
    observations_used: int
    transition_count: int
    first_observed_at: datetime | None
    last_observed_at: datetime | None
    is_current: bool
    status: CompletenessStatus
    gap_affected: bool
    reset_affected: bool
    boundary_ambiguous: bool


@dataclass(frozen=True)
class AggregateWeeklyDonations:
    """Week aggregate derived exclusively from player/week results."""

    week_id: str
    week_start_local: datetime
    week_end_local: datetime
    week_start_utc: datetime
    week_end_utc: datetime
    donations_confirmed: int
    donations_received_confirmed: int
    participant_count: int
    contributing_player_count: int
    reset_affected_player_count: int
    gap_affected_player_count: int
    boundary_ambiguous_player_count: int
    observation_count: int
    transition_evidence_count: int
    is_current: bool
    status: CompletenessStatus
    gap_affected: bool
    reset_affected: bool
    boundary_ambiguous: bool


@dataclass(frozen=True)
class DonationsWeeklyResult:
    """Deterministically ordered pure derivation output."""

    metric_version: str
    timezone_name: str
    weeks: tuple[AggregateWeeklyDonations, ...]
    player_weeks: tuple[PlayerWeeklyDonations, ...]
    transitions: tuple[DonationTransition, ...]


@dataclass
class _MutablePlayerWeek:
    observations: set[datetime]
    donations_confirmed: int = 0
    donations_received_confirmed: int = 0
    positive_donation_transition_count: int = 0
    positive_received_transition_count: int = 0
    reset_count: int = 0
    unavailable_count: int = 0
    gap_count: int = 0
    boundary_ambiguous_count: int = 0
    transition_count: int = 0


@lru_cache(maxsize=1)
def _moscow_timezone() -> ZoneInfo:
    """Load Europe/Moscow from an installed IANA timezone database."""

    try:
        return ZoneInfo(TIMEZONE_NAME)
    except ZoneInfoNotFoundError:
        roots = [Path(item) for item in TZPATH]
        program_files = os.environ.get("ProgramFiles")
        if program_files:
            roots.append(Path(program_files) / "Git" / "mingw64" / "share" / "zoneinfo")
        for root in roots:
            candidate = root / "Europe" / "Moscow"
            if candidate.is_file():
                with candidate.open("rb") as stream:
                    return ZoneInfo.from_file(stream, key=TIMEZONE_NAME)
    raise DonationsWeeklyError("required timezone database is unavailable")


def _aware_utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidObservationError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _valid_counter(value: int | None) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 0)


def classify_counter_transition(before: int | None, after: int | None) -> CounterClassification:
    """Classify one counter pair without assigning it to a week."""

    if not _valid_counter(before) or not _valid_counter(after):
        raise InvalidObservationError("counter must be a non-negative integer or null")
    if before is None or after is None:
        return "unavailable"
    if after > before:
        return "increase"
    if after == before:
        return "unchanged"
    return "reset_or_unknown"


def _positive_delta(before: int | None, after: int | None) -> int:
    return after - before if before is not None and after is not None and after > before else 0


def _week_start_local(value: datetime) -> datetime:
    local = _aware_utc(value, "timestamp").astimezone(_moscow_timezone())
    monday = local.date() - timedelta(days=local.weekday())
    return datetime.combine(monday, time.min, tzinfo=_moscow_timezone())


def _week_id(start_local: datetime) -> str:
    iso_year, iso_week, _ = start_local.date().isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def week_window(value: datetime, *, as_of_utc: datetime) -> WeekWindow:
    """Return the Moscow calendar week containing ``value``."""

    as_of = _aware_utc(as_of_utc, "as_of_utc")
    start_local = _week_start_local(value)
    end_local = start_local + timedelta(days=7)
    return WeekWindow(
        week_id=_week_id(start_local),
        week_start_local=start_local,
        week_end_local=end_local,
        week_start_utc=start_local.astimezone(timezone.utc),
        week_end_utc=end_local.astimezone(timezone.utc),
        is_current=start_local <= as_of.astimezone(_moscow_timezone()) < end_local,
    )


def _week_windows(start: datetime, end: datetime, *, as_of: datetime) -> tuple[WeekWindow, ...]:
    current = _week_start_local(start)
    final = _week_start_local(end)
    windows = []
    while current <= final:
        windows.append(week_window(current, as_of_utc=as_of))
        current += timedelta(days=7)
    return tuple(windows)


def _validate_observations(
    observations: Iterable[DonationObservation], *, as_of: datetime
) -> tuple[DonationObservation, ...]:
    normalized = []
    for item in observations:
        if not isinstance(item, DonationObservation):
            raise InvalidObservationError("observation has an invalid type")
        if not isinstance(item.player_id_internal, str) or not item.player_id_internal:
            raise InvalidObservationError("internal identity is invalid")
        if not isinstance(item.membership_segment_id, str) or not item.membership_segment_id:
            raise InvalidObservationError("membership segment is invalid")
        if not _valid_counter(item.donations) or not _valid_counter(item.donations_received):
            raise InvalidObservationError("counter must be a non-negative integer or null")
        observed = _aware_utc(item.observed_at_utc, "observed_at_utc")
        if observed > as_of:
            raise InvalidObservationError("observation is later than as_of_utc")
        normalized.append(
            DonationObservation(
                item.player_id_internal,
                observed,
                item.donations,
                item.donations_received,
                item.membership_segment_id,
            )
        )

    normalized.sort(key=lambda item: (item.player_id_internal, item.observed_at_utc))
    prior_key: tuple[str, datetime] | None = None
    prior_player: str | None = None
    active_segment: str | None = None
    closed_segments: set[str] = set()
    for item in normalized:
        key = (item.player_id_internal, item.observed_at_utc)
        if key == prior_key:
            raise DuplicateObservationTimeError("duplicate observation time for one identity")
        prior_key = key
        if item.player_id_internal != prior_player:
            prior_player = item.player_id_internal
            active_segment = None
            closed_segments = set()
        if item.membership_segment_id != active_segment:
            if item.membership_segment_id in closed_segments:
                raise InvalidMembershipSequenceError("membership segment sequence is invalid")
            if active_segment is not None:
                closed_segments.add(active_segment)
            active_segment = item.membership_segment_id
    return tuple(normalized)


def _status(
    *, is_current: bool, observations_used: int, transition_count: int
) -> CompletenessStatus:
    if is_current:
        return "partial"
    if observations_used == 0:
        return "insufficient_data"
    if transition_count == 0:
        return "insufficient_data"
    # A proof threshold for complete coverage is intentionally deferred.
    return "partial"


def derive_weekly_donations(
    observations: Iterable[DonationObservation],
    *,
    as_of_utc: datetime,
    coverage_start_utc: datetime | None = None,
    gap_threshold: timedelta = DEFAULT_GAP_THRESHOLD,
) -> DonationsWeeklyResult:
    """Derive confirmed weekly lower bounds from normalized observations.

    Input order does not affect output. A missing counter does not bridge to a
    later value: both adjacent pairs are unavailable, and the later usable
    value becomes the baseline for only the following pair.
    """

    as_of = _aware_utc(as_of_utc, "as_of_utc")
    if not isinstance(gap_threshold, timedelta) or gap_threshold <= timedelta(0):
        raise InvalidObservationError("gap threshold must be positive")
    normalized = _validate_observations(observations, as_of=as_of)
    coverage_start = (
        _aware_utc(coverage_start_utc, "coverage_start_utc")
        if coverage_start_utc is not None
        else (normalized[0].observed_at_utc if normalized else None)
    )
    if coverage_start is None:
        return DonationsWeeklyResult(METRIC_VERSION, TIMEZONE_NAME, (), (), ())
    if coverage_start > as_of:
        raise InvalidObservationError("coverage start is later than as_of_utc")
    if normalized and coverage_start > min(item.observed_at_utc for item in normalized):
        raise InvalidObservationError("coverage start excludes an observation")

    windows = _week_windows(coverage_start, as_of, as_of=as_of)
    window_by_id = {item.week_id: item for item in windows}
    buckets: dict[tuple[str, str], _MutablePlayerWeek] = {}

    def bucket(player: str, week_id: str) -> _MutablePlayerWeek:
        return buckets.setdefault((player, week_id), _MutablePlayerWeek(set()))

    for item in normalized:
        bucket(item.player_id_internal, _week_id(_week_start_local(item.observed_at_utc))).observations.add(
            item.observed_at_utc
        )

    transitions = []
    previous_by_player: dict[str, DonationObservation] = {}
    for item in normalized:
        previous = previous_by_player.get(item.player_id_internal)
        previous_by_player[item.player_id_internal] = item
        if previous is None or previous.membership_segment_id != item.membership_segment_id:
            continue

        donations_class = classify_counter_transition(previous.donations, item.donations)
        received_class = classify_counter_transition(
            previous.donations_received, item.donations_received
        )
        donations_delta = _positive_delta(previous.donations, item.donations)
        received_delta = _positive_delta(
            previous.donations_received, item.donations_received
        )
        gap = item.observed_at_utc - previous.observed_at_utc > gap_threshold
        affected_windows = _week_windows(
            previous.observed_at_utc, item.observed_at_utc, as_of=as_of
        )
        affected_ids = tuple(window.week_id for window in affected_windows)
        boundary = len(affected_ids) > 1
        attribution: AttributionStatus = (
            "excluded_boundary_ambiguous" if boundary else "attributed"
        )
        transitions.append(
            DonationTransition(
                item.player_id_internal,
                item.membership_segment_id,
                previous.observed_at_utc,
                item.observed_at_utc,
                donations_class,
                donations_delta,
                received_class,
                received_delta,
                attribution,
                affected_ids,
                gap,
            )
        )

        for week_id in affected_ids:
            current = bucket(item.player_id_internal, week_id)
            if boundary:
                current.boundary_ambiguous_count += 1
            else:
                current.transition_count += 1
                current.donations_confirmed += donations_delta
                current.donations_received_confirmed += received_delta
                current.positive_donation_transition_count += int(donations_class == "increase")
                current.positive_received_transition_count += int(received_class == "increase")
            if gap:
                current.gap_count += 1
            if "reset_or_unknown" in (donations_class, received_class):
                current.reset_count += 1
            if "unavailable" in (donations_class, received_class):
                current.unavailable_count += 1

    player_results = []
    for (player, week_id), current in sorted(
        buckets.items(), key=lambda item: (window_by_id[item[0][1]].week_start_utc, item[0][0])
    ):
        window = window_by_id[week_id]
        observations_used = len(current.observations)
        ordered_observations = sorted(current.observations)
        player_results.append(
            PlayerWeeklyDonations(
                player,
                week_id,
                window.week_start_local,
                window.week_end_local,
                window.week_start_utc,
                window.week_end_utc,
                current.donations_confirmed,
                current.donations_received_confirmed,
                current.positive_donation_transition_count,
                current.positive_received_transition_count,
                current.reset_count,
                current.unavailable_count,
                current.gap_count,
                current.boundary_ambiguous_count,
                observations_used,
                current.transition_count,
                ordered_observations[0] if ordered_observations else None,
                ordered_observations[-1] if ordered_observations else None,
                window.is_current,
                _status(
                    is_current=window.is_current,
                    observations_used=observations_used,
                    transition_count=current.transition_count,
                ),
                current.gap_count > 0,
                current.reset_count > 0,
                current.boundary_ambiguous_count > 0,
            )
        )

    aggregates = []
    for window in windows:
        players = [item for item in player_results if item.week_id == window.week_id]
        participant_count = sum(item.observations_used > 0 for item in players)
        transition_count = sum(item.transition_count for item in players)
        aggregates.append(
            AggregateWeeklyDonations(
                window.week_id,
                window.week_start_local,
                window.week_end_local,
                window.week_start_utc,
                window.week_end_utc,
                sum(item.donations_confirmed for item in players),
                sum(item.donations_received_confirmed for item in players),
                participant_count,
                sum(
                    item.donations_confirmed > 0 or item.donations_received_confirmed > 0
                    for item in players
                ),
                sum(item.reset_affected for item in players),
                sum(item.gap_affected for item in players),
                sum(item.boundary_ambiguous for item in players),
                sum(item.observations_used for item in players),
                sum(item.transition_count + item.boundary_ambiguous_count for item in players),
                window.is_current,
                _status(
                    is_current=window.is_current,
                    observations_used=participant_count,
                    transition_count=transition_count,
                ),
                any(item.gap_affected for item in players),
                any(item.reset_affected for item in players),
                any(item.boundary_ambiguous for item in players),
            )
        )

    return DonationsWeeklyResult(
        METRIC_VERSION,
        TIMEZONE_NAME,
        tuple(aggregates),
        tuple(player_results),
        tuple(transitions),
    )
