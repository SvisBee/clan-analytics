from __future__ import annotations

import copy
import inspect
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clan_analytics.donations_weekly import (  # noqa: E402
    AggregateWeeklyDonations,
    DonationsWeeklyResult,
    METRIC_VERSION,
    PlayerWeeklyDonations,
    TIMEZONE_NAME,
    week_window,
)
from clan_analytics.donations_weekly_public import (  # noqa: E402
    CurrentPublicMember,
    DonationsWeeklyPublicError,
    build_public_weekly_donations,
    validate_public_weekly_donations,
)
import clan_analytics.donations_weekly_public as public_module  # noqa: E402


UTC = timezone.utc
AS_OF = datetime(2026, 8, 19, 12, tzinfo=UTC)
GENERATED = datetime(2026, 8, 19, 12, 5, tzinfo=UTC)
LATEST = datetime(2026, 8, 19, 12, tzinfo=UTC)
CURRENT_SAMPLE = datetime(2026, 8, 19, 10, tzinfo=UTC)
PREVIOUS_SAMPLE = datetime(2026, 8, 12, 10, tzinfo=UTC)
OLDER_SAMPLE = datetime(2026, 8, 5, 10, tzinfo=UTC)


def player_week(
    identity: str,
    sample: datetime,
    donations: int = 0,
    received: int = 0,
    *,
    as_of: datetime = AS_OF,
    status: str = "partial",
    observations: int = 1,
    transitions: int = 1,
    reset: bool = False,
    gap: bool = False,
    boundary: bool = False,
) -> PlayerWeeklyDonations:
    window = week_window(sample, as_of_utc=as_of)
    return PlayerWeeklyDonations(
        identity,
        window.week_id,
        window.week_start_local,
        window.week_end_local,
        window.week_start_utc,
        window.week_end_utc,
        donations,
        received,
        int(donations > 0),
        int(received > 0),
        int(reset),
        0,
        int(gap),
        int(boundary),
        observations,
        transitions,
        sample if observations else None,
        sample if observations else None,
        window.is_current,
        "partial" if window.is_current else status,
        gap,
        reset,
        boundary,
    )


def aggregate_week(
    sample: datetime,
    players: tuple[PlayerWeeklyDonations, ...] = (),
    *,
    as_of: datetime = AS_OF,
    status: str = "partial",
) -> AggregateWeeklyDonations:
    window = week_window(sample, as_of_utc=as_of)
    return AggregateWeeklyDonations(
        window.week_id,
        window.week_start_local,
        window.week_end_local,
        window.week_start_utc,
        window.week_end_utc,
        sum(item.donations_confirmed for item in players),
        sum(item.donations_received_confirmed for item in players),
        len(players),
        sum(
            item.donations_confirmed > 0 or item.donations_received_confirmed > 0
            for item in players
        ),
        sum(item.reset_affected for item in players),
        sum(item.gap_affected for item in players),
        sum(item.boundary_ambiguous for item in players),
        sum(item.observations_used for item in players),
        sum(item.transition_count for item in players),
        window.is_current,
        "partial" if window.is_current else status,
        any(item.gap_affected for item in players),
        any(item.reset_affected for item in players),
        any(item.boundary_ambiguous for item in players),
    )


def weekly_result(
    players: tuple[PlayerWeeklyDonations, ...],
    aggregates: tuple[AggregateWeeklyDonations, ...],
) -> DonationsWeeklyResult:
    return DonationsWeeklyResult(METRIC_VERSION, TIMEZONE_NAME, aggregates, players, ())


def roster(*items: tuple[str, str]) -> tuple[CurrentPublicMember, ...]:
    return tuple(CurrentPublicMember(identity, nickname) for identity, nickname in items)


def build(
    result: DonationsWeeklyResult,
    members: tuple[CurrentPublicMember, ...],
    *,
    as_of: datetime = AS_OF,
    generated: datetime = GENERATED,
) -> dict:
    return build_public_weekly_donations(
        result,
        members,
        generated_at_utc=generated,
        as_of_utc=as_of,
        latest_observed_at_utc=LATEST,
    )


class SelectionAndScopeTests(unittest.TestCase):
    def test_empty_result_still_selects_current_partial_week(self) -> None:
        payload = build(weekly_result((), ()), roster(("#PRIVATE_A", "Alpha")))
        self.assertEqual(1, len(payload["weeks"]))
        self.assertEqual(("current", "partial", 0), (
            payload["weeks"][0]["selection"],
            payload["weeks"][0]["status"],
            payload["weeks"][0]["participant_count"],
        ))

    def test_current_and_previous_usable_are_selected(self) -> None:
        current = player_week("#PRIVATE_A", CURRENT_SAMPLE, 4, 2)
        previous = player_week("#PRIVATE_A", PREVIOUS_SAMPLE, 7, 5)
        payload = build(
            weekly_result(
                (current, previous),
                (
                    aggregate_week(PREVIOUS_SAMPLE, (previous,)),
                    aggregate_week(CURRENT_SAMPLE, (current,)),
                ),
            ),
            roster(("#PRIVATE_A", "Alpha")),
        )
        self.assertEqual(["current", "previous_usable"], [w["selection"] for w in payload["weeks"]])

    def test_projection_never_selects_more_than_two_weeks(self) -> None:
        items = tuple(
            player_week("#PRIVATE_A", sample, amount, amount)
            for sample, amount in ((CURRENT_SAMPLE, 1), (PREVIOUS_SAMPLE, 2), (OLDER_SAMPLE, 3))
        )
        payload = build(
            weekly_result(items, tuple(aggregate_week(item.first_observed_at, (item,)) for item in items)),
            roster(("#PRIVATE_A", "Alpha")),
        )
        self.assertEqual(2, len(payload["weeks"]))
        self.assertEqual(items[1].week_id, payload["weeks"][1]["week_id"])

    def test_insufficient_previous_is_skipped_for_earlier_usable_week(self) -> None:
        current = player_week("#PRIVATE_A", CURRENT_SAMPLE, 1, 1)
        insufficient = player_week("#PRIVATE_A", PREVIOUS_SAMPLE, 0, 0, status="insufficient_data", transitions=0)
        older = player_week("#PRIVATE_A", OLDER_SAMPLE, 3, 2)
        payload = build(
            weekly_result(
                (current, insufficient, older),
                (
                    aggregate_week(OLDER_SAMPLE, (older,)),
                    aggregate_week(PREVIOUS_SAMPLE, (insufficient,), status="insufficient_data"),
                    aggregate_week(CURRENT_SAMPLE, (current,)),
                ),
            ),
            roster(("#PRIVATE_A", "Alpha")),
        )
        self.assertEqual(older.week_id, payload["weeks"][1]["week_id"])

    def test_no_usable_completed_week_means_current_only(self) -> None:
        current = player_week("#PRIVATE_A", CURRENT_SAMPLE, 1, 1)
        old = player_week("#PRIVATE_A", PREVIOUS_SAMPLE, 0, 0, status="insufficient_data", transitions=0)
        payload = build(
            weekly_result(
                (current, old),
                (
                    aggregate_week(PREVIOUS_SAMPLE, (old,), status="insufficient_data"),
                    aggregate_week(CURRENT_SAMPLE, (current,)),
                ),
            ),
            roster(("#PRIVATE_A", "Alpha")),
        )
        self.assertEqual(1, len(payload["weeks"]))

    def test_departed_player_and_contribution_are_excluded(self) -> None:
        current = player_week("#PRIVATE_A", CURRENT_SAMPLE, 5, 3)
        departed = player_week("#PRIVATE_DEPARTED", CURRENT_SAMPLE, 100, 90, reset=True)
        payload = build(
            weekly_result((current, departed), (aggregate_week(CURRENT_SAMPLE, (current, departed)),)),
            roster(("#PRIVATE_A", "Alpha")),
        )
        week = payload["weeks"][0]
        self.assertEqual((5, 3, 1), (
            week["donations_confirmed"], week["donations_received_confirmed"], week["participant_count"]
        ))
        self.assertFalse(week["reset_affected"])
        self.assertNotIn("Departed", json.dumps(payload))

    def test_current_member_without_historical_result_is_not_fabricated(self) -> None:
        previous = player_week("#PRIVATE_A", PREVIOUS_SAMPLE, 4, 2)
        payload = build(
            weekly_result((previous,), (aggregate_week(PREVIOUS_SAMPLE, (previous,)),)),
            roster(("#PRIVATE_A", "Alpha"), ("#PRIVATE_B", "Beta")),
        )
        self.assertEqual(["Alpha"], [row["nickname"] for row in payload["weeks"][1]["players"]])

    def test_current_baseline_with_zero_contribution_is_published(self) -> None:
        baseline = player_week("#PRIVATE_A", CURRENT_SAMPLE, observations=1, transitions=0)
        payload = build(
            weekly_result((baseline,), (aggregate_week(CURRENT_SAMPLE, (baseline,)),)),
            roster(("#PRIVATE_A", "Alpha")),
        )
        self.assertEqual(1, payload["weeks"][0]["participant_count"])
        self.assertEqual(0, payload["weeks"][0]["contributing_player_count"])

    def test_departed_only_completed_week_is_not_publicly_usable(self) -> None:
        departed = player_week("#PRIVATE_X", PREVIOUS_SAMPLE, 9, 4)
        payload = build(
            weekly_result((departed,), (aggregate_week(PREVIOUS_SAMPLE, (departed,)),)),
            roster(("#PRIVATE_A", "Alpha")),
        )
        self.assertEqual(1, len(payload["weeks"]))


class IdentityOrderingAndFlagsTests(unittest.TestCase):
    def test_private_identity_markers_are_absent_after_serialization(self) -> None:
        row = player_week("#PRIVATE_A", CURRENT_SAMPLE, 2, 1)
        rendered = json.dumps(build(
            weekly_result((row,), (aggregate_week(CURRENT_SAMPLE, (row,)),)),
            roster(("#PRIVATE_A", "Public Alpha")),
        ))
        for forbidden in ("#PRIVATE_A", "player_id_internal", "membership_segment_id"):
            self.assertNotIn(forbidden, rendered)

    def test_duplicate_public_nicknames_remain_two_rows(self) -> None:
        a = player_week("#PRIVATE_A", CURRENT_SAMPLE, 4, 2)
        b = player_week("#PRIVATE_B", CURRENT_SAMPLE, 3, 1)
        payload = build(
            weekly_result((a, b), (aggregate_week(CURRENT_SAMPLE, (a, b)),)),
            roster(("#PRIVATE_A", "Twin"), ("#PRIVATE_B", "Twin")),
        )
        self.assertEqual(["Twin", "Twin"], [row["nickname"] for row in payload["weeks"][0]["players"]])

    def test_current_display_name_is_used_for_stable_identity(self) -> None:
        historical_result = player_week("#PRIVATE_A", PREVIOUS_SAMPLE, 4, 2)
        payload = build(
            weekly_result((historical_result,), (aggregate_week(PREVIOUS_SAMPLE, (historical_result,)),)),
            roster(("#PRIVATE_A", "New Alpha")),
        )
        self.assertEqual("New Alpha", payload["weeks"][1]["players"][0]["nickname"])

    def test_same_nickname_does_not_join_different_identity(self) -> None:
        historical = player_week("#PRIVATE_OLD", PREVIOUS_SAMPLE, 8, 4)
        payload = build(
            weekly_result((historical,), (aggregate_week(PREVIOUS_SAMPLE, (historical,)),)),
            roster(("#PRIVATE_NEW", "Same Public Name")),
        )
        self.assertEqual(1, len(payload["weeks"]))

    def test_order_prefers_donations_then_received(self) -> None:
        a = player_week("#PRIVATE_A", CURRENT_SAMPLE, 5, 1)
        b = player_week("#PRIVATE_B", CURRENT_SAMPLE, 4, 99)
        c = player_week("#PRIVATE_C", CURRENT_SAMPLE, 5, 2)
        payload = build(
            weekly_result((b, a, c), (aggregate_week(CURRENT_SAMPLE, (a, b, c)),)),
            roster(("#PRIVATE_A", "A"), ("#PRIVATE_B", "B"), ("#PRIVATE_C", "C")),
        )
        self.assertEqual(["C", "A", "B"], [row["nickname"] for row in payload["weeks"][0]["players"]])

    def test_name_order_is_case_insensitive(self) -> None:
        a = player_week("#PRIVATE_A", CURRENT_SAMPLE, 1, 1)
        b = player_week("#PRIVATE_B", CURRENT_SAMPLE, 1, 1)
        payload = build(
            weekly_result((a, b), (aggregate_week(CURRENT_SAMPLE, (a, b)),)),
            roster(("#PRIVATE_A", "zulu"), ("#PRIVATE_B", "Alpha")),
        )
        self.assertEqual(["Alpha", "zulu"], [row["nickname"] for row in payload["weeks"][0]["players"]])

    def test_private_final_tiebreak_is_deterministic_and_not_serialized(self) -> None:
        a = player_week("#PRIVATE_A", CURRENT_SAMPLE, 1, 1, gap=True)
        b = player_week("#PRIVATE_B", CURRENT_SAMPLE, 1, 1, reset=True)
        payload = build(
            weekly_result((b, a), (aggregate_week(CURRENT_SAMPLE, (a, b)),)),
            roster(("#PRIVATE_B", "Twin"), ("#PRIVATE_A", "Twin")),
        )
        rows = payload["weeks"][0]["players"]
        self.assertEqual((True, False), (rows[0]["gap_affected"], rows[0]["reset_affected"]))
        self.assertNotIn("#PRIVATE", json.dumps(payload))

    def test_shuffled_inputs_produce_identical_projection(self) -> None:
        a = player_week("#PRIVATE_A", CURRENT_SAMPLE, 2, 1)
        b = player_week("#PRIVATE_B", CURRENT_SAMPLE, 3, 1)
        aggregate = aggregate_week(CURRENT_SAMPLE, (a, b))
        first = build(weekly_result((a, b), (aggregate,)), roster(("#PRIVATE_A", "A"), ("#PRIVATE_B", "B")))
        second = build(weekly_result((b, a), (aggregate,)), roster(("#PRIVATE_B", "B"), ("#PRIVATE_A", "A")))
        self.assertEqual(first, second)

    def test_flags_are_aggregated_only_from_published_rows(self) -> None:
        current = player_week("#PRIVATE_A", CURRENT_SAMPLE, 1, 1, gap=True, boundary=True)
        departed = player_week("#PRIVATE_X", CURRENT_SAMPLE, 1, 1, reset=True)
        week = build(
            weekly_result((current, departed), (aggregate_week(CURRENT_SAMPLE, (current, departed)),)),
            roster(("#PRIVATE_A", "Alpha")),
        )["weeks"][0]
        self.assertEqual((False, True, True), (
            week["reset_affected"], week["gap_affected"], week["boundary_ambiguous"]
        ))


class StatusTimeAndSchemaTests(unittest.TestCase):
    def simple_payload(self) -> dict:
        row = player_week("#PRIVATE_A", CURRENT_SAMPLE, 2, 1)
        return build(
            weekly_result((row,), (aggregate_week(CURRENT_SAMPLE, (row,)),)),
            roster(("#PRIVATE_A", "Alpha")),
        )

    def test_current_status_is_always_partial(self) -> None:
        row = player_week("#PRIVATE_A", CURRENT_SAMPLE, 2, 1)
        aggregate = aggregate_week(CURRENT_SAMPLE, (row,))
        object.__setattr__(aggregate, "status", "complete")
        self.assertEqual("partial", build(
            weekly_result((row,), (aggregate,)), roster(("#PRIVATE_A", "Alpha"))
        )["weeks"][0]["status"])

    def test_previous_complete_status_is_preserved_without_new_threshold(self) -> None:
        row = player_week("#PRIVATE_A", PREVIOUS_SAMPLE, 2, 1, status="complete")
        payload = build(
            weekly_result((row,), (aggregate_week(PREVIOUS_SAMPLE, (row,), status="complete"),)),
            roster(("#PRIVATE_A", "Alpha")),
        )
        self.assertEqual("complete", payload["weeks"][1]["status"])

    def test_moscow_current_week_is_selected_from_explicit_as_of(self) -> None:
        payload = build(weekly_result((), ()), ())
        expected = week_window(AS_OF, as_of_utc=AS_OF)
        self.assertEqual(expected.week_id, payload["weeks"][0]["week_id"])
        self.assertTrue(payload["weeks"][0]["week_start"].endswith("+03:00"))

    def test_iso_year_boundary_uses_core_week_contract(self) -> None:
        as_of = datetime(2027, 1, 2, 12, tzinfo=UTC)
        payload = build(weekly_result((), ()), (), as_of=as_of, generated=as_of)
        self.assertEqual(week_window(as_of, as_of_utc=as_of).week_id, payload["weeks"][0]["week_id"])

    def test_naive_generated_timestamp_fails_closed(self) -> None:
        with self.assertRaises(DonationsWeeklyPublicError):
            build(weekly_result((), ()), (), generated=datetime(2026, 8, 19, 12))

    def test_naive_as_of_timestamp_fails_closed(self) -> None:
        with self.assertRaises(DonationsWeeklyPublicError):
            build(weekly_result((), ()), (), as_of=datetime(2026, 8, 19, 12))

    def test_unknown_top_level_field_is_rejected(self) -> None:
        payload = self.simple_payload()
        payload["private"] = "forbidden"
        with self.assertRaises(DonationsWeeklyPublicError):
            validate_public_weekly_donations(payload)

    def test_schema_v1_uses_exact_compact_allowlists(self) -> None:
        payload = self.simple_payload()
        self.assertEqual({
            "schema_version", "timezone", "scope", "metric_semantics",
            "generated_at_utc", "latest_observed_at_utc", "weeks",
        }, set(payload))
        self.assertEqual({
            "week_id", "week_start", "week_end", "is_current", "selection",
            "status", "donations_confirmed", "donations_received_confirmed",
            "participant_count", "contributing_player_count", "reset_affected",
            "gap_affected", "boundary_ambiguous", "players",
        }, set(payload["weeks"][0]))
        self.assertEqual({
            "nickname", "donations_confirmed", "donations_received_confirmed",
            "reset_affected", "gap_affected", "boundary_ambiguous",
        }, set(payload["weeks"][0]["players"][0]))

    def test_projection_module_has_no_storage_api_or_output_integration(self) -> None:
        source = inspect.getsource(public_module)
        for forbidden in (
            "sqlite3", "site/data", "donations-weekly.json", ".write_text(",
            ".write_bytes(", "open(", "requests.",
        ):
            self.assertNotIn(forbidden, source)

    def test_unknown_player_fields_are_rejected(self) -> None:
        for field in (
            "tag", "player_tag", "clan_tag", "player_id", "internal_id",
            "player_id_internal", "player_hash", "segment_id", "payload_id",
            "observation_id", "fingerprint", "source_run_id",
        ):
            payload = self.simple_payload()
            payload["weeks"][0]["players"][0][field] = "private"
            with self.subTest(field=field), self.assertRaises(DonationsWeeklyPublicError):
                validate_public_weekly_donations(payload)

    def test_negative_counter_and_mismatched_totals_are_rejected(self) -> None:
        for mutation in ("negative", "mismatch"):
            payload = self.simple_payload()
            if mutation == "negative":
                payload["weeks"][0]["players"][0]["donations_confirmed"] = -1
            else:
                payload["weeks"][0]["donations_confirmed"] += 1
            with self.subTest(mutation=mutation), self.assertRaises(DonationsWeeklyPublicError):
                validate_public_weekly_donations(payload)

    def test_more_than_two_weeks_is_rejected(self) -> None:
        payload = self.simple_payload()
        payload["weeks"].extend(copy.deepcopy(payload["weeks"]) for _ in ())
        payload["weeks"] = payload["weeks"] * 3
        with self.assertRaises(DonationsWeeklyPublicError):
            validate_public_weekly_donations(payload)

    def test_duplicate_current_selection_is_rejected(self) -> None:
        payload = self.simple_payload()
        payload["weeks"].append(copy.deepcopy(payload["weeks"][0]))
        with self.assertRaises(DonationsWeeklyPublicError):
            validate_public_weekly_donations(payload)

    def test_invalid_totals_counts_flags_and_order_are_rejected(self) -> None:
        base = self.simple_payload()
        cases = []
        for field, value in (
            ("participant_count", 2),
            ("contributing_player_count", 0),
            ("gap_affected", True),
        ):
            payload = copy.deepcopy(base)
            payload["weeks"][0][field] = value
            cases.append(payload)
        for payload in cases:
            with self.assertRaises(DonationsWeeklyPublicError):
                validate_public_weekly_donations(payload)

    def test_existing_recursive_privacy_scanner_is_applied(self) -> None:
        payload = self.simple_payload()
        payload["weeks"][0]["players"][0]["nickname"] = "#PRIVATE_A"
        with self.assertRaises(DonationsWeeklyPublicError):
            validate_public_weekly_donations(payload)

    def test_invalid_or_duplicate_current_roster_identity_fails_closed(self) -> None:
        for members in (
            roster(("", "Alpha")),
            roster(("#PRIVATE_A", "Alpha"), ("#PRIVATE_A", "Beta")),
        ):
            with self.assertRaises(DonationsWeeklyPublicError):
                build(weekly_result((), ()), members)


class ProductionShapedFictionalFixtureTests(unittest.TestCase):
    def test_five_current_two_departed_projection_is_scope_safe(self) -> None:
        current_ids = tuple(f"#PRIVATE_{letter}" for letter in "ABCDE")
        departed_ids = ("#PRIVATE_X", "#PRIVATE_Y")
        current_rows = tuple(
            player_week(identity, CURRENT_SAMPLE, index + 1, index)
            for index, identity in enumerate(current_ids)
        )
        current_departed = tuple(
            player_week(identity, CURRENT_SAMPLE, 50, 40, reset=True)
            for identity in departed_ids
        )
        insufficient = player_week("#PRIVATE_A", PREVIOUS_SAMPLE, 0, 0, status="insufficient_data", transitions=0)
        older_rows = (
            player_week("#PRIVATE_A", OLDER_SAMPLE, 8, 5, gap=True),
            player_week("#PRIVATE_B", OLDER_SAMPLE, 6, 4, boundary=True),
            player_week("#PRIVATE_C", OLDER_SAMPLE, 0, 0),
            player_week("#PRIVATE_X", OLDER_SAMPLE, 99, 90, reset=True),
        )
        players = current_rows + current_departed + (insufficient,) + older_rows
        aggregates = (
            aggregate_week(OLDER_SAMPLE, older_rows),
            aggregate_week(PREVIOUS_SAMPLE, (insufficient,), status="insufficient_data"),
            aggregate_week(CURRENT_SAMPLE, current_rows + current_departed),
        )
        members = roster(
            ("#PRIVATE_A", "New Alpha"),
            ("#PRIVATE_B", "Twin"),
            ("#PRIVATE_C", "Twin"),
            ("#PRIVATE_D", "Delta"),
            ("#PRIVATE_E", "Echo"),
        )
        payload = build(weekly_result(players, aggregates), members)
        self.assertEqual(2, len(payload["weeks"]))
        self.assertEqual(5, payload["weeks"][0]["participant_count"])
        self.assertEqual(3, payload["weeks"][1]["participant_count"])
        self.assertEqual(14, payload["weeks"][1]["donations_confirmed"])
        self.assertEqual(9, payload["weeks"][1]["donations_received_confirmed"])
        self.assertTrue(payload["weeks"][1]["gap_affected"])
        self.assertTrue(payload["weeks"][1]["boundary_ambiguous"])
        self.assertFalse(payload["weeks"][1]["reset_affected"])
        rendered = json.dumps(payload)
        for forbidden in current_ids + departed_ids + ("player_id_internal", "fingerprint"):
            self.assertNotIn(forbidden, rendered)
        validate_public_weekly_donations(payload)


if __name__ == "__main__":
    unittest.main()
