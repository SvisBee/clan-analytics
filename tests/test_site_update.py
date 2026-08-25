from __future__ import annotations

import copy
import json
import subprocess
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
from clan_analytics.api.normalization import normalize_clan  # noqa: E402
from clan_analytics.clan_snapshot_history import (  # noqa: E402
    initialize_snapshot_store,
    record_confirmed_observation,
)


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class SiteUpdateTests(unittest.TestCase):
    def make_snapshot_database(
        self,
        root: Path,
        payload: dict,
        *,
        first_at: str = "2026-07-20T11:00:00Z",
        second_at: str = "2026-07-20T12:00:00Z",
    ) -> Path:
        path = root / "snapshot" / "history.sqlite3"
        initialize_snapshot_store(path)
        payload = copy.deepcopy(payload)
        for member in payload.get("memberList", []):
            member.setdefault("donations", 0)
            member.setdefault("donationsReceived", 0)
        before = copy.deepcopy(payload)
        for member in before.get("memberList", []):
            member["donations"] = max(0, int(member.get("donations", 0)) - 3)
            member["donationsReceived"] = max(
                0, int(member.get("donationsReceived", 0)) - 2
            )
        record_confirmed_observation(
            path,
            normalize_clan(before, collected_at=first_at, raw_source_reference="fictional"),
            first_at,
            "fictional-before",
            "tests-v1",
        )
        record_confirmed_observation(
            path,
            normalize_clan(payload, collected_at=second_at, raw_source_reference="fictional"),
            second_at,
            "fictional-after",
            "tests-v1",
        )
        return path

    def test_public_privacy_scan_rejects_private_key_variants_and_credentials(self) -> None:
        for payload in (
            {"playerTag": "private"}, {"player_tag": "private"},
            {"clanTag": "private"}, {"clan_tag": "private"},
            {"attackerTag": "private"}, {"defenderTag": "private"},
            {"opponentTag": "private"}, {"opponentName": "private"},
            {"outer": {"rawSourceReference": "private"}},
            {"outer": {"raw_source_reference": "private"}},
            {"dpapiMetadata": "private"}, {"access_token": "private"},
            {"player_id_internal": "private"},
            {"membershipSegmentId": "private"},
            {"payload_id": "private"}, {"observationId": "private"},
            {"fingerprint": "private"}, {"source_run_id": "private"},
            {"value": "a" * 64},
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
        payload = copy.deepcopy(payload)
        if raw_name == "raw_clan_response.json":
            for member in payload.get("memberList", []):
                member.setdefault("donations", 0)
                member.setdefault("donationsReceived", 0)
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
                snapshot_history_db=self.make_snapshot_database(root, clan),
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
            weekly = json.loads(
                (output / "site-data" / "donations-weekly.json").read_text()
            )
            self.assertEqual(2, weekly["schema_version"])
            self.assertEqual("game_counter_snapshot", weekly["metric_semantics"])
            self.assertIn("donations", weekly["weeks"][0])
            self.assertNotIn("donations_confirmed", weekly["weeks"][0])
            self.assertEqual("current_roster", weekly["scope"])
            self.assertEqual(6, len(PUBLIC_FILENAMES))
            self.assertEqual(
                "site/data/donations-weekly.json",
                summary["donations_weekly"]["logical_public_file_path"],
            )

    def test_star_accounting_fixture_flows_to_public_current_war_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            roster_run = self.make_probe(root, "roster", "raw_clan_response.json", load("clan.json"), "2026-07-20T12:00:00Z")
            current_run = self.make_probe(root, "current", "raw_current_war_response.json", load("current_war_stars_accounting.json"), "2026-07-20T12:01:00Z")
            war_log_run = self.make_probe(root, "warlog", "raw_war_log_response.json", {"items": []}, "2026-07-20T12:02:00Z")
            output = root / "output"
            build_site_update(
                roster_run=roster_run, current_war_run=current_run, war_log_run=war_log_run,
                existing_history_path=root / "history.json", existing_site_data_dir=root / "site-data",
                snapshot_history_db=self.make_snapshot_database(root, load("clan.json")), output_dir=output,
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
                snapshot_history_db=self.make_snapshot_database(
                    root, clan,
                    first_at="2026-07-20T13:00:00Z",
                    second_at="2026-07-20T14:00:00Z",
                ),
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
            snapshot_database = self.make_snapshot_database(root, clan)
            build_site_update(
                roster_run=roster_run,
                current_war_run=current_run,
                war_log_run=war_log_run,
                existing_history_path=history_path,
                existing_site_data_dir=site_data,
                snapshot_history_db=snapshot_database,
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
                snapshot_history_db=snapshot_database,
                output_dir=second,
            )
            self.assertEqual(summary["public_change_count"], 0)
            self.assertFalse(summary["history_changed"])

    def test_weekly_semantic_no_change_preserves_exact_public_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clan = load("clan.json")
            snapshot_database = self.make_snapshot_database(root, clan)
            current_run = self.make_probe(
                root, "current", "raw_current_war_response.json",
                {"state": "notInWar"}, "2026-07-20T12:01:00Z",
            )
            war_log_run = self.make_probe(
                root, "warlog", "raw_war_log_response.json",
                {"items": []}, "2026-07-20T12:02:00Z",
            )
            first = root / "first"
            build_site_update(
                roster_run=self.make_probe(
                    root, "roster-first", "raw_clan_response.json", clan,
                    "2026-07-20T12:00:00Z",
                ),
                current_war_run=current_run,
                war_log_run=war_log_run,
                existing_history_path=root / "history.json",
                existing_site_data_dir=root / "site-data",
                snapshot_history_db=snapshot_database,
                output_dir=first,
            )
            (root / "history.json").write_bytes((first / "history-next.json").read_bytes())
            for name in PUBLIC_FILENAMES:
                target = root / "site-data" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((first / "site-data" / name).read_bytes())

            second = root / "second"
            summary = build_site_update(
                roster_run=self.make_probe(
                    root, "roster-second", "raw_clan_response.json", clan,
                    "2026-07-20T13:00:00Z",
                ),
                current_war_run=current_run,
                war_log_run=war_log_run,
                existing_history_path=root / "history.json",
                existing_site_data_dir=root / "site-data",
                snapshot_history_db=snapshot_database,
                output_dir=second,
            )
            self.assertFalse(summary["public_changed"]["donations_weekly"])
            self.assertEqual(
                (first / "site-data" / "donations-weekly.json").read_bytes(),
                (second / "site-data" / "donations-weekly.json").read_bytes(),
            )

    def test_meaningful_weekly_change_changes_public_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clan = load("clan.json")
            snapshot_database = self.make_snapshot_database(root, clan)
            current_run = self.make_probe(root, "current", "raw_current_war_response.json", {"state": "notInWar"}, "2026-07-20T12:01:00Z")
            war_log_run = self.make_probe(root, "warlog", "raw_war_log_response.json", {"items": []}, "2026-07-20T12:02:00Z")
            first = root / "first"
            build_site_update(
                roster_run=self.make_probe(root, "roster-first", "raw_clan_response.json", clan, "2026-07-20T12:00:00Z"),
                current_war_run=current_run, war_log_run=war_log_run,
                existing_history_path=root / "history.json", existing_site_data_dir=root / "site-data",
                snapshot_history_db=snapshot_database, output_dir=first,
            )
            (root / "history.json").write_bytes((first / "history-next.json").read_bytes())
            for name in PUBLIC_FILENAMES:
                target = root / "site-data" / name; target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((first / "site-data" / name).read_bytes())

            changed_clan = copy.deepcopy(clan)
            for member in changed_clan["memberList"]:
                member.setdefault("donations", 0)
                member.setdefault("donationsReceived", 0)
            changed_clan["memberList"][1]["donations"] += 5
            changed_clan["memberList"][1]["donationsReceived"] += 4
            record_confirmed_observation(
                snapshot_database,
                normalize_clan(changed_clan, collected_at="2026-07-20T13:00:00Z", raw_source_reference="fictional"),
                "2026-07-20T13:00:00Z", "fictional-change", "tests-v1",
            )
            second = root / "second"
            summary = build_site_update(
                roster_run=self.make_probe(root, "roster-second", "raw_clan_response.json", changed_clan, "2026-07-20T13:00:00Z"),
                current_war_run=current_run, war_log_run=war_log_run,
                existing_history_path=root / "history.json", existing_site_data_dir=root / "site-data",
                snapshot_history_db=snapshot_database, output_dir=second,
            )
            self.assertTrue(summary["public_changed"]["donations_weekly"])
            self.assertNotEqual(
                (first / "site-data" / "donations-weekly.json").read_bytes(),
                (second / "site-data" / "donations-weekly.json").read_bytes(),
            )

    def test_builder_uses_exact_current_identity_and_excludes_departed_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def payload(*members: tuple[str, str, int, int]) -> dict:
                clan = load("clan.json")
                clan["memberList"] = [
                    {
                        "tag": tag,
                        "name": name,
                        "donations": donations,
                        "donationsReceived": received,
                    }
                    for tag, name, donations, received in members
                ]
                return clan

            observations = (
                ("2026-07-20T10:00:00Z", payload(("#AAAA", "Old name", 10, 10), ("#BBBB", "Departed", 20, 20), ("#CCCC", "Twin", 5, 5))),
                ("2026-07-20T11:00:00Z", payload(("#AAAA", "Middle name", 12, 11), ("#BBBB", "Departed", 30, 30), ("#CCCC", "Twin", 7, 8))),
                ("2026-07-20T12:00:00Z", payload(("#AAAA", "Twin", 15, 13), ("#CCCC", "Twin", 10, 9))),
            )
            snapshot_database = root / "snapshot" / "history.sqlite3"
            initialize_snapshot_store(snapshot_database)
            for index, (at, clan) in enumerate(observations):
                record_confirmed_observation(
                    snapshot_database,
                    normalize_clan(clan, collected_at=at, raw_source_reference="fictional"),
                    at, f"fictional-{index}", "tests-v1",
                )
            current_clan = observations[-1][1]
            output = root / "output"
            build_site_update(
                roster_run=self.make_probe(root, "roster", "raw_clan_response.json", current_clan, "2026-07-20T12:00:00Z"),
                current_war_run=self.make_probe(root, "current", "raw_current_war_response.json", {"state": "notInWar"}, "2026-07-20T12:01:00Z"),
                war_log_run=self.make_probe(root, "warlog", "raw_war_log_response.json", {"items": []}, "2026-07-20T12:02:00Z"),
                existing_history_path=root / "history.json",
                existing_site_data_dir=root / "site-data",
                snapshot_history_db=snapshot_database,
                output_dir=output,
            )
            weekly = json.loads((output / "site-data" / "donations-weekly.json").read_text())
            current = weekly["weeks"][0]
            self.assertEqual(["Twin", "Twin"], [row["nickname"] for row in current["players"]])
            self.assertEqual((25, 22), (current["donations"], current["donations_received"]))
            self.assertNotIn("Departed", json.dumps(weekly))
            self.assertNotIn("#AAAA", json.dumps(weekly))

    def test_invalid_snapshot_store_fails_without_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            invalid = root / "invalid.sqlite3"
            invalid.write_bytes(b"not a sqlite database")
            with self.assertRaisesRegex(SiteUpdateError, "weekly donations build failed safely"):
                build_site_update(
                    roster_run=self.make_probe(root, "roster", "raw_clan_response.json", load("clan.json"), "2026-07-20T12:00:00Z"),
                    current_war_run=self.make_probe(root, "current", "raw_current_war_response.json", {"state": "notInWar"}, "2026-07-20T12:01:00Z"),
                    war_log_run=self.make_probe(root, "warlog", "raw_war_log_response.json", {"items": []}, "2026-07-20T12:02:00Z"),
                    existing_history_path=root / "history.json",
                    existing_site_data_dir=root / "site-data",
                    snapshot_history_db=invalid,
                    output_dir=root / "proposal",
                )
            self.assertFalse((root / "proposal").exists())

    def test_stale_roster_older_than_snapshot_store_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clan = load("clan.json")
            snapshot_database = self.make_snapshot_database(root, clan)
            with self.assertRaisesRegex(SiteUpdateError, "roster input is older"):
                build_site_update(
                    roster_run=self.make_probe(root, "roster", "raw_clan_response.json", clan, "2026-07-20T11:59:59Z"),
                    current_war_run=self.make_probe(root, "current", "raw_current_war_response.json", {"state": "notInWar"}, "2026-07-20T12:01:00Z"),
                    war_log_run=self.make_probe(root, "warlog", "raw_war_log_response.json", {"items": []}, "2026-07-20T12:02:00Z"),
                    existing_history_path=root / "history.json",
                    existing_site_data_dir=root / "site-data",
                    snapshot_history_db=snapshot_database,
                    output_dir=root / "proposal",
                )
            self.assertFalse((root / "proposal").exists())

    def test_builder_cli_rejects_nonproduction_snapshot_path(self) -> None:
        script = REPO_ROOT / "scripts" / "update" / "build_site_update.py"
        result = subprocess.run(
            [
                sys.executable, str(script),
                "--roster-run", "unused", "--current-war-run", "unused",
                "--war-log-run", "unused", "--history-path", "unused",
                "--site-data-dir", "unused", "--output-dir", "unused",
                "--workspace-root", str(Path(tempfile.gettempdir()) / "workspace"),
                "--snapshot-history-db", str(Path(tempfile.gettempdir()) / "other.sqlite3"),
            ],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("fixed workspace-local path", result.stderr)

    def test_updater_approved_apply_contract_includes_weekly_file(self) -> None:
        updater = (REPO_ROOT / "scripts" / "update" / "update_clan_site.ps1").read_text(encoding="utf-8")
        self.assertGreaterEqual(updater.count("donations-weekly.json"), 2)
        self.assertIn("--snapshot-history-db", updater)

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
                "snapshot_history_db": self.make_snapshot_database(root, load("clan.json")),
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
