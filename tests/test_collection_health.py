from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HEALTH_SCRIPT = REPO_ROOT / "scripts" / "update" / "collection_health.ps1"
SHOW_SCRIPT = REPO_ROOT / "scripts" / "update" / "show_clan_site_health.ps1"
NATIVE_SCRIPT = REPO_ROOT / "scripts" / "update" / "native_process.ps1"


def powershell(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )


class CollectionHealthTests(unittest.TestCase):
    def run_health(self, body: str) -> tuple[dict[str, object], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = Path(temporary.name) / "workspace"
        run_dir = workspace / "runs" / "site_update" / "run-1"
        command = f". '{HEALTH_SCRIPT}'; {body}"
        result = powershell(command.replace("$WORKSPACE", str(workspace)).replace("$RUN", str(run_dir)))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads((run_dir / "health.json").read_text(encoding="utf-8-sig")), workspace

    def test_success_records_atomic_local_artifacts(self) -> None:
        health, workspace = self.run_health(
            "$h=New-CollectionHealthRun -WorkspaceRoot '$WORKSPACE' -RunDirectory '$RUN' -RunId 'run-1' -Mode normal; "
            "$s=Add-CollectionHealthStage -Health $h -Stage bootstrap; Complete-CollectionHealthStage -Health $h -StageRecord $s -Status success -ResultCode success; "
            "Complete-CollectionHealth -Health $h -Status success -ResultCode success -SafeMessage 'ok' -ExitCode 0"
        )
        self.assertEqual(health["schema_version"], 1)
        self.assertEqual(health["status"], "success")
        self.assertEqual(health["result_code"], "success")
        self.assertTrue((workspace / "local" / "health" / "site_update" / "latest-run.json").is_file())
        self.assertTrue((workspace / "local" / "health" / "site_update" / "last-success.json").is_file())
        self.assertTrue((Path(health["run_directory"]) / "bootstrap.log").is_file())

    def test_no_change_updates_last_success(self) -> None:
        health, workspace = self.run_health(
            "$h=New-CollectionHealthRun -WorkspaceRoot '$WORKSPACE' -RunDirectory '$RUN' -RunId 'run-1' -Mode normal; "
            "Complete-CollectionHealth -Health $h -Status no_change -ResultCode no_public_change -SafeMessage 'no change' -ExitCode 0"
        )
        self.assertEqual(health["status"], "no_change")
        self.assertTrue((workspace / "local" / "health" / "site_update" / "last-success.json").is_file())

    def test_preview_does_not_replace_last_success_or_failure(self) -> None:
        health, workspace = self.run_health(
            "$h=New-CollectionHealthRun -WorkspaceRoot '$WORKSPACE' -RunDirectory '$RUN' -RunId 'run-1' -Mode preview; "
            "Complete-CollectionHealth -Health $h -Status success -ResultCode preview_success -SafeMessage 'preview' -ExitCode 0"
        )
        root = workspace / "local" / "health" / "site_update"
        self.assertEqual(health["result_code"], "preview_success")
        self.assertFalse((root / "last-success.json").exists())
        self.assertFalse((root / "latest-failure.json").exists())

    def test_http_403_has_vpn_hint_for_each_probe(self) -> None:
        for stage in ("roster_probe", "current_war_probe", "war_log_probe"):
            with self.subTest(stage=stage):
                command = f". '{HEALTH_SCRIPT}'; Get-CollectionHealthFailure -Stage '{stage}' -Text 'HTTP request failed with status 403' | ConvertTo-Json -Compress"
                result = powershell(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                value = json.loads(result.stdout)
                self.assertEqual(value["result_code"], "api_http_403")
                self.assertEqual(value["operator_hint_code"], "enable_approved_vpn")
                self.assertNotIn("token_invalid", value["safe_message"])

    def test_other_probe_failures_are_separated(self) -> None:
        for text, code in (("HTTP request failed with status 500", "api_http_other"), ("connection reset", "api_transport_failure")):
            with self.subTest(text=text):
                result = powershell(f". '{HEALTH_SCRIPT}'; Get-CollectionHealthFailure -Stage roster_probe -Text '{text}' | ConvertTo-Json -Compress")
                self.assertEqual(json.loads(result.stdout)["result_code"], code)

    def test_failure_sanitizes_exception_text(self) -> None:
        result = powershell(
            f". '{HEALTH_SCRIPT}'; Get-CollectionHealthFailure -Stage builder -Text 'Bearer secret at D:\\coc\\data' | ConvertTo-Json -Compress"
        )
        value = json.loads(result.stdout)
        self.assertEqual(value["result_code"], "builder_failure")
        self.assertNotIn("secret", value["safe_message"].lower())
        self.assertNotIn("D:\\", value["safe_message"])

    def test_health_write_failure_is_fail_closed(self) -> None:
        """A blocked parent path must fail rather than replace health with emptiness."""
        with tempfile.TemporaryDirectory() as temporary:
            blocked_parent = Path(temporary) / "not-a-directory"
            blocked_parent.write_text("block", encoding="utf-8")
            target = blocked_parent / "health.json"
            result = powershell(
                f". '{HEALTH_SCRIPT}'; Write-CollectionHealthJsonAtomic -Path '{target}' -Value @{{schema_version=1}}"
            )
        self.assertNotEqual(result.returncode, 0)

    def test_non_probe_failure_taxonomy(self) -> None:
        expected = {
            "bootstrap": "history_preflight_failure",
            "git_preflight": "git_dirty",
            "builder": "builder_failure",
            "public_validation": "public_validation_failure",
            "tests": "tests_failure",
            "snapshot_history": "snapshot_history_unexpected_failure",
            "atomic_apply": "atomic_apply_failure",
            "git_commit": "git_commit_failure",
            "git_push": "git_push_failure",
            "mutex": "mutex_held",
            "unknown": "unexpected_failure",
        }
        for stage, code in expected.items():
            with self.subTest(stage=stage):
                result = powershell(f". '{HEALTH_SCRIPT}'; Get-CollectionHealthFailure -Stage '{stage}' -Text 'failure' | ConvertTo-Json -Compress")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["result_code"], code)

    def test_normal_success_resolves_but_retains_latest_failure(self) -> None:
        health, workspace = self.run_health(
            "$h=New-CollectionHealthRun -WorkspaceRoot '$WORKSPACE' -RunDirectory '$RUN' -RunId 'run-1' -Mode normal; "
            "Complete-CollectionHealth -Health $h -Status failed -ResultCode builder_failure -SafeMessage 'safe' -ExitCode 2"
        )
        second = workspace / "runs" / "site_update" / "run-2"
        command = (
            f". '{HEALTH_SCRIPT}'; $h=New-CollectionHealthRun -WorkspaceRoot '{workspace}' -RunDirectory '{second}' -RunId 'run-2' -Mode normal; "
            "Complete-CollectionHealth -Health $h -Status success -ResultCode success -SafeMessage 'safe' -ExitCode 0"
        )
        result = powershell(command)
        self.assertEqual(result.returncode, 0, result.stderr)
        failure = json.loads((workspace / "local" / "health" / "site_update" / "latest-failure.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(failure["result_code"], "builder_failure")
        self.assertIn("resolved_at_utc", failure)

    def test_operator_command_handles_missing_health(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SHOW_SCRIPT), "-WorkspaceRoot", temporary],
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("has not been recorded", result.stdout)

    def test_operator_command_json_after_403_is_read_only(self) -> None:
        health, workspace = self.run_health(
            "$h=New-CollectionHealthRun -WorkspaceRoot '$WORKSPACE' -RunDirectory '$RUN' -RunId 'run-1' -Mode normal; "
            "Complete-CollectionHealth -Health $h -Status failed -ResultCode api_http_403 -SafeMessage 'safe' -OperatorHintCode enable_approved_vpn -ExitCode 2"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SHOW_SCRIPT), "-WorkspaceRoot", str(workspace), "-Json"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["latest_run"]["result_code"], "api_http_403")

    def test_operator_displays_valid_summaries_with_legacy_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "local" / "health" / "site_update"
            root.mkdir(parents=True)
            current = {
                "schema_version": 1, "run_id": "new-run", "mode": "normal", "status": "success",
                "result_code": "success", "process_exit_code": 0, "started_at_utc": "2026-01-01T00:00:00+00:00",
                "finished_at_utc": "2026-01-01T00:00:01+00:00", "duration_seconds": 1,
                "current_stage": "complete", "probes": {}, "builder": {"status": "success"},
                "validation": {"status": "success"}, "publication": {"apply": "success"}, "freshness": {},
            }
            legacy = {
                "schema_version": 1, "run_id": "legacy-run", "mode": "normal", "status": "failed",
                "result_code": "builder_failure", "started_at_utc": "2025-01-01T00:00:00+00:00",
                "finished_at_utc": "2025-01-01T00:00:01+00:00", "duration_seconds": 1,
                "current_stage": "complete", "safe_message": "safe",
            }
            (root / "latest-run.json").write_text(json.dumps(current), encoding="utf-8")
            (root / "last-success.json").write_text(json.dumps(current), encoding="utf-8")
            (root / "latest-failure.json").write_text(json.dumps(legacy), encoding="utf-8")
            arguments = ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SHOW_SCRIPT), "-WorkspaceRoot", temporary]
            human = subprocess.run(arguments, capture_output=True, text=True, check=False)
            payload = subprocess.run([*arguments, "-Json"], capture_output=True, text=True, check=False)
        self.assertEqual(human.returncode, 0, human.stderr)
        self.assertIn("Latest run: new-run", human.stdout)
        self.assertIn("Latest failure: legacy-run", human.stdout)
        self.assertNotRegex(human.stdout, r"(?i)[a-z]:\\|\\\\")
        self.assertEqual(payload.returncode, 0, payload.stderr)
        result = json.loads(payload.stdout)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["latest_run"]["run_id"], "new-run")
        self.assertEqual(result["last_success"]["run_id"], "new-run")
        self.assertTrue(result["latest_failure"]["legacy_record"])
        self.assertIsNone(result["latest_failure"]["process_exit_code"])
        self.assertIn("latest-failure uses legacy health schema.", result["warnings"])

    def test_taxonomy_and_stage_contract_are_present(self) -> None:
        text = HEALTH_SCRIPT.read_text(encoding="utf-8")
        for value in ("mutex_held", "git_dirty", "git_branch_ahead", "api_http_403", "builder_failure", "git_push_failure", "health_write_failure", "unexpected_failure"):
            self.assertIn(value, text)
        for stage in ("bootstrap", "mutex", "git_preflight", "roster_probe", "current_war_probe", "war_log_probe", "builder", "public_validation", "tests", "snapshot_history", "atomic_apply", "git_commit", "git_push", "complete"):
            self.assertIn(f"'{stage}'", text)

    def test_snapshot_history_failure_codes_are_safe_and_distinct(self) -> None:
        for code in (
            "snapshot_history_initialization_failure", "snapshot_history_validation_failure",
            "snapshot_history_schema_unsupported", "snapshot_history_conflict",
            "snapshot_history_out_of_order", "snapshot_history_locked",
            "snapshot_history_write_failure", "snapshot_history_result_write_failure",
            "snapshot_history_unexpected_failure",
        ):
            with self.subTest(code=code):
                result = powershell(f". '{HEALTH_SCRIPT}'; Get-CollectionHealthFailure -Stage snapshot_history -Text '{code}' | ConvertTo-Json -Compress")
                value = json.loads(result.stdout)
                self.assertEqual(value["result_code"], code)
                self.assertNotIn("D:\\", value["safe_message"])

    def test_native_stderr_warning_with_zero_exit_is_success_under_stop(self) -> None:
        command = (
            f". '{NATIVE_SCRIPT}'; $ErrorActionPreference='Stop'; "
            "$r=Invoke-NativeProcess -FilePath 'python' -Arguments @('-c', 'import sys; print(\"ok\"); print(\"warning from fictional helper\", file=sys.stderr)'); "
            "$r | ConvertTo-Json -Compress"
        )
        result = powershell(command)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["succeeded"])
        self.assertEqual(payload["process_exit_code"], 0)
        self.assertIn("ok", payload["stdout"])
        self.assertIn("warning", payload["stderr_safe"])

    def test_native_nonzero_stderr_is_failure_and_is_sanitized(self) -> None:
        command = (
            f". '{NATIVE_SCRIPT}'; $ErrorActionPreference='Stop'; "
            "$r=Invoke-NativeProcess -FilePath 'python' -Arguments @('-c', 'import sys; print(\"token=fictional-secret D:\\\\private\", file=sys.stderr); raise SystemExit(7)'); "
            "$r | ConvertTo-Json -Compress"
        )
        result = powershell(command)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["succeeded"])
        self.assertEqual(payload["process_exit_code"], 7)
        self.assertNotIn("fictional-secret", payload["stderr_safe"])
        self.assertNotIn("D:\\", payload["stderr_safe"])

    def test_synthetic_success_finalizes_all_stages_with_nonnegative_durations(self) -> None:
        health, workspace = self.run_health(
            "$h=New-CollectionHealthRun -WorkspaceRoot '$WORKSPACE' -RunDirectory '$RUN' -RunId 'run-1' -Mode normal; "
            "foreach($name in @('bootstrap','mutex','git_preflight','roster_probe','current_war_probe','war_log_probe','builder','public_validation','tests')) { $s=Start-HealthStage -Health $h -Stage $name; Complete-HealthStage -Health $h -StageRecord $s -Status success -ResultCode success }; "
            "Add-CollectionHealthDiagnostic -Health $h -Stage builder -Code native_stderr -SafeMessage 'fictional warning' -ProcessExitCode 0; "
            "Finalize-HealthRun -Health $h -Status success -ResultCode success -SafeMessage 'safe' -ProcessExitCode 0"
        )
        self.assertEqual(health["status"], "success")
        self.assertEqual(health["process_exit_code"], 0)
        self.assertGreaterEqual(health["duration_seconds"], 0)
        self.assertEqual(health["stages"][-1]["stage"], "complete")
        self.assertTrue(all(stage["status"] != "running" for stage in health["stages"]))
        self.assertTrue(all(stage["duration_seconds"] >= 0 for stage in health["stages"]))
        self.assertTrue((workspace / "local" / "health" / "site_update" / "last-success.json").is_file())

    def test_synthetic_builder_failure_finalizes_and_preserves_last_success(self) -> None:
        health, workspace = self.run_health(
            "$h=New-CollectionHealthRun -WorkspaceRoot '$WORKSPACE' -RunDirectory '$RUN' -RunId 'run-1' -Mode normal; "
            "$s=Start-HealthStage -Health $h -Stage bootstrap; Complete-HealthStage -Health $h -StageRecord $s -Status success -ResultCode success; "
            "$s=Start-HealthStage -Health $h -Stage builder; Fail-HealthStage -Health $h -StageRecord $s -ResultCode builder_failure; "
            "Finalize-HealthRun -Health $h -Status failed -ResultCode builder_failure -SafeMessage 'safe' -ProcessExitCode 1"
        )
        self.assertEqual(health["status"], "failed")
        self.assertEqual(health["process_exit_code"], 1)
        self.assertEqual(health["stages"][-2]["status"], "failed")
        self.assertEqual(health["stages"][-1]["stage"], "complete")
        self.assertEqual(health["stages"][-1]["status"], "failed")
        self.assertTrue(all(stage["status"] != "running" for stage in health["stages"]))
        self.assertFalse((workspace / "local" / "health" / "site_update" / "last-success.json").exists())
        self.assertTrue((workspace / "local" / "health" / "site_update" / "latest-failure.json").is_file())

    def test_operator_projection_redacts_all_windows_absolute_paths(self) -> None:
        health, workspace = self.run_health(
            "$h=New-CollectionHealthRun -WorkspaceRoot '$WORKSPACE' -RunDirectory '$RUN' -RunId 'run-1' -Mode normal; "
            "Finalize-HealthRun -Health $h -Status failed -ResultCode builder_failure -SafeMessage 'safe' -ProcessExitCode 1"
        )
        root = workspace / "local" / "health" / "site_update"
        for name in ("latest-run.json", "latest-failure.json"):
            path = root / name
            record = json.loads(path.read_text(encoding="utf-8-sig"))
            record["workspace_root"] = r"D:\\coc"
            record["run_directory"] = r"D:\\coc\\runs\\site_update\\run-1"
            path.write_text(json.dumps(record), encoding="utf-8")
        for suffix in ([], ["-Json"]):
            result = subprocess.run(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SHOW_SCRIPT), "-WorkspaceRoot", str(workspace), *suffix],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotRegex(result.stdout, r"(?i)[a-z]:\\")
            self.assertNotIn(str(workspace), result.stdout)
            self.assertIn("runs/site_update/run-1", result.stdout)

    def test_operator_error_output_has_no_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "local" / "health" / "site_update"
            root.mkdir(parents=True)
            (root / "latest-run.json").write_text("{", encoding="utf-8")
            result = subprocess.run(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SHOW_SCRIPT), "-WorkspaceRoot", temporary, "-Json"],
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(result.returncode, 1)
        self.assertNotRegex(result.stdout + result.stderr, r"(?i)[a-z]:\\")
        self.assertIn("unavailable", result.stdout)

    def test_updater_uses_scoped_native_runner(self) -> None:
        updater = (REPO_ROOT / "scripts" / "update" / "update_clan_site.ps1").read_text(encoding="utf-8")
        self.assertIn("native_process.ps1", updater)
        self.assertIn("Invoke-NativeProcess", updater)


if __name__ == "__main__":
    unittest.main()
