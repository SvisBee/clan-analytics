"""Narrow operator CLI for the local Clan Games event registry."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = REPO_ROOT.parent
SRC_ROOT = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from clan_analytics.clan_games_events import (  # noqa: E402
    REGISTRY_LOGICAL_PATH,
    ClanGamesEvent,
    EventRegistryError,
    get_active_event,
    get_event,
    get_upcoming_event,
    initialize_event_registry,
    list_events,
    load_event_registry,
    register_event,
    replace_event,
    validate_event_registry,
)


def _emit(value: dict[str, Any], *, stream: Any = sys.stdout) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True), file=stream)


def _logical_path(test_mode: bool) -> str:
    return "test-registry/event_registry.v1.json" if test_mode else REGISTRY_LOGICAL_PATH


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def resolve_registry_path(test_registry: str | None) -> tuple[Path, bool]:
    """Resolve only the fixed production path or a guarded system-temp test path."""

    expected_repo = WORKSPACE_ROOT / "repo"
    if REPO_ROOT != expected_repo:
        raise EventRegistryError(
            "invalid_registry", "repository layout does not match the workspace contract"
        )
    if test_registry is None:
        target = WORKSPACE_ROOT / REGISTRY_LOGICAL_PATH
        if target != WORKSPACE_ROOT / "data" / "clan_games" / "event_registry.v1.json":
            raise EventRegistryError(
                "invalid_registry", "production registry path guard failed"
            )
        return target, False
    target = Path(test_registry).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if (
        target.name != "event_registry.v1.json"
        or not _is_within(target, temp_root)
        or _is_within(target, WORKSPACE_ROOT)
    ):
        raise EventRegistryError(
            "invalid_registry",
            "test registry must use event_registry.v1.json inside the system temp directory",
        )
    return target, True


def _event_payload(event: ClanGamesEvent, as_of: datetime | str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = event.to_dict()
    if as_of is not None:
        payload["status"] = event.status(as_of)
    return payload


def _now(clock: Callable[[], datetime] | None = None) -> datetime:
    value = (clock or (lambda: datetime.now(timezone.utc)))()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise EventRegistryError("invalid_event", "operator clock must be timezone-aware")
    return value


def _build_event(
    args: argparse.Namespace,
    *,
    confirmed_at: datetime,
) -> ClanGamesEvent:
    return ClanGamesEvent.create(
        event_id=args.event_id,
        start_at=args.start,
        end_at=args.end,
        official_source_url=args.official_source_url,
        confirmed_at=confirmed_at,
    )


def _event_for_register(
    path: Path, args: argparse.Namespace, confirmed_at: datetime
) -> ClanGamesEvent:
    candidate = _build_event(args, confirmed_at=confirmed_at)
    registry = load_event_registry(path)
    existing = get_event(registry, candidate.event_id)
    if existing is not None and (
        existing.start_at_utc == candidate.start_at_utc
        and existing.end_at_utc == candidate.end_at_utc
        and existing.official_source_url == candidate.official_source_url
    ):
        return existing
    return candidate


def _add_event_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--official-source-url", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--test-registry",
        help="tests only: guarded event_registry.v1.json below the system temp directory",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("validate")
    commands.add_parser("list")
    status = commands.add_parser("status")
    status.add_argument("--as-of")
    register = commands.add_parser("register")
    _add_event_arguments(register)
    replace = commands.add_parser("replace")
    _add_event_arguments(replace)
    replace.add_argument("--confirm-replace", action="store_true")
    return parser


def execute(args: argparse.Namespace, *, clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    path, test_mode = resolve_registry_path(args.test_registry)
    logical_path = _logical_path(test_mode)
    if args.command == "init":
        result = initialize_event_registry(path)
        return {**result.to_dict(), "registry": logical_path}
    if args.command == "validate":
        registry = validate_event_registry(path)
        return {
            "status": "success",
            "result_code": "success",
            "registry": logical_path,
            "schema_version": registry.schema_version,
            "event_count": len(registry.events),
        }
    if args.command == "list":
        registry = load_event_registry(path)
        return {
            "status": "success",
            "result_code": "success",
            "registry": logical_path,
            "events": [event.to_dict() for event in list_events(registry)],
        }
    if args.command == "status":
        registry = load_event_registry(path)
        as_of: datetime | str = args.as_of if args.as_of is not None else _now(clock)
        active = get_active_event(registry, as_of)
        upcoming = get_upcoming_event(registry, as_of)
        return {
            "status": "success",
            "result_code": "success",
            "registry": logical_path,
            "active_event": _event_payload(active, as_of) if active else None,
            "upcoming_event": _event_payload(upcoming, as_of) if upcoming else None,
            "ended_event_count": sum(
                event.status(as_of) == "ended" for event in registry.events
            ),
        }
    confirmed_at = _now(clock)
    if args.command == "register":
        event = _event_for_register(path, args, confirmed_at)
        result = register_event(path, event)
    elif args.command == "replace":
        event = _build_event(args, confirmed_at=confirmed_at)
        result = replace_event(
            path,
            event,
            explicit_replace=args.confirm_replace,
            clock=clock,
        )
    else:
        raise EventRegistryError("invalid_event", "unsupported registry command")
    return {
        **result.to_dict(),
        "registry": logical_path,
        "event": event.to_dict(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _emit(execute(args))
        return 0
    except EventRegistryError as error:
        _emit(
            {
                "status": "failed",
                "result_code": error.result_code,
                "safe_message": error.safe_message,
                "registry": _logical_path(args.test_registry is not None),
            },
            stream=sys.stderr,
        )
        return 2
    except Exception:
        _emit(
            {
                "status": "failed",
                "result_code": "write_failure",
                "safe_message": "unexpected registry command failure",
                "registry": _logical_path(args.test_registry is not None),
            },
            stream=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
