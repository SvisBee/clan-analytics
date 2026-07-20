"""CLI for an explicitly approved local history v1-to-v2 migration."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
from clan_analytics.history_migration import MigrationError, execute_migration, preview_migration  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--backup-dir")
    parser.add_argument("--expected-source-sha256")
    parser.add_argument("--confirm-migration", action="store_true")
    args = parser.parse_args()
    try:
        if args.preview:
            result = preview_migration(Path(args.source), Path(args.output) if args.output else None)
        else:
            if not args.backup_dir or not args.expected_source_sha256:
                parser.error("execute requires --backup-dir and --expected-source-sha256")
            result = execute_migration(source_path=Path(args.source), backup_dir=Path(args.backup_dir), expected_source_sha256=args.expected_source_sha256, confirm=args.confirm_migration)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except MigrationError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
