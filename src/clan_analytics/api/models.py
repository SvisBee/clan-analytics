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
    attacks: tuple[WarAttackSnapshot, ...]


@dataclass(frozen=True)
class WarSnapshot:
    """A normalized current or historical detailed war snapshot."""

    state: str
    end_time: str | None
    attacks_per_member: int | None
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
