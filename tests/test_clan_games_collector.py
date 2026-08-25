from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from clan_analytics.api.clan_games import (  # noqa: E402
    GamesChampionSafeResult,
    GamesChampionSnapshot,
    GamesChampionSourceError,
)
from clan_analytics.api.models import (  # noqa: E402
    ClanMemberSnapshot,
    ClanSnapshot,
    SourceMetadata,
)
from clan_analytics.clan_games_collector import (  # noqa: E402
    DEFAULT_MAX_WORKERS,
    MAX_WORKERS,
    collect_clan_games_scan,
    read_current_roster_identities,
)
from clan_analytics.clan_games_events import ClanGamesEvent  # noqa: E402
from clan_analytics.clan_games_history import (  # noqa: E402
    get_scan_by_id,
    list_scan_summaries,
    load_event_player_observations,
)
from clan_analytics.clan_snapshot_history import (  # noqa: E402
    SnapshotStoreError,
    initialize_snapshot_store,
    record_confirmed_observation,
)


FAKE_TOKEN = "fictional-credential-never-used-on-network"
SOURCE_URL = "https://supercell.com/en/games/clashofclans/blog/news/fictional/"


def moment(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fixed(value: str):
    parsed = moment(value)
    return lambda: parsed


def event() -> ClanGamesEvent:
    return ClanGamesEvent.create(
        event_id="fictional-event",
        start_at="2026-09-10T06:00:00Z",
        end_at="2026-09-16T06:00:00Z",
        official_source_url=SOURCE_URL,
        confirmed_at="2026-08-20T12:00:00Z",
    )


def roster(*members: tuple[str, str]) -> ClanSnapshot:
    source = SourceMetadata(None, "2026-08-20T00:00:00Z", "fictional")
    return ClanSnapshot(
        "#CLAN1",
        "Fictional clan",
        10,
        tuple(
            ClanMemberSnapshot(
                tag, name, "member", 10, 1, 1, 1, 0, 0, 1, 1, source
            )
            for tag, name in members
        ),
        source,
    )


def successful_fetcher(calls: list[str], *, delay: float = 0.0):
    def fetcher(player_tag, *, token, timeout_seconds, transport, clock):
        calls.append(player_tag)
        if delay:
            time.sleep(delay)
        observed = clock().astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        return (
            GamesChampionSnapshot(
                player_tag_internal=player_tag,
                value=100,
                target=50_000,
                observed_at_utc=observed,
            ),
            GamesChampionSafeResult(
                "success", "success", observed, 1, 200, "valid", "valid"
            ),
        )

    return fetcher


class ClanGamesCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.roster_db = self.root / "snapshot.sqlite3"
        self.games_db = self.root / "clan-games.sqlite3"
        initialize_snapshot_store(self.roster_db)
        self.record_roster(
            roster(
                ("#CCC333", "Gamma"),
                ("#AAA111", "Alpha"),
                ("#BBB222", "Beta"),
            )
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def record_roster(
        self,
        value: ClanSnapshot,
        at: str = "2026-08-20T00:00:00Z",
        run: str = "fixture-1",
    ) -> None:
        record_confirmed_observation(self.roster_db, value, at, run, "tests-v1")

    def collect(self, fetcher, **overrides):
        arguments = {
            "event": event(),
            "scan_id": "fixture-scan",
            "scan_kind": "periodic",
            "roster_database_path": self.roster_db,
            "clan_games_database_path": self.games_db,
            "token": FAKE_TOKEN,
            "max_workers": 1,
            "clock": fixed("2026-09-11T00:00:00Z"),
            "fetcher": fetcher,
        }
        arguments.update(overrides)
        return collect_clan_games_scan(**arguments)

    def test_roster_adapter_reads_latest_tags_sorted_and_does_not_mutate_store(self):
        self.record_roster(
            roster(("#DDD444", "Delta"), ("#AAA111", "Renamed")),
            "2026-08-20T01:00:00Z",
            "fixture-2",
        )
        before = hashlib.sha256(self.roster_db.read_bytes()).hexdigest()
        self.assertEqual(
            ("#AAA111", "#DDD444"),
            read_current_roster_identities(self.roster_db),
        )
        self.assertEqual(before, hashlib.sha256(self.roster_db.read_bytes()).hexdigest())

    def test_roster_adapter_fails_closed_for_missing_empty_and_invalid_store(self):
        empty = self.root / "empty.sqlite3"
        invalid = self.root / "invalid.sqlite3"
        initialize_snapshot_store(empty)
        invalid.write_bytes(b"not sqlite")
        for candidate in (self.root / "missing.sqlite3", empty, invalid):
            with self.subTest(candidate=candidate.name), self.assertRaises(
                SnapshotStoreError
            ):
                read_current_roster_identities(candidate)

    def test_roster_larger_than_fifty_fails_without_network_or_store(self):
        self.record_roster(
            roster(
                *((f"#P{index:04d}", f"Member {index}") for index in range(51))
            ),
            "2026-08-20T01:00:00Z",
            "fixture-2",
        )
        calls: list[str] = []
        result = self.collect(successful_fetcher(calls))
        self.assertEqual("unexpected_roster_size", result.result_code)
        self.assertEqual([], calls)
        self.assertFalse(self.games_db.exists())

    def test_full_success_records_one_atomic_sorted_scan(self):
        calls: list[str] = []
        result = self.collect(successful_fetcher(calls))
        self.assertEqual(("success", "success"), (result.status, result.result_code))
        self.assertEqual((3, 3, 0, 0), (
            result.requested_count,
            result.successful_count,
            result.failed_count,
            result.skipped_count,
        ))
        self.assertTrue(result.observation_recorded)
        self.assertTrue(result.store_initialized)
        self.assertEqual(1, len(list_scan_summaries(self.games_db)))
        rows = load_event_player_observations(self.games_db, event().event_id)
        self.assertEqual(sorted(calls), [row["player_tag"] for row in rows])

    def test_independent_failure_is_partial_and_batch_continues(self):
        calls: list[str] = []

        def fetcher(tag, **kwargs):
            calls.append(tag)
            if tag == "#BBB222":
                raise GamesChampionSourceError("timeout", "fictional timeout")
            return successful_fetcher([])(tag, **kwargs)

        result = self.collect(fetcher)
        self.assertEqual("partial_success", result.status)
        self.assertEqual("partial_player_failures", result.result_code)
        self.assertEqual((2, 1, 0), (
            result.successful_count,
            result.failed_count,
            result.skipped_count,
        ))
        self.assertEqual(3, len(calls))

    def test_non_systemic_source_codes_continue(self):
        for code in (
            "api_transport_failure",
            "games_champion_missing",
            "invalid_player_schema",
            "invalid_json",
        ):
            with self.subTest(code=code):
                games = self.root / f"{code}.sqlite3"
                calls: list[str] = []

                def fetcher(tag, **kwargs):
                    calls.append(tag)
                    if tag == "#AAA111":
                        raise GamesChampionSourceError(code, "fictional failure")
                    return successful_fetcher([])(tag, **kwargs)

                result = self.collect(
                    fetcher,
                    clan_games_database_path=games,
                    scan_id=f"scan-{code}",
                )
                self.assertEqual("partial_success", result.status)
                self.assertEqual(3, len(calls))

    def test_unexpected_client_exception_is_bounded_player_failure(self):
        def fetcher(tag, **kwargs):
            if tag == "#AAA111":
                raise RuntimeError(f"private {tag} {FAKE_TOKEN}")
            return successful_fetcher([])(tag, **kwargs)

        result = self.collect(fetcher)
        self.assertEqual("partial_success", result.status)
        self.assertNotIn("#", json.dumps(result.to_dict()))
        self.assertNotIn(FAKE_TOKEN, json.dumps(result.to_dict()))

    def test_first_systemic_403_stops_incremental_scheduling(self):
        self.record_roster(
            roster(*((f"#AA{index:04d}", f"Member {index}") for index in range(10))),
            "2026-08-20T01:00:00Z",
            "fixture-2",
        )
        calls: list[str] = []

        def fetcher(tag, **kwargs):
            calls.append(tag)
            raise GamesChampionSourceError("api_http_403", "fictional forbidden")

        result = self.collect(fetcher, max_workers=1)
        self.assertEqual(("failed", "api_http_403"), (result.status, result.result_code))
        self.assertEqual((10, 1, 0, 1, 9), (
            result.requested_count,
            result.attempted_count,
            result.successful_count,
            result.failed_count,
            result.skipped_count,
        ))
        self.assertEqual("enable_approved_vpn", result.operator_hint_code)
        self.assertEqual(1, len(calls))

    def test_systemic_403_after_success_preserves_success(self):
        calls: list[str] = []

        def fetcher(tag, **kwargs):
            calls.append(tag)
            if len(calls) == 2:
                raise GamesChampionSourceError("api_http_403", "fictional forbidden")
            return successful_fetcher([])(tag, **kwargs)

        result = self.collect(fetcher, max_workers=1)
        self.assertEqual(("partial_success", "api_http_403"), (
            result.status,
            result.result_code,
        ))
        self.assertEqual((1, 1, 1), (
            result.successful_count,
            result.failed_count,
            result.skipped_count,
        ))

    def test_maximum_in_flight_never_exceeds_worker_bound(self):
        lock = threading.Lock()
        active = 0
        maximum = 0

        def fetcher(tag, **kwargs):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            try:
                return successful_fetcher([], delay=0.02)(tag, **kwargs)
            finally:
                with lock:
                    active -= 1

        result = self.collect(fetcher, max_workers=2)
        self.assertEqual("success", result.status)
        self.assertLessEqual(maximum, 2)
        self.assertGreaterEqual(maximum, 2)

    def test_completion_order_does_not_change_scan_authority(self):
        fingerprints: list[str] = []
        delay_sets = (
            {"#AAA111": 0.03, "#BBB222": 0.01, "#CCC333": 0.0},
            {"#AAA111": 0.0, "#BBB222": 0.01, "#CCC333": 0.03},
        )
        for index, delays in enumerate(delay_sets):
            database = self.root / f"order-{index}.sqlite3"

            def fetcher(tag, **kwargs):
                time.sleep(delays[tag])
                return successful_fetcher([])(tag, **kwargs)

            result = self.collect(
                fetcher,
                max_workers=3,
                clan_games_database_path=database,
            )
            self.assertEqual("success", result.status)
            fingerprints.append(list_scan_summaries(database)[0]["scan_fingerprint"])
        self.assertEqual(fingerprints[0], fingerprints[1])

    def test_worker_configuration_is_bounded(self):
        calls: list[str] = []
        for workers in (0, MAX_WORKERS + 1, True):
            with self.subTest(workers=workers):
                result = self.collect(successful_fetcher(calls), max_workers=workers)
                self.assertEqual("invalid_collector_configuration", result.result_code)
        self.assertEqual(DEFAULT_MAX_WORKERS, 4)
        self.assertEqual([], calls)

    def test_timing_matrix_and_naive_clock_reject_before_network(self):
        cases = (
            ("baseline", "2026-09-10T05:59:59Z", True),
            ("baseline", "2026-09-10T06:00:00Z", True),
            ("baseline", "2026-09-10T06:00:01Z", False),
            ("periodic", "2026-09-10T05:59:59Z", False),
            ("periodic", "2026-09-10T06:00:00Z", True),
            ("periodic", "2026-09-11T00:00:00Z", True),
            ("periodic", "2026-09-16T06:00:00Z", False),
            ("final", "2026-09-16T05:59:59Z", False),
            ("final", "2026-09-16T06:00:00Z", True),
            ("final", "2026-09-16T06:00:01Z", True),
        )
        for index, (kind, at, accepted) in enumerate(cases):
            with self.subTest(kind=kind, at=at):
                calls: list[str] = []
                result = self.collect(
                    successful_fetcher(calls),
                    scan_kind=kind,
                    scan_id=f"timing-{index}",
                    clan_games_database_path=self.root / f"timing-{index}.sqlite3",
                    clock=fixed(at),
                )
                self.assertEqual(accepted, result.status == "success")
                self.assertEqual(3 if accepted else 0, len(calls))
        calls = []
        result = self.collect(
            successful_fetcher(calls),
            clock=lambda: datetime(2026, 9, 11),
            clan_games_database_path=self.root / "naive.sqlite3",
        )
        self.assertEqual("collector_internal_failure", result.result_code)
        self.assertEqual([], calls)

    def test_existing_scan_returns_before_token_roster_and_network(self):
        calls: list[str] = []
        first = self.collect(successful_fetcher(calls))
        self.assertEqual("success", first.status)
        self.roster_db.unlink()
        retry = self.collect(successful_fetcher(calls), token=None)
        self.assertEqual(("no_change", "already_recorded"), (
            retry.status,
            retry.result_code,
        ))
        self.assertEqual(3, len(calls))
        self.assertIsNotNone(get_scan_by_id(self.games_db, "fixture-scan"))

    def test_existing_scan_identity_mismatch_fails_before_network(self):
        calls: list[str] = []
        self.assertEqual("success", self.collect(successful_fetcher(calls)).status)
        conflict = self.collect(
            successful_fetcher(calls),
            scan_kind="final",
            clock=fixed("2026-09-16T06:00:00Z"),
        )
        self.assertEqual(("failed", "scan_conflict"), (
            conflict.status,
            conflict.result_code,
        ))
        self.assertEqual(3, len(calls))

    def test_all_failures_are_recorded_without_zero_fill(self):
        def fetcher(tag, **kwargs):
            raise GamesChampionSourceError("timeout", "fictional timeout")

        result = self.collect(fetcher)
        self.assertEqual(("failed", "all_player_requests_failed"), (
            result.status,
            result.result_code,
        ))
        rows = load_event_player_observations(self.games_db, event().event_id)
        self.assertTrue(all(row["cumulative_value"] is None for row in rows))

    def test_safe_result_and_store_do_not_contain_raw_profile_marker(self):
        marker = "RAW-PROFILE-MARKER-NEVER-PERSIST"
        result = self.collect(successful_fetcher([]), transport={"marker": marker})
        serialized = json.dumps(result.to_dict(), sort_keys=True)
        self.assertNotIn(marker, serialized)
        self.assertNotIn("#AAA111", serialized)
        self.assertNotIn("player_tag", serialized)
        self.assertNotIn(FAKE_TOKEN, serialized)
        self.assertNotIn(marker.encode(), self.games_db.read_bytes())

    def test_missing_roster_and_credential_fail_before_store_or_network(self):
        calls: list[str] = []
        missing = self.collect(
            successful_fetcher(calls),
            roster_database_path=self.root / "missing.sqlite3",
        )
        self.assertEqual("roster_source_unavailable", missing.result_code)
        self.assertFalse(self.games_db.exists())
        no_token = self.collect(successful_fetcher(calls), token=None)
        self.assertEqual("credential_unavailable", no_token.result_code)
        self.assertFalse(self.games_db.exists())
        self.assertEqual([], calls)


class ClanGamesCollectorStaticTests(unittest.TestCase):
    def test_production_cli_and_secure_wrapper_keep_fixed_boundaries(self):
        cli = (REPO_ROOT / "scripts" / "clan_games" / "collect_games_champion.py")
        wrapper = (
            REPO_ROOT / "scripts" / "clan_games" / "run_games_champion_collector.ps1"
        )
        self.assertTrue(cli.is_file())
        self.assertTrue(wrapper.is_file())
        combined = cli.read_text(encoding="utf-8") + wrapper.read_text(encoding="utf-8")
        self.assertNotIn('add_argument("--token"', combined)
        self.assertNotIn("$plainToken" + " @collectorArguments", combined)
        self.assertNotIn("update_clan_site", combined)
        self.assertNotIn("Start-ScheduledTask", combined)
        self.assertIn("COC_API_TOKEN", combined)

    def test_cli_rejects_token_value_without_echo_and_maps_partial_to_zero(self):
        path = REPO_ROOT / "scripts" / "clan_games" / "collect_games_champion.py"
        spec = importlib.util.spec_from_file_location("collector_cli_fixture", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        secret = "fictional-secret-cli-value"
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            self.assertEqual(1, module.main(["--token", secret]))
        self.assertNotIn(secret, stderr.getvalue())
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            self.assertEqual(1, module.main(["--api-token", secret]))
        self.assertNotIn(secret, stderr.getvalue())
        arguments = [
            "--event-id", "fictional-event",
            "--scan-id", "fictional-scan",
            "--scan-kind", "periodic",
        ]
        safe_partial = {
            "status": "partial_success",
            "result_code": "partial_player_failures",
        }
        with patch.object(module, "execute", return_value=safe_partial), patch(
            "sys.stdout", io.StringIO()
        ):
            self.assertEqual(0, module.main(arguments))


if __name__ == "__main__":
    unittest.main()
