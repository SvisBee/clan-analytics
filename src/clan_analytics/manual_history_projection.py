"""Safe, allowlisted projection of local screenshot evidence into public history."""

from __future__ import annotations

import copy
import re
import warnings
from collections.abc import Mapping
from typing import Any


class ManualHistoryProjectionError(ValueError):
    """Raised when local manual evidence is not safe to project."""


_CLASSES = {"exact_api_match", "manual_only", "source_conflict", "ambiguous"}
_FORBIDDEN = {
    "playertag", "attackertag", "defendertag", "clantag", "opponenttag",
    "order", "attackorder", "globalorder", "token", "authorization", "dpapi",
    "sourcefiles", "sourcehashes", "linkedwarid", "evidenceid",
}


def _normal(key: object) -> str:
    return re.sub(r"[\s_.\-/]+", "", str(key).casefold())


def _scan(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if _normal(key) in _FORBIDDEN:
                raise ManualHistoryProjectionError("manual evidence contains a private field")
            _scan(nested)
    elif isinstance(value, list):
        for nested in value:
            _scan(nested)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ManualHistoryProjectionError(message)


def _integer(value: Any, minimum: int, maximum: int | None, message: str) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool), message)
    _require(value >= minimum and (maximum is None or value <= maximum), message)
    return value


def _number(value: Any, minimum: float, maximum: float, message: str) -> float | int:
    _require(isinstance(value, (int, float)) and not isinstance(value, bool), message)
    _require(minimum <= value <= maximum, message)
    return value


def _validate_record(evidence: Mapping[str, Any]) -> dict[str, Any]:
    participants, attacks, metrics, conflicts = (
        evidence.get("participants"), evidence.get("attacks"), evidence.get("metrics"),
        evidence.get("source_conflicts"),
    )
    _require(isinstance(participants, list) and isinstance(attacks, list), "manual sections are invalid")
    _require(isinstance(metrics, Mapping) and isinstance(conflicts, list), "manual sections are invalid")
    public_participants: list[dict[str, Any]] = []
    seen_positions: set[int] = set()
    for participant in participants:
        _require(isinstance(participant, Mapping), "manual participant must be an object")
        _scan(participant)
        name, position = participant.get("display_name_raw"), participant.get("map_position")
        _require(isinstance(name, str) and name.strip(), "invalid manual participant name")
        position = _integer(position, 1, 50, "invalid manual participant position")
        _require(position not in seen_positions, "duplicate manual participant position")
        seen_positions.add(position)
        public_participants.append({"nickname": name, "war_position": position})

    counts = {kind: 0 for kind in _CLASSES}
    screenshot_stars = 0
    public_attacks: list[dict[str, Any]] = []
    conflict_attacks: list[dict[str, Any]] = []
    attack_keys: set[tuple[int, int]] = set()
    for attack in attacks:
        _require(isinstance(attack, Mapping), "manual attack must be an object")
        _scan(attack)
        kind = attack.get("classification")
        _require(kind in _CLASSES, "invalid manual classification")
        attacker = _integer(attack.get("attacker_map_position"), 1, 50, "invalid manual attacker position")
        defender = attack.get("defender_map_position")
        if defender is not None:
            defender = _integer(defender, 1, 50, "invalid manual defender position")
        destruction = _number(attack.get("destruction_percentage"), 0, 100, "invalid manual destruction")
        slot = _integer(attack.get("screenshot_slot"), 1, 2, "invalid screenshot slot")
        stars = _integer(attack.get("stars"), 0, 3, "invalid manual stars")
        key = (attacker, slot)
        _require(key not in attack_keys, "duplicate manual attacker position and screenshot slot")
        attack_keys.add(key)
        counts[kind] += 1
        screenshot_stars += stars
        safe = {"attacker_map_position": attacker, "defender_map_position": defender,
                "destruction_percentage": destruction, "screenshot_slot": slot, "stars": stars}
        if kind == "source_conflict":
            conflict_attacks.append(safe)
        else:
            safe["source"] = {"exact_api_match": "api_and_screenshot", "manual_only": "screenshot_only", "ambiguous": "ambiguous"}[kind]
            public_attacks.append(safe)

    expected_counts = metrics.get("classification_counts")
    _require(isinstance(expected_counts, Mapping) and set(expected_counts) == _CLASSES, "manual classification counts mismatch")
    for kind, count in counts.items():
        _require(_integer(expected_counts.get(kind), 0, None, "invalid manual classification count") == count, "manual classification counts mismatch")
    _require(_integer(metrics.get("attack_stars_total"), 0, None, "invalid manual attack stars") == screenshot_stars, "manual attack stars mismatch")
    contribution = _integer(metrics.get("displayed_contribution_total"), 0, None, "invalid displayed contribution")
    if "attack_count" in metrics:
        _require(_integer(metrics["attack_count"], 0, None, "invalid manual attack count") == len(attacks), "manual attack count mismatch")
    if "participant_count" in metrics:
        _require(_integer(metrics["participant_count"], 0, None, "invalid manual participant count") == len(participants), "manual participant count mismatch")

    _require(len(conflicts) == counts["source_conflict"], "manual conflicts mismatch")
    public_conflicts: list[dict[str, Any]] = []
    matched_conflicts: set[tuple[int, int | None, float | int]] = set()
    for conflict in conflicts:
        _require(isinstance(conflict, Mapping), "manual conflict must be an object")
        _scan(conflict)
        _require(conflict.get("resolution") == "unresolved", "manual conflict must remain unresolved")
        shared, claims = conflict.get("shared_facts"), conflict.get("claims")
        _require(isinstance(shared, Mapping) and isinstance(claims, list) and len(claims) >= 2, "invalid manual conflict")
        attacker = _integer(shared.get("attacker_map_position"), 1, 50, "invalid conflict attacker position")
        defender = shared.get("defender_map_position")
        if defender is not None:
            defender = _integer(defender, 1, 50, "invalid conflict defender position")
        destruction = _number(shared.get("destruction_percentage"), 0, 100, "invalid conflict destruction")
        sources: dict[str, int] = {}
        for claim in claims:
            _require(isinstance(claim, Mapping), "invalid manual conflict claim")
            source = claim.get("source_type")
            _require(source in {"official_api", "game_screenshot"}, "invalid manual conflict source")
            _require(source not in sources, "duplicate manual conflict source")
            sources[source] = _integer(claim.get("stars"), 0, 3, "invalid conflict stars")
        _require(set(sources) == {"official_api", "game_screenshot"}, "manual conflict sources are incomplete")
        _require(sources["official_api"] != sources["game_screenshot"], "manual conflict claims are not distinct")
        match = (attacker, defender, destruction)
        _require(match not in matched_conflicts, "manual conflict is linked more than once")
        matched_conflicts.add(match)
        matching_attacks = [item for item in conflict_attacks if (item["attacker_map_position"], item["defender_map_position"], item["destruction_percentage"]) == match]
        _require(len(matching_attacks) == 1, "manual conflict shared facts mismatch")
        public_conflicts.append({"attacker_map_position": attacker, "defender_map_position": defender,
                                 "destruction_percentage": destruction, "api_stars": sources["official_api"],
                                 "screenshot_stars": sources["game_screenshot"], "resolution": "unresolved"})
    return {"participants": public_participants, "attacks": public_attacks,
            "metrics": {"classification_counts": counts, "attack_stars_total": screenshot_stars,
                        "displayed_contribution_total": contribution}, "conflicts": public_conflicts}


def validate_manual_overlay(payload: Any) -> list[dict[str, Any]]:
    """Strictly validate a schema-v1 overlay and return an allowlisted view."""
    _require(isinstance(payload, Mapping), "manual overlay must be an object")
    _require(type(payload.get("schema_version")) is int and payload.get("schema_version") == 1, "unsupported manual overlay schema")
    wars = payload.get("wars")
    _require(isinstance(wars, list), "manual overlay wars must be a list")
    _scan({key: value for key, value in payload.items() if key not in {"wars"}})
    evidence_ids: set[str] = set()
    linked_ids: set[str] = set()
    result: list[dict[str, Any]] = []
    for evidence in wars:
        _require(isinstance(evidence, Mapping), "manual evidence record must be an object")
        evidence_id, linked = evidence.get("evidence_id"), evidence.get("linked_war_id")
        _require(isinstance(evidence_id, str) and evidence_id.strip() and evidence_id not in evidence_ids, "duplicate or missing evidence_id")
        _require(isinstance(linked, str) and linked.strip() and linked not in linked_ids, "duplicate or missing linked_war_id")
        evidence_ids.add(evidence_id); linked_ids.add(linked)
        safe = _validate_record(evidence)
        safe["linked_war_id"] = linked
        result.append(safe)
    return result


def _date(value: Any) -> str | None:
    return value[:8][0:4] + "-" + value[:8][4:6] + "-" + value[:8][6:8] if isinstance(value, str) and len(value) >= 8 else None


def _display_key(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.strip().casefold().split())
    return normalized or None


def _authoritative_aliases(
    history: Mapping[str, Any], current_members: Any,
) -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = {}

    def add(name: Any, tag: Any) -> None:
        key = _display_key(name)
        if key and isinstance(tag, str) and tag:
            aliases.setdefault(key, set()).add(tag)

    for record in history.get("wars", []):
        if not isinstance(record, Mapping):
            continue
        snapshots = [record.get("canonical")]
        snapshots.extend(
            observation.get("snapshot")
            for observation in record.get("observations", [])
            if isinstance(observation, Mapping)
        )
        for snapshot in snapshots:
            if not isinstance(snapshot, Mapping):
                continue
            for member in snapshot.get("members", []):
                if isinstance(member, Mapping):
                    add(member.get("display_name"), member.get("player_tag"))
    for member in current_members or ():
        add(getattr(member, "display_name", None), getattr(member, "player_tag", None))
    return aliases


def _canonical_position_index(record: Mapping[str, Any]) -> dict[int, str]:
    canonical = record.get("canonical")
    if not isinstance(canonical, Mapping):
        return {}
    index: dict[int, str] = {}
    for member in canonical.get("members", []):
        if not isinstance(member, Mapping):
            continue
        position, tag = member.get("map_position"), member.get("player_tag")
        if isinstance(position, int) and not isinstance(position, bool) and isinstance(tag, str) and tag:
            index[position] = tag
    return index


def _rebind_index(
    source_items: list[dict[str, Any]], copy_items: list[dict[str, Any]], index: Mapping[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    positions = {id(item): position for position, item in enumerate(source_items)}
    return {
        key: copy_items[positions[id(item)]]
        for key, item in (index or {}).items()
        if id(item) in positions
    }


def _empty_metric(nickname: str) -> dict[str, Any]:
    return {"nickname": nickname, "war_participations": 0, "attacks_used": 0,
            "attacks_available": 0, "stars_earned": 0, "average_stars": None,
            "last_war_date": None, "data_status": "available"}


def _apply_manual_player_metrics(
    history: Mapping[str, Any], evidence: list[dict[str, Any]], projected: dict[str, Any],
    player_index: Mapping[str, dict[str, Any]] | None, roster_index: Mapping[str, dict[str, Any]] | None,
    current_members: Any,
) -> None:
    """Apply manual deltas once to private indexes, then copy safe totals to roster."""
    records = {str(record.get("war_id")): record for record in history.get("wars", []) if isinstance(record, Mapping) and isinstance(record.get("war_id"), str)}
    aliases = _authoritative_aliases(history, current_members)
    bridge_aliases: dict[str, set[str]] = {}
    for item in evidence:
        record = records.get(item["linked_war_id"])
        if record is None:
            continue
        positions = _canonical_position_index(record)
        for participant in item["participants"]:
            tag = positions.get(participant["war_position"])
            key = _display_key(participant["nickname"])
            if tag and key:
                bridge_aliases.setdefault(key, set()).add(tag)

    private_metrics = dict(player_index or {})
    current_names = {getattr(member, "player_tag", None): getattr(member, "display_name", None) for member in current_members or ()}
    for item in evidence:
        record = records.get(item["linked_war_id"])
        if record is None:
            continue
        canonical_positions = _canonical_position_index(record)
        canonical_tags = set(canonical_positions.values())
        war_log = record.get("war_log") if isinstance(record.get("war_log"), Mapping) else {}
        canonical = record.get("canonical")
        date = _date((canonical or {}).get("end_time") if isinstance(canonical, Mapping) else war_log.get("end_time"))
        attacks_per_member = ((canonical or {}).get("attacks_per_member") if isinstance(canonical, Mapping) else war_log.get("attacks_per_member"))
        normal_by_position: dict[int, list[dict[str, Any]]] = {}
        for attack in item["attacks"]:
            normal_by_position.setdefault(attack["attacker_map_position"], []).append(attack)
        conflicts_by_position: dict[int, int] = {}
        for conflict in item["conflicts"]:
            position = conflict["attacker_map_position"]
            conflicts_by_position[position] = conflicts_by_position.get(position, 0) + 1
        for participant in item["participants"]:
            position, nickname = participant["war_position"], participant["nickname"]
            key = _display_key(nickname)
            candidates = bridge_aliases.get(key, set()) if key else set()
            if len(candidates) != 1:
                candidates = aliases.get(key, set()) if key else set()
            if len(candidates) != 1:
                warnings.warn("manual participant could not be linked uniquely; metrics skipped", stacklevel=2)
                continue
            tag = next(iter(candidates))
            api_participation = tag in canonical_tags
            normal_attacks = normal_by_position.get(position, [])
            if api_participation:
                additions = [attack for attack in normal_attacks if attack["source"] == "screenshot_only"]
                conflict_used = 0
                add_participation = False
            else:
                additions = [attack for attack in normal_attacks if attack["source"] != "ambiguous"]
                conflict_used = conflicts_by_position.get(position, 0)
                add_participation = True
            if not additions and not conflict_used and not add_participation:
                continue
            metric = private_metrics.get(tag)
            has_api_metric = metric is not None
            if metric is None:
                metric = _empty_metric(current_names.get(tag) or nickname)
                private_metrics[tag] = metric
                projected.setdefault("player_metrics", []).append(metric)
            was_api_only = has_api_metric and metric.get("metrics_provenance", "official_api") == "official_api"
            metric["data_status"] = "available"
            if add_participation:
                metric["war_participations"] = int(metric.get("war_participations") or 0) + 1
                if isinstance(attacks_per_member, int) and not isinstance(attacks_per_member, bool):
                    metric["attacks_available"] = int(metric.get("attacks_available") or 0) + attacks_per_member
                else:
                    metric["attacks_available"] = None
                metric["manual_war_participations"] = int(metric.get("manual_war_participations") or 0) + 1
            used = len(additions) + conflict_used
            stars = sum(attack["stars"] for attack in additions)
            metric["attacks_used"] = int(metric.get("attacks_used") or 0) + used
            metric["stars_earned"] = int(metric.get("stars_earned") or 0) + stars
            metric["manual_attacks_used"] = int(metric.get("manual_attacks_used") or 0) + used
            metric["manual_stars_earned"] = int(metric.get("manual_stars_earned") or 0) + stars
            metric["last_war_date"] = max(filter(None, [metric.get("last_war_date"), date]), default=None)
            metric["average_stars"] = (round(metric["stars_earned"] / metric["attacks_used"], 2) if metric["attacks_used"] else None)
            metric["metrics_provenance"] = "api_and_screenshot" if has_api_metric else "screenshot"
            metric["new_stars_contributed"] = None
            metric["new_stars_contribution_status"] = "unavailable_with_manual_evidence"
            if conflict_used:
                metric["manual_conflict_sources"] = True

    for tag, roster_member in (roster_index or {}).items():
        metric = private_metrics.get(tag)
        if metric is None:
            continue
        for key in ("data_status", "war_participations", "attacks_used", "attacks_available",
                    "stars_earned", "average_stars", "last_war_date", "manual_war_participations",
                    "manual_attacks_used", "manual_stars_earned", "metrics_provenance"):
            if key in metric:
                roster_member[key] = metric[key]
        if "new_stars_contribution_status" in metric:
            roster_member["new_stars_contributed"] = metric["new_stars_contributed"]
            roster_member["new_stars_contribution_status"] = metric["new_stars_contribution_status"]
        if metric.get("manual_conflict_sources") is True:
            roster_member["manual_conflict_sources"] = True


def project_manual_history(history: Mapping[str, Any], overlay: Any, public_history: Mapping[str, Any], public_war_index: Mapping[str, dict[str, Any]] | None = None, public_player_index: Mapping[str, dict[str, Any]] | None = None, public_roster_index: Mapping[str, dict[str, Any]] | None = None, current_members: Any = ()) -> dict[str, Any]:
    """Attach allowlisted evidence using exact private links only during assembly."""
    projected = copy.deepcopy(dict(public_history))
    # Rebind exact indexed records to this copy by their object position, never by public fields.
    source_wars = list(public_history.get("wars", []))
    copy_wars = list(projected.get("wars", []))
    copy_index: dict[str, dict[str, Any]] = {}
    if public_war_index is not None:
        positions = {id(item): index for index, item in enumerate(source_wars)}
        for war_id, item in public_war_index.items():
            if id(item) in positions:
                copy_index[war_id] = copy_wars[positions[id(item)]]
    player_copy_index = _rebind_index(
        list(public_history.get("player_metrics", [])),
        list(projected.get("player_metrics", [])),
        public_player_index,
    )
    records = {str(record.get("war_id")): record for record in history.get("wars", []) if isinstance(record, Mapping) and isinstance(record.get("war_id"), str)}
    try:
        evidence = validate_manual_overlay(overlay)
    except ManualHistoryProjectionError:
        raise
    for item in evidence:
        war_id = item["linked_war_id"]
        record = records.get(war_id)
        if record is None:
            warnings.warn("manual history evidence references an unknown war; record skipped", stacklevel=2)
            continue
        war = copy_index.get(war_id)
        canonical, war_log = record.get("canonical"), record.get("war_log") or {}
        if war is None:
            if isinstance(canonical, Mapping):
                warnings.warn("manual history evidence has no exact public war link; record skipped", stacklevel=2)
                continue
            clan = war_log.get("clan") if isinstance(war_log, Mapping) else {}
            war = {"state": "warEnded", "lifecycle_status": record.get("lifecycle_status"),
                   "reconciliation_status": record.get("reconciliation_status"),
                   "end_time": _date(war_log.get("end_time") if isinstance(war_log, Mapping) else None),
                   "team_size": war_log.get("team_size") if isinstance(war_log, Mapping) else None,
                   "participants": 0, "attacks_per_member": war_log.get("attacks_per_member") if isinstance(war_log, Mapping) else None,
                   "attacks_used": 0, "attacks_available": None, "clan_stars": clan.get("stars") if isinstance(clan, Mapping) else None,
                   "attack_stars_total": None, "stars_consistency_status": "unavailable",
                   "new_stars_contribution_status": "unavailable", "members": []}
            projected.setdefault("wars", []).append(war)
        clan = war_log.get("clan") if isinstance(war_log, Mapping) else None
        if war.get("clan_stars") is None and isinstance(clan, Mapping):
            # This is the authoritative aggregate already retained in history,
            # not a manual screenshot-derived value.
            war["clan_stars"] = clan.get("stars")
        counts = item["metrics"]["classification_counts"]
        coverage = "official_partial_detail_with_manual_supplement" if isinstance(canonical, Mapping) else "official_aggregate_only_with_manual_detail"
        provenance = "mixed_sources" if isinstance(canonical, Mapping) else "screenshot"
        war["manual_detail"] = {"coverage_status": coverage, "provenance": provenance,
            "summary": {"participants": len(item["participants"]), "api_detailed_attacks": war.get("attacks_used", 0) if isinstance(canonical, Mapping) else 0,
                        "screenshot_attacks": sum(counts.values()), "screenshot_attack_stars": item["metrics"]["attack_stars_total"],
                        "official_contribution": item["metrics"]["displayed_contribution_total"], "exact_api_matches": counts["exact_api_match"],
                        "screenshot_only_attacks": counts["manual_only"], "unresolved_conflicts": counts["source_conflict"]},
            "participants": item["participants"], "attacks": item["attacks"], "source_conflicts": item["conflicts"]}
    if isinstance(projected.get("player_metrics"), list):
        _apply_manual_player_metrics(
            history, evidence, projected, player_copy_index, public_roster_index, current_members
        )
        projected["player_metrics"].sort(key=lambda item: str(item["nickname"]).casefold())
    projected["wars"].sort(key=lambda war: (war.get("end_time") or "", war.get("participants") or 0), reverse=True)
    projected["wars_observed"] = len(projected["wars"])
    return projected
