"""Run one explicit bounded Clan Games player collection scan."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = REPO_ROOT.parent
SRC_ROOT = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from clan_analytics.clan_games_collector import (  # noqa: E402
    DEFAULT_MAX_WORKERS,
    MAX_WORKERS,
    collect_clan_games_scan,
)
from clan_analytics.clan_games_events import (  # noqa: E402
    EventRegistryError,
    get_event,
    load_event_registry,
)
from clan_analytics.clan_games_history import SCAN_KINDS  # noqa: E402


REGISTRY_PATH = WORKSPACE_ROOT / "data" / "clan_games" / "event_registry.v1.json"
ROSTER_DATABASE_PATH = (
    WORKSPACE_ROOT
    / "data"
    / "clan_snapshot_history"
    / "clan_snapshot_history.v1.sqlite3"
)
CLAN_GAMES_DATABASE_PATH = (
    WORKSPACE_ROOT / "data" / "clan_games" / "clan_games.v1.sqlite3"
)
TOKEN_ENVIRONMENT_VARIABLE = "COC_API_TOKEN"


def _emit(payload: dict[str, Any], *, stream: Any = sys.stdout) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=stream)


def _safe_failure(result_code: str, scan_id: str, event_id: str, scan_kind: str):
    return {
        "scan_id": scan_id,
        "event_id": event_id,
        "scan_kind": scan_kind,
        "status": "failed",
        "result_code": result_code,
        "requested_count": 0,
        "attempted_count": 0,
        "successful_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "duration_seconds": 0.0,
        "observation_recorded": False,
        "store_initialized": False,
        "operator_hint_code": None,
        "logical_database_path": "data/clan_games/clan_games.v1.sqlite3",
    }


def _validate_layout() -> None:
    if REPO_ROOT != WORKSPACE_ROOT / "repo" or WORKSPACE_ROOT != Path("D:/coc"):
        raise RuntimeError("production workspace layout is invalid")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--scan-id", required=True)
    parser.add_argument("--scan-kind", required=True, choices=sorted(SCAN_KINDS))
    parser.add_argument(
        "--max-workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        choices=range(1, MAX_WORKERS + 1),
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--local-preflight", action="store_true", help=argparse.SUPPRESS)
    return parser


def execute(arguments: argparse.Namespace) -> dict[str, Any]:
    _validate_layout()
    registry = load_event_registry(REGISTRY_PATH)
    event = get_event(registry, arguments.event_id)
    if event is None:
        return _safe_failure(
            "event_not_found", arguments.scan_id, arguments.event_id, arguments.scan_kind
        )
    token = None if arguments.local_preflight else os.environ.get(
        TOKEN_ENVIRONMENT_VARIABLE
    )
    result = collect_clan_games_scan(
        event=event,
        scan_id=arguments.scan_id,
        scan_kind=arguments.scan_kind,
        roster_database_path=ROSTER_DATABASE_PATH,
        clan_games_database_path=CLAN_GAMES_DATABASE_PATH,
        token=token,
        max_workers=arguments.max_workers,
    )
    payload = result.to_dict()
    if arguments.local_preflight and payload["result_code"] == "credential_unavailable":
        payload["status"] = "ready"
        payload["result_code"] = "local_preflight_ready"
    return payload


def main(argv: list[str] | None = None) -> int:
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    if any(
        value.startswith("--")
        and any(word in value.casefold() for word in ("token", "secret", "credential"))
        for value in raw_arguments
    ):
        _emit(
            _safe_failure("token_argument_rejected", "invalid", "invalid", "invalid"),
            stream=sys.stderr,
        )
        return 1
    parser = build_parser()
    arguments = parser.parse_args(raw_arguments)
    try:
        payload = execute(arguments)
    except EventRegistryError as error:
        payload = _safe_failure(
            error.result_code,
            arguments.scan_id,
            arguments.event_id,
            arguments.scan_kind,
        )
    except Exception:
        payload = _safe_failure(
            "collector_internal_failure",
            arguments.scan_id,
            arguments.event_id,
            arguments.scan_kind,
        )
    stream = sys.stdout if payload["status"] in {
        "success",
        "partial_success",
        "no_change",
        "ready",
    } else sys.stderr
    _emit(payload, stream=stream)
    return 0 if payload["status"] in {
        "success",
        "partial_success",
        "no_change",
        "ready",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
