from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "update" / "builder_failure_bundle.py"
FIXTURES = Path(__file__).with_name("fixtures")


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], text=True, capture_output=True, check=False)


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class BuilderFailureBundleTests(unittest.TestCase):
    def inputs(self, root: Path, *, invalid_history: bool = False) -> list[str]:
        workspace = root / "workspace"
        probes = root / "probes"
        pairs = (
            ("roster", "raw_clan_response.json", "clan.json"),
            ("current", "raw_current_war_response.json", "current_war.json"),
            ("warlog", "raw_war_log_response.json", "war_log.json"),
        )
        for directory, raw_name, fixture in pairs:
            target = probes / directory
            target.mkdir(parents=True)
            if directory == "roster":
                payload = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
                for member in payload.get("memberList", []):
                    member.setdefault("donations", 0)
                    member.setdefault("donationsReceived", 0)
                write(target / raw_name, payload)
            else:
                (target / raw_name).write_bytes((FIXTURES / fixture).read_bytes())
            write(target / "probe_metadata.json", {"collected_at": "2026-07-20T12:00:00Z", "request_count": 1, "response_status": 200, "redirects_followed": 0})
        history = {"schema_version": 2, "wars": [], "diagnostics": []}
        if invalid_history:
            history["diagnostics"] = "invalid"
        history_path = workspace / "data" / "war_history" / "history.json"
        write(history_path, history)
        site_data = workspace / "repo" / "site" / "data"
        for name in ("roster.json", "current-war.json", "war-log.json", "war-history.json", "site-config.json"):
            write(site_data / name, {})
        secret = workspace / "data" / "secrets" / "api-token.txt"
        secret.parent.mkdir(parents=True)
        secret.write_text("Authorization: Bearer FICTIONAL-SECRET", encoding="utf-8")
        return ["prepare", "--workspace-root", str(workspace), "--run-id", "run-1", "--repository-head", "a" * 40,
                "--roster-run", str(probes / "roster"), "--current-war-run", str(probes / "current"),
                "--war-log-run", str(probes / "warlog"), "--history-path", str(history_path), "--site-data-dir", str(site_data)]

    def test_success_removes_pending_and_leaves_no_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); args = self.inputs(root)
            self.assertEqual(run(*args).returncode, 0)
            pending = root / "workspace" / "local" / "diagnostics" / "builder_failure" / ".pending" / "run-1"
            replay = run("replay", "--bundle", str(pending))
            self.assertEqual(replay.returncode, 0, replay.stderr)
            self.assertEqual(json.loads(replay.stdout)["status"], "success")
            self.assertEqual(run("finalize", "--workspace-root", str(root / "workspace"), "--run-id", "run-1", "--success").returncode, 0)
            diagnostic = root / "workspace" / "local" / "diagnostics" / "builder_failure"
            self.assertFalse((diagnostic / ".pending" / "run-1").exists())
            self.assertFalse((diagnostic / "run-1").exists())

    def test_failure_preserves_exact_inputs_hashes_and_safe_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); self.assertEqual(run(*self.inputs(root)).returncode, 0)
            result = run("finalize", "--workspace-root", str(root / "workspace"), "--run-id", "run-1",
                         "--process-exit-code", "2", "--safe-error-text", "clan_analytics.history.HistoryError: history.wars[12].war_log.clan.stars is invalid")
            self.assertEqual(result.returncode, 0, result.stderr)
            bundle = root / "workspace" / "local" / "diagnostics" / "builder_failure" / "run-1"
            manifest = json.loads((bundle / "reproduction-manifest.json").read_text())
            self.assertGreater(manifest["artifact_count"], 6)
            self.assertEqual(manifest["safe_error_path"], "history.wars[12].war_log.clan.stars")
            for item in manifest["artifacts"]:
                path = bundle / item["relative_filename"]
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), item["sha256"])
                self.assertFalse(Path(item["relative_filename"]).is_absolute())
            combined = "\n".join(path.read_text(errors="ignore") for path in bundle.rglob("*") if path.is_file())
            self.assertNotIn("FICTIONAL-SECRET", combined)
            self.assertNotIn("Authorization: Bearer", combined)
            self.assertNotIn(str(root), (bundle / "reproduction-manifest.json").read_text())

    def test_duplicate_run_id_fails_closed_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); args = self.inputs(root)
            self.assertEqual(run(*args).returncode, 0)
            self.assertEqual(run(*args).returncode, 2)

    def test_replay_is_offline_and_does_not_change_production_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); self.assertEqual(run(*self.inputs(root, invalid_history=True)).returncode, 0)
            history = root / "workspace" / "data" / "war_history" / "history.json"
            before = hashlib.sha256(history.read_bytes()).hexdigest()
            site_data = root / "workspace" / "repo" / "site" / "data"
            site_before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in site_data.glob("*.json")}
            self.assertEqual(run("finalize", "--workspace-root", str(root / "workspace"), "--run-id", "run-1", "--process-exit-code", "2").returncode, 0)
            bundle = root / "workspace" / "local" / "diagnostics" / "builder_failure" / "run-1"
            replay = run("replay", "--bundle", str(bundle))
            self.assertEqual(replay.returncode, 2)
            self.assertEqual(json.loads(replay.stdout)["status"], "reproduced_failure")
            self.assertEqual(hashlib.sha256(history.read_bytes()).hexdigest(), before)
            self.assertEqual({path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in site_data.glob("*.json")}, site_before)

    def test_updater_contract_uses_local_bundle_and_safe_health_fields(self) -> None:
        updater = (REPO_ROOT / "scripts" / "update" / "update_clan_site.ps1").read_text(encoding="utf-8")
        health = (REPO_ROOT / "scripts" / "update" / "collection_health.ps1").read_text(encoding="utf-8")
        self.assertIn("builder_failure_bundle.py", updater)
        self.assertIn("local/diagnostics/builder_failure/$runId", updater)
        self.assertNotIn("runs\\builder_failure", updater)
        for name in ("failure_bundle_created", "failure_bundle_capture_status", "failure_bundle_artifact_count", "failure_bundle_logical_reference"):
            self.assertIn(name, health + updater)

    def test_workspace_exclusions_cover_local_diagnostics(self) -> None:
        cbm_ignore = (REPO_ROOT.parent / ".cbmignore").read_text(encoding="utf-8")
        self.assertIn("/local/**", cbm_ignore)
        self.assertEqual(REPO_ROOT.resolve(), (REPO_ROOT / ".git").parent.resolve())
        self.assertFalse((REPO_ROOT / "local").exists())


if __name__ == "__main__":
    unittest.main()
