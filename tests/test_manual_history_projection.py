from __future__ import annotations

import copy
import json
import sys
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clan_analytics.manual_history_projection import (  # noqa: E402
    ManualHistoryProjectionError,
    project_manual_history,
    validate_manual_overlay,
)
from clan_analytics.site_update import SiteUpdateError, _scan_public  # noqa: E402


def overlay(**changes):
    item = {
        "evidence_id": "evidence-a", "linked_war_id": "war-a",
        "participants": [{"display_name_raw": "Fictional", "map_position": 1}],
        "attacks": [{"attacker_map_position": 1, "defender_map_position": 1,
                     "destruction_percentage": 100, "stars": 3, "screenshot_slot": 1,
                     "classification": "manual_only"}],
        "metrics": {"classification_counts": {"exact_api_match": 0, "manual_only": 1,
                    "source_conflict": 0, "ambiguous": 0}, "attack_stars_total": 3,
                    "displayed_contribution_total": 3}, "source_conflicts": [],
    }
    item.update(changes)
    return {"schema_version": 1, "wars": [item]}


def detailed_history():
    return {"wars": [{"war_id": "war-a", "canonical": {"state": "warEnded"},
                       "war_log": {}, "lifecycle_status": "finalized",
                       "reconciliation_status": "matched"}]}


def detailed_public():
    war = {"end_time": "2026-01-01", "lifecycle_status": "finalized",
           "reconciliation_status": "matched", "attacks_used": 18,
           "clan_stars": 45, "members": []}
    return {"schema_version": 2, "wars_observed": 1, "wars": [war]}, war


class ManualHistoryProjectionTests(unittest.TestCase):
    def validate_error(self, mutate):
        candidate = overlay(); mutate(candidate)
        with self.assertRaises(ManualHistoryProjectionError):
            validate_manual_overlay(candidate)

    def test_api_only_output_is_unchanged_without_overlay(self):
        public, _ = detailed_public()
        self.assertEqual(public, copy.deepcopy(public))

    def test_exact_war_id_links_detailed_war(self):
        public, war = detailed_public()
        result = project_manual_history(detailed_history(), overlay(), public, {"war-a": war})
        self.assertEqual(result["wars"][0]["manual_detail"]["summary"]["api_detailed_attacks"], 18)

    def test_exact_war_id_links_aggregate_only_war(self):
        history = {"wars": [{"war_id": "war-a", "canonical": None,
                    "war_log": {"end_time": "20260101T000000.000Z", "team_size": 1,
                                "clan": {"stars": 3}}, "lifecycle_status": "closed",
                    "reconciliation_status": "aggregate"}]}
        result = project_manual_history(history, overlay(), {"schema_version": 2, "wars": [], "wars_observed": 0}, {})
        self.assertEqual(result["wars"][0]["manual_detail"]["summary"]["api_detailed_attacks"], 0)

    def test_identical_public_fields_do_not_collide(self):
        public, first = detailed_public(); second = copy.deepcopy(first); public["wars"].append(second)
        history = detailed_history(); history["wars"].append({**copy.deepcopy(history["wars"][0]), "war_id": "war-b"})
        result = project_manual_history(history, overlay(), public, {"war-a": first, "war-b": second})
        self.assertIn("manual_detail", result["wars"][0]); self.assertNotIn("manual_detail", result["wars"][1])

    def test_unknown_link_is_skipped_safely(self):
        public, war = detailed_public(); candidate = overlay(linked_war_id="unknown")
        with warnings.catch_warnings(record=True) as caught:
            result = project_manual_history(detailed_history(), candidate, public, {"war-a": war})
        self.assertNotIn("manual_detail", result["wars"][0]); self.assertTrue(caught)

    def test_projection_has_no_internal_ids(self):
        public, war = detailed_public(); rendered = json.dumps(project_manual_history(detailed_history(), overlay(), public, {"war-a": war}))
        for value in ("war-a", "evidence-a", "linked_war_id", "evidence_id"):
            self.assertNotIn(value, rendered)

    def test_projection_does_not_mutate_inputs(self):
        public, war = detailed_public(); history, source = detailed_history(), overlay()
        originals = copy.deepcopy((history, public, source))
        project_manual_history(history, source, public, {"war-a": war})
        self.assertEqual((history, public, source), originals)

    def test_projection_is_deterministic(self):
        public, war = detailed_public()
        one = project_manual_history(detailed_history(), overlay(), public, {"war-a": war})
        two = project_manual_history(detailed_history(), overlay(), public, {"war-a": war})
        self.assertEqual(one, two)

    def test_conflict_is_not_in_public_attack_list(self):
        conflict = {"resolution": "unresolved", "shared_facts": {"attacker_map_position": 1, "defender_map_position": 1, "destruction_percentage": 100}, "claims": [{"source_type": "official_api", "stars": 1}, {"source_type": "game_screenshot", "stars": 2}]}
        candidate = overlay(attacks=[{"attacker_map_position": 1, "defender_map_position": 1, "destruction_percentage": 100, "stars": 2, "screenshot_slot": 1, "classification": "source_conflict"}], metrics={"classification_counts": {"exact_api_match": 0, "manual_only": 0, "source_conflict": 1, "ambiguous": 0}, "attack_stars_total": 2, "displayed_contribution_total": 1}, source_conflicts=[conflict])
        public, war = detailed_public(); detail = project_manual_history(detailed_history(), candidate, public, {"war-a": war})["wars"][0]["manual_detail"]
        self.assertEqual(detail["attacks"], []); self.assertNotIn("stars", detail["source_conflicts"][0])

    def test_manual_only_attack_remains_separate(self):
        safe = validate_manual_overlay(overlay())[0]
        self.assertEqual(safe["attacks"][0]["source"], "screenshot_only")

    def test_official_clan_score_is_never_replaced(self):
        public, war = detailed_public(); result = project_manual_history(detailed_history(), overlay(), public, {"war-a": war})
        self.assertEqual(result["wars"][0]["clan_stars"], 45)

    def test_missing_public_score_uses_authoritative_war_log_aggregate(self):
        public, war = detailed_public(); war["clan_stars"] = None
        history = detailed_history(); history["wars"][0]["war_log"] = {"clan": {"stars": 45}}
        result = project_manual_history(history, overlay(), public, {"war-a": war})
        self.assertEqual(result["wars"][0]["clan_stars"], 45)

    def test_active_war_is_not_touched_when_unlinked(self):
        public = {"schema_version": 2, "wars": [{"lifecycle_status": "active"}], "wars_observed": 1}
        result = project_manual_history({"wars": []}, overlay(), public, {})
        self.assertEqual(result, public)

    def test_invalid_schema_is_rejected(self):
        with self.assertRaises(ManualHistoryProjectionError): validate_manual_overlay({"schema_version": True, "wars": []})

    def test_private_tag_is_rejected(self):
        self.validate_error(lambda x: x["wars"][0]["attacks"][0].update({"playerTag": "x"}))

    def test_private_order_is_rejected(self):
        self.validate_error(lambda x: x["wars"][0]["attacks"][0].update({"globalOrder": 1}))

    def test_bool_stars_are_rejected(self):
        self.validate_error(lambda x: x["wars"][0]["attacks"][0].update({"stars": True}))

    def test_bool_destruction_is_rejected(self):
        self.validate_error(lambda x: x["wars"][0]["attacks"][0].update({"destruction_percentage": False}))

    def test_invalid_participant_position_is_rejected(self):
        self.validate_error(lambda x: x["wars"][0]["participants"][0].update({"map_position": 0}))

    def test_duplicate_participant_position_is_rejected(self):
        self.validate_error(lambda x: x["wars"][0]["participants"].append({"display_name_raw": "Other", "map_position": 1}))

    def test_duplicate_attacker_slot_is_rejected(self):
        self.validate_error(lambda x: x["wars"][0]["attacks"].append(copy.deepcopy(x["wars"][0]["attacks"][0])))

    def test_metric_counts_mismatch_is_rejected(self):
        self.validate_error(lambda x: x["wars"][0]["metrics"]["classification_counts"].update({"manual_only": 2}))

    def test_invalid_displayed_contribution_is_rejected(self):
        self.validate_error(lambda x: x["wars"][0]["metrics"].update({"displayed_contribution_total": True}))

    def test_resolved_conflict_is_rejected(self):
        self.validate_error(lambda x: x["wars"][0].update({"source_conflicts": [{"resolution": "resolved"}]}))

    def test_conflict_without_official_claim_is_rejected(self):
        self._bad_conflict("game_screenshot", "game_screenshot")

    def test_conflict_without_screenshot_claim_is_rejected(self):
        self._bad_conflict("official_api", "official_api")

    def _bad_conflict(self, first, second):
        conflict = {"resolution": "unresolved", "shared_facts": {"attacker_map_position": 1, "defender_map_position": 1, "destruction_percentage": 100}, "claims": [{"source_type": first, "stars": 1}, {"source_type": second, "stars": 2}]}
        candidate = overlay(attacks=[{"attacker_map_position": 1, "defender_map_position": 1, "destruction_percentage": 100, "stars": 2, "screenshot_slot": 1, "classification": "source_conflict"}], metrics={"classification_counts": {"exact_api_match": 0, "manual_only": 0, "source_conflict": 1, "ambiguous": 0}, "attack_stars_total": 2, "displayed_contribution_total": 1}, source_conflicts=[conflict])
        with self.assertRaises(ManualHistoryProjectionError): validate_manual_overlay(candidate)

    def test_conflict_shared_facts_mismatch_is_rejected(self):
        conflict = {"resolution": "unresolved", "shared_facts": {"attacker_map_position": 2, "defender_map_position": 1, "destruction_percentage": 100}, "claims": [{"source_type": "official_api", "stars": 1}, {"source_type": "game_screenshot", "stars": 2}]}
        candidate = overlay(attacks=[{"attacker_map_position": 1, "defender_map_position": 1, "destruction_percentage": 100, "stars": 2, "screenshot_slot": 1, "classification": "source_conflict"}], metrics={"classification_counts": {"exact_api_match": 0, "manual_only": 0, "source_conflict": 1, "ambiguous": 0}, "attack_stars_total": 2, "displayed_contribution_total": 1}, source_conflicts=[conflict])
        with self.assertRaises(ManualHistoryProjectionError): validate_manual_overlay(candidate)

    def test_public_privacy_scan_rejects_internal_projection_keys(self):
        for key in ("warId", "linked_war_id", "evidence-id", "source_hashes", "attackOrder"):
            with self.assertRaises(SiteUpdateError): _scan_public({key: "private"})


class ManualPlayerMetricTests(unittest.TestCase):
    def _inputs(self, *, aggregate=False, source="manual_only"):
        canonical = None if aggregate else {"state": "warEnded", "end_time": "20260101T000000.000Z", "attacks_per_member": 2, "members": [{"player_tag": "tag-a", "display_name": "Alpha", "map_position": 1}]}
        history = {"wars": [{"war_id": "war-a", "canonical": canonical,
                    "war_log": {"end_time": "20260101T000000.000Z", "attacks_per_member": 2, "clan": {"stars": 3}},
                    "lifecycle_status": "closed", "reconciliation_status": "matched", "observations": []}]}
        item = overlay()["wars"][0]; item["participants"][0]["display_name_raw"] = "Alpha"; item["attacks"][0]["classification"] = source
        item["metrics"]["classification_counts"] = {"exact_api_match": int(source == "exact_api_match"), "manual_only": int(source == "manual_only"), "source_conflict": 0, "ambiguous": 0}
        public_war = {"end_time": "2026-01-01", "attacks_used": 1, "clan_stars": 3, "members": []}
        public = {"schema_version": 2, "wars": [] if aggregate else [public_war], "wars_observed": 0 if aggregate else 1,
                  "player_metrics": [{"nickname": "Alpha", "data_status": "available", "war_participations": 2, "attacks_used": 3, "attacks_available": 4, "stars_earned": 9, "average_stars": 3, "last_war_date": "2026-01-02", "new_stars_contributed": 4}]}
        roster = {"nickname": "Alpha", "data_status": "available", "war_participations": 2, "attacks_used": 3, "attacks_available": 4, "stars_earned": 9, "average_stars": 3, "last_war_date": "2026-01-02"}
        return history, {"schema_version": 1, "wars": [item]}, public, public_war, roster

    def _project(self, *, aggregate=False, source="manual_only"):
        history, evidence, public, war, roster = self._inputs(aggregate=aggregate, source=source)
        return project_manual_history(history, evidence, public, {} if aggregate else {"war-a": war}, {"tag-a": public["player_metrics"][0]}, {"tag-a": roster}, [SimpleNamespace(player_tag="tag-a", display_name="Alpha")]), roster

    def test_aggregate_manual_participation_increments_wars(self):
        result, _ = self._project(aggregate=True)
        self.assertEqual(result["player_metrics"][0]["war_participations"], 3)

    def test_aggregate_manual_attacks_increment_used_and_available(self):
        result, _ = self._project(aggregate=True)
        metric = result["player_metrics"][0]
        self.assertEqual((metric["attacks_used"], metric["attacks_available"]), (4, 6))

    def test_aggregate_manual_stars_increment_earned(self):
        result, _ = self._project(aggregate=True)
        self.assertEqual(result["player_metrics"][0]["stars_earned"], 12)

    def test_partial_exact_api_attack_is_not_double_counted(self):
        result, _ = self._project(source="exact_api_match")
        self.assertEqual(result["player_metrics"][0]["attacks_used"], 3)

    def test_partial_screenshot_only_attack_is_added(self):
        result, _ = self._project(source="manual_only")
        self.assertEqual(result["player_metrics"][0]["attacks_used"], 4)

    def test_manual_metric_nulls_new_star_contribution(self):
        result, _ = self._project(aggregate=True)
        metric = result["player_metrics"][0]
        self.assertIsNone(metric["new_stars_contributed"])
        self.assertEqual(metric["new_stars_contribution_status"], "unavailable_with_manual_evidence")

    def test_roster_and_history_metrics_are_equal(self):
        result, roster = self._project(aggregate=True)
        metric = result["player_metrics"][0]
        for key in ("war_participations", "attacks_used", "attacks_available", "stars_earned", "average_stars"):
            self.assertEqual(metric[key], roster[key])

    def test_repeated_projection_is_non_accumulating(self):
        one, _ = self._project(aggregate=True)
        two, _ = self._project(aggregate=True)
        self.assertEqual(one, two)

    def test_ambiguous_alias_is_not_linked(self):
        history, evidence, public, war, roster = self._inputs(aggregate=True)
        history["wars"][0]["observations"] = [{"snapshot": {"members": [{"player_tag": "tag-b", "display_name": "Alpha"}]}}]
        result = project_manual_history(history, evidence, public, {}, {"tag-a": public["player_metrics"][0]}, {"tag-a": roster}, [SimpleNamespace(player_tag="tag-a", display_name="Alpha")])
        self.assertEqual(result["player_metrics"][0]["war_participations"], 2)

    def test_bridge_alias_links_variant_across_aggregate_war(self):
        history, evidence, public, _, roster = self._inputs(aggregate=True)
        history["wars"].append({"war_id": "war-b", "canonical": {"members": [{"player_tag": "tag-a", "display_name": "Alpha API", "map_position": 1}]}, "observations": []})
        evidence["wars"][0]["participants"][0]["display_name_raw"] = "Alpha API"
        result = project_manual_history(history, evidence, public, {}, {"tag-a": public["player_metrics"][0]}, {"tag-a": roster}, [SimpleNamespace(player_tag="tag-a", display_name="Alpha")])
        self.assertEqual(result["player_metrics"][0]["war_participations"], 3)


if __name__ == "__main__":
    unittest.main()
