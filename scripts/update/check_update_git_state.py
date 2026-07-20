"""Read-only, fail-closed Git preflight for the unattended updater."""
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout.strip()


def check(repo: Path) -> str:
    if git(repo, "branch", "--show-current") != "main":
        raise RuntimeError("updater requires branch main")
    if git(repo, "status", "--porcelain=v1"):
        raise RuntimeError("Git working tree or index is not clean")
    behind_text, ahead_text = git(repo, "rev-list", "--left-right", "--count", "origin/main...HEAD").split()
    behind, ahead = int(behind_text), int(ahead_text)
    if behind:
        raise RuntimeError(f"local main is behind origin/main by {behind} commit(s); update manually")
    if ahead:
        head = git(repo, "rev-parse", "HEAD")
        paths = git(repo, "diff", "--name-only", "origin/main..HEAD").replace("\n", ", ")
        raise RuntimeError(f"local main is ahead by {ahead}; updater will not push {head}. Inspect paths [{paths}], push manually, then retry")
    return "git preflight PASS"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()
    try:
        print(check(Path(args.repo)))
        return 0
    except (RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
