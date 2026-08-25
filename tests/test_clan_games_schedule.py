from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from clan_analytics.clan_games_events import (  # noqa: E402
    ClanGamesEvent,
    ClanGamesEventRegistry,
)
from clan_analytics.clan_games_history import event_definition_fingerprint  # noqa: E402
from clan_analytics.clan_games_schedule import (  # noqa: E402
    ClanGamesScheduleError,
    deterministic_scan_id,
    no_event_registry_decision,
    plan_clan_games_scan,
)


SOURCE = "https://supercell.com/en/games/clashofclans/blog/news/fictional/"


def at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def event(
    event_id: str = "fictional-event",
    start: str = "2026-09-10T06:00:00Z",
    end: str = "2026-09-16T06:00:00Z",
) -> ClanGamesEvent:
    return ClanGamesEvent.create(
        event_id=event_id,
        start_at=start,
        end_at=end,
        official_source_url=SOURCE,
        confirmed_at="2026-08-20T00:00:00Z",
    )


def registry(*events: ClanGamesEvent) -> ClanGamesEventRegistry:
    return ClanGamesEventRegistry(1, tuple(events))


def summary(
    value: ClanGamesEvent,
    kind: str,
    slot: str,
    *,
    scan_id: str | None = None,
) -> dict[str, str]:
    return {
        "scan_id": scan_id or deterministic_scan_id(value.event_id, kind, at(slot)),
        "event_id": value.event_id,
        "scan_kind": kind,
        "definition_id": event_definition_fingerprint(value),
    }


class ClanGamesScheduleTests(unittest.TestCase):
    def test_no_registry_and_empty_registry_are_idle(self) -> None:
        self.assertEqual("no_event_registry", no_event_registry_decision().result_code)
        decision = plan_clan_games_scan(
            registry(), [], as_of=at("2026-09-01T00:00:00Z")
        )
        self.assertEqual(("no_scan_due", False), (
            decision.result_code,
            decision.collector_due,
        ))

    def test_upcoming_event_outside_baseline_window_has_no_scan(self) -> None:
        decision = plan_clan_games_scan(
            registry(event()), [], as_of=at("2026-09-09T00:00:00Z")
        )
        self.assertEqual("no_scan_due", decision.action)

    def test_baseline_window_and_exact_start_are_due_with_stable_id(self) -> None:
        value = event()
        for as_of in ("2026-09-10T00:00:00Z", "2026-09-10T06:00:00Z"):
            with self.subTest(as_of=as_of):
                first = plan_clan_games_scan(registry(value), [], as_of=at(as_of))
                second = plan_clan_games_scan(registry(value), [], as_of=at(as_of))
                self.assertEqual(("baseline_due", "baseline"), (
                    first.action,
                    first.scan_kind,
                ))
                self.assertEqual("2026-09-10T00:00:00.000000Z", first.scheduled_for_utc)
                self.assertEqual(first.scan_id, second.scan_id)

    def test_exact_start_with_baseline_schedules_periodic_slot_zero(self) -> None:
        value = event()
        scans = [summary(value, "baseline", "2026-09-10T00:00:00Z")]
        decision = plan_clan_games_scan(
            registry(value), scans, as_of=at("2026-09-10T06:00:00Z")
        )
        self.assertEqual(("periodic_due", "2026-09-10T06:00:00.000000Z"), (
            decision.action,
            decision.scheduled_for_utc,
        ))

    def test_baseline_missed_does_not_block_periodic(self) -> None:
        decision = plan_clan_games_scan(
            registry(event()), [], as_of=at("2026-09-10T07:00:00Z")
        )
        self.assertEqual("periodic_due", decision.action)
        self.assertTrue(decision.baseline_missed)
        self.assertFalse(decision.baseline_available)

    def test_existing_current_periodic_returns_baseline_warning_or_no_due(self) -> None:
        value = event()
        periodic = summary(value, "periodic", "2026-09-10T06:00:00Z")
        missed = plan_clan_games_scan(
            registry(value), [periodic], as_of=at("2026-09-10T07:00:00Z")
        )
        self.assertEqual("baseline_missed", missed.action)
        with_baseline = plan_clan_games_scan(
            registry(value),
            [periodic, summary(value, "baseline", "2026-09-10T00:00:00Z")],
            as_of=at("2026-09-10T07:00:00Z"),
        )
        self.assertEqual("no_scan_due", with_baseline.action)

    def test_missed_periodic_slots_schedule_only_latest_slot(self) -> None:
        value = event()
        decision = plan_clan_games_scan(
            registry(value),
            [summary(value, "baseline", "2026-09-10T00:00:00Z")],
            as_of=at("2026-09-11T01:30:00Z"),
        )
        self.assertEqual("periodic_due", decision.action)
        self.assertEqual("2026-09-11T00:00:00.000000Z", decision.scheduled_for_utc)

    def test_final_exact_end_is_due_and_existing_final_completes_event(self) -> None:
        value = event()
        end = "2026-09-16T06:00:00Z"
        due = plan_clan_games_scan(registry(value), [], as_of=at(end))
        self.assertEqual(("final_due", "final"), (due.action, due.scan_kind))
        complete = plan_clan_games_scan(
            registry(value), [summary(value, "final", end)], as_of=at(end)
        )
        self.assertEqual("event_complete", complete.action)

    def test_old_missing_final_is_not_backfilled_after_later_event_started(self) -> None:
        old = event("old-event", "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z")
        current = event(
            "current-event", "2026-09-10T00:00:00Z", "2026-09-12T00:00:00Z"
        )
        scans = [
            summary(current, "baseline", "2026-09-09T18:00:00Z"),
            summary(current, "periodic", "2026-09-10T06:00:00Z"),
        ]
        decision = plan_clan_games_scan(
            registry(old, current), scans, as_of=at("2026-09-10T07:00:00Z")
        )
        self.assertEqual("no_scan_due", decision.action)
        self.assertEqual("current-event", decision.event_id)

    def test_active_periodic_has_priority_over_upcoming_baseline(self) -> None:
        current = event("current", "2026-09-10T00:00:00Z", "2026-09-10T10:00:00Z")
        upcoming = event("upcoming", "2026-09-10T11:00:00Z", "2026-09-11T11:00:00Z")
        decision = plan_clan_games_scan(
            registry(current, upcoming), [], as_of=at("2026-09-10T07:00:00Z")
        )
        self.assertEqual(("periodic_due", "current"), (
            decision.action,
            decision.event_id,
        ))

    def test_naive_clock_and_changed_definition_fail_closed(self) -> None:
        value = event()
        with self.assertRaises(ClanGamesScheduleError):
            plan_clan_games_scan(
                registry(value), [], as_of=datetime(2026, 9, 10, 0, 0)
            )
        changed = event(end="2026-09-17T06:00:00Z")
        with self.assertRaises(ClanGamesScheduleError) as caught:
            plan_clan_games_scan(
                registry(changed),
                [summary(value, "baseline", "2026-09-10T00:00:00Z")],
                as_of=at("2026-09-10T01:00:00Z"),
            )
        self.assertEqual("schedule_conflict", caught.exception.result_code)

    def test_scan_id_is_bounded_and_depends_on_all_inputs(self) -> None:
        slot = at("2026-09-10T00:00:00Z")
        ids = {
            deterministic_scan_id("event-a", "baseline", slot),
            deterministic_scan_id("event-b", "baseline", slot),
            deterministic_scan_id("event-a", "periodic", slot),
            deterministic_scan_id("event-a", "baseline", at("2026-09-10T06:00:00Z")),
        }
        self.assertEqual(4, len(ids))
        self.assertTrue(all(len(value) <= 128 for value in ids))


if __name__ == "__main__":
    unittest.main()
