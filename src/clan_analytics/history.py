"""Recoverable detailed-war history with immutable observations.

Schema v2 separates detailed current-war facts, war-log aggregates, inferred
lifecycle state, and public projections. Stable game tags remain internal.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .api.models import (
    SourceMetadata,
    WarAttackSnapshot,
    WarLogEntrySnapshot,
    WarLogSideSnapshot,
    WarLogSnapshot,
    WarMemberSnapshot,
    WarSnapshot,
)
from .api.normalization import calculate_war_star_metrics


HISTORY_SCHEMA_VERSION = 2
_LIFECYCLE_ACTIVE_STATES = {"preparation", "inWar"}
_DETAILED_STATES = {"preparation", "inWar", "warEnded", "notInWar"}
_LIFECYCLE_STATUSES = {
    "active", "finalized_detailed", "closed_without_final_detailed",
    "closed_war_log_without_final_detail", "closed_war_log_only", "incomplete", "ambiguous",
}
_RECONCILIATION_STATUSES = {
    "not_reconciled", "awaiting_war_log", "matched", "unmatched_aggregate_only", "ambiguous",
}
_RECORD_DIAGNOSTICS = {
    "identity_ambiguous", "not_in_war_ambiguous", "conflicting_attack_observation",
    "regressing_detailed_state_observed", "aggregate_only_matched_late_detail",
    "war_log_match_ambiguous", "no_detailed_snapshot", "regressing_clan_stars_observed",
    "conflicting_final_clan_stars_observed", "identity_timeline_incompatible",
    "identity_game_time_window_incompatible",
}
_TOP_DIAGNOSTIC_STATUSES = {
    "ignored_incomplete_detailed_snapshot", "identity_ambiguous",
    "aggregate_detail_match_ambiguous", "war_log_match_ambiguous",
}
_TIME_FIELDS = ("preparation_start_time", "start_time", "end_time")
# Project heuristic, not an API rule: one ordinary-war lifecycle should not
# join observations whose known game times are separated by weeks or months.
PROGRESSIVE_GAME_TIME_WINDOW = timedelta(days=7)


class HistoryError(ValueError):
    """Raised when local history is malformed or cannot be handled safely."""


def empty_history() -> dict[str, Any]:
    return {"schema_version": HISTORY_SCHEMA_VERSION, "wars": [], "diagnostics": []}


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


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


def _snapshot_payload(war: WarSnapshot) -> dict[str, Any]:
    return _json_copy(asdict(war))


def _observation_content(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    content = _json_copy(snapshot)
    source = content.get("source")
    if isinstance(source, dict):
        source.pop("collected_at", None)
        source.pop("raw_source_reference", None)
        # Source timestamps describe collection/publication provenance, not game facts.
        source.pop("source_timestamp", None)
    return content


def observation_fingerprint(war: WarSnapshot) -> str:
    """Fingerprint facts, excluding collection time and local source path."""

    return hashlib.sha256(
        _canonical_json(_observation_content(_snapshot_payload(war)))
    ).hexdigest()


def _identity_seed(war: WarSnapshot) -> dict[str, Any]:
    times = [
        value
        for value in (
            war.preparation_start_time,
            war.start_time,
            war.end_time,
        )
        if value
    ]
    if times:
        return {"kind": "first_known_time", "value": times[0]}
    tags = sorted(member.player_tag for member in war.members)
    if tags:
        return {
            "kind": "first_observation",
            "collected_at": war.source.collected_at,
            "member_tags": tags,
        }
    raise HistoryError("cannot identify a detailed war without time or members")


def build_war_id(war: WarSnapshot) -> str:
    """Assign a deterministic ID once; later evidence matches without changing it."""

    return hashlib.sha256(_canonical_json(_identity_seed(war))).hexdigest()


def _new_observation(war: WarSnapshot) -> dict[str, Any]:
    snapshot = _snapshot_payload(war)
    return {
        "fingerprint": observation_fingerprint(war),
        "collected_at": war.source.collected_at,
        "snapshot": snapshot,
    }


def _known_times(snapshot: Mapping[str, Any]) -> dict[str, str]:
    return {
        field: str(snapshot[field])
        for field in _TIME_FIELDS
        if snapshot.get(field)
    }


def _member_tags(snapshot: Mapping[str, Any]) -> set[str]:
    members = snapshot.get("members")
    if not isinstance(members, list):
        return set()
    return {
        str(member.get("player_tag"))
        for member in members
        if isinstance(member, Mapping) and member.get("player_tag")
    }


def _timeline_is_compatible(times: Mapping[str, str]) -> bool:
    """Return whether the available lifecycle timestamps are chronologically ordered."""

    try:
        parsed = {
            field: _parse_timestamp(value, f"identity.{field}")
            for field, value in times.items()
        }
    except HistoryError:
        return False
    preparation = parsed.get("preparation_start_time")
    start = parsed.get("start_time")
    end = parsed.get("end_time")
    return not (
        preparation is not None and start is not None and preparation > start
        or start is not None and end is not None and start > end
        or preparation is not None and end is not None and preparation > end
    )


def _identity_evidence(record: Mapping[str, Any], war: WarSnapshot) -> tuple[int, str]:
    canonical = record.get("canonical")
    if not isinstance(canonical, Mapping):
        return (-1, "missing_canonical")
    incoming = _snapshot_payload(war)
    old_times = _known_times(canonical)
    new_times = _known_times(incoming)
    shared_time_fields = set(old_times) & set(new_times)
    if any(old_times[field] != new_times[field] for field in shared_time_fields):
        return (-1, "conflicting_time")
    if not _timeline_is_compatible({**old_times, **new_times}):
        return (-1, "incompatible_timeline")
    if not shared_time_fields and old_times and new_times:
        combined_times = [
            _parse_timestamp(value, "identity.game_time")
            for value in (*old_times.values(), *new_times.values())
        ]
        if max(combined_times) - min(combined_times) > PROGRESSIVE_GAME_TIME_WINDOW:
            return (-1, "incompatible_game_time_window")

    score = 0
    if shared_time_fields:
        score += 100 + 20 * len(shared_time_fields)
    for field in ("team_size", "attacks_per_member"):
        old_value = canonical.get(field)
        new_value = incoming.get(field)
        if old_value is not None and new_value is not None:
            if old_value != new_value:
                # A shared lifecycle timestamp is stronger evidence than an
                # aggregate scalar that may arrive late or regress. Preserve
                # the canonical scalar during merge and record its conflict.
                if not shared_time_fields:
                    return (-1, f"conflicting_{field}")
            else:
                score += 5

    old_tags = _member_tags(canonical)
    new_tags = _member_tags(incoming)
    if old_tags and new_tags:
        overlap = len(old_tags & new_tags)
        if overlap:
            score += min(40, overlap * 2)
        elif shared_time_fields:
            return (-1, "conflicting_members")

    if not shared_time_fields:
        if record.get("lifecycle_status") not in {"active", "incomplete"}:
            return (-1, "no_shared_strong_evidence")
        if not old_tags or not new_tags:
            return (-1, "insufficient_evidence")
        # Roster overlap alone is not a war identity. With no shared timestamp,
        # require an active, recent record and an exact participant set plus
        # compatible known war settings. Distinct known timestamp fields are
        # progressive evidence (for example preparation then start), not proof
        # of a separate war by themselves.
        if old_tags != new_tags:
            return (-1, "insufficient_roster_evidence")
        old_collected = str(record.get("last_collected_at") or "")
        try:
            old_dt = datetime.fromisoformat(old_collected.replace("Z", "+00:00"))
            new_dt = datetime.fromisoformat(war.source.collected_at.replace("Z", "+00:00"))
        except ValueError:
            return (-1, "invalid_collection_time")
        if abs((new_dt - old_dt).total_seconds()) > 3 * 60 * 60:
            return (-1, "stale_roster_evidence")
        if not any(
            canonical.get(field) is not None and incoming.get(field) is not None
            for field in ("team_size", "attacks_per_member")
        ):
            return (-1, "insufficient_war_setting_evidence")
        score += 80 if old_times and new_times else 60
    return (score, "compatible")


def _attack_key(attack: Mapping[str, Any]) -> tuple[Any, ...]:
    order = attack.get("order")
    if type(order) is int and order > 0:
        return ("order", order)
    return (
        "facts",
        attack.get("attacker_tag"),
        attack.get("defender_tag"),
        attack.get("stars"),
        attack.get("destruction_percentage"),
    )


def _merge_attacks(
    old: Sequence[Mapping[str, Any]], new: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    merged = [_json_copy(attack) for attack in old]
    by_key = {_attack_key(attack): attack for attack in merged}
    diagnostics: list[str] = []
    for attack in new:
        key = _attack_key(attack)
        existing = by_key.get(key)
        if existing is None:
            copied = _json_copy(attack)
            merged.append(copied)
            by_key[key] = copied
        elif existing != attack:
            diagnostics.append("conflicting_attack_observation")
    merged.sort(
        key=lambda item: (
            type(item.get("order")) is not int,
            item.get("order") if type(item.get("order")) is int else 0,
            str(item.get("attacker_tag") or ""),
            str(item.get("defender_tag") or ""),
        )
    )
    return merged, diagnostics


def _merge_snapshots(
    old: Mapping[str, Any], new: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Build a monotonic canonical view while observations retain original facts."""

    merged = _json_copy(old)
    diagnostics: list[str] = []
    old_state = str(merged.get("state") or "")
    new_state = str(new.get("state") or "")
    state_rank = {"preparation": 1, "inWar": 2, "warEnded": 3}
    if new_state in state_rank and (
        old_state not in state_rank or state_rank[new_state] >= state_rank[old_state]
    ):
        merged["state"] = new_state
    elif new_state in state_rank and state_rank.get(old_state, 0) > state_rank[new_state]:
        diagnostics.append("regressing_detailed_state_observed")
    for field in (
        "preparation_start_time",
        "start_time",
        "end_time",
        "team_size",
        "attacks_per_member",
    ):
        value = new.get(field)
        existing = merged.get(field)
        if value is None:
            continue
        if existing is None:
            merged[field] = value
        elif existing != value:
            # An older or contradictory observation must never overwrite a
            # previously canonical scalar. Immutable observations retain it.
            diagnostics.append(f"conflicting_canonical_{field}_observed")

    incoming_clan_stars = new.get("clan_stars")
    canonical_clan_stars = merged.get("clan_stars")
    if incoming_clan_stars is not None:
        if canonical_clan_stars is None:
            merged["clan_stars"] = incoming_clan_stars
        elif canonical_clan_stars != incoming_clan_stars:
            if old_state == "warEnded":
                if new_state == "warEnded":
                    diagnostics.append("conflicting_final_clan_stars_observed")
                elif incoming_clan_stars < canonical_clan_stars:
                    diagnostics.append("regressing_clan_stars_observed")
            elif new_state == "warEnded" and incoming_clan_stars >= canonical_clan_stars:
                # The first detailed final snapshot is authoritative over an
                # active-war aggregate, even when a prior active snapshot was
                # incomplete or stale.
                merged["clan_stars"] = incoming_clan_stars
            elif new_state == "warEnded":
                diagnostics.append("conflicting_final_clan_stars_observed")
            elif incoming_clan_stars > canonical_clan_stars:
                merged["clan_stars"] = incoming_clan_stars
            else:
                diagnostics.append("regressing_clan_stars_observed")

    old_members = {
        str(member["player_tag"]): member
        for member in merged.get("members", [])
        if isinstance(member, Mapping) and member.get("player_tag")
    }
    for incoming in new.get("members", []):
        if not isinstance(incoming, Mapping) or not incoming.get("player_tag"):
            continue
        tag = str(incoming["player_tag"])
        existing = old_members.get(tag)
        if existing is None:
            copied = _json_copy(incoming)
            merged.setdefault("members", []).append(copied)
            old_members[tag] = copied
            continue
        for field in ("display_name", "town_hall_level", "map_position"):
            if incoming.get(field) is not None:
                existing[field] = incoming[field]
        attacks, attack_diagnostics = _merge_attacks(
            existing.get("attacks", []), incoming.get("attacks", [])
        )
        existing["attacks"] = attacks
        diagnostics.extend(attack_diagnostics)

    merged["members"] = sorted(
        old_members.values(),
        key=lambda member: (
            member.get("map_position") is None,
            member.get("map_position") or 0,
            str(member.get("player_tag")),
        ),
    )
    merged["source"] = _json_copy(new.get("source", merged.get("source", {})))
    return merged, sorted(set(diagnostics))


def _lifecycle_for_state(state: str) -> str:
    if state == "warEnded":
        return "finalized_detailed"
    if state in _LIFECYCLE_ACTIVE_STATES:
        return "active"
    return "incomplete"


def _new_record(war: WarSnapshot, *, ambiguous: bool = False) -> dict[str, Any]:
    observation = _new_observation(war)
    return {
        "record_kind": "detailed",
        "war_id": build_war_id(war),
        "identity": {
            "seed": _identity_seed(war),
            "strong_identifiers": _known_times(observation["snapshot"]),
            "evidence_status": "ambiguous" if ambiguous else "matched",
            "holding_fingerprint": observation["fingerprint"] if ambiguous else None,
        },
        "first_collected_at": observation["collected_at"],
        "last_collected_at": observation["collected_at"],
        "lifecycle_status": "ambiguous" if ambiguous else _lifecycle_for_state(war.state),
        "reconciliation_status": "not_reconciled",
        "states_seen": [war.state],
        "observations": [observation],
        "canonical": observation["snapshot"],
        "war_log": None,
        "diagnostics": ["identity_ambiguous"] if ambiguous else [],
        "migration": None,
    }


def _is_int(value: Any, *, minimum: int | None = None, maximum: int | None = None) -> bool:
    return type(value) is int and (minimum is None or value >= minimum) and (maximum is None or value <= maximum)


_SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}")
_COMPACT_TIMESTAMP_PATTERN = re.compile(r"\d{8}T\d{6}\.\d{3}Z")


def _parse_timestamp(value: Any, path: str) -> datetime:
    """Accept only project compact timestamps or timezone-aware ISO-8601."""
    if not isinstance(value, str):
        raise HistoryError(f"{path} must be a timestamp string")
    try:
        if _COMPACT_TIMESTAMP_PATTERN.fullmatch(value):
            return datetime.strptime(value, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc)
        if "T" not in value or not (value.endswith("Z") or re.search(r"[+-]\d{2}:\d{2}$", value)):
            raise ValueError("timezone is missing")
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        if parsed.tzinfo is None:
            raise ValueError("timezone is missing")
        return parsed
    except ValueError as error:
        raise HistoryError(f"{path} is not a supported timestamp") from error


def _valid_timestamp(value: Any) -> bool:
    try:
        _parse_timestamp(value, "timestamp")
        return True
    except HistoryError:
        return False


def _validate_snapshot(snapshot: Mapping[str, Any], path: str) -> None:
    if snapshot.get("state") not in _DETAILED_STATES:
        raise HistoryError(f"{path}.state is invalid")
    for field in _TIME_FIELDS:
        value = snapshot.get(field)
        if value is not None and not _valid_timestamp(value):
            raise HistoryError(f"{path}.{field} is invalid")
    for field in ("clan_tag", "opponent_tag"):
        value = snapshot.get(field)
        if value is not None and (not isinstance(value, str) or not value):
            raise HistoryError(f"{path}.{field} is invalid")
    for field, minimum, maximum in (("team_size", 1, 50), ("attacks_per_member", 1, 10), ("clan_stars", 0, 150)):
        value = snapshot.get(field)
        if value is not None and not _is_int(value, minimum=minimum, maximum=maximum):
            raise HistoryError(f"{path}.{field} is invalid")
    members = snapshot.get("members")
    if not isinstance(members, list):
        raise HistoryError(f"{path}.members must be an array")
    tags: set[str] = set()
    positions: set[int] = set()
    orders: set[int] = set()
    for member_index, member in enumerate(members):
        member_path = f"{path}.members[{member_index}]"
        if not isinstance(member, Mapping):
            raise HistoryError(f"{member_path} must be an object")
        tag = member.get("player_tag")
        if not isinstance(tag, str) or not tag or tag in tags:
            raise HistoryError(f"{member_path}.player_tag is invalid or duplicate")
        tags.add(tag)
        if not isinstance(member.get("display_name"), str):
            raise HistoryError(f"{member_path}.display_name is invalid")
        for field, minimum, maximum in (("town_hall_level", 1, 20), ("map_position", 1, 50)):
            value = member.get(field)
            if value is not None and not _is_int(value, minimum=minimum, maximum=maximum):
                raise HistoryError(f"{member_path}.{field} is invalid")
        position = member.get("map_position")
        if position is not None:
            if position in positions:
                raise HistoryError(f"{member_path}.map_position is duplicate")
            positions.add(position)
        attacks = member.get("attacks")
        if not isinstance(attacks, list):
            raise HistoryError(f"{member_path}.attacks must be an array")
        for attack_index, attack in enumerate(attacks):
            attack_path = f"{member_path}.attacks[{attack_index}]"
            if not isinstance(attack, Mapping):
                raise HistoryError(f"{attack_path} must be an object")
            if not isinstance(attack.get("attacker_tag"), str) or not attack["attacker_tag"]:
                raise HistoryError(f"{attack_path}.attacker_tag is invalid")
            if attack["attacker_tag"] != tag:
                raise HistoryError(f"{attack_path}.attacker_tag does not match member")
            defender = attack.get("defender_tag")
            if defender is not None and (not isinstance(defender, str) or not defender):
                raise HistoryError(f"{attack_path}.defender_tag is invalid")
            if not _is_int(attack.get("stars"), minimum=0, maximum=3):
                raise HistoryError(f"{attack_path}.stars is invalid")
            destruction = attack.get("destruction_percentage")
            if destruction is not None and not _is_int(destruction, minimum=0, maximum=100):
                raise HistoryError(f"{attack_path}.destruction_percentage is invalid")
            order = attack.get("order")
            if order is not None:
                if not _is_int(order, minimum=1) or order in orders:
                    raise HistoryError(f"{attack_path}.order is invalid or duplicate")
                orders.add(order)
    source = snapshot.get("source")
    if not isinstance(source, Mapping) or set(source) != {"source_timestamp", "collected_at", "raw_source_reference"}:
        raise HistoryError(f"{path}.source is invalid")
    if not _valid_timestamp(source.get("collected_at")) or not isinstance(source.get("raw_source_reference"), str) or not source["raw_source_reference"]:
        raise HistoryError(f"{path}.source is invalid")
    if source.get("source_timestamp") is not None and not _valid_timestamp(source["source_timestamp"]):
        raise HistoryError(f"{path}.source_timestamp is invalid")


def _validate_war_log(value: Any, path: str) -> None:
    if not isinstance(value, Mapping):
        raise HistoryError(f"{path} must be an object")
    for field in ("end_time", "result", "team_size", "attacks_per_member", "battle_modifier", "clan", "opponent", "source"):
        if field not in value:
            raise HistoryError(f"{path}.{field} is missing")
    if value["end_time"] is not None and not _valid_timestamp(value["end_time"]):
        raise HistoryError(f"{path}.end_time is invalid")
    if value["result"] not in {None, "win", "lose", "tie"}:
        raise HistoryError(f"{path}.result is invalid")
    for field, minimum, maximum in (("team_size", 1, 50), ("attacks_per_member", 1, 10)):
        if value[field] is not None and not _is_int(value[field], minimum=minimum, maximum=maximum):
            raise HistoryError(f"{path}.{field} is invalid")
    if value["battle_modifier"] is not None and not isinstance(value["battle_modifier"], str):
        raise HistoryError(f"{path}.battle_modifier is invalid")
    for side_name in ("clan", "opponent"):
        side = value[side_name]
        if not isinstance(side, Mapping) or set(side) != {"clan_tag", "name", "stars", "destruction_percentage", "attacks"}:
            raise HistoryError(f"{path}.{side_name} is invalid")
        if side["clan_tag"] is not None and (not isinstance(side["clan_tag"], str) or not side["clan_tag"]):
            raise HistoryError(f"{path}.{side_name}.clan_tag is invalid")
        if side["name"] is not None and not isinstance(side["name"], str):
            raise HistoryError(f"{path}.{side_name}.name is invalid")
        if side["stars"] is not None and not _is_int(side["stars"], minimum=0, maximum=150):
            raise HistoryError(f"{path}.{side_name}.stars is invalid")
        destruction = side["destruction_percentage"]
        if destruction is not None and (type(destruction) not in {int, float} or not 0 <= destruction <= 100):
            raise HistoryError(f"{path}.{side_name}.destruction_percentage is invalid")
        if side["attacks"] is not None and not _is_int(side["attacks"], minimum=0, maximum=500):
            raise HistoryError(f"{path}.{side_name}.attacks is invalid")
    source = value["source"]
    if not isinstance(source, Mapping) or set(source) != {"source_timestamp", "collected_at", "raw_source_reference"} or not _valid_timestamp(source.get("collected_at")) or not isinstance(source.get("raw_source_reference"), str) or not source["raw_source_reference"]:
        raise HistoryError(f"{path}.source is invalid")
    if source.get("source_timestamp") is not None and not _valid_timestamp(source["source_timestamp"]):
        raise HistoryError(f"{path}.source_timestamp is invalid")


def _validate_diagnostics(value: Any, path: str, *, top_level: bool) -> None:
    if not isinstance(value, list):
        raise HistoryError(f"{path} must be an array")
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if top_level:
            if not isinstance(item, Mapping) or item.get("status") not in _TOP_DIAGNOSTIC_STATUSES:
                raise HistoryError(f"{item_path} is invalid")
            if set(item) - {"status", "candidate_count", "state"}:
                raise HistoryError(f"{item_path} has unknown fields")
            if "candidate_count" in item and not _is_int(item["candidate_count"], minimum=2):
                raise HistoryError(f"{item_path}.candidate_count is invalid")
            if "state" in item and item["state"] not in _DETAILED_STATES:
                raise HistoryError(f"{item_path}.state is invalid")
        elif not isinstance(item, str) or not (
            item in _RECORD_DIAGNOSTICS or re.fullmatch(r"conflicting_canonical_(preparation_start_time|start_time|end_time|team_size|attacks_per_member|clan_stars)_observed", item)
        ):
            raise HistoryError(f"{item_path} is invalid")


def _validate_identity(value: Any, path: str, *, kind: str) -> None:
    if not isinstance(value, Mapping):
        raise HistoryError(f"{path} is invalid")
    allowed = {"seed", "strong_identifiers", "evidence_status"}
    if kind == "detailed":
        allowed.add("holding_fingerprint")
    if set(value) != allowed or not isinstance(value.get("seed"), Mapping) or not isinstance(value.get("strong_identifiers"), Mapping):
        raise HistoryError(f"{path} is invalid")
    seed = value["seed"]
    seed_kind = seed.get("kind")
    if seed_kind == "first_known_time":
        if set(seed) != {"kind", "value"} or not _valid_timestamp(seed.get("value")):
            raise HistoryError(f"{path}.seed is invalid")
    elif seed_kind == "first_observation":
        if set(seed) != {"kind", "collected_at", "member_tags"} or not _valid_timestamp(seed.get("collected_at")) or not isinstance(seed.get("member_tags"), list) or not seed["member_tags"] or any(not isinstance(tag, str) or not tag for tag in seed["member_tags"]):
            raise HistoryError(f"{path}.seed is invalid")
    elif seed_kind == "war_log_aggregate":
        if set(seed) != {"kind", "id"} or not isinstance(seed.get("id"), str) or not _SHA256_PATTERN.fullmatch(seed["id"]):
            raise HistoryError(f"{path}.seed is invalid")
    else:
        raise HistoryError(f"{path}.seed.kind is invalid")
    if set(value["strong_identifiers"]) - set(_TIME_FIELDS) or any(not _valid_timestamp(timestamp) for timestamp in value["strong_identifiers"].values()):
        raise HistoryError(f"{path}.strong_identifiers is invalid")
    evidence = value.get("evidence_status")
    allowed_evidence = {"matched", "ambiguous", "matched_aggregate_only"} if kind == "detailed" else {"aggregate_only"}
    if evidence not in allowed_evidence:
        raise HistoryError(f"{path}.evidence_status is invalid")
    if kind == "detailed":
        holding = value.get("holding_fingerprint")
        if evidence == "ambiguous":
            if not isinstance(holding, str) or not _SHA256_PATTERN.fullmatch(holding):
                raise HistoryError(f"{path}.holding_fingerprint is invalid")
        elif holding is not None:
            raise HistoryError(f"{path}.holding_fingerprint must be null")


def _validate_history_v2(history: Mapping[str, Any]) -> None:
    if history.get("schema_version") != HISTORY_SCHEMA_VERSION:
        raise HistoryError("unsupported history schema version")
    wars = history.get("wars")
    if not isinstance(wars, list):
        raise HistoryError("history.wars must be an array")
    _validate_diagnostics(history.get("diagnostics"), "history.diagnostics", top_level=True)
    seen_ids: set[str] = set()
    for index, record in enumerate(wars):
        if not isinstance(record, Mapping):
            raise HistoryError(f"history.wars[{index}] must be an object")
        war_id = record.get("war_id")
        if not isinstance(war_id, str) or not _SHA256_PATTERN.fullmatch(war_id):
            raise HistoryError(f"history.wars[{index}].war_id is invalid")
        if war_id in seen_ids:
            raise HistoryError("history contains duplicate war ids")
        seen_ids.add(war_id)
        kind = record.get("record_kind")
        if kind not in {"detailed", "aggregate_only"}:
            raise HistoryError(f"history.wars[{index}].record_kind is invalid")
        if record.get("lifecycle_status") not in _LIFECYCLE_STATUSES:
            raise HistoryError(f"history.wars[{index}].lifecycle_status is invalid")
        if record.get("reconciliation_status") not in _RECONCILIATION_STATUSES:
            raise HistoryError(f"history.wars[{index}].reconciliation_status is invalid")
        first, last = record.get("first_collected_at"), record.get("last_collected_at")
        if not _valid_timestamp(first) or not _valid_timestamp(last) or _parse_timestamp(first, f"history.wars[{index}].first_collected_at") > _parse_timestamp(last, f"history.wars[{index}].last_collected_at"):
            raise HistoryError(f"history.wars[{index}] collection timestamps are invalid")
        states = record.get("states_seen")
        if not isinstance(states, list) or len(states) != len(set(states)) or any(state not in _DETAILED_STATES for state in states):
            raise HistoryError(f"history.wars[{index}].states_seen is invalid")
        _validate_identity(record.get("identity"), f"history.wars[{index}].identity", kind=kind)
        _validate_diagnostics(record.get("diagnostics"), f"history.wars[{index}].diagnostics", top_level=False)
        observations = record.get("observations")
        if not isinstance(observations, list):
            raise HistoryError(f"history.wars[{index}].observations must be an array")
        fingerprints: set[str] = set()
        for observation in observations:
            if not isinstance(observation, Mapping):
                raise HistoryError("history observation must be an object")
            fingerprint = observation.get("fingerprint")
            if not isinstance(fingerprint, str) or not _SHA256_PATTERN.fullmatch(fingerprint):
                raise HistoryError("history observation fingerprint is invalid")
            if fingerprint in fingerprints:
                raise HistoryError("history contains duplicate observations")
            fingerprints.add(fingerprint)
            if not isinstance(observation.get("snapshot"), Mapping):
                raise HistoryError("history observation snapshot is missing")
            if not _valid_timestamp(observation.get("collected_at")):
                raise HistoryError("history observation collected_at is invalid")
            snapshot = observation["snapshot"]
            _validate_snapshot(snapshot, f"history.wars[{index}].observations")
            calculated = hashlib.sha256(_canonical_json(_observation_content(snapshot))).hexdigest()
            if fingerprint != calculated:
                raise HistoryError("history observation fingerprint does not match snapshot")
        canonical = record.get("canonical")
        war_log = record.get("war_log")
        if kind == "detailed":
            if not observations or not isinstance(canonical, Mapping):
                raise HistoryError(f"history.wars[{index}] detailed record is incomplete")
            _validate_snapshot(canonical, f"history.wars[{index}].canonical")
            if record.get("lifecycle_status") == "finalized_detailed" and canonical.get("state") != "warEnded":
                raise HistoryError(f"history.wars[{index}] finalized detailed state is inconsistent")
            if record.get("lifecycle_status") == "active" and canonical.get("state") == "warEnded":
                raise HistoryError(f"history.wars[{index}] active state is inconsistent")
            if record.get("lifecycle_status") == "closed_without_final_detailed" and canonical.get("state") == "warEnded":
                raise HistoryError(f"history.wars[{index}] closed state is inconsistent")
            if record.get("reconciliation_status") == "matched" and war_log is None:
                raise HistoryError(f"history.wars[{index}] matched record lacks war_log")
            if record.get("reconciliation_status") == "awaiting_war_log" and record.get("lifecycle_status") != "closed_without_final_detailed":
                raise HistoryError(f"history.wars[{index}] awaiting reconciliation is inconsistent")
        else:
            if observations or canonical is not None or record.get("lifecycle_status") != "closed_war_log_only":
                raise HistoryError(f"history.wars[{index}] aggregate-only record is inconsistent")
            if war_log is None:
                raise HistoryError(f"history.wars[{index}] aggregate-only record lacks war_log")
            if record.get("reconciliation_status") != "unmatched_aggregate_only" or states:
                raise HistoryError(f"history.wars[{index}] aggregate-only reconciliation is inconsistent")
        if war_log is not None:
            _validate_war_log(war_log, f"history.wars[{index}].war_log")
            war_log_id = record.get("war_log_id")
            if not isinstance(war_log_id, str) or not _SHA256_PATTERN.fullmatch(war_log_id):
                raise HistoryError(f"history.wars[{index}].war_log_id is invalid")
        elif record.get("war_log_id") is not None:
            raise HistoryError(f"history.wars[{index}].war_log_id is inconsistent")
        migration = record.get("migration")
        if migration is not None and (
            not isinstance(migration, Mapping) or migration.get("from_schema_version") != 1
        ):
            raise HistoryError(f"history.wars[{index}].migration is invalid")


def migrate_history(history: Mapping[str, Any]) -> dict[str, Any]:
    """Deterministically migrate v1 to v2 without inventing observations."""

    version = history.get("schema_version")
    if version == HISTORY_SCHEMA_VERSION:
        migrated = _json_copy(history)
        _validate_history_v2(migrated)
        return migrated
    if version != 1:
        raise HistoryError(f"unsupported history schema version: {version}")
    wars = history.get("wars")
    if not isinstance(wars, list):
        raise HistoryError("history.wars must be an array")

    result = empty_history()
    for index, old in enumerate(wars):
        if not isinstance(old, Mapping) or not isinstance(old.get("latest"), Mapping):
            raise HistoryError(f"history.wars[{index}].latest must be an object")
        war = war_from_dict(old["latest"])
        record = _new_record(war)
        legacy_id = old.get("war_id")
        if isinstance(legacy_id, str) and len(legacy_id) == 64:
            record["war_id"] = legacy_id
        record["first_collected_at"] = str(
            old.get("first_collected_at") or war.source.collected_at
        )
        record["last_collected_at"] = str(
            old.get("last_collected_at") or war.source.collected_at
        )
        record["states_seen"] = list(old.get("states_seen") or [war.state])
        if old.get("finalized"):
            record["lifecycle_status"] = "finalized_detailed"
        record["migration"] = {
            "from_schema_version": 1,
            "observation_limit": "only legacy latest snapshot was available",
            "legacy_observation_count": old.get("observations"),
        }
        result["wars"].append(record)
    _validate_history_v2(result)
    return result


def ensure_history_v2(history: Mapping[str, Any] | None) -> dict[str, Any]:
    if history is None:
        return empty_history()
    return migrate_history(history)


def merge_war_history(
    history: Mapping[str, Any] | None,
    war: WarSnapshot,
) -> tuple[dict[str, Any], bool]:
    """Append a distinct observation and update a monotonic canonical snapshot."""

    current = ensure_history_v2(history)
    records: list[dict[str, Any]] = current["wars"]

    if war.state == "notInWar":
        active = [
            record
            for record in records
            if record.get("lifecycle_status") in {"active", "incomplete"}
        ]
        if len(active) == 1:
            record = active[0]
            observation = _new_observation(war)
            if not any(
                item.get("fingerprint") == observation["fingerprint"]
                for item in record["observations"]
            ):
                record["observations"].append(observation)
            record["lifecycle_status"] = "closed_without_final_detailed"
            record["reconciliation_status"] = "awaiting_war_log"
            if "notInWar" not in record["states_seen"]:
                record["states_seen"].append("notInWar")
            record["last_collected_at"] = war.source.collected_at
            return current, True
        if len(active) > 1:
            for record in active:
                record["lifecycle_status"] = "ambiguous"
                record["diagnostics"] = sorted(
                    set(record.get("diagnostics", [])) | {"not_in_war_ambiguous"}
                )
            return current, True
        return current, False

    if not war.members:
        current["diagnostics"].append(
            {"status": "ignored_incomplete_detailed_snapshot", "state": war.state}
        )
        return current, True

    ranked = []
    rejected_reasons: list[str] = []
    for record in records:
        if record.get("observations"):
            score, reason = _identity_evidence(record, war)
            if score >= 0:
                ranked.append((score, record, reason))
            else:
                rejected_reasons.append(reason)
    ranked.sort(key=lambda item: item[0], reverse=True)
    best = ranked[0][0] if ranked else None
    candidates = [item[1] for item in ranked if item[0] == best] if ranked else []
    if len(candidates) > 1:
        observation = _new_observation(war)
        if any(
            record.get("identity", {}).get("holding_fingerprint") == observation["fingerprint"]
            for record in records
            if isinstance(record.get("identity"), Mapping)
        ):
            return current, False
        record = _new_record(war, ambiguous=True)
        records.append(record)
        current["diagnostics"].append(
            {"status": "identity_ambiguous", "candidate_count": len(candidates)}
        )
        return current, True
    if not candidates:
        aggregate_candidates = _aggregate_detailed_candidates(records, war)
        if len(aggregate_candidates) == 1:
            record = aggregate_candidates[0]
            observation = _new_observation(war)
            record["record_kind"] = "detailed"
            record["observations"] = [observation]
            record["canonical"] = observation["snapshot"]
            record["first_collected_at"] = min(str(record["first_collected_at"]), observation["collected_at"])
            record["last_collected_at"] = max(str(record["last_collected_at"]), observation["collected_at"])
            record["states_seen"] = [war.state]
            record["identity"] = {
                "seed": record["identity"]["seed"],
                "strong_identifiers": _known_times(observation["snapshot"]),
                "evidence_status": "matched_aggregate_only",
                "holding_fingerprint": None,
            }
            record["reconciliation_status"] = "matched"
            record["lifecycle_status"] = _lifecycle_for_state(war.state)
            if war.state == "warEnded":
                record["lifecycle_status"] = "finalized_detailed"
            record["diagnostics"] = sorted(set(record.get("diagnostics", [])) | {"aggregate_only_matched_late_detail"})
            return current, True
        if len(aggregate_candidates) > 1:
            observation = _new_observation(war)
            if any(
                record.get("identity", {}).get("holding_fingerprint") == observation["fingerprint"]
                for record in records
                if isinstance(record.get("identity"), Mapping)
            ):
                return current, False
            record = _new_record(war, ambiguous=True)
            records.append(record)
            current["diagnostics"].append({"status": "aggregate_detail_match_ambiguous", "candidate_count": len(aggregate_candidates)})
            return current, True
        record = _new_record(war)
        if "incompatible_timeline" in rejected_reasons:
            record["diagnostics"] = ["identity_timeline_incompatible"]
        elif "incompatible_game_time_window" in rejected_reasons:
            record["diagnostics"] = ["identity_game_time_window_incompatible"]
        if any(existing.get("war_id") == record["war_id"] for existing in records):
            record["war_id"] = hashlib.sha256(
                _canonical_json({"seed": record["identity"]["seed"], "fingerprint": observation_fingerprint(war)})
            ).hexdigest()
        records.append(record)
        return current, True

    record = candidates[0]
    observation = _new_observation(war)
    if any(
        existing.get("fingerprint") == observation["fingerprint"]
        for existing in record["observations"]
    ):
        return current, False

    canonical, diagnostics = _merge_snapshots(record["canonical"], observation["snapshot"])
    record["observations"].append(observation)
    record["canonical"] = canonical
    record["last_collected_at"] = max(
        str(record.get("last_collected_at") or ""), observation["collected_at"]
    )
    if war.state not in record["states_seen"]:
        record["states_seen"].append(war.state)
    record["identity"]["strong_identifiers"].update(_known_times(canonical))
    record["diagnostics"] = sorted(
        set(record.get("diagnostics", [])) | set(diagnostics)
    )
    if war.state == "warEnded":
        record["lifecycle_status"] = "finalized_detailed"
    elif record["lifecycle_status"] not in {
        "finalized_detailed",
        "closed_war_log_without_final_detail",
    }:
        record["lifecycle_status"] = _lifecycle_for_state(war.state)
    records.sort(
        key=lambda item: (
            _iso_key(item.get("canonical", {}).get("end_time")), item["war_id"]
        )
    )
    return current, True


def _war_log_payload(entry: WarLogEntrySnapshot) -> dict[str, Any]:
    return _json_copy(asdict(entry))


def _war_log_id(entry: WarLogEntrySnapshot) -> str:
    return hashlib.sha256(
        _canonical_json(
            {
                "end_time": entry.end_time,
                "team_size": entry.team_size,
                "clan_stars": entry.clan.stars,
                "opponent_stars": entry.opponent.stars,
                "result": entry.result,
            }
        )
    ).hexdigest()


def _war_log_match_score(snapshot: Mapping[str, Any], entry: WarLogEntrySnapshot) -> tuple[int, list[str]]:
    """Return a fail-closed reconciliation score and its explicit evidence."""

    end_time = snapshot.get("end_time")
    if not entry.end_time or not end_time or entry.end_time != end_time:
        return (-1, ["missing_or_conflicting_exact_end_time"])
    score = 100
    evidence = ["exact_end_time"]
    for field, entry_value in (("team_size", entry.team_size), ("attacks_per_member", entry.attacks_per_member)):
        value = snapshot.get(field)
        if value is not None and entry_value is not None:
            if value != entry_value:
                return (-1, [f"conflicting_{field}"])
            score += 10
            evidence.append(field)
    for field, entry_value, label in (
        ("clan_tag", entry.clan.clan_tag, "clan_tag"),
        ("opponent_tag", entry.opponent.clan_tag, "opponent_tag"),
    ):
        value = snapshot.get(field)
        if value is not None and entry_value is not None:
            if value != entry_value:
                return (-1, [f"conflicting_{label}"])
            score += 25
            evidence.append(label)
    clan_stars = snapshot.get("clan_stars")
    if clan_stars is not None and entry.clan.stars is not None:
        # Active detailed snapshots legitimately precede final war-log stars.
        if clan_stars == entry.clan.stars:
            score += 5
            evidence.append("clan_stars")
    # Exact end time alone is not enough: an independently compatible field is
    # required to protect against malformed/reused timestamps.
    if score < 110:
        return (-1, ["insufficient_supporting_evidence"])
    return (score, evidence)


def _war_log_candidates(records: Sequence[dict[str, Any]], entry: WarLogEntrySnapshot) -> list[dict[str, Any]]:
    return [
        record for record in records
        if isinstance(record.get("canonical"), Mapping)
        and _war_log_match_score(record["canonical"], entry)[0] >= 110
    ]


def _aggregate_detailed_candidates(records: Sequence[dict[str, Any]], war: WarSnapshot) -> list[dict[str, Any]]:
    snapshot = _snapshot_payload(war)
    candidates = []
    for record in records:
        if record.get("record_kind") != "aggregate_only" or not isinstance(record.get("war_log"), Mapping):
            continue
        try:
            entry = war_log_entry_from_dict(record["war_log"])
        except HistoryError:
            continue
        if _war_log_match_score(snapshot, entry)[0] >= 110:
            candidates.append(record)
    return candidates


def reconcile_war_log(
    history: Mapping[str, Any], war_log: WarLogSnapshot
) -> tuple[dict[str, Any], bool]:
    """Attach official aggregates or create aggregate-only records safely."""

    current = ensure_history_v2(history)
    changed = False
    records: list[dict[str, Any]] = current["wars"]
    for entry in war_log.entries:
        payload = _war_log_payload(entry)
        log_id = _war_log_id(entry)
        already = next(
            (record for record in records if record.get("war_log_id") == log_id), None
        )
        if already is not None:
            continue
        candidates = _war_log_candidates(records, entry)
        if len(candidates) == 1:
            record = candidates[0]
            record["war_log"] = payload
            record["war_log_id"] = log_id
            record["reconciliation_status"] = "matched"
            if record.get("lifecycle_status") != "finalized_detailed":
                record["lifecycle_status"] = "closed_war_log_without_final_detail"
            changed = True
        elif len(candidates) > 1:
            for record in candidates:
                record["reconciliation_status"] = "ambiguous"
                record["lifecycle_status"] = "ambiguous"
                record["diagnostics"] = sorted(
                    set(record.get("diagnostics", [])) | {"war_log_match_ambiguous"}
                )
            current["diagnostics"].append(
                {"status": "war_log_match_ambiguous", "candidate_count": len(candidates)}
            )
            changed = True
        elif entry.end_time or entry.team_size is not None:
            records.append(
                {
                    "record_kind": "aggregate_only",
                    "war_id": log_id,
                    "war_log_id": log_id,
                    "identity": {
                        "seed": {"kind": "war_log_aggregate", "id": log_id},
                        "strong_identifiers": {"end_time": entry.end_time}
                        if entry.end_time
                        else {},
                        "evidence_status": "aggregate_only",
                    },
                    "first_collected_at": entry.source.collected_at,
                    "last_collected_at": entry.source.collected_at,
                    "lifecycle_status": "closed_war_log_only",
                    "reconciliation_status": "unmatched_aggregate_only",
                    "states_seen": [],
                    "observations": [],
                    "canonical": None,
                    "war_log": payload,
                    "diagnostics": ["no_detailed_snapshot"],
                    "migration": None,
                }
            )
            changed = True
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
        clan_stars=payload.get("clan_stars"),
        clan_tag=payload.get("clan_tag"),
        opponent_tag=payload.get("opponent_tag"),
        members=tuple(members),
        source=source,
    )


def war_log_entry_from_dict(payload: Mapping[str, Any]) -> WarLogEntrySnapshot:
    """Rebuild a validated internal aggregate only for reconciliation."""
    try:
        source_payload = payload["source"]
        clan_payload = payload["clan"]
        opponent_payload = payload["opponent"]
        if not all(isinstance(value, Mapping) for value in (source_payload, clan_payload, opponent_payload)):
            raise KeyError("mapping")
        source = SourceMetadata(
            source_timestamp=source_payload.get("source_timestamp"),
            collected_at=str(source_payload.get("collected_at") or ""),
            raw_source_reference=str(source_payload.get("raw_source_reference") or ""),
        )
        return WarLogEntrySnapshot(
            end_time=payload.get("end_time"), result=payload.get("result"),
            team_size=payload.get("team_size"), attacks_per_member=payload.get("attacks_per_member"),
            battle_modifier=payload.get("battle_modifier"),
            clan=WarLogSideSnapshot(**dict(clan_payload)),
            opponent=WarLogSideSnapshot(**dict(opponent_payload)),
            source=source,
        )
    except (KeyError, TypeError) as error:
        raise HistoryError("war log aggregate is invalid") from error


def detailed_wars(history: Mapping[str, Any]) -> tuple[WarSnapshot, ...]:
    current = ensure_history_v2(history)
    return tuple(
        war_from_dict(record["canonical"])
        for record in current["wars"]
        if isinstance(record.get("canonical"), Mapping)
    )


def _public_member(
    member: WarMemberSnapshot,
    attacks_per_member: int | None,
    contributions: Mapping[str, int] | None,
) -> dict[str, Any]:
    attacks_used = len(member.attacks)
    stars_earned = sum(attack.stars for attack in member.attacks)
    return {
        "war_position": member.map_position,
        "nickname": member.display_name,
        "town_hall_level": member.town_hall_level,
        "attacks_used": attacks_used,
        "attacks_available": attacks_per_member,
        "stars_earned": stars_earned,
        "new_stars_contributed": (
            contributions.get(member.player_tag, 0) if contributions is not None else None
        ),
        "average_stars": round(stars_earned / attacks_used, 2) if attacks_used else None,
    }


def build_public_war_history(history: Mapping[str, Any]) -> dict[str, Any]:
    """Build neutral detailed history without stable game identifiers."""

    current = ensure_history_v2(history)
    public_wars: list[dict[str, Any]] = []
    player_totals: dict[str, dict[str, Any]] = {}
    for record in current["wars"]:
        if not isinstance(record.get("canonical"), Mapping):
            continue
        war = war_from_dict(record["canonical"])
        metrics = calculate_war_star_metrics(war)
        contributions = metrics["contributions_by_player_tag"]
        members = [
            _public_member(member, war.attacks_per_member, contributions)
            for member in war.members
        ]
        members.sort(
            key=lambda member: (
                member["war_position"] is None,
                member["war_position"] or 0,
                str(member["nickname"]).casefold(),
            )
        )
        attacks_used = sum(member["attacks_used"] for member in members)
        attacks_available = (
            len(members) * war.attacks_per_member
            if war.attacks_per_member is not None
            else None
        )
        public_wars.append(
            {
                "state": war.state,
                "lifecycle_status": record["lifecycle_status"],
                "reconciliation_status": record["reconciliation_status"],
                "end_time": _date_only(war.end_time),
                "team_size": war.team_size,
                "participants": len(members),
                "attacks_per_member": war.attacks_per_member,
                "attacks_used": attacks_used,
                "attacks_available": attacks_available,
                "clan_stars": metrics["clan_stars"],
                "attack_stars_total": metrics["attack_stars_total"],
                "stars_consistency_status": metrics["stars_consistency_status"],
                "new_stars_contribution_status": metrics[
                    "new_stars_contribution_status"
                ],
                "members": members,
            }
        )
        date = _date_only(war.end_time)
        for member in war.members:
            public_member = _public_member(member, war.attacks_per_member, contributions)
            totals = player_totals.setdefault(
                member.player_tag,
                {
                    "nickname": member.display_name,
                    "war_participations": 0,
                    "attacks_used": 0,
                    "attacks_available": 0,
                    "stars_earned": 0,
                    "new_stars_contributed": 0 if contributions is not None else None,
                    "last_war_date": None,
                },
            )
            totals["war_participations"] += 1
            totals["attacks_used"] += public_member["attacks_used"]
            if public_member["attacks_available"] is not None:
                totals["attacks_available"] += public_member["attacks_available"]
            totals["stars_earned"] += public_member["stars_earned"]
            if totals["new_stars_contributed"] is not None:
                contribution = public_member["new_stars_contributed"]
                if contribution is None:
                    totals["new_stars_contributed"] = None
                else:
                    totals["new_stars_contributed"] += contribution
            if date and (totals["last_war_date"] is None or date >= totals["last_war_date"]):
                totals["last_war_date"] = date
                totals["nickname"] = member.display_name
    public_wars.sort(
        key=lambda item: (_iso_key(item["end_time"]), item["participants"]), reverse=True
    )
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


def load_history(path: Path) -> dict[str, Any]:
    """Read and validate history without treating corruption as an empty history."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HistoryError(f"history read stage failed: {path}") from error
    if not isinstance(payload, Mapping):
        raise HistoryError(f"history validation stage failed: {path}")
    try:
        return ensure_history_v2(payload)
    except HistoryError as error:
        raise HistoryError(f"history validation stage failed: {path}: {error}") from error


def write_history_atomic(path: Path, history: Mapping[str, Any]) -> Path | None:
    """Flush, back up, and atomically replace a validated history file."""

    validated = ensure_history_v2(history)
    parent = path.parent
    temp = parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    backup = parent / f"{path.name}.bak"
    backup_temp = parent / f".{path.name}.{uuid.uuid4().hex}.bak.tmp"
    try:
        parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(validated, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            raise HistoryError(f"history write stage failed: {path}") from error
        if path.exists():
            try:
                with path.open("rb") as source, backup_temp.open("wb") as target:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(backup_temp, backup)
            except OSError as error:
                raise HistoryError(f"history backup stage failed: {path}") from error
        try:
            os.replace(temp, path)
        except OSError as error:
            raise HistoryError(f"history replace stage failed: {path}") from error
        return backup if backup.exists() else None
    finally:
        try:
            temp.unlink(missing_ok=True)
            backup_temp.unlink(missing_ok=True)
        except OSError:
            pass


def restore_history_backup(path: Path, backup: Path) -> None:
    """Validate a backup before atomically restoring it over the target."""

    try:
        restored = load_history(backup)
    except HistoryError as error:
        raise HistoryError(f"history restore validation stage failed: {backup}") from error
    temp = path.parent / f".{path.name}.{uuid.uuid4().hex}.restore.tmp"
    try:
        data = json.dumps(restored, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        except OSError as error:
            raise HistoryError(f"history restore replace stage failed: {path}") from error
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
