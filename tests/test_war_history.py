from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
FIXTURES = Path(__file__).with_name("fixtures")
sys.path.insert(0, str(SRC_ROOT))

from clan_analytics.api.normalization import build_public_roster, normalize_clan, normalize_current_war  # noqa: E402
from clan_analytics.history import (  # noqa: E402
    build_public_war_history,
    detailed_wars,
    empty_history,
    merge_war_history,
)


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class WarHistoryTests(unittest.TestCase):
    def normalized_war(self, *, collected_at: str = "2026-07-20T12:00:00Z"):
        return normalize_current_war(
            load("current_war.json"),
            collected_at=collected_at,
            raw_source_reference="fixture",
        )

    def test_same_war_is_updated_without_duplicate(self) -> None:
        first, changed = merge_war_history(empty_history(), self.normalized_war())
        self.assertTrue(changed)
        self.assertEqual(len(first["wars"]), 1)

        second, changed = merge_war_history(first, self.normalized_war())
        self.assertFalse(changed)
        self.assertEqual(len(second["wars"]), 1)
        self.assertEqual(second["wars"][0]["observations"], 1)

    def test_newer_snapshot_increments_observation_but_keeps_one_war(self) -> None:
        first, _ = merge_war_history(empty_history(), self.normalized_war())
        payload = load("current_war.json")
        payload["clan"]["members"][0]["attacks"] = [
            {
                "attackerTag": "#DEMO002",
                "defenderTag": "#TARGET003",
                "stars": 3,
                "destructionPercentage": 100,
                "order": 4,
            }
        ]
        newer = normalize_current_war(
            payload,
            collected_at="2026-07-20T13:00:00Z",
            raw_source_reference="fixture-2",
        )
        second, changed = merge_war_history(first, newer)
        self.assertTrue(changed)
        self.assertEqual(len(second["wars"]), 1)
        self.assertEqual(second["wars"][0]["observations"], 2)

    def test_public_history_is_position_ordered_and_tag_free(self) -> None:
        history, _ = merge_war_history(empty_history(), self.normalized_war())
        public = build_public_war_history(history)
        self.assertEqual(public["wars_observed"], 1)
        self.assertEqual(
            [member["war_position"] for member in public["wars"][0]["members"]],
            [1, 2],
        )
        rendered = json.dumps(public)
        self.assertNotIn("#DEMO", rendered)
        self.assertNotIn("player_tag", rendered)

    def test_roster_uses_one_latest_snapshot_per_war(self) -> None:
        history, _ = merge_war_history(empty_history(), self.normalized_war())
        clan = normalize_clan(
            load("clan.json"),
            collected_at="2026-07-20T12:00:00Z",
            raw_source_reference="clan-fixture",
        )
        roster = build_public_roster(clan, detailed_wars(history))
        by_name = {member["nickname"]: member for member in roster["members"]}
        self.assertEqual(by_name["Demo Player 01"]["war_participations"], 1)
        self.assertEqual(by_name["Demo Player 01"]["stars_earned"], 5)


if __name__ == "__main__":
    unittest.main()
