from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
FIXTURES = Path(__file__).with_name("fixtures")
sys.path.insert(0, str(SRC_ROOT))

from clan_analytics.site_update import (  # noqa: E402
    PUBLIC_FILENAMES,
    SiteUpdateError,
    _scan_public,
    build_site_update,
)


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class SiteUpdateTests(unittest.TestCase):
    def test_public_privacy_scan_rejects_private_key_variants_and_credentials(self) -> None:
        for payload in (
            {"playerTag": "private"}, {"player_tag": "private"},
            {"clanTag": "private"}, {"clan_tag": "private"},
            {"attackerTag": "private"}, {"defenderTag": "private"},
            {"opponentTag": "private"}, {"opponentName": "private"},
            {"outer": {"rawSourceReference": "private"}},
            {"outer": {"raw_source_reference": "private"}},
            {"dpapiMetadata": "private"}, {"access_token": "private"},
            {"label": "Authorization: Bearer private"}, {"label": "Bearer private"},
            {"diagnostics": "C:\\private\\history.json"},
            {"diagnostics": "D:\\private\\history.json"},
            {"diagnostics": "\\\\server\\share\\history.json"},
        ):
            with self.assertRaises(SiteUpdateError):
                _scan_public(payload)

    def test_public_privacy_scan_allows_safe_public_values(self) -> None:
        _scan_public({
            "badge_url": "https://example.invalid/badge.png",
            "message": "Противник и состав пока не опубликованы.",
            "diagnostics": ["consistent", "unavailable"],
            "note": "Слово token в обычном публичном тексте не является credential.",
            "date": "2026-07-20",
        })

    def make_probe(self, root: Path, name: str, raw_name: str, payload, collected_at: str):
        run = root / name
        write(run / raw_name, payload)
        write(
            run / "probe_metadata.json",
            {
                "collected_at": collected_at,
                "request_count": 1,
                "response_status": 200,
                "redirects_followed": 0,
            },
        )
        return run

    def test_builds_public_files_and_next_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clan = load("clan.json")
            clan["badgeUrls"] = {"large": "https://example.invalid/badge.png"}
            roster_run = self.make_probe(
                root,
                "roster",
                "raw_clan_response.json",
                clan,
                "2026-07-20T12:00:00Z",
            )
            current_run = self.make_probe(
                root,
                "current",
                "raw_current_war_response.json",
                load("current_war.json"),
                "2026-07-20T12:01:00Z",
            )
            war_log_run = self.make_probe(
                root,
                "warlog",
                "raw_war_log_response.json",
                load("war_log.json"),
                "2026-07-20T12:02:00Z",
            )
            output = root / "output"
            site_data = root / "site-data"
            summary = build_site_update(
                roster_run=roster_run,
                current_war_run=current_run,
                war_log_run=war_log_run,
                existing_history_path=root / "history.json",
                existing_site_data_dir=site_data,
                output_dir=output,
            )
            self.assertEqual(summary["members"], 2)
            self.assertEqual(summary["history_wars"], 1)
            self.assertEqual(
                {path.name for path in (output / "site-data").iterdir()},
                set(PUBLIC_FILENAMES),
            )
            current = json.loads(
                (output / "site-data" / "current-war.json").read_text()
            )
            self.assertEqual(
                [member["war_position"] for member in current["members"]],
                [1, 2],
            )
            combined = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (output / "site-data").iterdir()
            )
            self.assertNotIn("#DEMO", combined)
            self.assertNotIn("player_tag", combined)

    def test_star_accounting_fixture_flows_to_public_current_war_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            roster_run = self.make_probe(root, "roster", "raw_clan_response.json", load("clan.json"), "2026-07-20T12:00:00Z")
            current_run = self.make_probe(root, "current", "raw_current_war_response.json", load("current_war_stars_accounting.json"), "2026-07-20T12:01:00Z")
            war_log_run = self.make_probe(root, "warlog", "raw_war_log_response.json", {"items": []}, "2026-07-20T12:02:00Z")
            output = root / "output"
            build_site_update(
                roster_run=roster_run, current_war_run=current_run, war_log_run=war_log_run,
                existing_history_path=root / "history.json", existing_site_data_dir=root / "site-data", output_dir=output,
            )
            current = json.loads((output / "site-data" / "current-war.json").read_text())
            self.assertEqual(current["clan_stars"], 38)
            self.assertEqual(current["stars_earned"], 38)
            self.assertEqual(current["attack_stars_total"], 43)
            self.assertEqual(current["attacks_used"], 18)
            self.assertEqual(current["attacks_available"], 30)
            rendered = json.dumps(current)
            for forbidden in ("#P", "#D", "player_tag", "attacker_tag", "defender_tag"):
                self.assertNotIn(forbidden, rendered)

    def test_roster_rebuild_reflects_membership_and_town_hall_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clan = load("clan.json")
            clan["memberList"] = [clan["memberList"][0]]
            clan["memberList"][0]["role"] = "admin"
            clan["memberList"][0]["townHallLevel"] = 12
            roster_run = self.make_probe(
                root, "roster", "raw_clan_response.json", clan, "2026-07-20T14:00:00Z"
            )
            current_run = self.make_probe(
                root, "current", "raw_current_war_response.json", {"state": "notInWar"}, "2026-07-20T14:01:00Z"
            )
            war_log_run = self.make_probe(
                root, "warlog", "raw_war_log_response.json", {"items": []}, "2026-07-20T14:02:00Z"
            )
            output = root / "output"
            build_site_update(
                roster_run=roster_run,
                current_war_run=current_run,
                war_log_run=war_log_run,
                existing_history_path=root / "history.json",
                existing_site_data_dir=root / "site-data",
                output_dir=output,
            )
            roster = json.loads((output / "site-data" / "roster.json").read_text())
            self.assertEqual(roster["composition"]["total_members"], 1)
            self.assertEqual(roster["members"][0]["town_hall_level"], 12)
            self.assertEqual(roster["members"][0]["clan_role"], "admin")

    def test_identical_second_build_has_no_public_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clan = load("clan.json")
            roster_run = self.make_probe(root, "roster", "raw_clan_response.json", clan, "2026-07-20T12:00:00Z")
            current_run = self.make_probe(root, "current", "raw_current_war_response.json", load("current_war.json"), "2026-07-20T12:01:00Z")
            war_log_run = self.make_probe(root, "warlog", "raw_war_log_response.json", load("war_log.json"), "2026-07-20T12:02:00Z")
            first = root / "first"
            history_path = root / "history.json"
            site_data = root / "site-data"
            build_site_update(
                roster_run=roster_run,
                current_war_run=current_run,
                war_log_run=war_log_run,
                existing_history_path=history_path,
                existing_site_data_dir=site_data,
                output_dir=first,
            )
            history_path.write_text((first / "history-next.json").read_text(), encoding="utf-8")
            for name in PUBLIC_FILENAMES:
                write(site_data / name, json.loads((first / "site-data" / name).read_text()))

            second = root / "second"
            summary = build_site_update(
                roster_run=roster_run,
                current_war_run=current_run,
                war_log_run=war_log_run,
                existing_history_path=history_path,
                existing_site_data_dir=site_data,
                output_dir=second,
            )
            self.assertEqual(summary["public_change_count"], 0)
            self.assertFalse(summary["history_changed"])

    def test_v1_history_requires_explicit_migration_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            roster_run = self.make_probe(root, "roster", "raw_clan_response.json", load("clan.json"), "2026-07-20T12:00:00Z")
            current_run = self.make_probe(root, "current", "raw_current_war_response.json", load("current_war.json"), "2026-07-20T12:01:00Z")
            war_log_run = self.make_probe(root, "warlog", "raw_war_log_response.json", {"items": []}, "2026-07-20T12:02:00Z")
            history_path = root / "history.json"
            write(history_path, {"schema_version": 1, "wars": []})
            arguments = {
                "roster_run": roster_run,
                "current_war_run": current_run,
                "war_log_run": war_log_run,
                "existing_history_path": history_path,
                "existing_site_data_dir": root / "site-data",
            }
            with self.assertRaisesRegex(SiteUpdateError, "separately approved migration"):
                build_site_update(output_dir=root / "blocked", **arguments)
            self.assertFalse((root / "blocked").exists())

            build_site_update(
                output_dir=root / "allowed",
                allow_history_migration=True,
                **arguments,
            )
            migrated = json.loads((root / "allowed" / "history-next.json").read_text())
            self.assertEqual(migrated["schema_version"], 2)


if __name__ == "__main__":
    unittest.main()
