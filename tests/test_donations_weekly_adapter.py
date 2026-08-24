from __future__ import annotations

import hashlib
import inspect
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import clan_analytics.donations_weekly_adapter as adapter_module
from clan_analytics.api.models import ClanMemberSnapshot, ClanSnapshot, SourceMetadata
from clan_analytics.clan_snapshot_history import (
    initialize_snapshot_store,
    record_confirmed_observation,
)
from clan_analytics.donations_weekly import derive_weekly_donations
from clan_analytics.donations_weekly_adapter import (
    SnapshotDonationValidationError,
    _connect_read_only,
    read_snapshot_donation_observations,
)


SOURCE = SourceMetadata(None, "2026-08-17T00:00:00Z", "fictional")


def member(
    player: str,
    donations: int | None,
    received: int | None,
    *,
    name: str = "Fictional",
    town_hall: int = 10,
) -> ClanMemberSnapshot:
    return ClanMemberSnapshot(
        player,
        name,
        "member",
        town_hall,
        100,
        1,
        1,
        donations,
        received,
        1000,
        1000,
        SOURCE,
    )


def clan(*members: ClanMemberSnapshot) -> ClanSnapshot:
    return ClanSnapshot("FICTIONAL_CLAN", "Fictional", 1, tuple(members), SOURCE)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SnapshotDonationAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "snapshot.sqlite3"
        initialize_snapshot_store(self.path)
        self.run_number = 0

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def record(self, snapshot: ClanSnapshot, at: str) -> None:
        self.run_number += 1
        record_confirmed_observation(
            self.path,
            snapshot,
            at,
            f"fictional-run-{self.run_number}",
            "fictional-v1",
        )

    def derive(self, *, as_of: datetime = datetime(2026, 8, 23, 12, tzinfo=timezone.utc)):
        loaded = read_snapshot_donation_observations(self.path)
        return loaded, derive_weekly_donations(loaded.observations, as_of_utc=as_of)

    def test_connection_is_read_only_and_adapter_does_not_mutate_store(self) -> None:
        self.record(clan(member("PLAYER_A", 1, 2)), "2026-08-17T06:00:00Z")
        before = digest(self.path)
        connection = _connect_read_only(self.path)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("CREATE TABLE forbidden_write (value INTEGER)")
        finally:
            connection.close()
        read_snapshot_donation_observations(self.path)
        self.assertEqual(before, digest(self.path))

    def test_invalid_store_fails_with_safe_typed_error(self) -> None:
        invalid = Path(self.temporary.name) / "invalid.sqlite3"
        invalid.write_text("not a database", encoding="utf-8")
        with self.assertRaises(SnapshotDonationValidationError) as caught:
            read_snapshot_donation_observations(invalid)
        self.assertNotIn(str(invalid), str(caught.exception))

    def test_first_snapshot_is_baseline_for_both_members(self) -> None:
        self.record(
            clan(member("PLAYER_A", 10, 5), member("PLAYER_B", 20, 10)),
            "2026-08-17T06:00:00Z",
        )
        loaded, result = self.derive()
        self.assertEqual(1, loaded.summary.observation_count)
        self.assertEqual(2, loaded.summary.emitted_member_observation_count)
        self.assertEqual(2, loaded.summary.membership_segment_count)
        self.assertEqual(0, len(result.transitions))

    def test_continuous_presence_keeps_segment_and_counts_positive_delta(self) -> None:
        self.record(
            clan(member("PLAYER_A", 10, 5), member("PLAYER_B", 20, 10)),
            "2026-08-17T06:00:00Z",
        )
        self.record(
            clan(member("PLAYER_A", 14, 8), member("PLAYER_B", 22, 11)),
            "2026-08-17T07:00:00Z",
        )
        loaded, result = self.derive()
        self.assertEqual(2, loaded.summary.membership_segment_count)
        self.assertEqual((6, 4), (result.weeks[0].donations_confirmed, result.weeks[0].donations_received_confirmed))

    def test_leave_and_rejoin_create_new_segment_without_cross_absence_delta(self) -> None:
        self.record(
            clan(member("PLAYER_A", 10, 5), member("PLAYER_B", 50, 30)),
            "2026-08-17T06:00:00Z",
        )
        self.record(clan(member("PLAYER_A", 12, 6)), "2026-08-17T07:00:00Z")
        self.record(
            clan(member("PLAYER_A", 14, 7), member("PLAYER_B", 3, 2)),
            "2026-08-17T08:00:00Z",
        )
        loaded, result = self.derive()
        player_b = [item for item in loaded.observations if item.player_id_internal == "PLAYER_B"]
        self.assertEqual(["segment-1", "segment-2"], [item.membership_segment_id for item in player_b])
        self.assertEqual(3, loaded.summary.membership_segment_count)
        self.assertEqual(1, loaded.summary.rejoin_segment_start_count)
        self.assertFalse(any(item.player_id_internal == "PLAYER_B" for item in result.transitions))

    def test_non_donation_payload_change_does_not_split_segment(self) -> None:
        self.record(
            clan(member("PLAYER_A", 10, 5, name="First", town_hall=10)),
            "2026-08-17T06:00:00Z",
        )
        self.record(
            clan(member("PLAYER_A", 10, 5, name="Renamed", town_hall=11)),
            "2026-08-17T07:00:00Z",
        )
        loaded, result = self.derive()
        self.assertEqual(1, loaded.summary.membership_segment_count)
        self.assertEqual("unchanged", result.transitions[0].donations_classification)

    def test_reused_payload_emits_each_confirmed_timestamp(self) -> None:
        snapshot = clan(member("PLAYER_A", 10, 5))
        self.record(snapshot, "2026-08-17T06:00:00Z")
        self.record(snapshot, "2026-08-17T07:00:00Z")
        loaded, result = self.derive()
        self.assertEqual(2, loaded.summary.observation_count)
        self.assertEqual(2, loaded.summary.emitted_member_observation_count)
        self.assertEqual(1, len(result.transitions))
        self.assertEqual("unchanged", result.transitions[0].donations_classification)

    def test_continuous_counter_decrease_reaches_core_as_reset(self) -> None:
        self.record(clan(member("PLAYER_A", 100, 80)), "2026-08-17T06:00:00Z")
        self.record(clan(member("PLAYER_A", 5, 3)), "2026-08-17T07:00:00Z")
        _, result = self.derive()
        self.assertEqual("reset_or_unknown", result.transitions[0].donations_classification)
        self.assertEqual(0, result.weeks[0].donations_confirmed)

    def test_long_gap_keeps_membership_segment_and_core_marks_gap(self) -> None:
        self.record(clan(member("PLAYER_A", 10, 5)), "2026-08-17T06:00:00Z")
        self.record(clan(member("PLAYER_A", 15, 8)), "2026-08-17T09:00:01Z")
        loaded, result = self.derive()
        self.assertEqual(1, loaded.summary.membership_segment_count)
        self.assertTrue(result.transitions[0].gap_affected)
        self.assertEqual(5, result.weeks[0].donations_confirmed)

    def test_week_boundary_attribution_remains_in_pure_core(self) -> None:
        self.record(clan(member("PLAYER_A", 10, 5)), "2026-08-23T20:00:00Z")
        self.record(clan(member("PLAYER_A", 15, 8)), "2026-08-23T21:00:00Z")
        _, result = self.derive(as_of=datetime(2026, 8, 24, 1, tzinfo=timezone.utc))
        self.assertEqual("excluded_boundary_ambiguous", result.transitions[0].attribution_status)
        self.assertEqual(0, sum(item.donations_confirmed for item in result.weeks))

    def test_output_order_and_summary_are_deterministic(self) -> None:
        self.record(
            clan(member("PLAYER_B", 2, 2), member("PLAYER_A", 1, 1)),
            "2026-08-17T06:00:00Z",
        )
        first = read_snapshot_donation_observations(self.path)
        second = read_snapshot_donation_observations(self.path)
        self.assertEqual(first, second)
        self.assertEqual(["PLAYER_A", "PLAYER_B"], [item.player_id_internal for item in first.observations])
        self.assertEqual(2, first.summary.distinct_internal_player_count)
        self.assertEqual(2, first.summary.current_member_count)

    def test_current_identity_set_comes_only_from_latest_confirmed_snapshot(self) -> None:
        self.record(
            clan(member("PLAYER_A", 1, 1), member("PLAYER_B", 1, 1)),
            "2026-08-17T06:00:00Z",
        )
        self.record(clan(member("PLAYER_A", 2, 2)), "2026-08-17T07:00:00Z")
        loaded = read_snapshot_donation_observations(self.path)
        self.assertEqual(frozenset({"PLAYER_A"}), loaded.current_player_ids_internal)

    def test_invalid_counter_error_does_not_include_identity(self) -> None:
        self.record(clan(member("PLAYER_A", -1, 1)), "2026-08-17T06:00:00Z")
        with self.assertRaises(SnapshotDonationValidationError) as caught:
            read_snapshot_donation_observations(self.path)
        self.assertNotIn("PLAYER_A", str(caught.exception))

    def test_adapter_contains_no_weekly_business_logic_or_public_helper(self) -> None:
        source = inspect.getsource(adapter_module)
        for forbidden in (
            "derive_weekly_donations",
            "classify_counter_transition",
            "week_window(",
            "DEFAULT_GAP_THRESHOLD",
        ):
            self.assertNotIn(forbidden, source)
        self.assertFalse(any(name.startswith("build_public") for name in dir(adapter_module)))
        self.assertIn("mode=ro", source)
        self.assertNotIn("journal_mode", source)


if __name__ == "__main__":
    unittest.main()
