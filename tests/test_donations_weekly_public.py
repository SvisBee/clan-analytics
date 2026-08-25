from __future__ import annotations

import copy
import inspect
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clan_analytics.donations_weekly import DonationObservation, TIMEZONE_NAME, week_window  # noqa: E402
from clan_analytics.donations_weekly_public import (  # noqa: E402
    CurrentPublicMember,
    DonationsWeeklyPublicError,
    METRIC_SEMANTICS,
    build_public_weekly_donations,
    validate_public_weekly_donations,
)
import clan_analytics.donations_weekly_public as public_module  # noqa: E402


UTC = timezone.utc
AS_OF = datetime(2026, 8, 25, 12, tzinfo=UTC)
PREVIOUS_EARLY = datetime(2026, 8, 18, 10, tzinfo=UTC)
PREVIOUS_NEAR = datetime(2026, 8, 23, 20, tzinfo=UTC)
PREVIOUS_STALE = datetime(2026, 8, 23, 18, tzinfo=UTC)
CURRENT_EARLY = datetime(2026, 8, 24, 1, tzinfo=UTC)
CURRENT_LATEST = datetime(2026, 8, 25, 11, tzinfo=UTC)


def observation(
    identity: str,
    at: datetime,
    donations: int | None,
    received: int | None,
    segment: str = "segment-1",
) -> DonationObservation:
    return DonationObservation(identity, at, donations, received, segment)


def roster(*items: tuple[str, str]) -> tuple[CurrentPublicMember, ...]:
    return tuple(CurrentPublicMember(identity, nickname) for identity, nickname in items)


def build(
    observations: tuple[DonationObservation, ...],
    members: tuple[CurrentPublicMember, ...],
    *,
    freshness_threshold: timedelta = timedelta(hours=2),
) -> dict:
    return build_public_weekly_donations(
        observations,
        members,
        as_of_utc=AS_OF,
        freshness_threshold=freshness_threshold,
    )


class RawCounterSemanticsTests(unittest.TestCase):
    def test_latest_raw_value_is_published_without_summing(self) -> None:
        observations = tuple(
            observation("#A", at, value, value + 1)
            for at, value in (
                (CURRENT_EARLY, 100),
                (CURRENT_EARLY + timedelta(hours=1), 250),
                (CURRENT_EARLY + timedelta(hours=2), 480),
                (CURRENT_LATEST, 850),
            )
        )
        current = build(observations, roster(("#A", "Alpha")))["weeks"][0]
        self.assertEqual((850, 851), (current["donations"], current["donations_received"]))
        self.assertEqual(
            {"nickname": "Alpha", "donations": 850, "donations_received": 851},
            current["players"][0],
        )

    def test_reset_publishes_zero_current_and_pre_boundary_value_previous(self) -> None:
        payload = build(
            (
                observation("#A", PREVIOUS_NEAR, 850, 620),
                observation("#A", CURRENT_EARLY, 0, 0),
            ),
            roster(("#A", "Alpha")),
        )
        current, previous = payload["weeks"]
        self.assertEqual((0, 0), (current["donations"], current["donations_received"]))
        self.assertEqual((850, 620), (previous["donations"], previous["donations_received"]))
        self.assertTrue(current["coverage"]["reset_observed"])

    def test_previous_uses_latest_not_sum_or_max(self) -> None:
        payload = build(
            (
                observation("#A", PREVIOUS_EARLY, 100, 90),
                observation("#A", PREVIOUS_EARLY + timedelta(hours=1), 300, 250),
                observation("#A", PREVIOUS_NEAR, 200, 180),
                observation("#A", CURRENT_LATEST, 30, 20),
            ),
            roster(("#A", "Alpha")),
        )
        previous = payload["weeks"][1]
        self.assertEqual((200, 180), (previous["donations"], previous["donations_received"]))

    def test_previous_missing_member_is_absent_not_zero(self) -> None:
        payload = build(
            (
                observation("#A", PREVIOUS_NEAR, 10, 5),
                observation("#A", CURRENT_LATEST, 11, 6),
                observation("#B", CURRENT_LATEST, 2, 1),
            ),
            roster(("#A", "Alpha"), ("#B", "Beta")),
        )
        previous = payload["weeks"][1]
        self.assertEqual(["Alpha"], [row["nickname"] for row in previous["players"]])
        self.assertEqual(1, previous["coverage"]["missing_player_count"])
        self.assertTrue(previous["coverage"]["insufficient_data"])
        self.assertEqual("partial", previous["status"])

    def test_stale_previous_evidence_is_displayed_and_flagged(self) -> None:
        payload = build(
            (
                observation("#A", PREVIOUS_STALE, 12, 8),
                observation("#A", CURRENT_LATEST, 1, 1),
            ),
            roster(("#A", "Alpha")),
        )
        previous = payload["weeks"][1]
        self.assertEqual(12, previous["donations"])
        self.assertTrue(previous["coverage"]["stale_end_snapshot"])
        self.assertEqual(1, previous["coverage"]["stale_player_count"])
        self.assertEqual("partial", previous["status"])

    def test_near_boundary_previous_evidence_is_recorded(self) -> None:
        payload = build(
            (
                observation("#A", PREVIOUS_NEAR, 12, 8),
                observation("#A", CURRENT_LATEST, 1, 1),
            ),
            roster(("#A", "Alpha")),
        )
        self.assertEqual("recorded", payload["weeks"][1]["status"])

    def test_same_values_new_observation_is_byte_stable(self) -> None:
        first = build(
            (observation("#A", CURRENT_EARLY, 10, 5),),
            roster(("#A", "Alpha")),
        )
        second = build(
            (
                observation("#A", CURRENT_EARLY, 10, 5),
                observation("#A", CURRENT_LATEST, 10, 5),
            ),
            roster(("#A", "Alpha")),
        )
        self.assertEqual(
            json.dumps(first, ensure_ascii=False, sort_keys=True),
            json.dumps(second, ensure_ascii=False, sort_keys=True),
        )
        self.assertIsNone(first["weeks"][0]["snapshot_at_utc"])

    def test_changed_raw_value_changes_payload(self) -> None:
        first = build((observation("#A", CURRENT_EARLY, 10, 5),), roster(("#A", "Alpha")))
        second = build(
            (
                observation("#A", CURRENT_EARLY, 10, 5),
                observation("#A", CURRENT_LATEST, 11, 5),
            ),
            roster(("#A", "Alpha")),
        )
        self.assertNotEqual(first, second)

    def test_null_or_missing_current_counter_fails_closed(self) -> None:
        for observations in (
            (),
            (observation("#A", CURRENT_LATEST, None, 1),),
            (observation("#A", CURRENT_LATEST, 1, None),),
        ):
            with self.subTest(observations=observations), self.assertRaises(DonationsWeeklyPublicError):
                build(observations, roster(("#A", "Alpha")))


class ScopeIdentityAndOrderingTests(unittest.TestCase):
    def test_departed_member_is_excluded(self) -> None:
        payload = build(
            (
                observation("#A", CURRENT_LATEST, 5, 3),
                observation("#X", CURRENT_LATEST, 500, 300),
            ),
            roster(("#A", "Alpha")),
        )
        self.assertEqual((5, 3, 1), (
            payload["weeks"][0]["donations"],
            payload["weeks"][0]["donations_received"],
            payload["weeks"][0]["participant_count"],
        ))

    def test_joined_current_member_uses_raw_value_without_baseline(self) -> None:
        current = build(
            (observation("#NEW", CURRENT_LATEST, 70, 40),),
            roster(("#NEW", "New member")),
        )["weeks"][0]
        self.assertEqual((70, 40), (current["donations"], current["donations_received"]))

    def test_same_run_current_snapshot_supports_new_roster_identity(self) -> None:
        payload = build_public_weekly_donations(
            (),
            roster(("#NEW", "New member")),
            as_of_utc=AS_OF,
            current_raw_counters={"#NEW": (70, 40)},
        )
        current = payload["weeks"][0]
        self.assertEqual((70, 40, 1), (
            current["donations"], current["donations_received"], current["participant_count"]
        ))

    def test_duplicate_nicknames_remain_separate_and_private_tiebreak_is_stable(self) -> None:
        observations = (
            observation("#B", CURRENT_LATEST, 4, 2),
            observation("#A", CURRENT_LATEST, 4, 2),
        )
        first = build(observations, roster(("#B", "Twin"), ("#A", "Twin")))
        second = build(tuple(reversed(observations)), roster(("#A", "Twin"), ("#B", "Twin")))
        self.assertEqual(first, second)
        self.assertEqual(["Twin", "Twin"], [row["nickname"] for row in first["weeks"][0]["players"]])
        self.assertNotIn("#A", json.dumps(first))

    def test_order_and_active_donor_count_use_donations_only(self) -> None:
        current = build(
            (
                observation("#A", CURRENT_LATEST, 5, 1),
                observation("#B", CURRENT_LATEST, 4, 99),
                observation("#C", CURRENT_LATEST, 0, 100),
            ),
            roster(("#A", "A"), ("#B", "B"), ("#C", "C")),
        )["weeks"][0]
        self.assertEqual(["A", "B", "C"], [row["nickname"] for row in current["players"]])
        self.assertEqual(2, current["contributing_player_count"])


class SchemaPrivacyAndTimeTests(unittest.TestCase):
    def simple_payload(self) -> dict:
        return build(
            (
                observation("#A", PREVIOUS_NEAR, 4, 2),
                observation("#A", CURRENT_LATEST, 1, 1),
            ),
            roster(("#A", "Alpha")),
        )

    def test_schema_v2_exact_allowlists_and_semantics(self) -> None:
        payload = self.simple_payload()
        self.assertEqual(2, payload["schema_version"])
        self.assertEqual(METRIC_SEMANTICS, payload["metric_semantics"])
        self.assertEqual(TIMEZONE_NAME, payload["timezone"])
        self.assertEqual({"schema_version", "timezone", "scope", "metric_semantics", "weeks"}, set(payload))
        self.assertEqual({"nickname", "donations", "donations_received"}, set(payload["weeks"][0]["players"][0]))
        self.assertNotIn("generated_at_utc", payload)
        self.assertNotIn("latest_observed_at_utc", payload)

    def test_week_selection_is_current_then_immediately_previous(self) -> None:
        payload = self.simple_payload()
        self.assertEqual(["current", "previous"], [week["selection"] for week in payload["weeks"]])
        expected = week_window(AS_OF, as_of_utc=AS_OF)
        self.assertEqual(expected.week_id, payload["weeks"][0]["week_id"])
        self.assertTrue(payload["weeks"][0]["week_start"].endswith("+03:00"))

    def test_unknown_fields_negative_values_totals_and_order_fail_closed(self) -> None:
        mutations = []
        unknown = self.simple_payload(); unknown["private"] = True; mutations.append(unknown)
        negative = self.simple_payload(); negative["weeks"][0]["players"][0]["donations"] = -1; mutations.append(negative)
        total = self.simple_payload(); total["weeks"][0]["donations"] += 1; mutations.append(total)
        order = self.simple_payload()
        order["weeks"][0]["players"].append({"nickname": "Z", "donations": 99, "donations_received": 0})
        order["weeks"][0]["participant_count"] += 1
        order["weeks"][0]["donations"] += 99
        order["weeks"][0]["contributing_player_count"] += 1
        mutations.append(order)
        for payload in mutations:
            with self.subTest(payload=payload), self.assertRaises(DonationsWeeklyPublicError):
                validate_public_weekly_donations(payload)

    def test_private_and_obsolete_fields_are_rejected_or_absent(self) -> None:
        payload = self.simple_payload()
        rendered = json.dumps(payload)
        for forbidden in (
            "#A", "player_tag", "player_id_internal", "payload_id", "observation_id",
            "fingerprint", "source_run_id", "donations_confirmed", "reset_affected",
            "gap_affected", "boundary_ambiguous", "previous_usable",
        ):
            self.assertNotIn(forbidden, rendered)
        for field in ("tag", "player_tag", "internal_id", "payload_id"):
            changed = copy.deepcopy(payload)
            changed["weeks"][0]["players"][0][field] = "private"
            with self.assertRaises(DonationsWeeklyPublicError):
                validate_public_weekly_donations(changed)

    def test_projection_module_has_no_storage_api_or_output_integration(self) -> None:
        source = inspect.getsource(public_module)
        for forbidden in ("sqlite3", "site/data", ".write_text(", ".write_bytes(", "requests."):
            self.assertNotIn(forbidden, source)

    def test_invalid_roster_and_naive_as_of_fail_closed(self) -> None:
        with self.assertRaises(DonationsWeeklyPublicError):
            build_public_weekly_donations((), roster(("#A", "Alpha")), as_of_utc=datetime(2026, 8, 25, 12))
        for members in (roster(("", "Alpha")), roster(("#A", "Alpha"), ("#A", "Beta"))):
            with self.assertRaises(DonationsWeeklyPublicError):
                build((), members)


if __name__ == "__main__":
    unittest.main()
