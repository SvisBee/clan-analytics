from __future__ import annotations

import ast
import json
import re
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
FIXTURES = Path(__file__).with_name("fixtures")
sys.path.insert(0, str(SRC_ROOT))

from clan_analytics.api import (  # noqa: E402
    NormalizationError,
    build_composition_summary,
    build_public_roster,
    build_public_war_summary,
    normalize_clan,
    normalize_current_war,
    normalize_player_profile,
)


def load_fixture(name: str):
    with (FIXTURES / name).open(encoding="utf-8") as fixture:
        return json.load(fixture)


class ApiNormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clan = normalize_clan(
            load_fixture("clan.json"),
            collected_at="2026-07-19T09:05:00Z",
            raw_source_reference="fixtures/clan.json",
        )
        self.wars = tuple(
            normalize_current_war(
                payload,
                collected_at="2026-07-19T09:05:00Z",
                raw_source_reference=f"fixtures/war_history.json#{index}",
            )
            for index, payload in enumerate(load_fixture("war_history.json"))
        )

    def test_full_clan_fixture_normalizes_in_stable_tag_order(self) -> None:
        self.assertEqual(self.clan.name, "Example Clan")
        self.assertEqual(
            [member.player_tag for member in self.clan.members],
            ["#DEMO001", "#DEMO002"],
        )

    def test_official_clan_member_integer_fields_map_to_internal_snapshot(self) -> None:
        member = self.clan.members[0]
        self.assertEqual(member.exp_level, 250)
        self.assertEqual(member.clan_rank, 1)
        self.assertEqual(member.previous_clan_rank, 2)
        self.assertEqual(member.donations, 1234)
        self.assertEqual(member.donations_received, 987)
        self.assertEqual(member.trophies, 5500)
        self.assertEqual(member.builder_base_trophies, 4800)

    def test_missing_optional_clan_member_fields_are_null(self) -> None:
        member = self.clan.members[1]
        for field in (
            "exp_level",
            "clan_rank",
            "previous_clan_rank",
            "donations",
            "donations_received",
            "trophies",
            "builder_base_trophies",
        ):
            self.assertIsNone(getattr(member, field), field)

    def test_boolean_is_not_accepted_for_clan_member_integer_fields(self) -> None:
        payload = load_fixture("clan.json")
        payload["memberList"][0]["donations"] = True
        with self.assertRaisesRegex(
            NormalizationError, r"memberList\[0\]\.donations must be an integer"
        ):
            normalize_clan(
                payload,
                collected_at="2026-07-19T09:05:00Z",
                raw_source_reference="inline",
            )

    def test_invalid_clan_member_integer_type_has_clear_error(self) -> None:
        payload = load_fixture("clan.json")
        payload["memberList"][0]["trophies"] = "5500"
        with self.assertRaisesRegex(
            NormalizationError, r"memberList\[0\]\.trophies must be an integer"
        ):
            normalize_clan(
                payload,
                collected_at="2026-07-19T09:05:00Z",
                raw_source_reference="inline",
            )

    def test_negative_clan_member_integer_is_rejected(self) -> None:
        payload = load_fixture("clan.json")
        payload["memberList"][0]["clanRank"] = -1
        with self.assertRaisesRegex(
            NormalizationError, r"memberList\[0\]\.clanRank must be zero or greater"
        ):
            normalize_clan(
                payload,
                collected_at="2026-07-19T09:05:00Z",
                raw_source_reference="inline",
            )

    def test_war_attack_destruction_percentage_requires_official_integer_type(self) -> None:
        payload = load_fixture("war_history.json")[0]
        payload["clan"]["members"][0]["attacks"][0][
            "destructionPercentage"
        ] = 99.5
        with self.assertRaisesRegex(
            NormalizationError,
            r"attacks\[0\]\.destructionPercentage must be an integer",
        ):
            normalize_current_war(
                payload,
                collected_at="2026-07-19T09:05:00Z",
                raw_source_reference="inline",
            )

    def test_missing_optional_player_fields_are_null(self) -> None:
        profile = normalize_player_profile(
            load_fixture("player_profiles.json")[1],
            collected_at="2026-07-19T09:05:00Z",
            raw_source_reference="fixtures/player_profiles.json#1",
        )
        self.assertIsNone(profile.clan_role)
        self.assertIsNone(profile.town_hall_level)

    def test_telegram_requires_separate_local_field_and_consent(self) -> None:
        roster = build_public_roster(self.clan, self.wars)
        self.assertNotIn("telegram_username", roster["members"][0])

        local_profiles = {
            "#DEMO001": {
                "telegram_username": "@demo_contact",
                "telegram_public_consent": True,
            }
        }
        consented = build_public_roster(
            self.clan, self.wars, local_public_profiles=local_profiles
        )
        self.assertEqual(consented["members"][0]["telegram_username"], "@demo_contact")
        self.assertNotIn("telegram_username", consented["members"][1])

    def test_game_tag_is_never_published_or_used_as_telegram(self) -> None:
        roster = build_public_roster(self.clan, self.wars)
        rendered = json.dumps(roster)
        self.assertNotIn("player_tag", rendered)
        self.assertNotIn("#DEMO001", rendered)

    def test_war_metrics_are_deterministic_and_count_unused_attack(self) -> None:
        member = build_public_roster(self.clan, self.wars)["members"][0]
        self.assertEqual(member["war_participations"], 2)
        self.assertEqual(member["attacks_used"], 3)
        self.assertEqual(member["attacks_available"], 4)
        self.assertEqual(member["stars_earned"], 6)
        self.assertEqual(member["average_stars"], 2.0)
        self.assertEqual(member["last_war_date"], "2026-07-18")

    def test_player_without_war_history_gets_insufficient_data(self) -> None:
        member = build_public_roster(self.clan, self.wars)["members"][1]
        self.assertEqual(member["data_status"], "insufficient_data")
        self.assertEqual(member["war_participations"], 0)
        self.assertIsNone(member["attacks_used"])
        self.assertIsNone(member["average_stars"])

    def test_public_projection_excludes_internal_and_private_fields(self) -> None:
        roster = build_public_roster(self.clan, self.wars)
        forbidden = {
            "player_tag",
            "leadership_note",
            "review_status",
            "manual_flags",
            "consent_flags",
            "raw_source_reference",
            "source_timestamp",
            "collected_at",
            "exp_level",
            "clan_rank",
            "previous_clan_rank",
            "donations",
            "donations_received",
            "trophies",
            "builder_base_trophies",
        }
        for member in roster["members"]:
            self.assertTrue(forbidden.isdisjoint(member))

    def test_war_and_composition_summaries_are_neutral(self) -> None:
        war = build_public_war_summary(self.wars[0])
        self.assertEqual(war["attacks_used"], 2)
        self.assertEqual(war["attacks_available"], 2)
        self.assertEqual(war["stars_earned"], 5)

        composition = build_composition_summary(self.clan)
        self.assertEqual(composition["total_members"], 2)
        self.assertEqual(composition["members_with_limited_data"], 1)
        self.assertEqual(
            composition["town_hall_distribution"],
            [{"town_hall_level": 16, "members": 1}],
        )

    def test_malformed_input_has_clear_error(self) -> None:
        with self.assertRaisesRegex(
            NormalizationError, "clan.tag must be a non-empty string"
        ):
            normalize_clan(
                {"name": "Broken fixture"},
                collected_at="2026-07-19T09:05:00Z",
                raw_source_reference="inline",
            )

    def test_fixtures_are_fictional_and_contain_no_secret_or_real_contact(self) -> None:
        fixture_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(FIXTURES.glob("*.json"))
        )
        self.assertNotIn("Authorization", fixture_text)
        self.assertNotIn("Bearer ", fixture_text)
        self.assertNotIn("apiToken", fixture_text)
        self.assertNotIn("telegram_username", fixture_text)
        self.assertNotIn("@", fixture_text)
        tags = re.findall(r"#[A-Z0-9-]+", fixture_text)
        self.assertTrue(tags)
        self.assertTrue(
            all(tag.startswith(("#DEMO", "#TARGET")) for tag in tags)
        )

    def test_normalization_modules_have_no_network_imports_or_urls(self) -> None:
        forbidden_imports = {"http", "requests", "socket", "urllib"}
        modules = [
            SRC_ROOT / "clan_analytics" / "api" / "models.py",
            SRC_ROOT / "clan_analytics" / "api" / "normalization.py",
        ]
        for module_path in modules:
            source = module_path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            imported_roots = {
                alias.name.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            imported_roots.update(
                node.module.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            )
            self.assertTrue(forbidden_imports.isdisjoint(imported_roots), module_path)
            self.assertNotIn("http://", source, module_path)
            self.assertNotIn("https://", source, module_path)


if __name__ == "__main__":
    unittest.main()
