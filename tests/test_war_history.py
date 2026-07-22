from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
FIXTURES = Path(__file__).with_name("fixtures")
sys.path.insert(0, str(SRC_ROOT))

from clan_analytics.api.normalization import (  # noqa: E402
    build_public_roster,
    normalize_clan,
    normalize_current_war,
    normalize_war_log,
)
from clan_analytics.history import (  # noqa: E402
    HISTORY_SCHEMA_VERSION,
    HistoryError,
    build_public_war_history,
    detailed_wars,
    empty_history,
    load_history,
    merge_war_history,
    migrate_history,
    reconcile_war_log,
    restore_history_backup,
    write_history_atomic,
)


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def war_payload(**changes):
    payload = load("current_war.json")
    for key, value in changes.items():
        payload[key] = value
    return payload


def normalized(payload=None, *, collected_at="2026-07-20T12:00:00Z"):
    return normalize_current_war(
        war_payload() if payload is None else payload,
        collected_at=collected_at,
        raw_source_reference="fixture",
    )


def v1_history():
    war = normalized()
    from dataclasses import asdict

    return {
        "schema_version": 1,
        "wars": [
            {
                "war_id": "a" * 64,
                "first_collected_at": "2026-07-20T12:00:00Z",
                "last_collected_at": "2026-07-20T13:00:00Z",
                "observations": 2,
                "finalized": False,
                "states_seen": ["inWar"],
                "latest": asdict(war),
            }
        ],
    }


class WarHistoryObservationTests(unittest.TestCase):
    def test_identical_snapshot_is_deduplicated(self) -> None:
        first, _ = merge_war_history(empty_history(), normalized())
        second, changed = merge_war_history(
            first, normalized(collected_at="2026-07-20T13:00:00Z")
        )
        self.assertFalse(changed)
        self.assertEqual(len(second["wars"]), 1)
        self.assertEqual(len(second["wars"][0]["observations"]), 1)

    def test_sequential_change_adds_immutable_observation(self) -> None:
        first, _ = merge_war_history(empty_history(), normalized())
        payload = war_payload()
        payload["clan"]["members"][0]["attacks"] = [
            {
                "attackerTag": "#DEMO002",
                "defenderTag": "#TARGET003",
                "stars": 3,
                "destructionPercentage": 100,
                "order": 4,
            }
        ]
        second, changed = merge_war_history(
            first, normalized(payload, collected_at="2026-07-20T13:00:00Z")
        )
        self.assertTrue(changed)
        self.assertEqual(len(second["wars"]), 1)
        record = second["wars"][0]
        self.assertEqual(len(record["observations"]), 2)
        original_by_tag = {
            item["player_tag"]: item
            for item in record["observations"][0]["snapshot"]["members"]
        }
        self.assertEqual(len(original_by_tag["#DEMO002"]["attacks"]), 0)
        self.assertEqual(sum(len(item["attacks"]) for item in record["canonical"]["members"]), 3)

    def test_missing_timestamps_can_be_refined_without_new_war(self) -> None:
        early = war_payload(preparationStartTime=None, startTime=None, endTime=None)
        first, _ = merge_war_history(empty_history(), normalized(early))
        second, _ = merge_war_history(
            first, normalized(collected_at="2026-07-20T13:00:00Z")
        )
        self.assertEqual(len(second["wars"]), 1)
        self.assertEqual(second["wars"][0]["canonical"]["end_time"], "20260720T180000.000Z")

    def test_same_roster_without_timestamps_does_not_merge_after_time_window(self) -> None:
        early = war_payload(preparationStartTime=None, startTime=None, endTime=None)
        first, _ = merge_war_history(empty_history(), normalized(early, collected_at="2026-07-20T12:00:00Z"))
        second, _ = merge_war_history(first, normalized(early, collected_at="2026-07-20T17:01:00Z"))
        self.assertEqual(len(second["wars"]), 2)

    def test_appearing_fields_and_changed_member_set_match_active_war(self) -> None:
        early = war_payload(attacksPerMember=None)
        early["clan"]["members"] = early["clan"]["members"][:1]
        first, _ = merge_war_history(empty_history(), normalized(early))
        second, _ = merge_war_history(
            first, normalized(collected_at="2026-07-20T13:00:00Z")
        )
        self.assertEqual(len(second["wars"]), 1)
        self.assertEqual(len(second["wars"][0]["canonical"]["members"]), 2)

    def test_conflicting_strong_time_creates_new_war(self) -> None:
        first, _ = merge_war_history(empty_history(), normalized())
        other = war_payload(startTime="20260722T170000.000Z", endTime="20260722T180000.000Z")
        second, _ = merge_war_history(first, normalized(other))
        self.assertEqual(len(second["wars"]), 2)

    def test_ambiguous_identity_gets_diagnostic_instead_of_auto_merge(self) -> None:
        early = war_payload(preparationStartTime=None, startTime=None, endTime=None)
        history, _ = merge_war_history(empty_history(), normalized(early))
        duplicate = json.loads(json.dumps(history["wars"][0]))
        duplicate["war_id"] = "c" * 64
        history["wars"].append(duplicate)
        result, changed = merge_war_history(
            history, normalized(early, collected_at="2026-07-20T13:00:00Z")
        )
        self.assertTrue(changed)
        self.assertEqual(len(result["wars"]), 3)
        self.assertEqual(result["wars"][-1]["lifecycle_status"], "ambiguous")
        repeated, changed = merge_war_history(result, normalized(early, collected_at="2026-07-20T14:00:00Z"))
        self.assertFalse(changed)
        self.assertEqual(len(repeated["wars"]), 3)

    def test_source_timestamp_does_not_create_an_observation(self) -> None:
        first_payload = war_payload(sourceTimestamp="2026-07-20T12:00:00Z")
        second_payload = war_payload(sourceTimestamp="2026-07-20T13:00:00Z")
        first, _ = merge_war_history(empty_history(), normalized(first_payload))
        second, changed = merge_war_history(first, normalized(second_payload, collected_at="2026-07-20T13:00:00Z"))
        self.assertFalse(changed)
        self.assertEqual(len(second["wars"][0]["observations"]), 1)

    def test_less_complete_snapshot_does_not_delete_known_facts(self) -> None:
        first, _ = merge_war_history(empty_history(), normalized())
        regressed = war_payload(attacksPerMember=None, endTime=None)
        regressed["clan"]["members"] = regressed["clan"]["members"][:1]
        regressed["clan"]["members"][0]["attacks"] = []
        second, _ = merge_war_history(
            first, normalized(regressed, collected_at="2026-07-20T13:00:00Z")
        )
        canonical = second["wars"][0]["canonical"]
        self.assertEqual(canonical["attacks_per_member"], 2)
        self.assertEqual(canonical["end_time"], "20260720T180000.000Z")
        self.assertEqual(sum(len(item["attacks"]) for item in canonical["members"]), 2)

    def test_conflicting_non_star_scalars_keep_first_canonical_fact(self) -> None:
        first, _ = merge_war_history(empty_history(), normalized())
        conflicting = war_payload()
        conflicting["clan"]["stars"] = 99
        conflicting["teamSize"] = 9
        second, _ = merge_war_history(first, normalized(conflicting, collected_at="2026-07-20T13:00:00Z"))
        record = second["wars"][0]
        self.assertEqual(record["canonical"]["clan_stars"], 99)
        self.assertEqual(record["canonical"]["team_size"], 2)
        self.assertIn("conflicting_canonical_team_size_observed", record["diagnostics"])

    def test_active_clan_stars_rise_but_do_not_regress(self) -> None:
        first_payload = war_payload(); first_payload["clan"]["stars"] = 38
        history, _ = merge_war_history(empty_history(), normalized(first_payload))
        higher_payload = war_payload(); higher_payload["clan"]["stars"] = 41
        history, _ = merge_war_history(history, normalized(higher_payload, collected_at="2026-07-20T13:00:00Z"))
        self.assertEqual(history["wars"][0]["canonical"]["clan_stars"], 41)
        lower_payload = war_payload(); lower_payload["clan"]["stars"] = 38
        lower_payload["clan"]["members"][0]["mapPosition"] = 3
        history, _ = merge_war_history(history, normalized(lower_payload, collected_at="2026-07-20T14:00:00Z"))
        record = history["wars"][0]
        self.assertEqual(record["canonical"]["clan_stars"], 41)
        self.assertEqual(record["diagnostics"].count("regressing_clan_stars_observed"), 1)
        repeated, _ = merge_war_history(history, normalized(lower_payload, collected_at="2026-07-20T15:00:00Z"))
        self.assertEqual(repeated["wars"][0]["diagnostics"].count("regressing_clan_stars_observed"), 1)

    def test_lifecycle_clan_stars_38_to_41_to_final_45_is_monotonic(self) -> None:
        payload_38 = war_payload(); payload_38["clan"]["stars"] = 38
        history, _ = merge_war_history(empty_history(), normalized(payload_38))
        payload_41 = war_payload(); payload_41["clan"]["stars"] = 41
        history, _ = merge_war_history(history, normalized(payload_41, collected_at="2026-07-20T13:00:00Z"))
        self.assertEqual(history["wars"][0]["canonical"]["clan_stars"], 41)
        final = war_payload(state="warEnded"); final["clan"]["stars"] = 45
        history, _ = merge_war_history(history, normalized(final, collected_at="2026-07-20T14:00:00Z"))
        record = history["wars"][0]
        self.assertEqual(record["canonical"]["clan_stars"], 45)
        self.assertEqual(record["canonical"]["state"], "warEnded")
        self.assertEqual(record["lifecycle_status"], "finalized_detailed")
        self.assertEqual(record["states_seen"], ["inWar", "warEnded"])
        stale = war_payload(); stale["clan"]["stars"] = 41
        history, _ = merge_war_history(history, normalized(stale, collected_at="2026-07-20T15:00:00Z"))
        self.assertEqual(history["wars"][0]["canonical"]["clan_stars"], 45)
        conflicting_final = war_payload(state="warEnded"); conflicting_final["clan"]["stars"] = 44
        history, _ = merge_war_history(history, normalized(conflicting_final, collected_at="2026-07-20T16:00:00Z"))
        record = history["wars"][0]
        self.assertEqual(record["canonical"]["clan_stars"], 45)
        self.assertEqual(record["diagnostics"].count("conflicting_final_clan_stars_observed"), 1)
        repeated, changed = merge_war_history(history, normalized(final, collected_at="2026-07-20T17:00:00Z"))
        self.assertFalse(changed)
        self.assertEqual(repeated["wars"][0]["canonical"]["clan_stars"], 45)

    def test_first_final_cannot_lower_known_active_clan_stars(self) -> None:
        active = war_payload(); active["clan"]["stars"] = 45
        history, _ = merge_war_history(empty_history(), normalized(active))
        lower_final = war_payload(state="warEnded"); lower_final["clan"]["stars"] = 44
        history, _ = merge_war_history(history, normalized(lower_final, collected_at="2026-07-20T13:00:00Z"))
        record = history["wars"][0]
        self.assertEqual(record["canonical"]["state"], "warEnded")
        self.assertEqual(record["lifecycle_status"], "finalized_detailed")
        self.assertEqual(record["canonical"]["clan_stars"], 45)
        self.assertIn("conflicting_final_clan_stars_observed", record["diagnostics"])
        self.assertTrue(any(observation["snapshot"]["clan_stars"] == 44 for observation in record["observations"]))
        repeated, _ = merge_war_history(history, normalized(lower_final, collected_at="2026-07-20T14:00:00Z"))
        self.assertEqual(repeated["wars"][0]["diagnostics"].count("conflicting_final_clan_stars_observed"), 1)

    def test_old_attack_reappearing_does_not_duplicate_canonical_attack(self) -> None:
        first, _ = merge_war_history(empty_history(), normalized())
        payload = war_payload()
        payload["clan"]["members"][0]["name"] = "Beta renamed"
        second, _ = merge_war_history(first, normalized(payload, collected_at="2026-07-20T13:00:00Z"))
        self.assertEqual(sum(len(item["attacks"]) for item in second["wars"][0]["canonical"]["members"]), 2)

    def test_map_position_nickname_and_town_hall_changes_are_observed(self) -> None:
        first, _ = merge_war_history(empty_history(), normalized())
        payload = war_payload()
        member = payload["clan"]["members"][0]
        member["name"] = "Alpha"
        member["townhallLevel"] = 11
        member["mapPosition"] = 3
        second, _ = merge_war_history(first, normalized(payload, collected_at="2026-07-20T13:00:00Z"))
        record = second["wars"][0]
        self.assertEqual(len(record["observations"]), 2)
        by_tag = {item["player_tag"]: item for item in record["canonical"]["members"]}
        self.assertEqual(by_tag["#DEMO002"]["town_hall_level"], 11)
        self.assertEqual(by_tag["#DEMO002"]["map_position"], 3)

    def test_duplicate_nicknames_remain_separate_by_tag_and_private(self) -> None:
        payload = war_payload()
        for member in payload["clan"]["members"]:
            member["name"] = "Same name"
        history, _ = merge_war_history(empty_history(), normalized(payload))
        public = build_public_war_history(history)
        self.assertEqual(len(public["wars"][0]["members"]), 2)
        rendered = json.dumps(public)
        self.assertNotIn("#DEMO", rendered)
        self.assertNotIn("player_tag", rendered)

    def test_temporarily_missing_player_returns_without_duplicate(self) -> None:
        partial = war_payload()
        partial["clan"]["members"] = partial["clan"]["members"][:1]
        first, _ = merge_war_history(empty_history(), normalized(partial))
        second, _ = merge_war_history(first, normalized(collected_at="2026-07-20T13:00:00Z"))
        tags = [item["player_tag"] for item in second["wars"][0]["canonical"]["members"]]
        self.assertEqual(sorted(tags), ["#DEMO001", "#DEMO002"])


class WarLifecycleAndReconciliationTests(unittest.TestCase):
    def test_preparation_to_in_war_to_war_ended(self) -> None:
        preparation = war_payload(state="preparation")
        history, _ = merge_war_history(empty_history(), normalized(preparation))
        history, _ = merge_war_history(history, normalized(collected_at="2026-07-20T13:00:00Z"))
        ended = war_payload(state="warEnded")
        history, _ = merge_war_history(history, normalized(ended, collected_at="2026-07-20T14:00:00Z"))
        record = history["wars"][0]
        self.assertEqual(record["states_seen"], ["preparation", "inWar", "warEnded"])
        self.assertEqual(record["lifecycle_status"], "finalized_detailed")

    def test_finalized_canonical_state_never_regresses(self) -> None:
        ended = war_payload(state="warEnded")
        history, _ = merge_war_history(empty_history(), normalized(ended))
        history, _ = merge_war_history(history, normalized(war_payload(state="inWar"), collected_at="2026-07-20T13:00:00Z"))
        self.assertEqual(history["wars"][0]["canonical"]["state"], "warEnded")
        self.assertIn("regressing_detailed_state_observed", history["wars"][0]["diagnostics"])

    def test_in_war_to_not_in_war_closes_without_invented_final_facts(self) -> None:
        history, _ = merge_war_history(empty_history(), normalized())
        no_war = normalized(
            {"state": "notInWar"}, collected_at="2026-07-20T14:00:00Z"
        )
        history, changed = merge_war_history(history, no_war)
        self.assertTrue(changed)
        record = history["wars"][0]
        self.assertEqual(record["lifecycle_status"], "closed_without_final_detailed")
        self.assertEqual(record["reconciliation_status"], "awaiting_war_log")
        self.assertEqual(record["canonical"]["state"], "inWar")

    def test_matching_war_log_closes_missing_final_snapshot(self) -> None:
        history, _ = merge_war_history(empty_history(), normalized())
        history, _ = merge_war_history(history, normalized({"state": "notInWar"}))
        log_payload = load("war_log.json")
        log_payload["items"] = [log_payload["items"][0]]
        log_payload["items"][0]["endTime"] = "20260720T180000.000Z"
        log_payload["items"][0]["teamSize"] = 2
        log_payload["items"][0]["opponent"]["tag"] = "#TARGETCLAN"
        log = normalize_war_log(log_payload, collected_at="2026-07-20T19:00:00Z", raw_source_reference="fixture")
        reconciled, changed = reconcile_war_log(history, log)
        self.assertTrue(changed)
        record = reconciled["wars"][0]
        self.assertEqual(record["lifecycle_status"], "closed_war_log_without_final_detail")
        self.assertEqual(record["reconciliation_status"], "matched")
        self.assertEqual(record["canonical"]["state"], "inWar")

    def test_unknown_war_log_entry_is_aggregate_only(self) -> None:
        log = normalize_war_log(load("war_log.json"), collected_at="2026-07-20T19:00:00Z", raw_source_reference="fixture")
        history, changed = reconcile_war_log(empty_history(), log)
        self.assertTrue(changed)
        aggregate = [item for item in history["wars"] if item["observations"] == []]
        self.assertEqual(len(aggregate), 2)
        self.assertTrue(all(item["lifecycle_status"] == "closed_war_log_only" for item in aggregate))

    def test_aggregate_only_null_canonical_survives_history_merge(self) -> None:
        log = normalize_war_log(load("war_log.json"), collected_at="2026-07-20T19:00:00Z", raw_source_reference="fixture")
        aggregate_history, _ = reconcile_war_log(empty_history(), log)
        aggregate_before = json.loads(json.dumps(
            next(record for record in aggregate_history["wars"] if record["canonical"] is None)
        ))

        # This is the exact nullable access that failed before the fix.
        with self.assertRaises(AttributeError):
            aggregate_before.get("canonical", {}).get("end_time")

        merged, changed = merge_war_history(aggregate_history, normalized())
        self.assertTrue(changed)
        preserved = next(record for record in merged["wars"] if record["war_id"] == aggregate_before["war_id"])
        self.assertIsNone(preserved["canonical"])
        self.assertEqual(preserved["war_log"], aggregate_before["war_log"])
        self.assertEqual(preserved["lifecycle_status"], "closed_war_log_only")
        self.assertEqual(preserved["reconciliation_status"], "unmatched_aggregate_only")

    def test_null_canonical_sort_is_deterministic_for_mixed_history(self) -> None:
        log = normalize_war_log(load("war_log.json"), collected_at="2026-07-20T19:00:00Z", raw_source_reference="fixture")
        aggregate_history, _ = reconcile_war_log(empty_history(), log)
        first, _ = merge_war_history(aggregate_history, normalized())
        second, _ = merge_war_history(
            json.loads(json.dumps(aggregate_history)), normalized()
        )

        self.assertEqual(
            [(record["war_id"], record["canonical"]) for record in first["wars"]],
            [(record["war_id"], record["canonical"]) for record in second["wars"]],
        )
        self.assertTrue(any(record["canonical"] is None for record in first["wars"]))
        self.assertTrue(any(isinstance(record["canonical"], dict) for record in first["wars"]))

    def test_war_log_only_later_detailed_merges_one_record(self) -> None:
        payload = load("war_log.json")
        entry = payload["items"][0]
        entry.update({"endTime": "20260720T180000.000Z", "teamSize": 2})
        entry["opponent"]["tag"] = "#TARGETCLAN"
        log = normalize_war_log(payload, collected_at="2026-07-20T19:00:00Z", raw_source_reference="fixture")
        history, _ = reconcile_war_log(empty_history(), log)
        history, _ = merge_war_history(history, normalized())
        self.assertEqual(len(history["wars"]), 2)
        matched = [record for record in history["wars"] if record["record_kind"] == "detailed"]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["reconciliation_status"], "matched")
        repeated, changed = merge_war_history(history, normalized())
        self.assertFalse(changed)
        repeated, changed = reconcile_war_log(repeated, log)
        self.assertFalse(changed)
        self.assertEqual(len(repeated["wars"]), 2)

    def test_war_log_without_exact_end_time_is_not_auto_matched(self) -> None:
        history, _ = merge_war_history(empty_history(), normalized())
        payload = load("war_log.json")
        entry = payload["items"][0]
        entry.update({"endTime": None, "teamSize": 2})
        log = normalize_war_log(payload, collected_at="2026-07-20T19:00:00Z", raw_source_reference="fixture")
        reconciled, _ = reconcile_war_log(history, log)
        self.assertEqual(reconciled["wars"][0]["reconciliation_status"], "not_reconciled")

    def test_repeated_reconciliation_is_idempotent(self) -> None:
        log = normalize_war_log(load("war_log.json"), collected_at="2026-07-20T19:00:00Z", raw_source_reference="fixture")
        first, _ = reconcile_war_log(empty_history(), log)
        second, changed = reconcile_war_log(first, log)
        self.assertFalse(changed)
        self.assertEqual(first, second)

    def test_ambiguous_reconciliation_does_not_auto_merge(self) -> None:
        first, _ = merge_war_history(empty_history(), normalized())
        duplicate = json.loads(json.dumps(first["wars"][0]))
        duplicate["war_id"] = "b" * 64
        first["wars"].append(duplicate)
        log_payload = load("war_log.json")
        log_payload["items"] = [log_payload["items"][0]]
        log_payload["items"][0]["endTime"] = "20260720T180000.000Z"
        log_payload["items"][0]["teamSize"] = 2
        log_payload["items"][0]["opponent"]["tag"] = "#TARGETCLAN"
        log = normalize_war_log(log_payload, collected_at="2026-07-20T19:00:00Z", raw_source_reference="fixture")
        result, _ = reconcile_war_log(first, log)
        self.assertTrue(all(item["reconciliation_status"] == "ambiguous" for item in result["wars"]))

    def test_progressive_timestamp_identity_requires_continuity_evidence(self) -> None:
        early = war_payload(preparationStartTime=None, startTime=None, endTime=None)
        first, _ = merge_war_history(empty_history(), normalized(early))
        later = war_payload(preparationStartTime=None, endTime=None)
        # A newly observed start timestamp may refine the same active record when
        # there is a short collection gap and roster overlap.
        refined, _ = merge_war_history(first, normalized(later, collected_at="2026-07-20T13:00:00Z"))
        self.assertEqual(len(refined["wars"]), 1)
        # A conflicting known strong time never gets silently joined.
        conflicting = war_payload(preparationStartTime=None, startTime="20260722T120000.000Z", endTime=None)
        split, _ = merge_war_history(refined, normalized(conflicting, collected_at="2026-07-20T14:00:00Z"))
        self.assertEqual(len(split["wars"]), 2)

    def test_progressive_timestamps_require_chronological_timeline(self) -> None:
        preparation = war_payload(preparationStartTime="20260720T100000.000Z", startTime=None, endTime=None)
        history, _ = merge_war_history(empty_history(), normalized(preparation))
        compatible_start = war_payload(preparationStartTime=None, startTime="20260720T120000.000Z", endTime=None)
        history, _ = merge_war_history(history, normalized(compatible_start, collected_at="2026-07-20T13:00:00Z"))
        compatible_end = war_payload(preparationStartTime=None, startTime=None, endTime="20260720T180000.000Z")
        history, _ = merge_war_history(history, normalized(compatible_end, collected_at="2026-07-20T14:00:00Z"))
        self.assertEqual(len(history["wars"]), 1)
        original_id = history["wars"][0]["war_id"]
        timeline_early, _ = merge_war_history(empty_history(), normalized(preparation))
        reversed_end = war_payload(preparationStartTime=None, startTime=None, endTime="20260720T090000.000Z")
        split, _ = merge_war_history(timeline_early, normalized(reversed_end, collected_at="2026-07-20T15:00:00Z"))
        self.assertEqual(len(split["wars"]), 2)
        self.assertEqual(history["wars"][0]["war_id"], original_id)
        self.assertTrue(any("identity_timeline_incompatible" in record["diagnostics"] for record in split["wars"]))

    def test_progressive_game_time_window_blocks_distant_wars(self) -> None:
        preparation = war_payload(preparationStartTime="20260720T100000.000Z", startTime=None, endTime=None)
        history, _ = merge_war_history(empty_history(), normalized(preparation))
        distant_start = war_payload(preparationStartTime=None, startTime="20260820T120000.000Z", endTime=None)
        result, _ = merge_war_history(history, normalized(distant_start, collected_at="2026-07-20T13:00:00Z"))
        self.assertEqual(len(result["wars"]), 2)
        self.assertTrue(any("identity_game_time_window_incompatible" in record["diagnostics"] for record in result["wars"]))
        repeated, changed = merge_war_history(result, normalized(distant_start, collected_at="2026-07-20T14:00:00Z"))
        self.assertFalse(changed)
        self.assertEqual(len(repeated["wars"]), 2)


class HistoryMigrationAndRecoveryTests(unittest.TestCase):
    def test_migration_v1_to_v2_preserves_latest_as_one_observation(self) -> None:
        migrated = migrate_history(v1_history())
        self.assertEqual(migrated["schema_version"], 2)
        record = migrated["wars"][0]
        self.assertEqual(record["war_id"], "a" * 64)
        self.assertEqual(len(record["observations"]), 1)
        self.assertEqual(record["migration"]["legacy_observation_count"], 2)
        self.assertIn("only legacy latest", record["migration"]["observation_limit"])

    def test_repeated_migration_is_idempotent(self) -> None:
        once = migrate_history(v1_history())
        self.assertEqual(migrate_history(once), once)

    def test_unknown_future_schema_fails(self) -> None:
        with self.assertRaisesRegex(HistoryError, "unsupported history schema version: 99"):
            migrate_history({"schema_version": 99, "wars": []})

    def test_valid_json_with_tampered_fingerprint_fails_closed(self) -> None:
        history, _ = merge_war_history(empty_history(), normalized())
        history["wars"][0]["observations"][0]["fingerprint"] = "0" * 64
        with self.assertRaisesRegex(HistoryError, "fingerprint"):
            migrate_history(history)

    def test_valid_json_with_invalid_lifecycle_or_aggregate_shape_fails_closed(self) -> None:
        history, _ = merge_war_history(empty_history(), normalized())
        history["wars"][0]["lifecycle_status"] = "invented"
        with self.assertRaisesRegex(HistoryError, "lifecycle_status"):
            migrate_history(history)
        aggregate, _ = reconcile_war_log(empty_history(), normalize_war_log(load("war_log.json"), collected_at="2026-07-20T19:00:00Z", raw_source_reference="fixture"))
        aggregate["wars"][0]["canonical"] = {"state": "inWar"}
        with self.assertRaisesRegex(HistoryError, "aggregate-only"):
            migrate_history(aggregate)

    def test_semantic_validation_rejects_identity_source_diagnostics_and_relations(self) -> None:
        history, _ = merge_war_history(empty_history(), normalized())
        variants = []
        broken = json.loads(json.dumps(history)); broken["wars"][0]["identity"]["seed"] = "not-a-seed"; variants.append((broken, "identity"))
        broken = json.loads(json.dumps(history)); broken["wars"][0]["observations"][0]["snapshot"]["source"]["raw_source_reference"] = ""; variants.append((broken, "source is invalid"))
        broken = json.loads(json.dumps(history)); broken["wars"][0]["diagnostics"] = ["invented"]; variants.append((broken, "diagnostics"))
        broken = json.loads(json.dumps(history)); broken["wars"][0]["reconciliation_status"] = "matched"; variants.append((broken, "lacks war_log"))
        for broken, message in variants:
            with self.assertRaisesRegex(HistoryError, message):
                migrate_history(broken)

    def test_timestamp_and_hash_validation_rejects_parseable_corruption(self) -> None:
        history, _ = merge_war_history(empty_history(), normalized())
        for value in ("abc", "2026-invalid", "2026-02-30T12:00:00Z", "2026-07-20T12:00:00"):
            broken = json.loads(json.dumps(history))
            broken["wars"][0]["first_collected_at"] = value
            with self.assertRaisesRegex(HistoryError, "collection timestamps"):
                migrate_history(broken)
        broken = json.loads(json.dumps(history))
        broken["wars"][0]["war_id"] = "g" * 64
        with self.assertRaisesRegex(HistoryError, "war_id"):
            migrate_history(broken)
        broken = json.loads(json.dumps(history))
        broken["wars"][0]["observations"][0]["fingerprint"] = "a" * 63
        with self.assertRaisesRegex(HistoryError, "fingerprint"):
            migrate_history(broken)

    def test_timestamp_validation_accepts_compact_and_iso_offsets_and_orders_datetimes(self) -> None:
        history, _ = merge_war_history(empty_history(), normalized())
        record = history["wars"][0]
        record["first_collected_at"] = "2026-07-20T14:39:49+02:00"
        record["last_collected_at"] = "2026-07-20T12:40:49Z"
        record["observations"][0]["collected_at"] = "2026-07-20T12:39:49+00:00"
        record["observations"][0]["snapshot"]["source"]["collected_at"] = "20260720T123949.000Z"
        # The first time is 12:39:49Z, before the second despite lexical order.
        self.assertEqual(migrate_history(history)["schema_version"], 2)

    def test_corrupted_history_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "history.json"
            path.write_text('{"schema_version":', encoding="utf-8")
            with self.assertRaisesRegex(HistoryError, "read stage failed"):
                load_history(path)
            self.assertNotEqual(path.read_text(encoding="utf-8"), "")

    def test_atomic_write_creates_backup_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "history.json"
            original = json.dumps(empty_history())
            path.write_text(original, encoding="utf-8")
            history, _ = merge_war_history(empty_history(), normalized())
            backup = write_history_atomic(path, history)
            self.assertIsNotNone(backup)
            self.assertEqual(backup.read_text(encoding="utf-8"), original)
            self.assertEqual(load_history(path), history)

    def test_interrupted_replace_preserves_original_and_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "history.json"
            original = json.dumps(empty_history())
            path.write_text(original, encoding="utf-8")
            history, _ = merge_war_history(empty_history(), normalized())
            real_replace = os.replace

            def fail_target_replace(source, destination):
                if Path(destination) == path:
                    raise OSError("fixture failure")
                return real_replace(source, destination)

            with patch.object(os, "replace", side_effect=fail_target_replace):
                with self.assertRaisesRegex(HistoryError, "replace stage failed"):
                    write_history_atomic(path, history)
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertTrue((path.parent / "history.json.bak").is_file())
            self.assertEqual(list(path.parent.glob(".*.tmp")), [])

    def test_backup_failure_preserves_original(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "history.json"
            original = json.dumps(empty_history())
            path.write_text(original, encoding="utf-8")
            history, _ = merge_war_history(empty_history(), normalized())
            real_open = Path.open

            def guarded_open(target, mode="r", *args, **kwargs):
                if ".bak.tmp" in target.name and "w" in mode:
                    raise OSError("fixture backup failure")
                return real_open(target, mode, *args, **kwargs)

            with patch.object(Path, "open", guarded_open):
                with self.assertRaisesRegex(HistoryError, "backup stage failed"):
                    write_history_atomic(path, history)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_write_failure_preserves_original(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "history.json"
            original = json.dumps(empty_history())
            path.write_text(original, encoding="utf-8")
            history, _ = merge_war_history(empty_history(), normalized())
            real_open = Path.open

            def guarded_open(target, mode="r", *args, **kwargs):
                if target.suffix == ".tmp" and "w" in mode:
                    raise OSError("fixture write failure")
                return real_open(target, mode, *args, **kwargs)

            with patch.object(Path, "open", guarded_open):
                with self.assertRaisesRegex(HistoryError, "write stage failed"):
                    write_history_atomic(path, history)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_valid_backup_can_be_restored_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "history.json"
            backup = root / "known-good.json"
            good, _ = merge_war_history(empty_history(), normalized())
            backup.write_text(json.dumps(good), encoding="utf-8")
            path.write_text('{"broken":', encoding="utf-8")
            restore_history_backup(path, backup)
            self.assertEqual(load_history(path), good)

    def test_invalid_backup_is_never_restored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "history.json"
            original = json.dumps(empty_history())
            path.write_text(original, encoding="utf-8")
            backup = root / "broken.json"
            backup.write_text("not-json", encoding="utf-8")
            with self.assertRaisesRegex(HistoryError, "restore validation stage failed"):
                restore_history_backup(path, backup)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_public_history_and_roster_exclude_private_identity(self) -> None:
        history, _ = merge_war_history(empty_history(), normalized())
        clan = normalize_clan(load("clan.json"), collected_at="2026-07-20T12:00:00Z", raw_source_reference="fixture")
        payloads = [build_public_war_history(history), build_public_roster(clan, detailed_wars(history))]
        rendered = json.dumps(payloads)
        for forbidden in ("#DEMO", "player_tag", "attacker_tag", "defender_tag", "raw_source_reference"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
