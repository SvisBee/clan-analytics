"""Read-only snapshot-history adapter for weekly donation derivation.

This module converts confirmed snapshot rows into the pure Phase 1 input
model. It derives membership continuity only; week, delta, reset, gap, and
completeness semantics remain exclusively in ``donations_weekly``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .clan_snapshot_history import (
    CANONICAL_TIMESTAMP_FORMAT,
    SnapshotStoreError,
    validate_snapshot_store,
)
from .donations_weekly import DonationObservation


class SnapshotDonationAdapterError(RuntimeError):
    """Base error for safe read-only snapshot donation loading."""


class SnapshotDonationValidationError(SnapshotDonationAdapterError):
    """The validated store cannot produce safe donation observations."""


@dataclass(frozen=True)
class SnapshotDonationReadSummary:
    """Aggregate-only evidence about one read-only adapter result."""

    observation_count: int
    emitted_member_observation_count: int
    distinct_internal_player_count: int
    membership_segment_count: int
    segment_start_count: int
    rejoin_segment_start_count: int
    current_member_count: int
    earliest_observed_at_utc: datetime | None
    latest_observed_at_utc: datetime | None


@dataclass(frozen=True)
class SnapshotDonationReadResult:
    """Private in-memory observations plus an aggregate-safe summary."""

    observations: tuple[DonationObservation, ...]
    summary: SnapshotDonationReadSummary
    current_player_ids_internal: frozenset[str]


def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _parse_observed_at(value: object) -> datetime:
    if not isinstance(value, str):
        raise SnapshotDonationValidationError("snapshot observation timestamp is invalid")
    try:
        return datetime.strptime(value, CANONICAL_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise SnapshotDonationValidationError(
            "snapshot observation timestamp is invalid"
        ) from error


def _valid_counter(value: object) -> bool:
    return value is None or (
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
    )


def read_snapshot_donation_observations(
    path: str | Path,
) -> SnapshotDonationReadResult:
    """Read confirmed donation observations without modifying snapshot state.

    Membership is continuous only when an identity is present in two adjacent
    confirmed clan observations. A first appearance or reappearance after any
    confirmed absence starts a new deterministic segment.
    """

    target = Path(path)
    try:
        validate_snapshot_store(target)
    except (SnapshotStoreError, sqlite3.DatabaseError, OSError) as error:
        raise SnapshotDonationValidationError(
            "snapshot donation store validation failed"
        ) from error

    connection: sqlite3.Connection | None = None
    try:
        connection = _connect_read_only(target)
        observation_rows = connection.execute(
            "SELECT payload_id, observed_at_utc "
            "FROM snapshot_observation WHERE status = 'confirmed' "
            "ORDER BY observed_at_utc"
        ).fetchall()

        parsed_times = [_parse_observed_at(row["observed_at_utc"]) for row in observation_rows]
        if parsed_times != sorted(parsed_times) or len(parsed_times) != len(set(parsed_times)):
            raise SnapshotDonationValidationError(
                "snapshot observation chronology is invalid"
            )

        output: list[DonationObservation] = []
        previous_present: set[str] = set()
        ever_seen: set[str] = set()
        segment_numbers: dict[str, int] = {}
        segment_starts = 0
        rejoin_starts = 0

        for row, observed_at in zip(observation_rows, parsed_times, strict=True):
            member_rows = connection.execute(
                "SELECT player_tag, donations, donations_received "
                "FROM member_state WHERE payload_id = ? ORDER BY player_tag",
                (row["payload_id"],),
            ).fetchall()
            current_present: set[str] = set()
            for member in member_rows:
                player = member["player_tag"]
                if not isinstance(player, str) or not player or player in current_present:
                    raise SnapshotDonationValidationError(
                        "snapshot member identity is invalid"
                    )
                if not _valid_counter(member["donations"]) or not _valid_counter(
                    member["donations_received"]
                ):
                    raise SnapshotDonationValidationError(
                        "snapshot donation counter is invalid"
                    )
                current_present.add(player)
                if player not in previous_present:
                    if player in ever_seen:
                        rejoin_starts += 1
                    segment_numbers[player] = segment_numbers.get(player, 0) + 1
                    segment_starts += 1
                output.append(
                    DonationObservation(
                        player_id_internal=player,
                        observed_at_utc=observed_at,
                        donations=member["donations"],
                        donations_received=member["donations_received"],
                        membership_segment_id=f"segment-{segment_numbers[player]}",
                    )
                )
            ever_seen.update(current_present)
            previous_present = current_present

        summary = SnapshotDonationReadSummary(
            observation_count=len(observation_rows),
            emitted_member_observation_count=len(output),
            distinct_internal_player_count=len(ever_seen),
            membership_segment_count=segment_starts,
            segment_start_count=segment_starts,
            rejoin_segment_start_count=rejoin_starts,
            current_member_count=len(previous_present),
            earliest_observed_at_utc=parsed_times[0] if parsed_times else None,
            latest_observed_at_utc=parsed_times[-1] if parsed_times else None,
        )
        return SnapshotDonationReadResult(
            observations=tuple(output),
            summary=summary,
            current_player_ids_internal=frozenset(previous_present),
        )
    except SnapshotDonationAdapterError:
        raise
    except sqlite3.DatabaseError as error:
        raise SnapshotDonationValidationError(
            "snapshot donation read failed"
        ) from error
    finally:
        if connection is not None:
            connection.close()
