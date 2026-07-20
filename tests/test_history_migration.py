from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from clan_analytics.history_migration import MigrationError, execute_migration, preview_migration  # noqa: E402

CLI = REPO_ROOT / "scripts" / "update" / "migrate_war_history_v1_to_v2.py"


def write_v1(path: Path) -> str:
    payload = non_empty_v1()
    data = json.dumps(payload).encode("utf-8")
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def non_empty_v1() -> dict[str, object]:
    return {
        "schema_version": 1,
        "wars": [{
            "war_id": "a" * 64,
            "first_collected_at": "2026-07-20T12:00:00Z",
            "last_collected_at": "2026-07-20T13:00:00Z",
            "observations": 2,
            "finalized": False,
            "states_seen": ["inWar"],
            "latest": {
                "state": "inWar", "preparation_start_time": "20260720T100000.000Z",
                "start_time": "20260720T120000.000Z", "end_time": "20260721T120000.000Z",
                "team_size": 1, "attacks_per_member": 2, "clan_stars": 1,
                "clan_tag": "#CLAN", "opponent_tag": "#OPP",
                "members": [{"player_tag": "#PLAYER", "display_name": "Renamed", "town_hall_level": 16, "map_position": 1, "attacks": [{"attacker_tag": "#PLAYER", "defender_tag": "#ENEMY", "stars": 1, "destruction_percentage": 50, "order": 1}]}],
                "source": {"source_timestamp": "2026-07-20T13:00:00Z", "collected_at": "2026-07-20T13:00:00Z", "raw_source_reference": "fixture"},
            },
        }],
    }


class HistoryMigrationCommandTests(unittest.TestCase):
    def test_cli_preview_and_execute_migrate_realistic_non_empty_v1(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "history.json"
            source.write_text(json.dumps(non_empty_v1()), encoding="utf-8")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            proposal = root / "proposal.json"
            preview = subprocess.run(
                [sys.executable, str(CLI), "--source", str(source), "--preview", "--output", str(proposal)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(preview.returncode, 0, preview.stderr)
            proposed = json.loads(proposal.read_text(encoding="utf-8"))
            self.assertEqual(proposed["schema_version"], 2)
            self.assertEqual(len(proposed["wars"]), 1)
            self.assertEqual(proposed["wars"][0]["canonical"]["members"][0]["player_tag"], "#PLAYER")
            self.assertEqual(json.loads(source.read_text(encoding="utf-8"))["schema_version"], 1)
            execute = subprocess.run(
                [sys.executable, str(CLI), "--source", str(source), "--backup-dir", str(root / "backup"),
                 "--expected-source-sha256", digest, "--confirm-migration"],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(execute.returncode, 0, execute.stderr)
            report = json.loads(execute.stdout)
            self.assertEqual(report["wars"], 1)
            self.assertEqual(json.loads(source.read_text(encoding="utf-8"))["schema_version"], 2)
            self.assertEqual(len(list((root / "backup").glob("*.json"))), 1)

    def test_preview_does_not_change_source_and_can_write_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "history.json"
            digest = write_v1(source)
            proposal = root / "proposal.json"
            result = preview_migration(source, proposal)
            self.assertEqual(result["source_sha256"], digest)
            self.assertEqual(json.loads(source.read_text())["schema_version"], 1)
            self.assertEqual(json.loads(proposal.read_text())["schema_version"], 2)

    def test_preview_rejects_corrupted_and_unknown_schema_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corrupt = root / "corrupt.json"
            corrupt.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(MigrationError, "JSON stage"):
                preview_migration(corrupt)
            future = root / "future.json"
            future.write_text(json.dumps({"schema_version": 99, "wars": []}), encoding="utf-8")
            with self.assertRaisesRegex(MigrationError, "validation stage"):
                preview_migration(future)

    def test_execute_hash_mismatch_and_backup_collision_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "history.json"
            digest = write_v1(source)
            with self.assertRaisesRegex(MigrationError, "does not match"):
                execute_migration(source_path=source, backup_dir=root / "backup", expected_source_sha256="0" * 64, confirm=True)
            self.assertEqual(json.loads(source.read_text())["schema_version"], 1)
            backup = root / "backup"
            from datetime import datetime, timezone
            with patch("clan_analytics.history_migration.datetime") as clock:
                clock.now.return_value = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
                execute_migration(source_path=source, backup_dir=backup, expected_source_sha256=digest, confirm=True)
            source.write_bytes(json.dumps(non_empty_v1()).encode("utf-8"))
            with patch("clan_analytics.history_migration.datetime") as clock:
                clock.now.return_value = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
                with self.assertRaisesRegex(MigrationError, "backup collision"):
                    execute_migration(source_path=source, backup_dir=backup, expected_source_sha256=digest, confirm=True)

    def test_execute_migrates_and_replace_failure_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "history.json"
            digest = write_v1(source)
            result = execute_migration(source_path=source, backup_dir=root / "backup", expected_source_sha256=digest, confirm=True)
            self.assertEqual(json.loads(source.read_text())["schema_version"], 2)
            self.assertTrue(Path(result["backup_path"]).is_file())
            self.assertEqual(json.loads(Path(result["backup_path"]).read_text())["schema_version"], 1)

            source.write_bytes(json.dumps({"schema_version": 1, "wars": []}).encode())
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            real_replace = os.replace
            with patch.object(os, "replace", side_effect=OSError("fixture replace failure")):
                with self.assertRaisesRegex(MigrationError, "replace stage failed"):
                    execute_migration(source_path=source, backup_dir=root / "backup2", expected_source_sha256=digest, confirm=True)
            self.assertEqual(json.loads(source.read_text())["schema_version"], 1)

    def test_post_write_validation_failure_rolls_back_to_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "history.json"
            digest = write_v1(source)
            with patch("clan_analytics.history_migration.load_history", side_effect=ValueError("fixture read-back failure")):
                with self.assertRaisesRegex(MigrationError, "replace stage failed"):
                    execute_migration(source_path=source, backup_dir=root / "backup", expected_source_sha256=digest, confirm=True)
            self.assertEqual(json.loads(source.read_text())["schema_version"], 1)
