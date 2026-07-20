"""Read-only full history validation for updater preflight."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
from clan_analytics.history import HistoryError, load_history  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    source = Path(args.source)
    if not source.exists():
        print(json.dumps({"status": "missing_history"}))
        return 0
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise HistoryError("history root must be an object")
        if raw.get("schema_version") == 1:
            raise HistoryError("schema v1 requires separate migrate_war_history_v1_to_v2.py workflow")
        history = load_history(source)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, HistoryError) as error:
        print(f"Error: history preflight failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "valid_v2", "wars": len(history["wars"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
