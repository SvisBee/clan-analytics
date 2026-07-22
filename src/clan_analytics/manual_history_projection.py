"""Safe optional projection of local screenshot evidence into public history."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any


class ManualHistoryProjectionError(ValueError):
    """Raised when local manual evidence is not safe to project."""


_CLASSES = {"exact_api_match", "manual_only", "source_conflict", "ambiguous"}
_FORBIDDEN = {
    "playertag", "attackertag", "defendertag", "clantag", "opponenttag",
    "order", "attackorder", "globalorder", "token", "authorization", "dpapi",
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


def validate_manual_overlay(payload: Any) -> list[dict[str, Any]]:
    """Validate schema-v1 evidence without exposing private local provenance."""

    _require(isinstance(payload, Mapping), "manual overlay must be an object")
    _require(payload.get("schema_version") == 1, "unsupported manual overlay schema")
    wars = payload.get("wars")
    _require(isinstance(wars, list), "manual overlay wars must be a list")
    _scan(payload)
    evidence_ids: set[str] = set()
    linked_ids: set[str] = set()
    result: list[dict[str, Any]] = []
    for evidence in wars:
        _require(isinstance(evidence, Mapping), "manual evidence record must be an object")
        evidence_id, linked = evidence.get("evidence_id"), evidence.get("linked_war_id")
        _require(isinstance(evidence_id, str) and evidence_id and evidence_id not in evidence_ids, "duplicate or missing evidence_id")
        _require(isinstance(linked, str) and linked and linked not in linked_ids, "duplicate or missing linked_war_id")
        evidence_ids.add(evidence_id); linked_ids.add(linked)
        participants, attacks, metrics = evidence.get("participants"), evidence.get("attacks"), evidence.get("metrics")
        _require(isinstance(participants, list) and isinstance(attacks, list) and isinstance(metrics, Mapping), "manual evidence sections are invalid")
        counts = {kind: 0 for kind in _CLASSES}; stars = 0
        public_attacks: list[dict[str, Any]] = []
        for attack in attacks:
            _require(isinstance(attack, Mapping), "manual attack must be an object")
            kind, attack_stars, destruction, slot = attack.get("classification"), attack.get("stars"), attack.get("destruction_percentage"), attack.get("screenshot_slot")
            _require(kind in _CLASSES, "invalid manual classification")
            _require(isinstance(attack_stars, int) and 0 <= attack_stars <= 3, "invalid manual stars")
            _require(isinstance(destruction, (int, float)) and 0 <= destruction <= 100, "invalid manual destruction")
            _require(slot in (1, 2), "invalid screenshot slot")
            counts[kind] += 1; stars += attack_stars
            source = {"exact_api_match": "api_and_screenshot", "manual_only": "screenshot_only", "source_conflict": "source_conflict", "ambiguous": "ambiguous"}[kind]
            public_attacks.append({"attacker_map_position": attack.get("attacker_map_position"), "defender_map_position": attack.get("defender_map_position"), "destruction_percentage": destruction, "stars": attack_stars, "screenshot_slot": slot, "source": source})
        _require(metrics.get("classification_counts") == counts, "manual classification counts mismatch")
        _require(metrics.get("attack_stars_total") == stars, "manual attack stars mismatch")
        conflicts = evidence.get("source_conflicts")
        _require(isinstance(conflicts, list) and len(conflicts) == counts["source_conflict"], "manual conflicts mismatch")
        public_conflicts = []
        for conflict in conflicts:
            _require(isinstance(conflict, Mapping) and conflict.get("resolution") == "unresolved", "manual conflict must remain unresolved")
            claims, shared = conflict.get("claims"), conflict.get("shared_facts")
            _require(isinstance(claims, list) and len(claims) >= 2 and isinstance(shared, Mapping), "invalid manual conflict")
            types = [claim.get("source_type") for claim in claims if isinstance(claim, Mapping)]
            claim_stars = [claim.get("stars") for claim in claims if isinstance(claim, Mapping)]
            _require(len(types) == len(claims) and len(set(types)) >= 2 and len(set(claim_stars)) >= 2, "manual conflict claims are not distinct")
            _require(all(isinstance(value, int) and 0 <= value <= 3 for value in claim_stars), "invalid conflict stars")
            public_conflicts.append({"attacker_map_position": shared.get("attacker_map_position"), "defender_map_position": shared.get("defender_map_position"), "destruction_percentage": shared.get("destruction_percentage"), "api_stars": next((claim["stars"] for claim in claims if claim.get("source_type") == "official_api"), None), "screenshot_stars": next((claim["stars"] for claim in claims if claim.get("source_type") == "game_screenshot"), None), "resolution": "unresolved"})
        result.append({"linked_war_id": linked, "participants": [{"nickname": item.get("display_name_raw"), "war_position": item.get("map_position")} for item in participants], "attacks": public_attacks, "metrics": {"attack_stars_total": stars, "displayed_contribution_total": metrics.get("displayed_contribution_total"), "classification_counts": counts}, "conflicts": public_conflicts})
    return result


def _date(value: Any) -> str | None:
    return value[:8][0:4] + "-" + value[:8][4:6] + "-" + value[:8][6:8] if isinstance(value, str) and len(value) >= 8 else None


def project_manual_history(history: Mapping[str, Any], overlay: Any, public_history: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of public history with optional, separately sourced details."""

    projected = copy.deepcopy(dict(public_history))
    evidence_by_war = {item["linked_war_id"]: item for item in validate_manual_overlay(overlay)}
    records = {record.get("war_id"): record for record in history.get("wars", []) if isinstance(record, Mapping)}
    by_public_key = {(item.get("end_time"), item.get("lifecycle_status"), item.get("reconciliation_status")): item for item in projected.get("wars", []) if isinstance(item, Mapping)}
    for war_id, evidence in evidence_by_war.items():
        record = records.get(war_id)
        if record is None:
            continue
        canonical, war_log = record.get("canonical"), record.get("war_log") or {}
        clan, opponent = war_log.get("clan") or {}, war_log.get("opponent") or {}
        end_time = _date((canonical or {}).get("end_time") or war_log.get("end_time"))
        key = (end_time, record.get("lifecycle_status"), record.get("reconciliation_status"))
        war = by_public_key.get(key)
        if war is None:
            war = {"state": (canonical or {}).get("state", "warEnded"), "lifecycle_status": record.get("lifecycle_status"), "reconciliation_status": record.get("reconciliation_status"), "end_time": end_time, "team_size": war_log.get("team_size"), "participants": 0, "attacks_per_member": war_log.get("attacks_per_member"), "attacks_used": 0, "attacks_available": None, "clan_stars": clan.get("stars"), "attack_stars_total": None, "stars_consistency_status": "unavailable", "new_stars_contribution_status": "unavailable", "members": []}
            projected.setdefault("wars", []).append(war)
        manual_only = evidence["metrics"]["classification_counts"]["manual_only"]
        coverage = "official_aggregate_only_with_manual_detail" if canonical is None else "official_partial_detail_with_manual_supplement"
        war["manual_detail"] = {"coverage_status": coverage, "provenance": "screenshot", "participants": evidence["participants"], "attacks": evidence["attacks"], "manual_attacks": len(evidence["attacks"]), "screenshot_attack_stars": evidence["metrics"]["attack_stars_total"], "official_contribution": evidence["metrics"]["displayed_contribution_total"], "classification_counts": evidence["metrics"]["classification_counts"], "exact_api_matches": evidence["metrics"]["classification_counts"]["exact_api_match"], "manual_only_attacks": manual_only, "source_conflicts": evidence["conflicts"]}
    projected["wars"].sort(key=lambda war: (war.get("end_time") or "", war.get("participants") or 0), reverse=True)
    projected["wars_observed"] = len(projected["wars"])
    return projected
