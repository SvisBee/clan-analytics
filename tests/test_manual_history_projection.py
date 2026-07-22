from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clan_analytics.manual_history_projection import (
    ManualHistoryProjectionError,
    project_manual_history,
    validate_manual_overlay,
)


def overlay(**changes):
    item = {
        "evidence_id": "evidence-a", "linked_war_id": "war-a", "participants": [{"display_name_raw": "Player", "map_position": 1}],
        "attacks": [{"attacker_map_position": 1, "defender_map_position": 1, "destruction_percentage": 100, "stars": 3, "screenshot_slot": 1, "classification": "manual_only"}],
        "metrics": {"classification_counts": {"exact_api_match": 0, "manual_only": 1, "source_conflict": 0, "ambiguous": 0}, "attack_stars_total": 3, "displayed_contribution_total": 3},
        "source_conflicts": [],
    }
    item.update(changes)
    return {"schema_version": 1, "wars": [item]}


class ManualHistoryProjectionTests(unittest.TestCase):
    def test_valid_overlay_is_projected_without_mutating_inputs(self):
        history = {"wars": [{"war_id": "war-a", "canonical": None, "war_log": {"end_time": "20260101T000000.000Z", "team_size": 1, "clan": {"stars": 3}, "opponent": {"stars": 0}}, "lifecycle_status": "closed_war_log_only", "reconciliation_status": "unmatched_aggregate_only"}]}
        public = {"schema_version": 2, "wars": [], "wars_observed": 0}
        original_history, original_public = copy.deepcopy(history), copy.deepcopy(public)
        result = project_manual_history(history, overlay(), public)
        self.assertEqual(history, original_history); self.assertEqual(public, original_public)
        self.assertEqual(result["wars"][0]["manual_detail"]["coverage_status"], "official_aggregate_only_with_manual_detail")
        self.assertNotIn("linked_war_id", str(result))

    def test_invalid_schema_is_rejected(self):
        with self.assertRaises(ManualHistoryProjectionError): validate_manual_overlay({"schema_version": 2, "wars": []})

    def test_private_tag_and_order_are_rejected(self):
        for key in ("playerTag", "attackerTag", "defender_tag", "globalOrder"):
            candidate = overlay(); candidate["wars"][0]["attacks"][0][key] = "private"
            with self.assertRaises(ManualHistoryProjectionError): validate_manual_overlay(candidate)

    def test_invalid_attack_values_and_resolved_conflict_are_rejected(self):
        for key, value in (("stars", 4), ("destruction_percentage", 101), ("screenshot_slot", 3)):
            candidate = overlay(); candidate["wars"][0]["attacks"][0][key] = value
            with self.assertRaises(ManualHistoryProjectionError): validate_manual_overlay(candidate)
        candidate = overlay(source_conflicts=[{"resolution": "resolved", "claims": [], "shared_facts": {}}])
        candidate["wars"][0]["metrics"]["classification_counts"]["source_conflict"] = 1
        with self.assertRaises(ManualHistoryProjectionError): validate_manual_overlay(candidate)

    def test_unknown_link_is_not_projected(self):
        result = project_manual_history({"wars": []}, overlay(), {"schema_version": 2, "wars": [], "wars_observed": 0})
        self.assertEqual(result["wars"], [])
