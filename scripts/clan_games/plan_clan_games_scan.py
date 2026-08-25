"""Read local Clan Games authority and emit one safe scheduling decision."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = REPO_ROOT.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from clan_analytics.clan_games_events import (  # noqa: E402
    EventRegistryError,
    load_event_registry,
)
from clan_analytics.clan_games_history import (  # noqa: E402
    ClanGamesStoreError,
    list_scan_summaries,
)
from clan_analytics.clan_games_schedule import (  # noqa: E402
    ClanGamesScheduleError,
    no_event_registry_decision,
    plan_clan_games_scan,
)


REGISTRY_RELATIVE_PATH = Path("data/clan_games/event_registry.v1.json")
DATABASE_RELATIVE_PATH = Path("data/clan_games/clan_games.v1.sqlite3")


def _emit(payload: dict[str, Any], *, stream: Any = sys.stdout) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=stream)


def _clock_now(clock: Callable[[], datetime] | None) -> datetime:
    value = (clock or (lambda: datetime.now(timezone.utc)))()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ClanGamesScheduleError(
            "schedule_conflict", "schedule clock must be timezone-aware"
        )
    return value


def execute(
    workspace_root: Path,
    *,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Execute read-only planning for production or an injected temp workspace."""

    root = workspace_root.resolve()
    registry_path = root / REGISTRY_RELATIVE_PATH
    database_path = root / DATABASE_RELATIVE_PATH
    if not registry_path.is_file():
        decision = no_event_registry_decision()
    else:
        registry = load_event_registry(registry_path)
        summaries = list_scan_summaries(database_path) if database_path.is_file() else []
        decision = plan_clan_games_scan(
            registry,
            summaries,
            as_of=_clock_now(clock),
        )
    return {"status": "success", **decision.to_dict()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    try:
        if REPO_ROOT != Path("D:/coc/repo") or WORKSPACE_ROOT != Path("D:/coc"):
            raise ClanGamesScheduleError(
                "schedule_conflict", "production workspace layout is invalid"
            )
        payload = execute(WORKSPACE_ROOT)
        _emit(payload)
        return 0
    except EventRegistryError as error:
        _emit(
            {
                "status": "failed",
                "action": "schedule_error",
                "result_code": error.result_code,
                "collector_due": False,
            },
            stream=sys.stderr,
        )
        return 1
    except (ClanGamesScheduleError, ClanGamesStoreError) as error:
        _emit(
            {
                "status": "failed",
                "action": "schedule_error",
                "result_code": error.result_code,
                "collector_due": False,
            },
            stream=sys.stderr,
        )
        return 1
    except Exception:
        _emit(
            {
                "status": "failed",
                "action": "schedule_error",
                "result_code": "schedule_error",
                "collector_due": False,
            },
            stream=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
