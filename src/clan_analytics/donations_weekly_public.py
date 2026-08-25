"""Privacy-safe raw counter projection for weekly donations schema v2.

The public metric does not derive counter deltas. Current values are the
latest confirmed raw game counters for current roster members; previous values
are their last confirmed raw counters observed in the preceding Moscow week.
Private identity exists only for the in-memory join.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .donations_weekly import DEFAULT_GAP_THRESHOLD, DonationObservation, TIMEZONE_NAME, week_window
from .site_update import SiteUpdateError, _scan_public


SCHEMA_VERSION = 2
SCOPE = "current_roster"
METRIC_SEMANTICS = "game_counter_snapshot"
_TOP_LEVEL_FIELDS = {"schema_version", "timezone", "scope", "metric_semantics", "weeks"}
_WEEK_FIELDS = {
    "week_id", "week_start", "week_end", "selection", "status",
    "snapshot_at_utc", "donations", "donations_received", "participant_count",
    "contributing_player_count", "coverage", "players",
}
_COVERAGE_FIELDS = {
    "stale_end_snapshot", "stale_player_count", "missing_player_count",
    "insufficient_data", "reset_observed",
}
_PLAYER_FIELDS = {"nickname", "donations", "donations_received"}
_STATUSES = {"current", "recorded", "partial"}


class DonationsWeeklyPublicError(ValueError):
    """The public weekly projection or one of its inputs is invalid."""


@dataclass(frozen=True)
class CurrentPublicMember:
    """One current roster identity and its allowlisted public display name."""

    player_id_internal: str
    nickname: str


def _aware_utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DonationsWeeklyPublicError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _utc_text(value: datetime, field: str) -> str:
    return _aware_utc(value, field).isoformat().replace("+00:00", "Z")


def _local_text(value: datetime, field: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DonationsWeeklyPublicError(f"{field} must be timezone-aware")
    return value.isoformat()


def _non_negative_integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DonationsWeeklyPublicError(f"{field} must be a non-negative integer")
    return value


def _validated_roster(members: Iterable[CurrentPublicMember]) -> tuple[CurrentPublicMember, ...]:
    output = []
    identities: set[str] = set()
    for member in members:
        if not isinstance(member, CurrentPublicMember):
            raise DonationsWeeklyPublicError("current roster member has an invalid type")
        if not isinstance(member.player_id_internal, str) or not member.player_id_internal:
            raise DonationsWeeklyPublicError("current roster identity is invalid")
        if member.player_id_internal in identities:
            raise DonationsWeeklyPublicError("current roster identity is duplicated")
        if not isinstance(member.nickname, str) or not member.nickname.strip():
            raise DonationsWeeklyPublicError("current public nickname is invalid")
        identities.add(member.player_id_internal)
        output.append(CurrentPublicMember(member.player_id_internal, member.nickname.strip()))
    return tuple(output)


def _validated_observations(
    observations: Iterable[DonationObservation], *, as_of_utc: datetime
) -> dict[str, tuple[DonationObservation, ...]]:
    grouped: dict[str, list[DonationObservation]] = defaultdict(list)
    seen: set[tuple[str, datetime]] = set()
    for item in observations:
        if not isinstance(item, DonationObservation):
            raise DonationsWeeklyPublicError("donation observation has an invalid type")
        if not isinstance(item.player_id_internal, str) or not item.player_id_internal:
            raise DonationsWeeklyPublicError("donation observation identity is invalid")
        observed_at = _aware_utc(item.observed_at_utc, "observed_at_utc")
        if observed_at > as_of_utc:
            raise DonationsWeeklyPublicError("donation observation is later than as_of_utc")
        key = (item.player_id_internal, observed_at)
        if key in seen:
            raise DonationsWeeklyPublicError("donation observation timestamp is duplicated")
        seen.add(key)
        for field, value in (("donations", item.donations), ("donations_received", item.donations_received)):
            if value is not None:
                _non_negative_integer(value, field)
        grouped[item.player_id_internal].append(item)
    return {
        identity: tuple(sorted(items, key=lambda item: item.observed_at_utc))
        for identity, items in grouped.items()
    }


def _public_rows(
    selected: Mapping[str, DonationObservation], roster: tuple[CurrentPublicMember, ...]
) -> list[dict[str, Any]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for member in roster:
        item = selected.get(member.player_id_internal)
        if item is None or item.donations is None or item.donations_received is None:
            continue
        rows.append((member.player_id_internal, {
            "nickname": member.nickname,
            "donations": item.donations,
            "donations_received": item.donations_received,
        }))
    rows.sort(key=lambda pair: (
        -pair[1]["donations"], -pair[1]["donations_received"],
        pair[1]["nickname"].casefold(), pair[0],
    ))
    return [row for _, row in rows]


def _week_payload(
    *, window: Any, selection: str, status: str,
    snapshot_at_utc: datetime | None, players: list[dict[str, Any]],
    stale_player_count: int, missing_player_count: int, reset_observed: bool,
) -> dict[str, Any]:
    return {
        "week_id": window.week_id,
        "week_start": _local_text(window.week_start_local, "week_start"),
        "week_end": _local_text(window.week_end_local, "week_end"),
        "selection": selection,
        "status": status,
        "snapshot_at_utc": _utc_text(snapshot_at_utc, "snapshot_at_utc") if snapshot_at_utc else None,
        "donations": sum(row["donations"] for row in players),
        "donations_received": sum(row["donations_received"] for row in players),
        "participant_count": len(players),
        "contributing_player_count": sum(row["donations"] > 0 for row in players),
        "coverage": {
            "stale_end_snapshot": stale_player_count > 0,
            "stale_player_count": stale_player_count,
            "missing_player_count": missing_player_count,
            "insufficient_data": missing_player_count > 0 or not players,
            "reset_observed": reset_observed,
        },
        "players": players,
    }


def _counter_decreased(before: DonationObservation, after: DonationObservation) -> bool:
    return any(
        left is not None and right is not None and right < left
        for left, right in (
            (before.donations, after.donations),
            (before.donations_received, after.donations_received),
        )
    )


def _string_values(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for nested in value.values():
            yield from _string_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _string_values(nested)
    elif isinstance(value, str):
        yield value


def build_public_weekly_donations(
    observations: Iterable[DonationObservation],
    current_roster: Iterable[CurrentPublicMember],
    *,
    as_of_utc: datetime,
    freshness_threshold: timedelta = DEFAULT_GAP_THRESHOLD,
    current_raw_counters: Mapping[str, tuple[int | None, int | None]] | None = None,
) -> dict[str, Any]:
    """Build schema v2 directly from confirmed raw counter observations."""

    as_of = _aware_utc(as_of_utc, "as_of_utc")
    if not isinstance(freshness_threshold, timedelta) or freshness_threshold <= timedelta(0):
        raise DonationsWeeklyPublicError("freshness_threshold must be positive")
    roster = _validated_roster(current_roster)
    grouped = _validated_observations(observations, as_of_utc=as_of)
    latest_confirmed_at = max(
        (item.observed_at_utc for items in grouped.values() for item in items),
        default=None,
    )
    current = week_window(as_of, as_of_utc=as_of)
    previous = week_window(current.week_start_utc - timedelta(microseconds=1), as_of_utc=as_of)

    current_selected: dict[str, DonationObservation] = {}
    previous_selected: dict[str, DonationObservation] = {}
    first_current: dict[str, DonationObservation] = {}
    for member in roster:
        items = grouped.get(member.player_id_internal, ())
        if current_raw_counters is not None and member.player_id_internal in current_raw_counters:
            donations, received = current_raw_counters[member.player_id_internal]
            for field, value in (("donations", donations), ("donations_received", received)):
                if value is not None:
                    _non_negative_integer(value, f"current_raw_counters.{field}")
            current_selected[member.player_id_internal] = DonationObservation(
                member.player_id_internal,
                as_of,
                donations,
                received,
                "same-run-current-snapshot",
            )
        elif items and items[-1].observed_at_utc == latest_confirmed_at:
            current_selected[member.player_id_internal] = items[-1]
        prior = [item for item in items if previous.week_start_utc <= item.observed_at_utc < previous.week_end_utc]
        if prior:
            previous_selected[member.player_id_internal] = prior[-1]
        after_boundary = [item for item in items if current.week_start_utc <= item.observed_at_utc <= as_of]
        if after_boundary:
            first_current[member.player_id_internal] = after_boundary[0]

    missing_current = [
        member.player_id_internal for member in roster
        if member.player_id_internal not in current_selected
        or current_selected[member.player_id_internal].donations is None
        or current_selected[member.player_id_internal].donations_received is None
    ]
    if missing_current:
        raise DonationsWeeklyPublicError("latest raw counters are unavailable for a current roster member")

    current_players = _public_rows(current_selected, roster)
    previous_players = _public_rows(previous_selected, roster)
    previous_missing = len(roster) - len(previous_players)
    roster_ids = {member.player_id_internal for member in roster}
    stale_count = sum(
        previous.week_end_utc - item.observed_at_utc > freshness_threshold
        for identity, item in previous_selected.items()
        if identity in roster_ids and item.donations is not None and item.donations_received is not None
    )
    reset_observed = any(
        identity in first_current and _counter_decreased(item, first_current[identity])
        for identity, item in previous_selected.items()
    )
    previous_snapshot = max(
        (
            item.observed_at_utc
            for item in previous_selected.values()
            if item.donations is not None and item.donations_received is not None
        ),
        default=None,
    )

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "timezone": TIMEZONE_NAME,
        "scope": SCOPE,
        "metric_semantics": METRIC_SEMANTICS,
        "weeks": [
            _week_payload(
                window=current, selection="current", status="current",
                snapshot_at_utc=None, players=current_players,
                stale_player_count=0, missing_player_count=0,
                reset_observed=reset_observed,
            ),
            _week_payload(
                window=previous, selection="previous",
                status="recorded" if previous_players and previous_missing == 0 and stale_count == 0 else "partial",
                snapshot_at_utc=previous_snapshot, players=previous_players,
                stale_player_count=stale_count, missing_player_count=previous_missing,
                reset_observed=False,
            ),
        ],
    }
    validate_public_weekly_donations(payload)
    private_values = set(grouped) | {member.player_id_internal for member in roster}
    if private_values.intersection(_string_values(payload)):
        raise DonationsWeeklyPublicError("private identity leaked into public projection")
    return payload


def _exact_fields(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    if set(value) != expected:
        raise DonationsWeeklyPublicError(f"{path} fields do not match schema v2")


def _parse_timestamp(value: object, field: str, *, require_utc: bool) -> datetime:
    if not isinstance(value, str):
        raise DonationsWeeklyPublicError(f"{field} must be a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DonationsWeeklyPublicError(f"{field} must be a timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DonationsWeeklyPublicError(f"{field} must be timezone-aware")
    if require_utc and (not value.endswith("Z") or parsed.utcoffset() != timedelta(0)):
        raise DonationsWeeklyPublicError(f"{field} must be canonical UTC")
    return parsed


def validate_public_weekly_donations(payload: Mapping[str, Any]) -> None:
    """Fail closed unless ``payload`` exactly matches public schema v2."""

    if not isinstance(payload, Mapping):
        raise DonationsWeeklyPublicError("public weekly payload must be an object")
    _exact_fields(payload, _TOP_LEVEL_FIELDS, "payload")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise DonationsWeeklyPublicError("public weekly schema version is unsupported")
    if payload["timezone"] != TIMEZONE_NAME:
        raise DonationsWeeklyPublicError("public weekly timezone is invalid")
    if payload["scope"] != SCOPE or payload["metric_semantics"] != METRIC_SEMANTICS:
        raise DonationsWeeklyPublicError("public weekly semantics are invalid")

    weeks = payload["weeks"]
    if not isinstance(weeks, list) or not 1 <= len(weeks) <= 2:
        raise DonationsWeeklyPublicError("public weekly payload must contain one or two weeks")
    starts: list[datetime] = []
    for index, week in enumerate(weeks):
        path = f"weeks[{index}]"
        if not isinstance(week, Mapping):
            raise DonationsWeeklyPublicError(f"{path} must be an object")
        _exact_fields(week, _WEEK_FIELDS, path)
        if not isinstance(week["week_id"], str) or re.fullmatch(r"\d{4}-W\d{2}", week["week_id"]) is None:
            raise DonationsWeeklyPublicError(f"{path}.week_id is invalid")
        start = _parse_timestamp(week["week_start"], f"{path}.week_start", require_utc=False)
        end = _parse_timestamp(week["week_end"], f"{path}.week_end", require_utc=False)
        if end - start != timedelta(days=7) or start.weekday() != 0 or any((start.hour, start.minute, start.second, start.microsecond)):
            raise DonationsWeeklyPublicError(f"{path} boundaries do not define a calendar week")
        iso_year, iso_week, _ = start.date().isocalendar()
        if week["week_id"] != f"{iso_year}-W{iso_week:02d}":
            raise DonationsWeeklyPublicError(f"{path}.week_id does not match boundaries")
        starts.append(start)
        if week["selection"] not in {"current", "previous"} or week["status"] not in _STATUSES:
            raise DonationsWeeklyPublicError(f"{path} selection or status is invalid")
        snapshot = week["snapshot_at_utc"]
        if snapshot is not None:
            parsed_snapshot = _parse_timestamp(snapshot, f"{path}.snapshot_at_utc", require_utc=True)
            if not start.astimezone(timezone.utc) <= parsed_snapshot < end.astimezone(timezone.utc):
                raise DonationsWeeklyPublicError(f"{path}.snapshot_at_utc is outside the week")
        for field in ("donations", "donations_received", "participant_count", "contributing_player_count"):
            _non_negative_integer(week[field], f"{path}.{field}")
        coverage = week["coverage"]
        if not isinstance(coverage, Mapping):
            raise DonationsWeeklyPublicError(f"{path}.coverage must be an object")
        _exact_fields(coverage, _COVERAGE_FIELDS, f"{path}.coverage")
        for field in ("stale_end_snapshot", "insufficient_data", "reset_observed"):
            if not isinstance(coverage[field], bool):
                raise DonationsWeeklyPublicError(f"{path}.coverage.{field} is invalid")
        for field in ("stale_player_count", "missing_player_count"):
            _non_negative_integer(coverage[field], f"{path}.coverage.{field}")
        if coverage["stale_end_snapshot"] != (coverage["stale_player_count"] > 0):
            raise DonationsWeeklyPublicError(f"{path}.coverage stale fields disagree")
        players = week["players"]
        if not isinstance(players, list):
            raise DonationsWeeklyPublicError(f"{path}.players must be a list")
        for player_index, player in enumerate(players):
            player_path = f"{path}.players[{player_index}]"
            if not isinstance(player, Mapping):
                raise DonationsWeeklyPublicError(f"{player_path} must be an object")
            _exact_fields(player, _PLAYER_FIELDS, player_path)
            if not isinstance(player["nickname"], str) or not player["nickname"].strip():
                raise DonationsWeeklyPublicError(f"{player_path}.nickname is invalid")
            _non_negative_integer(player["donations"], f"{player_path}.donations")
            _non_negative_integer(player["donations_received"], f"{player_path}.donations_received")
        order = [(-row["donations"], -row["donations_received"], row["nickname"].casefold()) for row in players]
        if order != sorted(order):
            raise DonationsWeeklyPublicError(f"{path}.players ordering is invalid")
        if week["participant_count"] != len(players):
            raise DonationsWeeklyPublicError(f"{path}.participant_count does not match players")
        if week["donations"] != sum(row["donations"] for row in players) or week["donations_received"] != sum(row["donations_received"] for row in players):
            raise DonationsWeeklyPublicError(f"{path} totals do not match players")
        if week["contributing_player_count"] != sum(row["donations"] > 0 for row in players):
            raise DonationsWeeklyPublicError(f"{path}.contributing_player_count does not match players")
        expected_insufficient = coverage["missing_player_count"] > 0 or not players
        if coverage["insufficient_data"] != expected_insufficient:
            raise DonationsWeeklyPublicError(f"{path}.coverage insufficient fields disagree")

    if weeks[0]["selection"] != "current" or weeks[0]["status"] != "current" or weeks[0]["snapshot_at_utc"] is not None:
        raise DonationsWeeklyPublicError("first selected week must be current without a churn timestamp")
    current_coverage = weeks[0]["coverage"]
    if (
        current_coverage["stale_end_snapshot"]
        or current_coverage["stale_player_count"] != 0
        or current_coverage["missing_player_count"] != 0
        or current_coverage["insufficient_data"]
    ):
        raise DonationsWeeklyPublicError("current week coverage is invalid")
    if len(weeks) == 2:
        if weeks[1]["selection"] != "previous" or weeks[1]["status"] not in {"recorded", "partial"}:
            raise DonationsWeeklyPublicError("second selected week must be previous")
        if starts[1] + timedelta(days=7) != starts[0]:
            raise DonationsWeeklyPublicError("previous selected week is not immediately previous")
        previous = weeks[1]
        should_be_recorded = (
            previous["participant_count"] > 0
            and not previous["coverage"]["stale_end_snapshot"]
            and previous["coverage"]["missing_player_count"] == 0
        )
        if (previous["status"] == "recorded") != should_be_recorded:
            raise DonationsWeeklyPublicError("previous status does not match coverage")
        if previous["participant_count"] and previous["snapshot_at_utc"] is None:
            raise DonationsWeeklyPublicError("previous snapshot timestamp is required")
    try:
        _scan_public(payload, "$.donations_weekly")
    except SiteUpdateError as error:
        raise DonationsWeeklyPublicError("public weekly payload failed privacy validation") from error
    if any(value.startswith("#") for value in _string_values(payload)):
        raise DonationsWeeklyPublicError("public weekly payload contains a private marker")
