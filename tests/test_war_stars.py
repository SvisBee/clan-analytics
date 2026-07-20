from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
FIXTURES = Path(__file__).with_name("fixtures")

from clan_analytics.api.current_war_probe import build_public_current_war_preview  # noqa: E402
from clan_analytics.api.models import WarAttackSnapshot, WarMemberSnapshot  # noqa: E402
from clan_analytics.api.normalization import (  # noqa: E402
    calculate_war_star_metrics,
    normalize_current_war,
)


def normalized(payload):
    return normalize_current_war(
        payload,
        collected_at="2026-07-20T12:00:00Z",
        raw_source_reference="fixture",
    )


def repeated(first_stars, second_stars, *, orders=(1, 2), defenders=("#D1", "#D1")):
    payload = {
        "state": "warEnded",
        "teamSize": 2,
        "attacksPerMember": 2,
        "endTime": "20260720T180000.000Z",
        "clan": {
            "tag": "#CLAN",
            "name": "Fixture",
            "stars": max(first_stars, second_stars),
            "members": [
                {
                    "tag": "#P1",
                    "name": "One",
                    "mapPosition": 1,
                    "attacks": [{"attackerTag": "#P1", "defenderTag": defenders[0], "stars": first_stars, "order": orders[0]}],
                },
                {
                    "tag": "#P2",
                    "name": "Two",
                    "mapPosition": 2,
                    "attacks": [{"attackerTag": "#P2", "defenderTag": defenders[1], "stars": second_stars, "order": orders[1]}],
                },
            ],
        },
    }
    return normalized(payload)


class WarStarAccountingTests(unittest.TestCase):
    def assert_contributions(self, first, second, expected):
        metrics = calculate_war_star_metrics(repeated(first, second))
        self.assertEqual(metrics["attack_stars_total"], first + second)
        self.assertEqual(metrics["clan_stars"], max(first, second))
        self.assertEqual(metrics["contributions_by_player_tag"], {"#P1": expected[0], "#P2": expected[1]})

    def test_repeat_one_then_three(self) -> None:
        self.assert_contributions(1, 3, (1, 2))

    def test_repeat_two_then_two(self) -> None:
        self.assert_contributions(2, 2, (2, 0))

    def test_repeat_three_then_one(self) -> None:
        self.assert_contributions(3, 1, (3, 0))

    def test_multiple_targets_use_global_attack_order_not_input_order(self) -> None:
        war = repeated(3, 1, orders=(4, 1), defenders=("#D1", "#D1"))
        metrics = calculate_war_star_metrics(war)
        self.assertEqual(metrics["contributions_by_player_tag"], {"#P2": 1, "#P1": 2})

    def test_missing_order_makes_contribution_unavailable(self) -> None:
        war = repeated(1, 3)
        attack = replace(war.members[0].attacks[0], order=None)
        member = replace(war.members[0], attacks=(attack,))
        war = replace(war, members=(member, war.members[1]))
        metrics = calculate_war_star_metrics(war)
        self.assertIsNone(metrics["contributions_by_player_tag"])
        self.assertEqual(metrics["new_stars_contribution_status"], "unavailable_invalid_attack_order")

    def test_duplicate_order_makes_contribution_unavailable(self) -> None:
        metrics = calculate_war_star_metrics(repeated(1, 3, orders=(1, 1)))
        self.assertIsNone(metrics["contributions_by_player_tag"])

    def test_non_numeric_order_makes_contribution_unavailable(self) -> None:
        war = repeated(1, 3)
        bad = WarAttackSnapshot("#P1", "#D1", 1, None, "bad")  # type: ignore[arg-type]
        member = WarMemberSnapshot("#P1", "One", None, 1, (bad,))
        metrics = calculate_war_star_metrics(replace(war, members=(member, war.members[1])))
        self.assertIsNone(metrics["contributions_by_player_tag"])

    def test_official_score_is_not_replaced_on_mismatch(self) -> None:
        war = replace(repeated(1, 3), clan_stars=2)
        metrics = calculate_war_star_metrics(war)
        self.assertEqual(metrics["clan_stars"], 2)
        self.assertEqual(metrics["reconstructed_clan_stars"], 3)
        self.assertEqual(metrics["stars_consistency_status"], "inconsistent")

    def test_acceptance_fixture_uses_38_not_attack_sum_43(self) -> None:
        payload = json.loads((FIXTURES / "current_war_stars_accounting.json").read_text(encoding="utf-8"))
        public = build_public_current_war_preview(normalized(payload))
        self.assertEqual(public["attacks_used"], 18)
        self.assertEqual(public["attacks_available"], 30)
        self.assertEqual(public["clan_stars"], 38)
        self.assertEqual(public["attack_stars_total"], 43)
        self.assertEqual(public["stars_consistency_status"], "consistent")

    def test_public_projection_contains_no_private_identifiers(self) -> None:
        public = build_public_current_war_preview(repeated(1, 3))
        rendered = json.dumps(public)
        for forbidden in ("#P", "#D", "player_tag", "attackerTag", "defenderTag", "attacker_tag", "defender_tag"):
            self.assertNotIn(forbidden, rendered)

    def test_site_labels_official_and_player_star_metrics_explicitly(self) -> None:
        html = (REPO_ROOT / "site" / "index.html").read_text(encoding="utf-8")
        script = (REPO_ROOT / "site" / "assets" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("Звёзды клана", html)
        self.assertIn("Звёзды в атаках", script)
        contract = (REPO_ROOT / "site" / "assets" / "js" / "current-war-contract.js").read_text(encoding="utf-8")
        self.assertIn("war?.clan_stars", contract)


if __name__ == "__main__":
    unittest.main()
