"""Local detailed-war history with tag-free public exports.

The persistent history is intentionally outside Git. Stable player tags are kept
only in the internal history so repeated snapshots of the same player can be
joined safely. Public projections never include tags or opponent identity.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from typing import Any, Mapping, Sequence

from .api.models import (
    SourceMetadata,
    WarAttackSnapshot,
    WarMemberSnapshot,
    WarSnapshot,
)


HISTORY_SCHEMA_VERSION = 1


class HistoryError(ValueError):
    """Raised when local history is malformed or cannot be merged safely."""


def empty_history() -> dict[str, Any]:
    return {"schema_version": HISTORY_SCHEMA_VERSION, "wars": []}


def _iso_key(value: str | None) -> str:
    return value or ""


def _date_only(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) >= 8 and value[:8].isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _war_identity_payload(war: WarSnapshot) -> dict[str, Any]:
    return {
        "preparation_start_time": war.preparation_start_time,
        "start_time": war.start_time,
        "end_time": war.end_time,
        "team_size": war.team_size,
        "attacks_per_member": war.attacks_per_member,
        "member_tags": sorted(member.player_tag for member in war.members),
    }


def build_war_id(war: WarSnapshot) -> str:
    """Build a deterministic internal identifier without publishing it."""

    if not war.members:
        raise HistoryError("cannot identify a detailed war without clan members")
    payload = json.dumps(
        _war_identity_payload(war),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_history(history: Mapping[str, Any]) -> None:
    if history.get("schema_version") != HISTORY_SCHEMA_VERSION:
        raise HistoryError("unsupported history schema version")
    wars = history.get("wars")
    if not isinstance(wars, list):
        raise HistoryError("history.wars must be an array")
    seen: set[str] = set()
    for index, record in enumerate(wars):
        if not isinstance(record, Mapping):
            raise HistoryError(f"history.wars[{index}] must be an object")
        war_id = record.get("war_id")
        if not isinstance(war_id, str) or len(war_id) != 64:
            raise HistoryError(f"history.wars[{index}].war_id is invalid")
        if war_id in seen:
            raise HistoryError("history contains duplicate war ids")
        seen.add(war_id)
        if not isinstance(record.get("latest"), Mapping):
            raise HistoryError(f"history.wars[{index}].latest must be an object")


def merge_war_history(
    history: Mapping[str, Any] | None,
    war: WarSnapshot,
) -> tuple[dict[str, Any], bool]:
    """Insert or update one detailed war without creating duplicates."""

    current = empty_history() if history is None else json.loads(json.dumps(history))
    _validate_history(current)

    if war.state == "notInWar" or not war.members:
        return current, False

    war_id = build_war_id(war)
    collected_at = war.source.collected_at
    records: list[dict[str, Any]] = current["wars"]
    existing = next((record for record in records if record["war_id"] == war_id), None)
    latest = json.loads(json.dumps(asdict(war)))

    if existing is None:
        records.append(
            {
                "war_id": war_id,
                "first_collected_at": collected_at,
                "last_collected_at": collected_at,
                "observations": 1,
                "finalized": war.state == "warEnded",
                "states_seen": [war.state],
                "latest": latest,
            }
        )
        changed = True
    else:
        old_collected_at = str(existing.get("last_collected_at") or "")
        if collected_at < old_collected_at:
            return current, False

        old_latest = existing["latest"]
        same_snapshot = old_latest == latest
        same_collection = collected_at == old_collected_at
        if same_snapshot and same_collection:
            return current, False

        existing["last_collected_at"] = collected_at
        if not same_collection:
            existing["observations"] = int(existing.get("observations", 0)) + 1
        states = list(existing.get("states_seen") or [])
        if war.state not in states:
            states.append(war.state)
        existing["states_seen"] = states
        existing["finalized"] = bool(existing.get("finalized")) or war.state == "warEnded"
        # Never replace a final detailed snapshot with a provisional state.
        if not (existing["finalized"] and war.state != "warEnded" and old_latest.get("state") == "warEnded"):
            existing["latest"] = latest
        changed = True

    records.sort(
        key=lambda record: (
            _iso_key(record["latest"].get("end_time")),
            record["war_id"],
        )
    )
    return current, changed


def war_from_dict(payload: Mapping[str, Any]) -> WarSnapshot:
    source_payload = payload.get("source")
    if not isinstance(source_payload, Mapping):
        raise HistoryError("war source is missing")
    source = SourceMetadata(
        source_timestamp=source_payload.get("source_timestamp"),
        collected_at=str(source_payload.get("collected_at") or ""),
        raw_source_reference=str(source_payload.get("raw_source_reference") or ""),
    )

    members_payload = payload.get("members")
    if not isinstance(members_payload, Sequence) or isinstance(members_payload, (str, bytes)):
        raise HistoryError("war members must be an array")

    members: list[WarMemberSnapshot] = []
    for raw_member in members_payload:
        if not isinstance(raw_member, Mapping):
            raise HistoryError("war member must be an object")
        raw_attacks = raw_member.get("attacks")
        if not isinstance(raw_attacks, Sequence) or isinstance(raw_attacks, (str, bytes)):
            raise HistoryError("war attacks must be an array")
        attacks = tuple(
            WarAttackSnapshot(
                attacker_tag=str(attack["attacker_tag"]),
                defender_tag=attack.get("defender_tag"),
                stars=int(attack["stars"]),
                destruction_percentage=attack.get("destruction_percentage"),
                order=attack.get("order"),
            )
            for attack in raw_attacks
        )
        members.append(
            WarMemberSnapshot(
                player_tag=str(raw_member["player_tag"]),
                display_name=str(raw_member["display_name"]),
                town_hall_level=raw_member.get("town_hall_level"),
                map_position=raw_member.get("map_position"),
                attacks=attacks,
            )
        )

    return WarSnapshot(
        state=str(payload["state"]),
        preparation_start_time=payload.get("preparation_start_time"),
        start_time=payload.get("start_time"),
        end_time=payload.get("end_time"),
        team_size=payload.get("team_size"),
        attacks_per_member=payload.get("attacks_per_member"),
        members=tuple(members),
        source=source,
    )


def detailed_wars(history: Mapping[str, Any]) -> tuple[WarSnapshot, ...]:
    _validate_history(history)
    return tuple(war_from_dict(record["latest"]) for record in history["wars"])


def _public_member(member: WarMemberSnapshot, attacks_per_member: int | None) -> dict[str, Any]:
    attacks_used = len(member.attacks)
    stars_earned = sum(attack.stars for attack in member.attacks)
    return {
        "war_position": member.map_position,
        "nickname": member.display_name,
        "town_hall_level": member.town_hall_level,
        "attacks_used": attacks_used,
        "attacks_available": attacks_per_member,
        "stars_earned": stars_earned,
        "average_stars": round(stars_earned / attacks_used, 2) if attacks_used else None,
    }


def build_public_war_history(history: Mapping[str, Any]) -> dict[str, Any]:
    """Build a neutral public history without stable game identifiers."""

    wars = detailed_wars(history)
    public_wars: list[dict[str, Any]] = []
    player_totals: dict[str, dict[str, Any]] = {}

    for war in wars:
        members = [_public_member(member, war.attacks_per_member) for member in war.members]
        members.sort(
            key=lambda member: (
                member["war_position"] is None,
                member["war_position"] if member["war_position"] is not None else 0,
                str(member["nickname"]).casefold(),
            )
        )
        attacks_used = sum(member["attacks_used"] for member in members)
        stars_earned = sum(member["stars_earned"] for member in members)
        attacks_available = (
            len(members) * war.attacks_per_member
            if war.attacks_per_member is not None
            else None
        )
        public_wars.append(
            {
                "state": war.state,
                "end_time": _date_only(war.end_time),
                "team_size": war.team_size,
                "participants": len(members),
                "attacks_per_member": war.attacks_per_member,
                "attacks_used": attacks_used,
                "attacks_available": attacks_available,
                "stars_earned": stars_earned,
                "members": members,
            }
        )

        public_by_position = {
            member["war_position"]: member
            for member in members
            if member["war_position"] is not None
        }
        date = _date_only(war.end_time)
        for internal_member in war.members:
            public_member = (
                public_by_position.get(internal_member.map_position)
                if internal_member.map_position is not None
                else _public_member(internal_member, war.attacks_per_member)
            )
            if public_member is None:
                public_member = _public_member(internal_member, war.attacks_per_member)
            totals = player_totals.setdefault(
                internal_member.player_tag,
                {
                    "nickname": internal_member.display_name,
                    "war_participations": 0,
                    "attacks_used": 0,
                    "attacks_available": 0,
                    "stars_earned": 0,
                    "last_war_date": None,
                },
            )
            totals["war_participations"] += 1
            totals["attacks_used"] += public_member["attacks_used"]
            if public_member["attacks_available"] is not None:
                totals["attacks_available"] += public_member["attacks_available"]
            totals["stars_earned"] += public_member["stars_earned"]
            if date and (totals["last_war_date"] is None or date >= totals["last_war_date"]):
                totals["last_war_date"] = date
                totals["nickname"] = internal_member.display_name

    public_wars.sort(key=lambda item: (_iso_key(item["end_time"]), item["participants"]), reverse=True)
    public_players = []
    for totals in player_totals.values():
        attacks_used = totals["attacks_used"]
        totals["average_stars"] = (
            round(totals["stars_earned"] / attacks_used, 2) if attacks_used else None
        )
        public_players.append(totals)
    public_players.sort(key=lambda item: str(item["nickname"]).casefold())

    return {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "wars_observed": len(public_wars),
        "wars": public_wars,
        "player_metrics": public_players,
    }
