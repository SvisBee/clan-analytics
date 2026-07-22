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


def project_manual_history(history: Mapping[str, Any], overlay: Any, public_history: Mapping[str, Any], public_war_index: Mapping[str, dict[str, Any]] | None = None) -> dict[str, Any]:
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
    records = {str(record.get("war_id")): record for record in history.get("wars", []) if isinstance(record, Mapping) and isinstance(record.get("war_id"), str)}
    try:
        evidence = validate_manual_overlay(overlay)
    except ManualHistoryProjectionError:
        raise
    for item in evidence:
        war_id = item.pop("linked_war_id")
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
    projected["wars"].sort(key=lambda war: (war.get("end_time") or "", war.get("participants") or 0), reverse=True)
    projected["wars_observed"] = len(projected["wars"])
    return projected
