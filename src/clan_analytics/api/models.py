"""Immutable normalized snapshots used by local transformations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceMetadata:
    """Provenance supplied by the caller, never generated implicitly."""

    source_timestamp: str | None
    collected_at: str
    raw_source_reference: str


@dataclass(frozen=True)
class ClanMemberSnapshot:
    """A clan member keyed internally by the stable game tag."""

    player_tag: str
    display_name: str
    clan_role: str | None
    town_hall_level: int | None
    exp_level: int | None
    clan_rank: int | None
    previous_clan_rank: int | None
    donations: int | None
    donations_received: int | None
    trophies: int | None
    builder_base_trophies: int | None
    source: SourceMetadata


@dataclass(frozen=True)
class PlayerProfileSnapshot:
    """The small verified-for-project subset needed by the roster UI."""

    player_tag: str
    display_name: str
    clan_role: str | None
    town_hall_level: int | None
    source: SourceMetadata


@dataclass(frozen=True)
class WarAttackSnapshot:
    """One normalized attack without leadership interpretation."""

    attacker_tag: str
    defender_tag: str | None
    stars: int
    destruction_percentage: int | None
    order: int | None


@dataclass(frozen=True)
class WarMemberSnapshot:
    """A participant and the attacks present in one war snapshot."""

    player_tag: str
    display_name: str
    town_hall_level: int | None
    map_position: int | None
    attacks: tuple[WarAttackSnapshot, ...]


@dataclass(frozen=True)
class WarSnapshot:
    """A normalized current or historical detailed war snapshot."""

    state: str
    preparation_start_time: str | None
    start_time: str | None
    end_time: str | None
    team_size: int | None
    attacks_per_member: int | None
    clan_stars: int | None
    clan_tag: str | None
    opponent_tag: str | None
    members: tuple[WarMemberSnapshot, ...]
    source: SourceMetadata


@dataclass(frozen=True)
class ClanSnapshot:
    """A clan snapshot containing a stable, sorted member collection."""

    clan_tag: str
    name: str
    level: int | None
    members: tuple[ClanMemberSnapshot, ...]
    source: SourceMetadata


@dataclass(frozen=True)
class WarLogSideSnapshot:
    """One clan side in a historical war-log entry."""

    clan_tag: str | None
    name: str | None
    stars: int | None
    destruction_percentage: float | None
    attacks: int | None


@dataclass(frozen=True)
class WarLogEntrySnapshot:
    """One historical war-log entry without a claimed stable natural key."""

    end_time: str | None
    result: str | None
    team_size: int | None
    attacks_per_member: int | None
    battle_modifier: str | None
    clan: WarLogSideSnapshot
    opponent: WarLogSideSnapshot
    source: SourceMetadata


@dataclass(frozen=True)
class WarLogSnapshot:
    """A response-order snapshot of the official clan war log."""

    entries: tuple[WarLogEntrySnapshot, ...]
    source: SourceMetadata
