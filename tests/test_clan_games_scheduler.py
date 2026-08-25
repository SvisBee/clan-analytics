from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "clan_games"))

import plan_clan_games_scan as planner_cli  # noqa: E402


POWERSHELL = "powershell.exe"
SCHEDULER = REPO_ROOT / "scripts" / "clan_games" / "clan_games_scheduler.ps1"
INSTALLER = (
    REPO_ROOT / "scripts" / "clan_games" / "install_clan_games_collector_task.ps1"
)


def run_powershell(command: str, *, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def ps(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class ClanGamesPlanningCliTests(unittest.TestCase):
    def test_absent_registry_is_true_noop_and_does_not_create_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = planner_cli.execute(
                root, clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc)
            )
            self.assertEqual(("success", "no_event_registry", False), (
                result["status"], result["result_code"], result["collector_due"]
            ))
            self.assertFalse(
                (root / planner_cli.DATABASE_RELATIVE_PATH).exists()
            )


class ClanGamesSchedulerPowerShellTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.health = self.root / "health"
        self.marker = self.root / "collector-count.txt"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_planner(self, payload: dict, exit_code: int = 0) -> Path:
        path = self.root / "planner.py"
        path.write_text(
            "import json,sys\n"
            f"print(json.dumps({payload!r}))\n"
            f"raise SystemExit({exit_code})\n",
            encoding="utf-8",
        )
        return path

    def write_collector(self, payload: dict, exit_code: int = 0) -> Path:
        path = self.root / "collector.ps1"
        serialized = json.dumps(payload, sort_keys=True).replace("'", "''")
        path.write_text(
            "param([string]$EventId,[string]$ScanId,[string]$ScanKind)\n"
            f"$marker = {ps(self.marker)}\n"
            "$count = if (Test-Path -LiteralPath $marker) { "
            "[int](Get-Content -LiteralPath $marker -Raw) } else { 0 }\n"
            "Set-Content -LiteralPath $marker -Value ($count + 1) -NoNewline\n"
            f"'{serialized}' | Write-Output\n"
            f"exit {exit_code}\n",
            encoding="utf-8",
        )
        return path

    def invoke(self, planner: Path, collector: Path, *, check_site: bool = False):
        command = (
            f". {ps(SCHEDULER)}; "
            f"$code = Invoke-ClanGamesScheduler -WorkspaceRoot {ps(self.root)} "
            f"-PythonPath {ps(Path(sys.executable))} -PlannerPath {ps(planner)} "
            f"-CollectorPath {ps(collector)} -HealthRoot {ps(self.health)} "
            f"-CheckSiteMutex:${str(check_site).lower()}; exit $code"
        )
        return run_powershell(command)

    def test_no_due_writes_idle_health_without_invoking_collector(self) -> None:
        planner = self.write_planner({
            "status": "success", "action": "no_event_registry",
            "result_code": "no_event_registry", "collector_due": False,
            "event_id": None, "operator_hint_code": None,
        })
        collector = self.write_collector({"status": "success", "result_code": "success"})
        result = self.invoke(planner, collector)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(self.marker.exists())
        health = json.loads((self.health / "latest-run.json").read_text("utf-8"))
        self.assertEqual(("idle", "no_event_registry", False), (
            health["status"], health["result_code"], health["collector_invoked"]
        ))
        self.assertGreaterEqual(health["duration_seconds"], 0)
        self.assertFalse((self.health / "last-scan-success.json").exists())

    def test_due_invokes_collector_exactly_once_and_records_success(self) -> None:
        planner = self.write_planner({
            "status": "success", "action": "periodic_due",
            "result_code": "periodic_due", "collector_due": True,
            "event_id": "event-1", "scan_id": "scan-1", "scan_kind": "periodic",
            "scheduled_for_utc": "2026-09-10T06:00:00.000000Z",
            "operator_hint_code": None,
        })
        collector = self.write_collector({
            "status": "success", "result_code": "success", "operator_hint_code": None
        })
        result = self.invoke(planner, collector)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("1", self.marker.read_text("utf-8"))
        health = json.loads((self.health / "latest-run.json").read_text("utf-8"))
        self.assertEqual(("success", True, "scan-1"), (
            health["status"], health["collector_invoked"], health["scan_id"]
        ))
        self.assertTrue((self.health / "last-scan-success.json").exists())

    def test_partial_and_403_failure_have_bounded_health(self) -> None:
        due = {
            "status": "success", "action": "periodic_due",
            "result_code": "periodic_due", "collector_due": True,
            "event_id": "event-1", "scan_id": "scan-1", "scan_kind": "periodic",
            "scheduled_for_utc": "2026-09-10T06:00:00.000000Z",
            "operator_hint_code": None,
        }
        for status, code, exit_code, expected in (
            ("partial_success", "partial_player_failures", 0, "partial_success"),
            ("failed", "api_http_403", 1, "failed"),
        ):
            with self.subTest(status=status):
                self.health.mkdir(parents=True, exist_ok=True)
                collector = self.write_collector({
                    "status": status, "result_code": code,
                    "operator_hint_code": "verify_api_access" if exit_code else None,
                }, exit_code)
                result = self.invoke(self.write_planner(due), collector)
                self.assertEqual(exit_code, result.returncode, result.stderr)
                health = json.loads((self.health / "latest-run.json").read_text("utf-8"))
                self.assertEqual((expected, code), (health["status"], health["result_code"]))
                serialized = json.dumps(health)
                self.assertNotIn("#", serialized)
                self.assertNotIn(str(self.root), serialized)

    def test_site_mutex_busy_skips_planner_and_collector(self) -> None:
        planner_marker = self.root / "planner-called.txt"
        planner = self.root / "planner.py"
        planner.write_text(
            f"from pathlib import Path\nPath({str(planner_marker)!r}).write_text('1')\n",
            encoding="utf-8",
        )
        collector = self.write_collector({"status": "success", "result_code": "success"})
        command = (
            f". {ps(SCHEDULER)}; "
            f"$name = Get-WorkspaceMutexName -WorkspaceRoot {ps(self.root)}; "
            "$created=$false; $held=[Threading.Mutex]::new($false,$name,[ref]$created); "
            "$null=$held.WaitOne(0); "
            f"$job = Start-Job -ScriptBlock {{ param($s,$w,$p,$pl,$c,$h) . $s; "
            "Invoke-ClanGamesScheduler -WorkspaceRoot $w -PythonPath $p "
            "-PlannerPath $pl -CollectorPath $c -HealthRoot $h } "
            f"-ArgumentList {ps(SCHEDULER)},{ps(self.root)},{ps(Path(sys.executable))},{ps(planner)},{ps(collector)},{ps(self.health)}; "
            "$null=Wait-Job $job; Receive-Job $job; $exit=$job.ChildJobs[0].JobStateInfo.State; "
            "$held.ReleaseMutex(); $held.Dispose(); Remove-Job $job;"
        )
        result = run_powershell(command)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(planner_marker.exists())
        self.assertFalse(self.marker.exists())
        health = json.loads((self.health / "latest-run.json").read_text("utf-8"))
        self.assertEqual(("warning", "workspace_busy"), (
            health["status"], health["result_code"]
        ))

    def test_duplicate_scheduler_process_is_prevented_and_health_finalized(self) -> None:
        planner_marker = self.root / "planner-called.txt"
        planner = self.root / "planner.py"
        planner.write_text(
            f"from pathlib import Path\nPath({str(planner_marker)!r}).write_text('1')\n",
            encoding="utf-8",
        )
        collector = self.write_collector({"status": "success", "result_code": "success"})
        command = (
            f". {ps(SCHEDULER)}; "
            f"$name = Get-ClanGamesSchedulerMutexName -WorkspaceRoot {ps(self.root)}; "
            "$created=$false; $held=[Threading.Mutex]::new($false,$name,[ref]$created); "
            "$null=$held.WaitOne(0); "
            f"$job = Start-Job -ScriptBlock {{ param($s,$w,$p,$pl,$c,$h) . $s; "
            "Invoke-ClanGamesScheduler -WorkspaceRoot $w -PythonPath $p "
            "-PlannerPath $pl -CollectorPath $c -HealthRoot $h -CheckSiteMutex:$false } "
            f"-ArgumentList {ps(SCHEDULER)},{ps(self.root)},{ps(Path(sys.executable))},{ps(planner)},{ps(collector)},{ps(self.health)}; "
            "$null=Wait-Job $job; Receive-Job $job; "
            "$held.ReleaseMutex(); $held.Dispose(); Remove-Job $job;"
        )
        result = run_powershell(command)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(planner_marker.exists())
        self.assertFalse(self.marker.exists())
        health = json.loads((self.health / "latest-run.json").read_text("utf-8"))
        self.assertEqual(("warning", "scheduler_busy"), (
            health["status"], health["result_code"]
        ))

    def test_health_write_is_atomic_and_contains_no_temporary_files(self) -> None:
        planner = self.write_planner({
            "status": "success", "action": "baseline_missed",
            "result_code": "baseline_missed", "collector_due": False,
            "event_id": "event-1", "operator_hint_code": None,
        })
        collector = self.write_collector({"status": "success", "result_code": "success"})
        self.assertEqual(0, self.invoke(planner, collector).returncode)
        self.assertEqual([], list(self.health.glob("*.tmp")))
        warning = json.loads((self.health / "latest-warning.json").read_text("utf-8"))
        self.assertEqual("baseline_missed", warning["result_code"])


class ClanGamesTaskContractTests(unittest.TestCase):
    def test_scheduler_mutex_identity_is_stable_scoped_and_bounded(self) -> None:
        command = (
            f". {ps(SCHEDULER)}; @({ps('D:/coc')},{ps('D:/coc/')},{ps('d:/COC')},{ps('D:/other')}) "
            "| % { Get-ClanGamesSchedulerMutexName -WorkspaceRoot $_ }"
        )
        result = run_powershell(command)
        self.assertEqual(0, result.returncode, result.stderr)
        names = result.stdout.splitlines()
        self.assertEqual(names[0], names[1])
        self.assertEqual(names[0], names[2])
        self.assertNotEqual(names[0], names[3])
        self.assertRegex(
            names[0], r"^Local\\ClashClanAnalyticsClanGamesScheduler-[0-9a-f]{24}$"
        )
        self.assertNotIn("D:\\coc", names[0])

    def test_task_contract_is_dedicated_hourly_ignore_new_and_bounded(self) -> None:
        text = INSTALLER.read_text(encoding="utf-8")
        for expected in (
            "Clash Clan Analytics - Clan Games Collector",
            "run_clan_games_scheduler.ps1",
            "trigger_minute = 20",
            "repetition_interval = 'PT1H'",
            "multiple_instances = 'IgnoreNew'",
            "execution_time_limit = 'PT20M'",
            "StartWhenAvailable",
            "RunOnlyIfNetworkAvailable",
            "WakeToRun",
            "LogonType Interactive",
            "RunLevel Limited",
            "semantic_hash",
        ):
            self.assertIn(expected, text)
        self.assertNotIn("Clash Clan Analytics - Hourly Update", text)
        self.assertNotIn("-Force", text)

    def test_all_clan_games_powershell_files_parse(self) -> None:
        for path in (REPO_ROOT / "scripts" / "clan_games").glob("*.ps1"):
            command = (
                f"$tokens=$null;$errors=$null;[void][Management.Automation.Language.Parser]::ParseFile("
                f"{ps(path)},[ref]$tokens,[ref]$errors); if($errors.Count){{ $errors | % Message; exit 1 }}"
            )
            result = run_powershell(command)
            self.assertEqual(0, result.returncode, f"{path}: {result.stdout} {result.stderr}")

    def test_operator_health_handles_absent_malformed_legacy_and_json(self) -> None:
        script = REPO_ROOT / "scripts" / "clan_games" / "show_clan_games_health.ps1"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            absent = subprocess.run(
                [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-TestHealthRoot", str(root)],
                capture_output=True, text=True, timeout=20, check=False,
            )
            self.assertEqual(0, absent.returncode, absent.stderr)
            self.assertIn("has not run yet", absent.stdout)
            (root / "latest-run.json").write_text("{broken", encoding="utf-8")
            malformed = subprocess.run(
                [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-TestHealthRoot", str(root), "-Json"],
                capture_output=True, text=True, timeout=20, check=False,
            )
            self.assertEqual(0, malformed.returncode, malformed.stderr)
            self.assertEqual("unreadable", json.loads(malformed.stdout)["records"]["latest-run"]["status"])
            (root / "latest-run.json").write_text(
                json.dumps({"status": "legacy", "result_code": "no_scan_due"}), encoding="utf-8"
            )
            legacy = subprocess.run(
                [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-TestHealthRoot", str(root)],
                capture_output=True, text=True, timeout=20, check=False,
            )
            self.assertEqual(0, legacy.returncode, legacy.stderr)
            self.assertIn("Clan Games health: legacy", legacy.stdout)


if __name__ == "__main__":
    unittest.main()
