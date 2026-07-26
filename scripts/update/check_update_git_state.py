"""Read-only, fail-closed Git preflight for the unattended updater."""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout.strip()


class GitPreflightError(RuntimeError):
    def __init__(self, result_code: str, message: str, details: dict[str, object]) -> None:
        super().__init__(message)
        self.result_code = result_code
        self.details = details


def _status_summary(repo: Path) -> dict[str, object]:
    lines = [line for line in git(repo, "status", "--porcelain=v1").splitlines() if line]
    paths = [line[3:].replace("\\", "/") for line in lines if len(line) >= 4]
    staged = sum(1 for line in lines if line[:1] not in {" ", "?"})
    untracked = sum(1 for line in lines if line.startswith("??"))
    tracked = sum(1 for line in lines if not line.startswith("??"))
    return {
        "tracked_modified_count": tracked,
        "staged_count": staged,
        "untracked_count": untracked,
        "total_blocking_paths": len(paths),
        "paths": paths[:20],
        "truncated": len(paths) > 20,
    }


def inspect(repo: Path) -> dict[str, object]:
    details = _status_summary(repo)
    if git(repo, "branch", "--show-current") != "main":
        raise GitPreflightError("git_dirty", "updater requires branch main", details)
    if int(details["total_blocking_paths"]):
        raise GitPreflightError("git_dirty", "Git working tree or index is not clean", details)
    behind_text, ahead_text = git(repo, "rev-list", "--left-right", "--count", "origin/main...HEAD").split()
    behind, ahead = int(behind_text), int(ahead_text)
    details.update({"ahead": ahead, "behind": behind})
    if behind and ahead:
        raise GitPreflightError("git_branch_diverged", "local main is behind and ahead of origin/main; update manually", details)
    if behind:
        raise GitPreflightError("git_branch_behind", "local main is behind origin/main; update manually", details)
    if ahead:
        head = git(repo, "rev-parse", "HEAD")
        paths = git(repo, "diff", "--name-only", "origin/main..HEAD").splitlines()
        details.update({"paths": paths[:20], "total_blocking_paths": len(paths), "truncated": len(paths) > 20})
        raise GitPreflightError("git_branch_ahead", f"local main is ahead by {ahead}; updater will not push {head}. Inspect paths [{', '.join(paths)}], push manually, then retry", details)
    return details


def check(repo: Path) -> str:
    inspect(repo)
    return "git preflight PASS"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        details = inspect(Path(args.repo))
        if args.json:
            print(json.dumps({"ok": True, "result_code": "success", "details": details}, sort_keys=True))
        else:
            print("git preflight PASS")
        return 0
    except (GitPreflightError, RuntimeError, ValueError) as error:
        if args.json:
            payload = {
                "ok": False,
                "result_code": getattr(error, "result_code", "git_dirty"),
                "safe_message": str(error),
                "details": getattr(error, "details", {}),
            }
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
