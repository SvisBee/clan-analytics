"""Pure normalization for small, fictional official-shaped API fixtures.

Wire names and basic Swagger types used here were verified on 2026-07-19.
Requiredness, nullability, enum values, and the API base URL remain unverified.
Project-level invariants make malformed local fixtures fail with useful errors.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from .models import (
    ClanMemberSnapshot,
    ClanSnapshot,
    PlayerProfileSnapshot,
    SourceMetadata,
    WarAttackSnapshot,
    WarLogEntrySnapshot,
    WarLogSideSnapshot,
    WarLogSnapshot,
    WarMemberSnapshot,
    WarSnapshot,
)


class NormalizationError(ValueError):
    """Raised when an API-shaped fixture violates a project invariant."""


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise NormalizationError(f"{path} must be an object")
    return value


def _sequence(value: Any, path: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise NormalizationError(f"{path} must be an array")
    return value


def _required_string(payload: Mapping[str, Any], key: str, path: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise NormalizationError(f"{path}.{key} must be a non-empty string")
    return value.strip()


def _optional_string(payload: Mapping[str, Any], key: str, path: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise NormalizationError(f"{path}.{key} must be a string or null")
    return value.strip() or None


def _optional_int(payload: Mapping[str, Any], key: str, path: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise NormalizationError(f"{path}.{key} must be an integer or null")
    return value


def _optional_non_negative_int(
    payload: Mapping[str, Any], key: str, path: str
) -> int | None:
    value = _optional_int(payload, key, path)
    if value is not None and value < 0:
        raise NormalizationError(f"{path}.{key} must be zero or greater")
    return value


def _optional_positive_int(
    payload: Mapping[str, Any], key: str, path: str
) -> int | None:
    value = _optional_int(payload, key, path)
    if value is not None and value < 1:
        raise NormalizationError(f"{path}.{key} must be one or greater")
    return value



def _optional_non_negative_number(
    payload: Mapping[str, Any], key: str, path: str
) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NormalizationError(f"{path}.{key} must be a number or null")
    numeric = float(value)
    if numeric < 0:
        raise NormalizationError(f"{path}.{key} must be zero or greater")
    return numeric

def _stars(payload: Mapping[str, Any], path: str) -> int:
    value = payload.get("stars")
    if isinstance(value, bool) or not isinstance(value, int):
        raise NormalizationError(f"{path}.stars must be an integer")
    if not 0 <= value <= 3:
        raise NormalizationError(f"{path}.stars must be between 0 and 3")
    return value


def _source(
    *, collected_at: str, raw_source_reference: str, source_timestamp: str | None
) -> SourceMetadata:
    if not isinstance(collected_at, str) or not collected_at.strip():
        raise NormalizationError("collected_at must be a non-empty string")
    if not isinstance(raw_source_reference, str) or not raw_source_reference.strip():
        raise NormalizationError("raw_source_reference must be a non-empty string")
    return SourceMetadata(source_timestamp, collected_at, raw_source_reference)


def normalize_clan_members(
    payload: Sequence[Any],
    *,
    collected_at: str,
    raw_source_reference: str,
    source_timestamp: str | None = None,
) -> tuple[ClanMemberSnapshot, ...]:
    """Normalize and sort an API-shaped member array by internal player tag."""

    items = _sequence(payload, "memberList")
    source = _source(
        collected_at=collected_at,
        raw_source_reference=raw_source_reference,
        source_timestamp=source_timestamp,
    )
    members: list[ClanMemberSnapshot] = []
    seen_tags: set[str] = set()

    for index, raw_member in enumerate(items):
        path = f"memberList[{index}]"
        member = _mapping(raw_member, path)
        player_tag = _required_string(member, "tag", path)
        if player_tag in seen_tags:
            raise NormalizationError(f"{path}.tag duplicates {player_tag}")
        seen_tags.add(player_tag)
        members.append(
            ClanMemberSnapshot(
                player_tag=player_tag,
                display_name=_required_string(member, "name", path),
                clan_role=_optional_string(member, "role", path),
                town_hall_level=_optional_int(member, "townHallLevel", path),
                exp_level=_optional_non_negative_int(member, "expLevel", path),
                clan_rank=_optional_non_negative_int(member, "clanRank", path),
                previous_clan_rank=_optional_non_negative_int(
                    member, "previousClanRank", path
                ),
                donations=_optional_non_negative_int(member, "donations", path),
                donations_received=_optional_non_negative_int(
                    member, "donationsReceived", path
                ),
                trophies=_optional_non_negative_int(member, "trophies", path),
                builder_base_trophies=_optional_non_negative_int(
                    member, "builderBaseTrophies", path
                ),
                source=source,
            )
        )

    return tuple(sorted(members, key=lambda member: member.player_tag))


def normalize_clan(
    payload: Mapping[str, Any], *, collected_at: str, raw_source_reference: str
) -> ClanSnapshot:
    """Normalize the clan subset used by the current roster foundation."""

    clan = _mapping(payload, "clan")
    source_timestamp = _optional_string(clan, "sourceTimestamp", "clan")
    source = _source(
        collected_at=collected_at,
        raw_source_reference=raw_source_reference,
        source_timestamp=source_timestamp,
    )
    members_payload = clan.get("memberList", [])
    return ClanSnapshot(
        clan_tag=_required_string(clan, "tag", "clan"),
        name=_required_string(clan, "name", "clan"),
        level=_optional_int(clan, "clanLevel", "clan"),
        members=normalize_clan_members(
            _sequence(members_payload, "clan.memberList"),
            collected_at=collected_at,
            raw_source_reference=raw_source_reference,
            source_timestamp=source_timestamp,
        ),
        source=source,
    )


def normalize_player_profile(
    payload: Mapping[str, Any], *, collected_at: str, raw_source_reference: str
) -> PlayerProfileSnapshot:
    """Normalize only the profile fields needed by the roster UI."""

    player = _mapping(payload, "player")
    source_timestamp = _optional_string(player, "sourceTimestamp", "player")
    return PlayerProfileSnapshot(
        player_tag=_required_string(player, "tag", "player"),
        display_name=_required_string(player, "name", "player"),
        clan_role=_optional_string(player, "role", "player"),
        town_hall_level=_optional_int(player, "townHallLevel", "player"),
        source=_source(
            collected_at=collected_at,
            raw_source_reference=raw_source_reference,
            source_timestamp=source_timestamp,
        ),
    )


def normalize_war_attacks(payload: Sequence[Any]) -> tuple[WarAttackSnapshot, ...]:
    """Normalize attacks and return them in deterministic battle order."""

    attacks: list[WarAttackSnapshot] = []
    for index, raw_attack in enumerate(_sequence(payload, "attacks")):
        path = f"attacks[{index}]"
        attack = _mapping(raw_attack, path)
        attacks.append(
            WarAttackSnapshot(
                attacker_tag=_required_string(attack, "attackerTag", path),
                defender_tag=_optional_string(attack, "defenderTag", path),
                stars=_stars(attack, path),
                destruction_percentage=_optional_int(
                    attack, "destructionPercentage", path
                ),
                order=_optional_int(attack, "order", path),
            )
        )
    return tuple(
        sorted(
            attacks,
            key=lambda attack: (
                attack.order is None,
                attack.order if attack.order is not None else 0,
                attack.attacker_tag,
                attack.defender_tag or "",
            ),
        )
    )


def normalize_current_war(
    payload: Mapping[str, Any], *, collected_at: str, raw_source_reference: str
) -> WarSnapshot:
    """Normalize a detailed fictional war fixture without network access."""

    war = _mapping(payload, "war")
    state = _required_string(war, "state", "war")
    source_timestamp = _optional_string(war, "sourceTimestamp", "war")
    source = _source(
        collected_at=collected_at,
        raw_source_reference=raw_source_reference,
        source_timestamp=source_timestamp,
    )
    clan = _mapping(war.get("clan", {}), "war.clan")
    raw_members = _sequence(clan.get("members", []), "war.clan.members")
    members: list[WarMemberSnapshot] = []

    for index, raw_member in enumerate(raw_members):
        path = f"war.clan.members[{index}]"
        member = _mapping(raw_member, path)
        attacks_payload = member.get("attacks", [])
        members.append(
            WarMemberSnapshot(
                player_tag=_required_string(member, "tag", path),
                display_name=_required_string(member, "name", path),
                town_hall_level=_optional_int(member, "townhallLevel", path),
                map_position=_optional_positive_int(member, "mapPosition", path),
                attacks=normalize_war_attacks(_sequence(attacks_payload, f"{path}.attacks")),
            )
        )

    tags = [member.player_tag for member in members]
    if len(tags) != len(set(tags)):
        raise NormalizationError("war.clan.members contains duplicate player tags")

    positions = [member.map_position for member in members if member.map_position is not None]
    if len(positions) != len(set(positions)):
        raise NormalizationError("war.clan.members contains duplicate map positions")

    return WarSnapshot(
        state=state,
        preparation_start_time=_optional_string(
            war, "preparationStartTime", "war"
        ),
        start_time=_optional_string(war, "startTime", "war"),
        end_time=_optional_string(war, "endTime", "war"),
        team_size=_optional_non_negative_int(war, "teamSize", "war"),
        attacks_per_member=_optional_int(war, "attacksPerMember", "war"),
        members=tuple(
            sorted(
                members,
                key=lambda member: (
                    member.map_position is None,
                    member.map_position if member.map_position is not None else 0,
                    member.player_tag,
                ),
            )
        ),
        source=source,
    )




def _normalize_war_log_side(
    payload: Mapping[str, Any],
    *,
    path: str,
) -> WarLogSideSnapshot:
    side = _mapping(payload, path)
    return WarLogSideSnapshot(
        clan_tag=_optional_string(side, "tag", path),
        name=_optional_string(side, "name", path),
        stars=_optional_non_negative_int(side, "stars", path),
        destruction_percentage=_optional_non_negative_number(
            side, "destructionPercentage", path
        ),
        attacks=_optional_non_negative_int(side, "attacks", path),
    )


def normalize_war_log(
    payload: Mapping[str, Any],
    *,
    collected_at: str,
    raw_source_reference: str,
) -> WarLogSnapshot:
    """Normalize the verified war-log fields while preserving response order."""

    war_log = _mapping(payload, "warLog")
    source = _source(
        collected_at=collected_at,
        raw_source_reference=raw_source_reference,
        source_timestamp=None,
    )
    raw_items = _sequence(war_log.get("items", []), "warLog.items")
    entries: list[WarLogEntrySnapshot] = []

    for index, raw_entry in enumerate(raw_items):
        path = f"warLog.items[{index}]"
        entry = _mapping(raw_entry, path)
        entries.append(
            WarLogEntrySnapshot(
                end_time=_optional_string(entry, "endTime", path),
                result=_optional_string(entry, "result", path),
                team_size=_optional_non_negative_int(entry, "teamSize", path),
                attacks_per_member=_optional_non_negative_int(
                    entry, "attacksPerMember", path
                ),
                battle_modifier=_optional_string(entry, "battleModifier", path),
                clan=_normalize_war_log_side(
                    _mapping(entry.get("clan", {}), f"{path}.clan"),
                    path=f"{path}.clan",
                ),
                opponent=_normalize_war_log_side(
                    _mapping(entry.get("opponent", {}), f"{path}.opponent"),
                    path=f"{path}.opponent",
                ),
                source=source,
            )
        )

    return WarLogSnapshot(entries=tuple(entries), source=source)


def _date_only(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) >= 8 and value[:8].isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _member_war_metrics(
    player_tag: str, wars: Iterable[WarSnapshot]
) -> dict[str, Any]:
    participations = []
    for war in wars:
        member = next(
            (member for member in war.members if member.player_tag == player_tag), None
        )
        if member is not None:
            participations.append((war, member))

    if not participations:
        return {
            "data_status": "insufficient_data",
            "war_participations": 0,
            "attacks_used": None,
            "attacks_available": None,
            "stars_earned": None,
            "average_stars": None,
            "last_war_date": None,
        }

    attacks_used = sum(len(member.attacks) for _, member in participations)
    known_available = [
        war.attacks_per_member
        for war, _ in participations
        if war.attacks_per_member is not None
    ]
    stars_earned = sum(
        attack.stars for _, member in participations for attack in member.attacks
    )
    dates = [date for war, _ in participations if (date := _date_only(war.end_time))]
    return {
        "data_status": "available",
        "war_participations": len(participations),
        "attacks_used": attacks_used,
        "attacks_available": (
            sum(known_available)
            if len(known_available) == len(participations)
            else None
        ),
        "stars_earned": stars_earned,
        "average_stars": round(stars_earned / attacks_used, 2)
        if attacks_used
        else None,
        "last_war_date": max(dates) if dates else None,
    }


def build_public_roster(
    clan: ClanSnapshot,
    wars: Iterable[WarSnapshot] = (),
    *,
    local_public_profiles: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build an allowlist-only roster without publishing player tags."""

    war_history = tuple(wars)
    profiles = local_public_profiles or {}
    public_members = []

    for member in clan.members:
        item: dict[str, Any] = {
            "nickname": member.display_name,
            "clan_role": member.clan_role,
            "town_hall_level": member.town_hall_level,
            **_member_war_metrics(member.player_tag, war_history),
        }
        local_profile = profiles.get(member.player_tag, {})
        if local_profile.get("telegram_public_consent") is True:
            telegram_username = local_profile.get("telegram_username")
            if isinstance(telegram_username, str) and telegram_username.strip():
                item["telegram_username"] = telegram_username.strip()
        public_members.append(item)

    members_with_war_data = sum(
        member["data_status"] == "available"
        for member in public_members
    )

    return {
        "clan": {
            "name": clan.name,
            "level": clan.level,
        },
        "war_data_coverage": {
            "members_with_data": members_with_war_data,
            "members_without_data": len(public_members) - members_with_war_data,
        },
        "members": public_members,
    }


def build_public_war_summary(war: WarSnapshot) -> dict[str, Any]:
    """Build a neutral aggregate without player tags or performance labels."""

    attacks = [attack for member in war.members for attack in member.attacks]
    attacks_available = (
        len(war.members) * war.attacks_per_member
        if war.attacks_per_member is not None
        else None
    )
    return {
        "state": war.state,
        "end_time": _date_only(war.end_time),
        "participants": len(war.members),
        "attacks_used": len(attacks),
        "attacks_available": attacks_available,
        "stars_earned": sum(attack.stars for attack in attacks),
    }




def build_public_war_log_summary(war_log: WarLogSnapshot) -> dict[str, Any]:
    """Build neutral aggregate history without clan names or game tags."""

    dates = [
        parsed
        for entry in war_log.entries
        if (parsed := _date_only(entry.end_time)) is not None
    ]
    result_counts: dict[str, int] = {}
    team_size_counts: dict[int, int] = {}

    for entry in war_log.entries:
        if entry.result is not None:
            result_counts[entry.result] = result_counts.get(entry.result, 0) + 1
        if entry.team_size is not None:
            team_size_counts[entry.team_size] = (
                team_size_counts.get(entry.team_size, 0) + 1
            )

    return {
        "data_status": "available" if war_log.entries else "empty",
        "wars_observed": len(war_log.entries),
        "date_range": {
            "oldest": min(dates) if dates else None,
            "newest": max(dates) if dates else None,
        },
        "result_distribution": [
            {"result": result, "wars": result_counts[result]}
            for result in sorted(result_counts)
        ],
        "team_size_distribution": [
            {"team_size": team_size, "wars": team_size_counts[team_size]}
            for team_size in sorted(team_size_counts)
        ],
    }


def build_composition_summary(clan: ClanSnapshot) -> dict[str, Any]:
    """Build non-ranking roster coverage and Town Hall aggregates."""

    counts: dict[int, int] = {}
    limited_data = 0
    for member in clan.members:
        if member.town_hall_level is None or member.clan_role is None:
            limited_data += 1
        if member.town_hall_level is not None:
            counts[member.town_hall_level] = counts.get(member.town_hall_level, 0) + 1

    return {
        "total_members": len(clan.members),
        "town_hall_distribution": [
            {"town_hall_level": level, "members": counts[level]}
            for level in sorted(counts, reverse=True)
        ],
        "members_with_limited_composition_data": limited_data,
    }
