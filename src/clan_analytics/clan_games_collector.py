"""Bounded orchestration for one explicit Clan Games player scan.

The collector owns neither event selection nor scheduling. It reads the latest
confirmed roster identity set, performs one request per current member with a
bounded incremental queue, and submits exactly one complete scan candidate to
the local Clan Games store.
"""

from __future__ import annotations

import re
import sqlite3
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from .api.clan_games import (
    DEFAULT_TIMEOUT_SECONDS,
    GamesChampionSnapshot,
    GamesChampionSourceError,
    fetch_games_champion,
)
from .clan_games_events import ClanGamesEvent
from .clan_games_history import (
    LOGICAL_DATABASE_PATH,
    SCAN_KINDS,
    ClanGamesScan,
    ClanGamesStoreError,
    PlayerScanResult,
    get_scan_by_id,
    initialize_clan_games_store,
    record_clan_games_scan,
    validate_clan_games_store,
)
from .clan_snapshot_history import SnapshotStoreError, validate_snapshot_store


DEFAULT_MAX_WORKERS = 4
MAX_WORKERS = 8
MAX_ROSTER_SIZE = 50
SYSTEMIC_RESULT_CODE = "api_http_403"
SYSTEMIC_SKIP_CODE = "skipped_after_systemic_failure"
SYSTEMIC_OPERATOR_HINT = "enable_approved_vpn"
_SCAN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_PLAYER_TAG_PATTERN = re.compile(r"#[A-Z0-9]{3,20}")


@dataclass(frozen=True)
class ClanGamesCollectionResult:
    """Identity-free result safe for stdout and operational health reporting."""

    scan_id: str
    event_id: str
    scan_kind: str
    status: str
    result_code: str
    requested_count: int
    attempted_count: int
    successful_count: int
    failed_count: int
    skipped_count: int
    duration_seconds: float
    observation_recorded: bool
    store_initialized: bool
    operator_hint_code: str | None = None
    logical_database_path: str = LOGICAL_DATABASE_PATH

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_id": self.scan_id,
            "event_id": self.event_id,
            "scan_kind": self.scan_kind,
            "status": self.status,
            "result_code": self.result_code,
            "requested_count": self.requested_count,
            "attempted_count": self.attempted_count,
            "successful_count": self.successful_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "duration_seconds": self.duration_seconds,
            "observation_recorded": self.observation_recorded,
            "store_initialized": self.store_initialized,
            "operator_hint_code": self.operator_hint_code,
            "logical_database_path": self.logical_database_path,
        }


class RosterSourceError(SnapshotStoreError):
    """Bounded current-roster failure without private identity or path details."""

    def __init__(self, result_code: str) -> None:
        self.result_code = result_code
        super().__init__("current roster source is unavailable")


@dataclass(frozen=True)
class _AttemptOutcome:
    result: PlayerScanResult
    systemic_failure: bool = False


def _aware_utc(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("collector clock must be timezone-aware")
    try:
        offset = value.utcoffset()
    except (OverflowError, ValueError):
        offset = None
    if offset is None:
        raise ValueError("collector clock must be timezone-aware")
    return value.astimezone(timezone.utc)


def _canonical(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_canonical(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )


def _failure_result(
    *,
    event: ClanGamesEvent,
    scan_id: str,
    scan_kind: str,
    result_code: str,
    started: float,
    requested_count: int = 0,
    store_initialized: bool = False,
) -> ClanGamesCollectionResult:
    return ClanGamesCollectionResult(
        scan_id=scan_id,
        event_id=event.event_id,
        scan_kind=scan_kind,
        status="failed",
        result_code=result_code,
        requested_count=requested_count,
        attempted_count=0,
        successful_count=0,
        failed_count=0,
        skipped_count=0,
        duration_seconds=max(0.0, perf_counter() - started),
        observation_recorded=False,
        store_initialized=store_initialized,
    )


def read_current_roster_identities(path: str | Path) -> tuple[str, ...]:
    """Read only private tags from the latest confirmed snapshot observation."""

    target = Path(path).resolve()
    try:
        validate_snapshot_store(target)
    except SnapshotStoreError:
        raise RosterSourceError("roster_source_unavailable") from None
    except sqlite3.DatabaseError:
        raise RosterSourceError("roster_source_unavailable") from None
    connection = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA query_only = ON")
        observation = connection.execute(
            "SELECT payload_id FROM snapshot_observation "
            "WHERE status = 'confirmed' ORDER BY observed_at_utc DESC LIMIT 1"
        ).fetchone()
        if observation is None:
            raise RosterSourceError("no_roster_targets")
        rows = connection.execute(
            "SELECT player_tag FROM member_state WHERE payload_id = ? "
            "ORDER BY player_tag",
            (observation["payload_id"],),
        ).fetchall()
        identities = tuple(row["player_tag"] for row in rows)
        if not identities:
            raise RosterSourceError("no_roster_targets")
        if len(identities) > MAX_ROSTER_SIZE:
            raise RosterSourceError("unexpected_roster_size")
        if len(identities) != len(set(identities)) or any(
            not isinstance(tag, str) or not _PLAYER_TAG_PATTERN.fullmatch(tag)
            for tag in identities
        ):
            raise RosterSourceError("roster_source_unavailable")
        return identities
    except sqlite3.DatabaseError:
        raise RosterSourceError("roster_source_unavailable") from None
    finally:
        connection.close()


def _validate_timing(
    event: ClanGamesEvent, scan_kind: str, started_at: datetime
) -> bool:
    start = _parse_canonical(event.start_at_utc)
    end = _parse_canonical(event.end_at_utc)
    if scan_kind == "baseline":
        return started_at <= start
    if scan_kind == "periodic":
        return start <= started_at < end
    if scan_kind == "final":
        return started_at >= end
    return False


def _attempt_player(
    player_tag: str,
    *,
    token: str,
    timeout_seconds: int,
    transport: Any | None,
    clock: Callable[[], datetime],
    fetcher: Callable[..., tuple[GamesChampionSnapshot, Any]],
) -> _AttemptOutcome:
    attempted = _aware_utc(clock)
    try:
        snapshot, _safe_result = fetcher(
            player_tag,
            token=token,
            timeout_seconds=timeout_seconds,
            transport=transport,
            clock=clock,
        )
        if (
            not isinstance(snapshot, GamesChampionSnapshot)
            or snapshot.player_tag_internal != player_tag
        ):
            raise ValueError("source identity mismatch")
        return _AttemptOutcome(
            PlayerScanResult.success(snapshot, attempted_at=attempted)
        )
    except GamesChampionSourceError as error:
        return _AttemptOutcome(
            PlayerScanResult.failed(
                player_tag,
                result_code=error.result_code,
                attempted_at=attempted,
            ),
            systemic_failure=error.result_code == SYSTEMIC_RESULT_CODE,
        )
    except Exception:
        return _AttemptOutcome(
            PlayerScanResult.failed(
                player_tag,
                result_code="unexpected_error",
                attempted_at=attempted,
            )
        )


def _collect_incrementally(
    identities: tuple[str, ...],
    *,
    token: str,
    max_workers: int,
    timeout_seconds: int,
    transport: Any | None,
    clock: Callable[[], datetime],
    fetcher: Callable[..., tuple[GamesChampionSnapshot, Any]],
) -> tuple[tuple[PlayerScanResult, ...], bool]:
    results: list[PlayerScanResult] = []
    next_index = 0
    systemic_stop = False
    with ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix="clan-games-player"
    ) as executor:
        pending: dict[Future[_AttemptOutcome], str] = {}

        def submit_one() -> None:
            nonlocal next_index
            tag = identities[next_index]
            next_index += 1
            future = executor.submit(
                _attempt_player,
                tag,
                token=token,
                timeout_seconds=timeout_seconds,
                transport=transport,
                clock=clock,
                fetcher=fetcher,
            )
            pending[future] = tag

        while next_index < len(identities) and len(pending) < max_workers:
            submit_one()
        while pending:
            completed, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
            for future in completed:
                pending.pop(future)
                outcome = future.result()
                results.append(outcome.result)
                systemic_stop = systemic_stop or outcome.systemic_failure
            if not systemic_stop:
                while next_index < len(identities) and len(pending) < max_workers:
                    submit_one()
        if systemic_stop:
            results.extend(
                PlayerScanResult.skipped(
                    tag, result_code=SYSTEMIC_SKIP_CODE
                )
                for tag in identities[next_index:]
            )
    return tuple(sorted(results, key=lambda item: item.player_tag)), systemic_stop


def collect_clan_games_scan(
    *,
    event: ClanGamesEvent,
    scan_id: str,
    scan_kind: str,
    roster_database_path: str | Path,
    clan_games_database_path: str | Path,
    token: str | None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    transport: Any | None = None,
    clock: Callable[[], datetime] | None = None,
    fetcher: Callable[..., tuple[GamesChampionSnapshot, Any]] = fetch_games_champion,
) -> ClanGamesCollectionResult:
    """Collect and atomically record one explicit scan using injected boundaries."""

    timer_started = perf_counter()
    active_clock = clock or (lambda: datetime.now(timezone.utc))
    if not isinstance(event, ClanGamesEvent):
        raise TypeError("validated ClanGamesEvent is required")
    if not isinstance(scan_id, str) or not _SCAN_ID_PATTERN.fullmatch(scan_id):
        return _failure_result(
            event=event,
            scan_id="invalid",
            scan_kind=scan_kind,
            result_code="invalid_scan",
            started=timer_started,
        )
    if scan_kind not in SCAN_KINDS:
        return _failure_result(
            event=event,
            scan_id=scan_id,
            scan_kind=scan_kind,
            result_code="invalid_scan_kind",
            started=timer_started,
        )
    if (
        isinstance(max_workers, bool)
        or not isinstance(max_workers, int)
        or not 1 <= max_workers <= MAX_WORKERS
        or isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or timeout_seconds != DEFAULT_TIMEOUT_SECONDS
    ):
        return _failure_result(
            event=event,
            scan_id=scan_id,
            scan_kind=scan_kind,
            result_code="invalid_collector_configuration",
            started=timer_started,
        )

    store_path = Path(clan_games_database_path)
    store_initialized = False
    try:
        if store_path.exists():
            validate_clan_games_store(store_path)
            existing = get_scan_by_id(store_path, scan_id)
            if existing is not None:
                if (
                    existing["event_id"] != event.event_id
                    or existing["scan_kind"] != scan_kind
                ):
                    return _failure_result(
                        event=event,
                        scan_id=scan_id,
                        scan_kind=scan_kind,
                        result_code="scan_conflict",
                        started=timer_started,
                    )
                return ClanGamesCollectionResult(
                    scan_id=scan_id,
                    event_id=existing["event_id"],
                    scan_kind=existing["scan_kind"],
                    status="no_change",
                    result_code="already_recorded",
                    requested_count=existing["requested_count"],
                    attempted_count=existing["attempted_count"],
                    successful_count=existing["successful_count"],
                    failed_count=existing["failed_count"],
                    skipped_count=existing["skipped_count"],
                    duration_seconds=max(0.0, perf_counter() - timer_started),
                    observation_recorded=False,
                    store_initialized=False,
                )
        started_at = _aware_utc(active_clock)
        if not _validate_timing(event, scan_kind, started_at):
            return _failure_result(
                event=event,
                scan_id=scan_id,
                scan_kind=scan_kind,
                result_code="invalid_scan_timing",
                started=timer_started,
            )
        identities = read_current_roster_identities(roster_database_path)
        if not isinstance(token, str) or not token:
            return _failure_result(
                event=event,
                scan_id=scan_id,
                scan_kind=scan_kind,
                result_code="credential_unavailable",
                started=timer_started,
                requested_count=len(identities),
            )
        if not store_path.exists():
            initialize_clan_games_store(store_path)
            store_initialized = True
        validate_clan_games_store(store_path)
        player_results, systemic_stop = _collect_incrementally(
            identities,
            token=token,
            max_workers=max_workers,
            timeout_seconds=timeout_seconds,
            transport=transport,
            clock=active_clock,
            fetcher=fetcher,
        )
        timestamp_values = [started_at, _aware_utc(active_clock)]
        for result in player_results:
            for value in (result.attempted_at_utc, result.observed_at_utc):
                if value is not None:
                    timestamp_values.append(_parse_canonical(value))
        finished_at = max(timestamp_values)
        success_count = sum(item.result_status == "success" for item in player_results)
        if systemic_stop:
            result_code = SYSTEMIC_RESULT_CODE
        elif success_count == len(player_results):
            result_code = "success"
        elif success_count:
            result_code = "partial_player_failures"
        else:
            result_code = "all_player_requests_failed"
        scan = ClanGamesScan.create(
            scan_id=scan_id,
            event_id=event.event_id,
            scan_kind=scan_kind,
            started_at=started_at,
            finished_at=finished_at,
            player_results=player_results,
            result_code=result_code,
        )
        record_clan_games_scan(store_path, event, scan)
        attempted_count = sum(item.attempted_at_utc is not None for item in player_results)
        return ClanGamesCollectionResult(
            scan_id=scan_id,
            event_id=event.event_id,
            scan_kind=scan_kind,
            status=scan.status,
            result_code=result_code,
            requested_count=scan.requested_count,
            attempted_count=attempted_count,
            successful_count=scan.successful_count,
            failed_count=scan.failed_count,
            skipped_count=scan.skipped_count,
            duration_seconds=max(0.0, perf_counter() - timer_started),
            observation_recorded=True,
            store_initialized=store_initialized,
            operator_hint_code=(
                SYSTEMIC_OPERATOR_HINT if systemic_stop else None
            ),
        )
    except RosterSourceError as error:
        return _failure_result(
            event=event,
            scan_id=scan_id,
            scan_kind=scan_kind,
            result_code=error.result_code,
            started=timer_started,
            store_initialized=store_initialized,
        )
    except SnapshotStoreError:
        return _failure_result(
            event=event,
            scan_id=scan_id,
            scan_kind=scan_kind,
            result_code="roster_source_unavailable",
            started=timer_started,
            store_initialized=store_initialized,
        )
    except ClanGamesStoreError:
        return _failure_result(
            event=event,
            scan_id=scan_id,
            scan_kind=scan_kind,
            result_code="storage_failure",
            started=timer_started,
            store_initialized=store_initialized,
        )
    except (OSError, sqlite3.Error, ValueError):
        return _failure_result(
            event=event,
            scan_id=scan_id,
            scan_kind=scan_kind,
            result_code="collector_internal_failure",
            started=timer_started,
            store_initialized=store_initialized,
        )
