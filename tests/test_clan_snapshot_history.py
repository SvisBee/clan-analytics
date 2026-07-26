from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clan_analytics.api.models import ClanMemberSnapshot, ClanSnapshot, SourceMetadata
from clan_analytics.clan_snapshot_history import (
    BackupValidationError, ObservationConflictError, OutOfOrderObservationError,
    SnapshotValidationError, UnsupportedSchemaVersionError, build_canonical_snapshot,
    classify_donation_delta, create_validated_backup, derive_membership_events,
    initialize_snapshot_store, list_observations, record_confirmed_observation,
    validate_snapshot_store,
)


def clan(*members: tuple[str, str, str | None, int | None], level: int | None = 10) -> ClanSnapshot:
    source = SourceMetadata(None, "2026-07-27T00:00:00Z", "fictional")
    return ClanSnapshot("#TEST", "Fictional", level, tuple(
        ClanMemberSnapshot(tag, name, role, town_hall, 1, 1, 1, 10, 5, 100, 50, source)
        for tag, name, role, town_hall in members
    ), source)


class SnapshotStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(); self.path = Path(self.temp.name) / "history.sqlite3"
        initialize_snapshot_store(self.path)
        self.first = clan(("#AAA", "Alpha", "member", 10), ("#BBB", "Beta", "admin", 11))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def record(self, snapshot, at="2026-07-27T00:00:00Z", run="run-1"):
        return record_confirmed_observation(self.path, snapshot, at, run, "tests-v1")

    def test_initialize_is_idempotent_and_validates(self) -> None:
        initialize_snapshot_store(self.path); validate_snapshot_store(self.path)

    def test_unknown_schema_fails_closed(self) -> None:
        db = sqlite3.connect(self.path)
        try:
            db.execute("UPDATE schema_metadata SET schema_version = 2"); db.commit()
        finally:
            db.close()
        with self.assertRaises(UnsupportedSchemaVersionError): validate_snapshot_store(self.path)

    def test_canonicalization_is_order_independent_and_unicode_safe(self) -> None:
        reverse = clan(("#BBB", "Бета", "admin", 11), ("#AAA", "Alpha", "member", 10))
        one = build_canonical_snapshot(reverse); two = build_canonical_snapshot(clan(("#AAA", "Alpha", "member", 10), ("#BBB", "Бета", "admin", 11)))
        self.assertEqual(one.fingerprint, two.fingerprint); self.assertIn("Бета".encode(), one.serialized)

    def test_meaningful_change_changes_fingerprint(self) -> None:
        self.assertNotEqual(build_canonical_snapshot(self.first).fingerprint, build_canonical_snapshot(clan(("#AAA", "Alpha", "member", 12), ("#BBB", "Beta", "admin", 11))).fingerprint)

    def test_first_observation_is_baseline(self) -> None:
        self.record(self.first); self.assertEqual([], derive_membership_events(self.path))

    def test_deduplicates_payload_but_keeps_observations(self) -> None:
        first = self.record(self.first); second = self.record(self.first, "2026-07-27T01:00:00Z", "run-2")
        self.assertTrue(first.inserted_payload); self.assertFalse(second.inserted_payload); self.assertEqual(2, len(list_observations(self.path)))

    def test_exact_source_retry_is_idempotent(self) -> None:
        first = self.record(self.first); retry = self.record(self.first)
        self.assertFalse(retry.inserted_observation); self.assertEqual(first.observation_id, retry.observation_id)

    def test_source_conflict_and_time_guard_do_not_change_store(self) -> None:
        self.record(self.first)
        with self.assertRaises(ObservationConflictError): self.record(clan(("#AAA", "Changed", "member", 10)), run="run-1")
        with self.assertRaises(OutOfOrderObservationError): self.record(self.first, "2026-07-26T23:00:00Z", "old")
        with self.assertRaises(OutOfOrderObservationError): self.record(self.first, "2026-07-27T00:00:00Z", "equal")
        self.assertEqual(1, len(list_observations(self.path)))

    def test_naive_timestamp_is_rejected(self) -> None:
        with self.assertRaises(SnapshotValidationError): self.record(self.first, "2026-07-27T00:00:00", "naive")

    def test_offset_timestamp_normalizes_to_utc(self) -> None:
        self.record(self.first, "2026-07-27T03:00:00+03:00"); self.assertEqual("2026-07-27T00:00:00.000000Z", list_observations(self.path)[0]["observed_at_utc"])

    def test_fixed_width_timestamp_ordering_accepts_fractional_second(self) -> None:
        self.record(self.first, "2026-07-27T00:00:00Z", "run-1")
        self.record(self.first, "2026-07-27T00:00:00.500000Z", "run-2")
        self.record(self.first, "2026-07-27T00:00:01Z", "run-3")
        self.assertEqual(["2026-07-27T00:00:00.000000Z", "2026-07-27T00:00:00.500000Z", "2026-07-27T00:00:01.000000Z"], [item["observed_at_utc"] for item in list_observations(self.path)])

    def test_equivalent_offset_retry_is_idempotent(self) -> None:
        first = self.record(self.first, "2026-07-27T03:00:00+03:00")
        retry = self.record(self.first, "2026-07-27T00:00:00.000000Z")
        self.assertEqual(first.observation_id, retry.observation_id)

    def test_schema_and_logical_corruption_fail_safely(self) -> None:
        self.record(self.first)
        db = sqlite3.connect(self.path)
        try:
            db.execute("UPDATE snapshot_payload SET member_count = 99"); db.commit()
        finally:
            db.close()
        with self.assertRaises(SnapshotValidationError) as error: validate_snapshot_store(self.path)
        self.assertNotIn("#AAA", str(error.exception))

    def test_unexpected_application_table_is_rejected(self) -> None:
        db = sqlite3.connect(self.path)
        try:
            db.execute("CREATE TABLE unexpected_table (value TEXT)"); db.commit()
        finally:
            db.close()
        with self.assertRaises(SnapshotValidationError): validate_snapshot_store(self.path)

    def test_backup_is_standalone_without_sidecars(self) -> None:
        self.record(self.first); backup = Path(self.temp.name) / "standalone.sqlite3"; create_validated_backup(self.path, backup)
        self.assertFalse(Path(str(backup) + "-wal").exists()); self.assertFalse(Path(str(backup) + "-shm").exists()); validate_snapshot_store(backup)

    def test_join_left_rejoin_and_field_events(self) -> None:
        self.record(self.first)
        self.record(clan(("#AAA", "Renamed", "leader", 11), ("#CCC", "Gamma", "member", 9)), "2026-07-27T01:00:00Z", "run-2")
        self.record(clan(("#AAA", "Renamed", "leader", 11), ("#BBB", "Beta", "admin", 11)), "2026-07-27T02:00:00Z", "run-3")
        kinds = [event.event_type for event in derive_membership_events(self.path)]
        self.assertEqual(["left", "joined", "name_changed", "role_changed", "town_hall_changed", "left", "rejoined"], kinds)

    def test_trophy_or_donation_only_change_has_no_membership_event(self) -> None:
        self.record(self.first)
        source = self.first.source; changed = ClanSnapshot(self.first.clan_tag, self.first.name, self.first.level, (ClanMemberSnapshot("#AAA", "Alpha", "member", 10, 1, 1, 1, 11, 5, 100, 50, source), ClanMemberSnapshot("#BBB", "Beta", "admin", 11, 1, 1, 1, 10, 5, 100, 50, source)), source)
        self.record(changed, "2026-07-27T01:00:00Z", "run-2"); self.assertEqual([], derive_membership_events(self.path))

    def test_donation_classification(self) -> None:
        self.assertEqual("increase", classify_donation_delta(1, 2)); self.assertEqual("unchanged", classify_donation_delta(2, 2)); self.assertEqual("reset_or_unknown", classify_donation_delta(2, 1)); self.assertEqual("unavailable", classify_donation_delta(None, 1))

    def test_backup_is_valid_and_non_overwriting(self) -> None:
        self.record(self.first); backup = Path(self.temp.name) / "backup.sqlite3"; create_validated_backup(self.path, backup); validate_snapshot_store(backup)
        with self.assertRaises(BackupValidationError): create_validated_backup(self.path, backup)

    def test_public_site_path_is_rejected(self) -> None:
        with self.assertRaises(SnapshotValidationError): initialize_snapshot_store(Path(self.temp.name) / "site" / "unsafe.sqlite3")
