"""Privacy-safe in-memory public projection for weekly donations.

This Phase 3 module does not read SQLite, write JSON, call the API, or integrate
with the production builder. Private identities exist only long enough to join
derived weekly rows to the current public roster.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .donations_weekly import (
    AggregateWeeklyDonations,
    DonationsWeeklyResult,
    PlayerWeeklyDonations,
    TIMEZONE_NAME,
    week_window,
)
from .site_update import SiteUpdateError, _scan_public


SCHEMA_VERSION = 1
SCOPE = "current_roster"
METRIC_SEMANTICS = "confirmed_lower_bound"

_TOP_LEVEL_FIELDS = {
    "schema_version",
    "timezone",
    "scope",
    "metric_semantics",
    "generated_at_utc",
    "latest_observed_at_utc",
    "weeks",
}
_WEEK_FIELDS = {
    "week_id",
    "week_start",
    "week_end",
    "is_current",
    "selection",
    "status",
    "donations_confirmed",
    "donations_received_confirmed",
    "participant_count",
    "contributing_player_count",
    "reset_affected",
    "gap_affected",
    "boundary_ambiguous",
    "players",
}
_PLAYER_FIELDS = {
    "nickname",
    "donations_confirmed",
    "donations_received_confirmed",
    "reset_affected",
    "gap_affected",
    "boundary_ambiguous",
}
_STATUSES = {"complete", "partial", "insufficient_data"}


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


def _validated_roster(
    members: Iterable[CurrentPublicMember],
) -> tuple[CurrentPublicMember, ...]:
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


def _validate_internal_result(result: DonationsWeeklyResult) -> None:
    if not isinstance(result, DonationsWeeklyResult):
        raise DonationsWeeklyPublicError("weekly derivation result has an invalid type")
    if result.timezone_name != TIMEZONE_NAME:
        raise DonationsWeeklyPublicError("weekly derivation timezone is invalid")
    aggregate_ids: set[str] = set()
    for week in result.weeks:
        if not isinstance(week, AggregateWeeklyDonations) or week.week_id in aggregate_ids:
            raise DonationsWeeklyPublicError("weekly aggregate sequence is invalid")
        if week.status not in _STATUSES:
            raise DonationsWeeklyPublicError("weekly aggregate status is invalid")
        if not isinstance(week.is_current, bool):
            raise DonationsWeeklyPublicError("weekly aggregate current flag is invalid")
        _local_text(week.week_start_local, "week_start_local")
        _local_text(week.week_end_local, "week_end_local")
        aggregate_ids.add(week.week_id)
    player_keys: set[tuple[str, str]] = set()
    for item in result.player_weeks:
        if not isinstance(item, PlayerWeeklyDonations):
            raise DonationsWeeklyPublicError("player weekly result has an invalid type")
        if not isinstance(item.player_id_internal, str) or not item.player_id_internal:
            raise DonationsWeeklyPublicError("player weekly identity is invalid")
        if item.status not in _STATUSES:
            raise DonationsWeeklyPublicError("player weekly status is invalid")
        key = (item.player_id_internal, item.week_id)
        if key in player_keys:
            raise DonationsWeeklyPublicError("player weekly result is duplicated")
        player_keys.add(key)
        _non_negative_integer(item.donations_confirmed, "donations_confirmed")
        _non_negative_integer(
            item.donations_received_confirmed,
            "donations_received_confirmed",
        )


def _public_players(
    result: DonationsWeeklyResult,
    members_by_identity: Mapping[str, CurrentPublicMember],
    week_id: str,
) -> list[dict[str, Any]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for item in result.player_weeks:
        member = members_by_identity.get(item.player_id_internal)
        if item.week_id != week_id or member is None:
            continue
        rows.append(
            (
                item.player_id_internal,
                {
                    "nickname": member.nickname,
                    "donations_confirmed": item.donations_confirmed,
                    "donations_received_confirmed": item.donations_received_confirmed,
                    "reset_affected": item.reset_affected,
                    "gap_affected": item.gap_affected,
                    "boundary_ambiguous": item.boundary_ambiguous,
                },
            )
        )
    rows.sort(
        key=lambda pair: (
            -pair[1]["donations_confirmed"],
            -pair[1]["donations_received_confirmed"],
            pair[1]["nickname"].casefold(),
            pair[0],
        )
    )
    return [row for _, row in rows]


def _public_week(
    *,
    week_id: str,
    week_start: datetime,
    week_end: datetime,
    is_current: bool,
    selection: str,
    status: str,
    players: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "week_id": week_id,
        "week_start": _local_text(week_start, "week_start"),
        "week_end": _local_text(week_end, "week_end"),
        "is_current": is_current,
        "selection": selection,
        "status": status,
        "donations_confirmed": sum(row["donations_confirmed"] for row in players),
        "donations_received_confirmed": sum(
            row["donations_received_confirmed"] for row in players
        ),
        "participant_count": len(players),
        "contributing_player_count": sum(
            row["donations_confirmed"] > 0
            or row["donations_received_confirmed"] > 0
            for row in players
        ),
        "reset_affected": any(row["reset_affected"] for row in players),
        "gap_affected": any(row["gap_affected"] for row in players),
        "boundary_ambiguous": any(row["boundary_ambiguous"] for row in players),
        "players": players,
    }


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
    result: DonationsWeeklyResult,
    current_roster: Iterable[CurrentPublicMember],
    *,
    generated_at_utc: datetime,
    as_of_utc: datetime,
    latest_observed_at_utc: datetime | None,
) -> dict[str, Any]:
    """Build an allowlist-only current-roster projection in memory."""

    generated = _aware_utc(generated_at_utc, "generated_at_utc")
    as_of = _aware_utc(as_of_utc, "as_of_utc")
    latest = (
        _aware_utc(latest_observed_at_utc, "latest_observed_at_utc")
        if latest_observed_at_utc is not None
        else None
    )
    _validate_internal_result(result)
    roster = _validated_roster(current_roster)
    members_by_identity = {member.player_id_internal: member for member in roster}

    current_window = week_window(as_of, as_of_utc=as_of)
    aggregates = {week.week_id: week for week in result.weeks}
    current_aggregate = aggregates.get(current_window.week_id)
    current_players = _public_players(result, members_by_identity, current_window.week_id)
    weeks = [
        _public_week(
            week_id=current_window.week_id,
            week_start=(
                current_aggregate.week_start_local
                if current_aggregate is not None
                else current_window.week_start_local
            ),
            week_end=(
                current_aggregate.week_end_local
                if current_aggregate is not None
                else current_window.week_end_local
            ),
            is_current=True,
            selection="current",
            status="partial",
            players=current_players,
        )
    ]

    completed = sorted(
        (
            week
            for week in result.weeks
            if week.week_start_utc < current_window.week_start_utc
            and week.status != "insufficient_data"
        ),
        key=lambda week: week.week_start_utc,
        reverse=True,
    )
    for week in completed:
        players = _public_players(result, members_by_identity, week.week_id)
        if not players:
            continue
        weeks.append(
            _public_week(
                week_id=week.week_id,
                week_start=week.week_start_local,
                week_end=week.week_end_local,
                is_current=False,
                selection="previous_usable",
                status=week.status,
                players=players,
            )
        )
        break

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "timezone": TIMEZONE_NAME,
        "scope": SCOPE,
        "metric_semantics": METRIC_SEMANTICS,
        "generated_at_utc": _utc_text(generated, "generated_at_utc"),
        "latest_observed_at_utc": (
            _utc_text(latest, "latest_observed_at_utc") if latest is not None else None
        ),
        "weeks": weeks,
    }
    validate_public_weekly_donations(payload)

    private_values = {
        item.player_id_internal for item in result.player_weeks
    } | set(members_by_identity)
    if private_values.intersection(_string_values(payload)):
        raise DonationsWeeklyPublicError("private identity leaked into public projection")
    return payload


def _exact_fields(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    if set(value) != expected:
        raise DonationsWeeklyPublicError(f"{path} fields do not match schema v1")


def _parse_timestamp(value: object, field: str, *, require_utc: bool) -> datetime:
    if not isinstance(value, str):
        raise DonationsWeeklyPublicError(f"{field} must be a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DonationsWeeklyPublicError(f"{field} must be a timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DonationsWeeklyPublicError(f"{field} must be timezone-aware")
    if require_utc and (not value.endswith("Z") or parsed.utcoffset() != timezone.utc.utcoffset(None)):
        raise DonationsWeeklyPublicError(f"{field} must be canonical UTC")
    return parsed


def validate_public_weekly_donations(payload: Mapping[str, Any]) -> None:
    """Fail closed unless ``payload`` exactly matches public schema v1."""

    if not isinstance(payload, Mapping):
        raise DonationsWeeklyPublicError("public weekly payload must be an object")
    _exact_fields(payload, _TOP_LEVEL_FIELDS, "payload")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise DonationsWeeklyPublicError("public weekly schema version is unsupported")
    if payload["timezone"] != TIMEZONE_NAME:
        raise DonationsWeeklyPublicError("public weekly timezone is invalid")
    if payload["scope"] != SCOPE or payload["metric_semantics"] != METRIC_SEMANTICS:
        raise DonationsWeeklyPublicError("public weekly semantics are invalid")
    _parse_timestamp(payload["generated_at_utc"], "generated_at_utc", require_utc=True)
    latest = payload["latest_observed_at_utc"]
    if latest is not None:
        _parse_timestamp(latest, "latest_observed_at_utc", require_utc=True)

    weeks = payload["weeks"]
    if not isinstance(weeks, list) or not 1 <= len(weeks) <= 2:
        raise DonationsWeeklyPublicError("public weekly payload must contain one or two weeks")
    parsed_starts = []
    for index, week in enumerate(weeks):
        path = f"weeks[{index}]"
        if not isinstance(week, Mapping):
            raise DonationsWeeklyPublicError(f"{path} must be an object")
        _exact_fields(week, _WEEK_FIELDS, path)
        if not isinstance(week["week_id"], str) or re.fullmatch(
            r"\d{4}-W\d{2}", week["week_id"]
        ) is None:
            raise DonationsWeeklyPublicError(f"{path}.week_id is invalid")
        start = _parse_timestamp(week["week_start"], f"{path}.week_start", require_utc=False)
        end = _parse_timestamp(week["week_end"], f"{path}.week_end", require_utc=False)
        if end <= start:
            raise DonationsWeeklyPublicError(f"{path} boundaries are invalid")
        if end - start != timedelta(days=7) or start.weekday() != 0 or any(
            (start.hour, start.minute, start.second, start.microsecond)
        ):
            raise DonationsWeeklyPublicError(f"{path} boundaries do not define a calendar week")
        iso_year, iso_week, _ = start.date().isocalendar()
        if week["week_id"] != f"{iso_year}-W{iso_week:02d}":
            raise DonationsWeeklyPublicError(f"{path}.week_id does not match boundaries")
        parsed_starts.append(start)
        if not isinstance(week["is_current"], bool):
            raise DonationsWeeklyPublicError(f"{path}.is_current is invalid")
        if week["selection"] not in {"current", "previous_usable"}:
            raise DonationsWeeklyPublicError(f"{path}.selection is invalid")
        if week["status"] not in _STATUSES:
            raise DonationsWeeklyPublicError(f"{path}.status is invalid")
        for field in (
            "donations_confirmed",
            "donations_received_confirmed",
            "participant_count",
            "contributing_player_count",
        ):
            _non_negative_integer(week[field], f"{path}.{field}")
        for field in ("reset_affected", "gap_affected", "boundary_ambiguous"):
            if not isinstance(week[field], bool):
                raise DonationsWeeklyPublicError(f"{path}.{field} is invalid")

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
            for field in ("donations_confirmed", "donations_received_confirmed"):
                _non_negative_integer(player[field], f"{player_path}.{field}")
            for field in ("reset_affected", "gap_affected", "boundary_ambiguous"):
                if not isinstance(player[field], bool):
                    raise DonationsWeeklyPublicError(f"{player_path}.{field} is invalid")

        public_order = [
            (
                -player["donations_confirmed"],
                -player["donations_received_confirmed"],
                player["nickname"].casefold(),
            )
            for player in players
        ]
        if public_order != sorted(public_order):
            raise DonationsWeeklyPublicError(f"{path}.players ordering is invalid")

        if week["participant_count"] != len(players):
            raise DonationsWeeklyPublicError(f"{path}.participant_count does not match players")
        if week["donations_confirmed"] != sum(
            player["donations_confirmed"] for player in players
        ) or week["donations_received_confirmed"] != sum(
            player["donations_received_confirmed"] for player in players
        ):
            raise DonationsWeeklyPublicError(f"{path} totals do not match players")
        contributing = sum(
            player["donations_confirmed"] > 0
            or player["donations_received_confirmed"] > 0
            for player in players
        )
        if week["contributing_player_count"] != contributing:
            raise DonationsWeeklyPublicError(
                f"{path}.contributing_player_count does not match players"
            )
        for field in ("reset_affected", "gap_affected", "boundary_ambiguous"):
            if week[field] != any(player[field] for player in players):
                raise DonationsWeeklyPublicError(f"{path}.{field} does not match players")

    if weeks[0]["selection"] != "current" or weeks[0]["is_current"] is not True:
        raise DonationsWeeklyPublicError("first selected week must be current")
    if weeks[0]["status"] != "partial":
        raise DonationsWeeklyPublicError("current week must be partial")
    if len(weeks) == 2:
        if weeks[1]["selection"] != "previous_usable" or weeks[1]["is_current"] is not False:
            raise DonationsWeeklyPublicError("second selected week must be previous usable")
        if weeks[1]["status"] == "insufficient_data":
            raise DonationsWeeklyPublicError("previous selected week must be usable")
        if parsed_starts[1] >= parsed_starts[0]:
            raise DonationsWeeklyPublicError("previous selected week is not earlier")

    try:
        _scan_public(payload, "$.donations_weekly")
    except SiteUpdateError as error:
        raise DonationsWeeklyPublicError("public weekly payload failed privacy validation") from error
    if any(value.startswith("#") for value in _string_values(payload)):
        raise DonationsWeeklyPublicError("public weekly payload contains a private marker")
