from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RECORDER = REPO_ROOT / "scripts" / "update" / "record_clan_snapshot_history.py"
UPDATER = REPO_ROOT / "scripts" / "update" / "update_clan_site.ps1"
FIXTURE = Path(__file__).with_name("fixtures") / "clan.json"


class SnapshotHistoryUpdaterIntegrationTests(unittest.TestCase):
    def run_recorder(self, root: Path, *, source_run_id: str = "synthetic-run-1", metadata: dict[str, object] | None = None, allow_test_database: bool = True):
        raw = root / "roster.json"
        metadata_path = root / "metadata.json"
        result = root / "snapshot-history-result.json"
        raw.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        metadata_path.write_text(json.dumps(metadata or {
            "collected_at": "2026-07-26T12:00:00Z",
            "request_count": 1,
            "response_status": 200,
            "redirects_followed": 0,
        }), encoding="utf-8")
        database = root / "private" / "history.sqlite3"
        process = subprocess.run(
            [sys.executable, str(RECORDER), "--roster-json", str(raw), "--roster-metadata", str(metadata_path),
             "--database", str(database), "--workspace-root", str(root), "--source-run-id", source_run_id,
             "--result-json", str(result), *( ["--allow-test-database"] if allow_test_database else [])],
            capture_output=True, text=True, check=False,
        )
        return process, json.loads(result.read_text(encoding="utf-8")), database

    def test_records_confirmed_probe_then_is_idempotent_without_leaking_roster(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, first_result, database = self.run_recorder(root)
            second, second_result, _ = self.run_recorder(root)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertTrue(database.is_file())
            self.assertEqual(first_result["result_code"], "snapshot_history_success")
            self.assertTrue(first_result["initialized_store"])
            self.assertTrue(first_result["inserted_observation"])
            self.assertEqual(second_result["result_code"], "snapshot_history_idempotent")
            self.assertFalse(second_result["inserted_observation"])
            for result in (first_result, second_result):
                rendered = json.dumps(result)
                self.assertNotIn("#DEMO", rendered)
                self.assertNotIn("Example Clan", rendered)
                self.assertNotIn(str(root), rendered)
                self.assertEqual(result["logical_database_path"], "data/clan_snapshot_history/clan_snapshot_history.v1.sqlite3")

    def test_rejects_unconfirmed_roster_metadata_without_database_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            process, result, database = self.run_recorder(root, metadata={
                "collected_at": "2026-07-26T12:00:00Z", "request_count": 2,
                "response_status": 200, "redirects_followed": 0,
            })
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(result["result_code"], "snapshot_history_validation_failure")
        self.assertFalse(database.exists())

    def test_rejects_missing_or_naive_metadata_timestamp(self) -> None:
        for timestamp in (None, "2026-07-26T12:00:00"):
            with self.subTest(timestamp=timestamp), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                process, result, database = self.run_recorder(root, metadata={
                    "collected_at": timestamp, "request_count": 1,
                    "response_status": 200, "redirects_followed": 0,
                })
                self.assertNotEqual(process.returncode, 0)
                self.assertEqual(result["result_code"], "snapshot_history_validation_failure")
                self.assertFalse(database.exists())

    def test_result_contract_has_only_safe_operational_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            process, result, _ = self.run_recorder(root)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(result["mode"], "normal")
            self.assertEqual(result["safe_message"], "Confirmed roster observation recorded.")
            self.assertEqual(result["storage_schema_version"], 1)
            self.assertEqual(result["normalization_version"], "clan_snapshot_v1")

    def test_rejects_malformed_roster_and_nonproduction_database_without_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "roster.json"
            raw.write_text("{not-json", encoding="utf-8")
            metadata = root / "metadata.json"
            metadata.write_text(json.dumps({"collected_at": "2026-07-26T12:00:00Z", "request_count": 1, "response_status": 200, "redirects_followed": 0}), encoding="utf-8")
            result_path = root / "result.json"
            database = root / "private" / "history.sqlite3"
            malformed = subprocess.run([sys.executable, str(RECORDER), "--roster-json", str(raw), "--roster-metadata", str(metadata), "--database", str(database), "--workspace-root", str(root), "--source-run-id", "synthetic-malformed", "--result-json", str(result_path), "--allow-test-database"], capture_output=True, text=True, check=False)
            self.assertNotEqual(malformed.returncode, 0)
            self.assertEqual(json.loads(result_path.read_text(encoding="utf-8"))["result_code"], "snapshot_history_validation_failure")
            self.assertFalse(database.exists())
            raw.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
            guarded, guarded_result, _ = self.run_recorder(root, source_run_id="synthetic-guard", allow_test_database=False)
            self.assertNotEqual(guarded.returncode, 0)
            self.assertEqual(guarded_result["result_code"], "snapshot_history_validation_failure")
            self.assertFalse(database.exists())

    def test_normal_only_stage_is_after_tests_and_before_atomic_apply(self) -> None:
        script = UPDATER.read_text(encoding="utf-8")
        tests = script.index("$currentStage = 'tests'")
        preview_return = script.index("if ($PreviewOnly)", tests)
        snapshot = script.index("$currentStage = 'snapshot_history'")
        atomic_apply = script.index("$currentStage = 'atomic_apply'")
        self.assertLess(tests, preview_return)
        self.assertLess(preview_return, snapshot)
        self.assertLess(snapshot, atomic_apply)
        self.assertIn("snapshot-history-result.json", script)
        self.assertIn("raw_clan_response.json", script)


if __name__ == "__main__":
    unittest.main()
