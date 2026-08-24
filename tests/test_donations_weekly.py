from __future__ import annotations

import inspect
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import clan_analytics.donations_weekly as module
from clan_analytics.donations_weekly import (
    DEFAULT_GAP_THRESHOLD,
    DonationObservation,
    DuplicateObservationTimeError,
    InvalidMembershipSequenceError,
    InvalidObservationError,
    classify_counter_transition,
    derive_weekly_donations,
    week_window,
)


def utc(day: int, hour: int = 0, minute: int = 0, *, month: int = 8, year: int = 2026) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def observation(
    player: str,
    at: datetime,
    donations: int | None,
    received: int | None,
    segment: str = "SEGMENT_1",
) -> DonationObservation:
    return DonationObservation(player, at, donations, received, segment)


def derive(items, *, as_of=utc(23, 12), coverage_start=None):
    return derive_weekly_donations(
        items,
        as_of_utc=as_of,
        coverage_start_utc=coverage_start,
    )


class WeekContractTests(unittest.TestCase):
    def test_moscow_monday_boundary_uses_timezone_database(self) -> None:
        before = week_window(utc(23, 20, 59), as_of_utc=utc(24, 1))
        after = week_window(utc(23, 21), as_of_utc=utc(24, 1))
        self.assertEqual("Europe/Moscow", after.week_start_local.tzinfo.key)
        self.assertEqual("2026-W34", before.week_id)
        self.assertEqual("2026-W35", after.week_id)
        self.assertEqual(0, after.week_start_local.hour)
        self.assertEqual(21, after.week_start_utc.hour)

    def test_same_week_transition_is_attributed(self) -> None:
        result = derive([
            observation("PLAYER_A", utc(17, 6), 10, 20),
            observation("PLAYER_A", utc(17, 7), 13, 25),
        ])
        player = next(item for item in result.player_weeks if item.week_id == "2026-W34")
        self.assertEqual((3, 5), (player.donations_confirmed, player.donations_received_confirmed))

    def test_exact_boundary_interval_is_ambiguous(self) -> None:
        result = derive([
            observation("PLAYER_A", utc(23, 20), 10, 10),
            observation("PLAYER_A", utc(23, 21), 15, 14),
        ], as_of=utc(24, 1))
        transition = result.transitions[0]
        self.assertEqual("excluded_boundary_ambiguous", transition.attribution_status)
        self.assertEqual(("2026-W34", "2026-W35"), transition.affected_week_ids)
        self.assertEqual(0, sum(item.donations_confirmed for item in result.player_weeks))

    def test_monday_boundary_to_monday_one_hour_is_attributed(self) -> None:
        result = derive([
            observation("PLAYER_A", utc(23, 21), 10, 10),
            observation("PLAYER_A", utc(23, 22), 12, 11),
        ], as_of=utc(24, 1))
        self.assertEqual("attributed", result.transitions[0].attribution_status)
        self.assertEqual(2, result.weeks[-1].donations_confirmed)

    def test_iso_year_boundary_uses_week_start_iso_calendar(self) -> None:
        window = week_window(
            datetime(2027, 1, 1, 12, tzinfo=timezone.utc),
            as_of_utc=datetime(2027, 1, 2, tzinfo=timezone.utc),
        )
        self.assertEqual("2026-W53", window.week_id)
        self.assertEqual(datetime(2026, 12, 28).date(), window.week_start_local.date())

    def test_timezone_aware_non_utc_input_is_normalized(self) -> None:
        plus_three = timezone(timedelta(hours=3))
        result = derive([
            observation("PLAYER_A", datetime(2026, 8, 17, 9, tzinfo=plus_three), 1, 1),
            observation("PLAYER_A", datetime(2026, 8, 17, 10, tzinfo=plus_three), 2, 2),
        ])
        self.assertEqual(utc(17, 6), result.transitions[0].previous_observed_at_utc)

    def test_naive_datetime_is_rejected_without_identity_in_message(self) -> None:
        with self.assertRaises(InvalidObservationError) as caught:
            derive([observation("PLAYER_A", datetime(2026, 8, 17, 9), 1, 1)])
        self.assertNotIn("PLAYER_A", str(caught.exception))


class CounterSemanticsTests(unittest.TestCase):
    def test_counter_classifications(self) -> None:
        self.assertEqual("increase", classify_counter_transition(1, 2))
        self.assertEqual("unchanged", classify_counter_transition(2, 2))
        self.assertEqual("reset_or_unknown", classify_counter_transition(2, 1))
        self.assertEqual("unavailable", classify_counter_transition(None, 1))

    def test_positive_donations_and_received_are_independent(self) -> None:
        result = derive([
            observation("PLAYER_A", utc(17, 6), 10, 30),
            observation("PLAYER_A", utc(17, 7), 14, 32),
        ])
        player = result.player_weeks[0]
        self.assertEqual(4, player.donations_confirmed)
        self.assertEqual(2, player.donations_received_confirmed)
        self.assertEqual(1, player.positive_donation_transition_count)
        self.assertEqual(1, player.positive_received_transition_count)

    def test_unchanged_contributes_zero(self) -> None:
        result = derive([
            observation("PLAYER_A", utc(17, 6), 10, 20),
            observation("PLAYER_A", utc(17, 7), 10, 20),
        ])
        self.assertEqual((0, 0), (result.weeks[0].donations_confirmed, result.weeks[0].donations_received_confirmed))
        self.assertEqual("unchanged", result.transitions[0].donations_classification)

    def test_reset_contributes_zero_then_later_increase_uses_new_baseline(self) -> None:
        result = derive([
            observation("PLAYER_A", utc(17, 6), 100, 70),
            observation("PLAYER_A", utc(17, 7), 5, 4),
            observation("PLAYER_A", utc(17, 8), 8, 9),
        ])
        player = result.player_weeks[0]
        self.assertEqual((3, 5), (player.donations_confirmed, player.donations_received_confirmed))
        self.assertEqual(1, player.reset_count)
        self.assertTrue(player.reset_affected)

    def test_missing_does_not_bridge_to_next_usable_value(self) -> None:
        result = derive([
            observation("PLAYER_A", utc(17, 6), 10, 10),
            observation("PLAYER_A", utc(17, 7), None, None),
            observation("PLAYER_A", utc(17, 8), 15, 16),
            observation("PLAYER_A", utc(17, 9), 18, 20),
        ])
        player = result.player_weeks[0]
        self.assertEqual((3, 4), (player.donations_confirmed, player.donations_received_confirmed))
        self.assertEqual(2, player.unavailable_count)

    def test_multiple_positive_transitions_accumulate(self) -> None:
        result = derive([
            observation("PLAYER_A", utc(17, 6), 1, 2),
            observation("PLAYER_A", utc(17, 7), 4, 5),
            observation("PLAYER_A", utc(17, 8), 9, 11),
        ])
        self.assertEqual((8, 9), (result.weeks[0].donations_confirmed, result.weeks[0].donations_received_confirmed))

    def test_invalid_counter_is_rejected(self) -> None:
        for value in (-1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(InvalidObservationError):
                derive([observation("PLAYER_A", utc(17, 6), value, 1)])


class MembershipSemanticsTests(unittest.TestCase):
    def test_first_observation_is_baseline_only(self) -> None:
        result = derive([observation("PLAYER_A", utc(17, 6), 50, 40)])
        self.assertEqual(0, result.weeks[0].donations_confirmed)
        self.assertEqual(0, len(result.transitions))

    def test_rejoin_starts_new_segment_and_does_not_compare_across_absence(self) -> None:
        result = derive([
            observation("PLAYER_A", utc(17, 6), 50, 40, "SEGMENT_1"),
            observation("PLAYER_A", utc(17, 7), 55, 42, "SEGMENT_1"),
            observation("PLAYER_A", utc(17, 9), 3, 2, "SEGMENT_2"),
            observation("PLAYER_A", utc(17, 10), 7, 5, "SEGMENT_2"),
        ])
        self.assertEqual(2, len(result.transitions))
        self.assertEqual((9, 5), (result.weeks[0].donations_confirmed, result.weeks[0].donations_received_confirmed))
        self.assertEqual(0, result.weeks[0].reset_affected_player_count)

    def test_leave_without_later_segment_has_no_transition(self) -> None:
        result = derive([
            observation("PLAYER_A", utc(17, 6), 10, 10),
            observation("PLAYER_A", utc(17, 7), 12, 11),
            observation("PLAYER_B", utc(17, 8), 1, 1),
        ])
        self.assertEqual(1, len([item for item in result.transitions if item.player_id_internal == "PLAYER_A"]))

    def test_rename_is_not_part_of_identity_model(self) -> None:
        fields = DonationObservation.__dataclass_fields__
        self.assertNotIn("display_name", fields)
        self.assertIn("player_id_internal", fields)

    def test_reused_membership_segment_fails_closed(self) -> None:
        with self.assertRaises(InvalidMembershipSequenceError):
            derive([
                observation("PLAYER_A", utc(17, 6), 1, 1, "SEGMENT_1"),
                observation("PLAYER_A", utc(17, 7), 1, 1, "SEGMENT_2"),
                observation("PLAYER_A", utc(17, 8), 1, 1, "SEGMENT_1"),
            ])


class GapAndBoundaryTests(unittest.TestCase):
    def test_exactly_two_hours_is_not_a_gap(self) -> None:
        result = derive([
            observation("PLAYER_A", utc(17, 6), 1, 1),
            observation("PLAYER_A", utc(17, 8), 2, 2),
        ])
        self.assertFalse(result.transitions[0].gap_affected)
        self.assertEqual(DEFAULT_GAP_THRESHOLD, timedelta(hours=2))

    def test_positive_delta_through_same_week_gap_is_counted_and_flagged(self) -> None:
        result = derive([
            observation("PLAYER_A", utc(17, 6), 10, 10),
            observation("PLAYER_A", utc(17, 9), 14, 15),
        ])
        self.assertEqual((4, 5), (result.weeks[0].donations_confirmed, result.weeks[0].donations_received_confirmed))
        self.assertTrue(result.weeks[0].gap_affected)

    def test_long_cross_week_gap_is_excluded_and_marks_each_week(self) -> None:
        result = derive([
            observation("PLAYER_A", utc(16, 20), 10, 10),
            observation("PLAYER_A", utc(31, 0), 30, 35),
        ], as_of=utc(31, 1))
        transition = result.transitions[0]
        self.assertGreaterEqual(len(transition.affected_week_ids), 3)
        self.assertEqual(0, sum(item.donations_confirmed for item in result.weeks))
        affected = [item for item in result.weeks if item.week_id in transition.affected_week_ids]
        self.assertTrue(all(item.boundary_ambiguous and item.gap_affected for item in affected))

    def test_cross_week_reset_keeps_classification_without_negative_contribution(self) -> None:
        result = derive([
            observation("PLAYER_A", utc(23, 20), 100, 80),
            observation("PLAYER_A", utc(23, 21), 5, 3),
        ], as_of=utc(24, 1))
        self.assertEqual("reset_or_unknown", result.transitions[0].donations_classification)
        self.assertEqual(0, sum(item.donations_confirmed for item in result.weeks))
        self.assertTrue(all(item.reset_affected for item in result.weeks))

    def test_cross_week_unchanged_is_ambiguity_evidence(self) -> None:
        result = derive([
            observation("PLAYER_A", utc(23, 20), 10, 10),
            observation("PLAYER_A", utc(23, 21), 10, 10),
        ], as_of=utc(24, 1))
        self.assertEqual("unchanged", result.transitions[0].donations_classification)
        self.assertTrue(all(item.boundary_ambiguous for item in result.weeks))


class AggregateAndDeterminismTests(unittest.TestCase):
    def test_two_player_aggregate_is_sum_of_player_results(self) -> None:
        result = derive([
            observation("PLAYER_B", utc(17, 6), 5, 8),
            observation("PLAYER_A", utc(17, 6), 10, 20),
            observation("PLAYER_B", utc(17, 7), 9, 9),
            observation("PLAYER_A", utc(17, 7), 12, 25),
        ])
        week = result.weeks[0]
        players = [item for item in result.player_weeks if item.week_id == week.week_id]
        self.assertEqual(sum(item.donations_confirmed for item in players), week.donations_confirmed)
        self.assertEqual(sum(item.donations_received_confirmed for item in players), week.donations_received_confirmed)
        self.assertEqual((6, 6), (week.donations_confirmed, week.donations_received_confirmed))
        self.assertEqual(2, week.participant_count)

    def test_input_order_does_not_change_output(self) -> None:
        items = [
            observation("PLAYER_B", utc(17, 7), 9, 9),
            observation("PLAYER_A", utc(17, 6), 10, 20),
            observation("PLAYER_B", utc(17, 6), 5, 8),
            observation("PLAYER_A", utc(17, 7), 12, 25),
        ]
        self.assertEqual(derive(items), derive(reversed(items)))
        self.assertEqual(["PLAYER_A", "PLAYER_B"], [item.player_id_internal for item in derive(items).player_weeks])

    def test_duplicate_player_timestamp_fails_closed(self) -> None:
        with self.assertRaises(DuplicateObservationTimeError) as caught:
            derive([
                observation("PLAYER_A", utc(17, 6), 1, 1),
                observation("PLAYER_A", utc(17, 6), 2, 2),
            ])
        self.assertNotIn("PLAYER_A", str(caught.exception))

    def test_current_week_is_partial(self) -> None:
        result = derive([
            observation("PLAYER_A", utc(17, 6), 1, 1),
            observation("PLAYER_A", utc(17, 7), 2, 2),
        ], as_of=utc(17, 8))
        self.assertTrue(result.weeks[0].is_current)
        self.assertEqual("partial", result.weeks[0].status)

    def test_empty_intermediate_week_is_insufficient(self) -> None:
        result = derive(
            [observation("PLAYER_A", utc(3, 6), 1, 1)],
            as_of=utc(17, 6),
            coverage_start=utc(3, 6),
        )
        empty = next(item for item in result.weeks if item.week_id == "2026-W33")
        self.assertEqual("insufficient_data", empty.status)
        self.assertEqual(0, empty.participant_count)

    def test_empty_input_has_no_implicit_week(self) -> None:
        result = derive([], as_of=utc(17, 6))
        self.assertEqual((), result.weeks)

    def test_explicit_current_week_without_observations_is_partial(self) -> None:
        result = derive([], as_of=utc(17, 6), coverage_start=utc(17, 6))
        self.assertEqual(1, len(result.weeks))
        self.assertTrue(result.weeks[0].is_current)
        self.assertEqual("partial", result.weeks[0].status)

    def test_ambiguity_never_claims_complete(self) -> None:
        result = derive([
            observation("PLAYER_A", utc(16, 20), 1, 1),
            observation("PLAYER_A", utc(17, 6), 2, 2),
        ], as_of=utc(23, 20))
        self.assertTrue(any(item.boundary_ambiguous for item in result.weeks))
        self.assertNotIn("complete", [item.status for item in result.weeks])

    def test_all_confirmed_values_are_non_negative(self) -> None:
        result = derive([
            observation("PLAYER_A", utc(17, 6), 10, 10),
            observation("PLAYER_A", utc(17, 7), 1, 2),
        ])
        for item in (*result.player_weeks, *result.weeks):
            self.assertGreaterEqual(item.donations_confirmed, 0)
            self.assertGreaterEqual(item.donations_received_confirmed, 0)

    def test_phase_one_has_no_sqlite_or_public_projection_helper(self) -> None:
        source = inspect.getsource(module)
        self.assertNotIn("import sqlite3", source)
        self.assertFalse(any(name.startswith("build_public") for name in dir(module)))
        self.assertNotIn("display_name", DonationObservation.__dataclass_fields__)

    def test_fictional_partial_reset_gap_shape(self) -> None:
        result = derive([
            observation("PLAYER_A", utc(17, 3), 40, 20),
            observation("PLAYER_A", utc(17, 6), 44, 25),
            observation("PLAYER_A", utc(17, 7), 2, 1),
            observation("PLAYER_B", utc(17, 6), 10, 10),
            observation("PLAYER_B", utc(17, 7), 12, 13),
        ], as_of=utc(17, 8))
        week = result.weeks[0]
        self.assertEqual("partial", week.status)
        self.assertTrue(week.gap_affected)
        self.assertTrue(week.reset_affected)
        self.assertEqual((6, 8), (week.donations_confirmed, week.donations_received_confirmed))


if __name__ == "__main__":
    unittest.main()
