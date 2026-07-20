from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATE = REPO_ROOT / "scripts" / "update" / "validate_war_history.py"
GIT_CHECK = REPO_ROOT / "scripts" / "update" / "check_update_git_state.py"


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)


class HistoryPreflightCommandTests(unittest.TestCase):
    def test_history_modes_fail_before_any_fake_probe_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "probe-called"
            cases = {
                "missing": None,
                "v1": {"schema_version": 1, "wars": []},
                "invalid": "{",
                "invalid_v2": {"schema_version": 2, "wars": [], "diagnostics": "bad"},
                "future": {"schema_version": 99, "wars": [], "diagnostics": []},
                "valid": {"schema_version": 2, "wars": [], "diagnostics": []},
            }
            for name, payload in cases.items():
                source = root / f"{name}.json"
                if payload is not None:
                    source.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
                result = run(sys.executable, str(VALIDATE), "--source", str(source))
                self.assertEqual(result.returncode, 0 if name in {"missing", "valid"} else 2, result.stderr)
                self.assertFalse(marker.exists(), name)

    @unittest.skipUnless(shutil.which("powershell.exe"), "requires Windows PowerShell")
    def test_real_updater_rejects_invalid_history_before_config_or_probes(self) -> None:
        """Exercise the actual updater entry point in an isolated workspace.

        Each failure happens before local configuration is read and before a run
        directory or a probe can be created.  The production workspace is never
        passed to the subprocess.
        """
        updater = REPO_ROOT / "scripts" / "update" / "update_clan_site.ps1"
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "isolated-workspace"
            isolated_repo = workspace / "repo"
            (isolated_repo / "scripts" / "update").mkdir(parents=True)
            (isolated_repo / "src").mkdir()
            shutil.copy2(updater, isolated_repo / "scripts" / "update" / updater.name)
            shutil.copy2(VALIDATE, isolated_repo / "scripts" / "update" / VALIDATE.name)
            shutil.copytree(REPO_ROOT / "src" / "clan_analytics", isolated_repo / "src" / "clan_analytics")
            marker = workspace / "probe-called"
            # These wrappers must remain unreachable.  They would leave a marker
            # if a regression moved probing before the history preflight.
            for name in ("run_clan_roster_probe.ps1", "run_clan_current_war_probe.ps1", "run_clan_war_log_probe.ps1"):
                target = isolated_repo / "scripts" / "api" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("New-Item -ItemType File -Path '" + str(marker).replace("'", "''") + "' -Force | Out-Null", encoding="utf-8")

            invalid_cases = {
                "v1": {"schema_version": 1, "wars": []},
                "corrupt": "{",
                "invalid-v2": {"schema_version": 2, "wars": [], "diagnostics": "bad"},
                "future": {"schema_version": 3, "wars": [], "diagnostics": []},
            }
            for name, payload in invalid_cases.items():
                history = workspace / "data" / "war_history" / "history.json"
                history.parent.mkdir(parents=True, exist_ok=True)
                history.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
                result = run(
                    "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-File", str(isolated_repo / "scripts" / "update" / updater.name),
                    "-WorkspaceRoot", str(workspace), "-PreviewOnly",
                )
                combined = result.stdout + result.stderr
                self.assertNotEqual(result.returncode, 0, f"{name}: {combined}")
                self.assertRegex(combined, r"History val\s*idation preflight failed before network")
                self.assertNotIn("Local updater config is missing", combined)
                self.assertFalse(marker.exists(), name)
                self.assertFalse((workspace / "runs").exists(), name)


class GitPreflightCommandTests(unittest.TestCase):
    def make_repo(self, root: Path) -> Path:
        remote, repo = root / "remote.git", root / "repo"
        self.assertEqual(run("git", "init", "--bare", str(remote)).returncode, 0)
        self.assertEqual(run("git", "init", "-b", "main", str(repo)).returncode, 0)
        run("git", "config", "user.email", "fixture@example.invalid", cwd=repo)
        run("git", "config", "user.name", "Fixture", cwd=repo)
        (repo / "data.json").write_text("{}", encoding="utf-8")
        run("git", "add", "data.json", cwd=repo); run("git", "commit", "-m", "base", cwd=repo)
        run("git", "remote", "add", "origin", str(remote), cwd=repo); run("git", "push", "-u", "origin", "main", cwd=repo)
        return repo

    def test_clean_and_all_local_ahead_or_dirty_states_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self.make_repo(root)
            self.assertEqual(run(sys.executable, str(GIT_CHECK), "--repo", str(repo)).returncode, 0)
            for path in ("data.json", "code.py", "README.md"):
                (repo / path).write_text(path, encoding="utf-8")
                run("git", "add", path, cwd=repo); run("git", "commit", "-m", f"ahead {path}", cwd=repo)
            result = run(sys.executable, str(GIT_CHECK), "--repo", str(repo))
            self.assertEqual(result.returncode, 2)
            self.assertIn("will not push", result.stderr)
            self.assertIn("code.py", result.stderr)

    def test_dirty_and_staged_are_rejected_before_any_network_action(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = self.make_repo(Path(temporary))
            (repo / "dirty.txt").write_text("dirty", encoding="utf-8")
            self.assertNotEqual(run(sys.executable, str(GIT_CHECK), "--repo", str(repo)).returncode, 0)
            run("git", "add", "dirty.txt", cwd=repo)
            self.assertNotEqual(run(sys.executable, str(GIT_CHECK), "--repo", str(repo)).returncode, 0)

    def test_behind_and_diverged_repositories_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self.make_repo(root)
            peer = root / "peer"
            remote = root / "remote.git"
            self.assertEqual(run("git", "clone", "--branch", "main", str(remote), str(peer)).returncode, 0)
            run("git", "config", "user.email", "fixture@example.invalid", cwd=peer)
            run("git", "config", "user.name", "Fixture", cwd=peer)
            (peer / "remote.txt").write_text("remote", encoding="utf-8")
            run("git", "add", "remote.txt", cwd=peer); run("git", "commit", "-m", "remote", cwd=peer); run("git", "push", cwd=peer)
            run("git", "fetch", "origin", cwd=repo)
            behind = run(sys.executable, str(GIT_CHECK), "--repo", str(repo))
            self.assertEqual(behind.returncode, 2)
            self.assertIn("behind", behind.stderr)
            (repo / "local.txt").write_text("local", encoding="utf-8")
            run("git", "add", "local.txt", cwd=repo); run("git", "commit", "-m", "local", cwd=repo)
            diverged = run(sys.executable, str(GIT_CHECK), "--repo", str(repo))
            self.assertEqual(diverged.returncode, 2)
            self.assertIn("behind", diverged.stderr)
