"""CLI for building a proposed site update from completed probe runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from clan_analytics.site_update import SiteUpdateError, build_site_update  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--roster-run", required=True)
    result.add_argument("--current-war-run", required=True)
    result.add_argument("--war-log-run", required=True)
    result.add_argument("--history-path", required=True)
    result.add_argument("--site-data-dir", required=True)
    result.add_argument("--output-dir", required=True)
    result.add_argument(
        "--allow-history-migration",
        action="store_true",
        help="allow deterministic v1-to-v2 migration of the proposed history output",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        summary = build_site_update(
            roster_run=Path(args.roster_run),
            current_war_run=Path(args.current_war_run),
            war_log_run=Path(args.war_log_run),
            existing_history_path=Path(args.history_path),
            existing_site_data_dir=Path(args.site_data_dir),
            output_dir=Path(args.output_dir),
            allow_history_migration=args.allow_history_migration,
        )
        print("Site update build: PASS")
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    except SiteUpdateError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
