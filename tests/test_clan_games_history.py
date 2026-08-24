from __future__ import annotations

import inspect
import json
import sqlite3
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
PRODUCTION_STORE = (
    REPO_ROOT.parent / "data" / "clan_games" / "clan_games.v1.sqlite3"
)
sys.path.insert(0, str(SRC_ROOT))

from clan_analytics.api.clan_games import GamesChampionSnapshot  # noqa: E402
from clan_analytics.clan_games_events import ClanGamesEvent  # noqa: E402
from clan_analytics.clan_games_history import (  # noqa: E402
    LOGICAL_DATABASE_PATH,
    ClanGamesScan,
    ClanGamesStoreError,
    PlayerScanResult,
    UnsupportedClanGamesSchemaError,
    create_validated_clan_games_backup,
    event_definition_fingerprint,
    get_latest_scan,
    get_scans_by_kind,
    initialize_clan_games_store,
    list_scan_summaries,
    load_event_player_observations,
    record_clan_games_scan,
    scan_content_fingerprint,
    summarize_clan_games_store,
    validate_clan_games_store,
)


SOURCE = "https://supercell.com/en/games/clashofclans/blog/news/fictional-evidence/"
START = "2026-09-10T06:00:00.000000Z"
END = "2026-09-16T06:00:00.000000Z"


def fictional_event(
    event_id: str = "fictional-alpha",
    *,
    end: str = END,
    confirmed: str = "2026-08-20T12:00:00.000000Z",
) -> ClanGamesEvent:
    return ClanGamesEvent.create(
        event_id=event_id,
        start_at=START,
        end_at=end,
        official_source_url=SOURCE,
        confirmed_at=confirmed,
    )


def successful(
    tag: str = "#AAA111",
    value: int = 1_000,
    *,
    attempted: str = "2026-09-10T06:00:01.000000Z",
    observed: str = "2026-09-10T06:00:02.000000Z",
) -> PlayerScanResult:
    snapshot = GamesChampionSnapshot(
        player_tag_internal=tag,
        value=value,
        target=50_000,
        observed_at_utc=observed,
    )
    return PlayerScanResult.success(snapshot, attempted_at=attempted)


def scan(
    scan_id: str = "fictional-scan-1",
    *,
    event_id: str = "fictional-alpha",
    kind: str = "baseline",
    started: str = "2026-09-10T06:00:00.000000Z",
    finished: str = "2026-09-10T06:00:05.000000Z",
    results: tuple[PlayerScanResult, ...] | None = None,
    result_code: str | None = None,
) -> ClanGamesScan:
    return ClanGamesScan.create(
        scan_id=scan_id,
        event_id=event_id,
        scan_kind=kind,
        started_at=started,
        finished_at=finished,
        player_results=results or (successful(),),
        result_code=result_code,
    )


class ClanGamesStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "clan_games.sqlite3"
        self.event = fictional_event()
        initialize_clan_games_store(self.path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def record(self, value: ClanGamesScan | None = None, event=None):
        return record_clan_games_scan(
            self.path, event or self.event, value or scan()
        )

    def mutate(self, sql: str, parameters: tuple = ()) -> None:
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(sql, parameters)
            connection.commit()
        finally:
            connection.close()

    def assert_code(self, expected: str, callback) -> ClanGamesStoreError:
        with self.assertRaises(ClanGamesStoreError) as caught:
            callback()
        self.assertEqual(expected, caught.exception.result_code)
        self.assertNotIn("#AAA111", str(caught.exception))
        return caught.exception

    def test_initialize_creates_schema_v1_and_is_idempotent(self) -> None:
        validate_clan_games_store(self.path)
        result = initialize_clan_games_store(self.path)
        self.assertEqual("no_change", result.result_code)
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(
                (1, "clan_games", "stable"),
                connection.execute(
                    "SELECT schema_version, storage_kind, migration_state FROM schema_metadata"
                ).fetchone(),
            )
            self.assertEqual(
                [(0, 0, "event_definition_snapshot", "definition_id", "definition_id", "NO ACTION", "RESTRICT", "NONE")],
                connection.execute("PRAGMA foreign_key_list(collection_scan)").fetchall(),
            )
        finally:
            connection.close()

    def test_missing_store_has_bounded_error(self) -> None:
        missing = self.root / "missing.sqlite3"
        self.assert_code("store_not_found", lambda: validate_clan_games_store(missing))

    def test_invalid_existing_file_is_not_overwritten(self) -> None:
        invalid = self.root / "invalid.sqlite3"
        original = b"not-a-database"
        invalid.write_bytes(original)
        self.assert_code("invalid_store", lambda: initialize_clan_games_store(invalid))
        self.assertEqual(original, invalid.read_bytes())

    def test_initialization_publish_failure_leaves_no_store_or_sidecars(self) -> None:
        target = self.root / "publish-failure.sqlite3"
        with patch(
            "clan_analytics.clan_games_history.os.link",
            side_effect=OSError("fixture publish failure"),
        ):
            self.assert_code(
                "write_failure", lambda: initialize_clan_games_store(target)
            )
        self.assertFalse(target.exists())
        self.assertEqual([], list(self.root.glob(".*.init.sqlite3*")))

    def test_initialization_post_publish_validation_failure_removes_new_store(self) -> None:
        target = self.root / "post-validation-failure.sqlite3"
        with patch(
            "clan_analytics.clan_games_history.validate_clan_games_store",
            side_effect=[
                None,
                ClanGamesStoreError("invalid_store", "fixture invalid"),
            ],
        ):
            self.assert_code(
                "invalid_store", lambda: initialize_clan_games_store(target)
            )
        self.assertFalse(target.exists())
        self.assertFalse(Path(str(target) + "-wal").exists())
        self.assertFalse(Path(str(target) + "-shm").exists())

    def test_unknown_schema_version_fails_closed(self) -> None:
        self.mutate("UPDATE schema_metadata SET schema_version = 2")
        with self.assertRaises(UnsupportedClanGamesSchemaError) as caught:
            validate_clan_games_store(self.path)
        self.assertEqual("unsupported_schema", caught.exception.result_code)

    def test_missing_table_and_wrong_column_fail_closed(self) -> None:
        self.mutate("DROP TABLE player_scan_result")
        self.assert_code("invalid_store", lambda: validate_clan_games_store(self.path))
        other = self.root / "wrong-column.sqlite3"
        initialize_clan_games_store(other)
        connection = sqlite3.connect(other)
        try:
            connection.execute("ALTER TABLE collection_scan ADD COLUMN unexpected TEXT")
            connection.commit()
        finally:
            connection.close()
        self.assert_code("invalid_store", lambda: validate_clan_games_store(other))
        third = self.root / "unexpected-trigger.sqlite3"
        initialize_clan_games_store(third)
        connection = sqlite3.connect(third)
        try:
            connection.execute(
                "CREATE TRIGGER unexpected_trigger AFTER INSERT ON schema_metadata BEGIN SELECT 1; END"
            )
            connection.commit()
        finally:
            connection.close()
        self.assert_code("invalid_store", lambda: validate_clan_games_store(third))

    def test_unexpected_application_table_or_index_fails_closed(self) -> None:
        self.mutate("CREATE TABLE unexpected_table (value TEXT)")
        self.assert_code("invalid_store", lambda: validate_clan_games_store(self.path))
        other = self.root / "unexpected-index.sqlite3"
        initialize_clan_games_store(other)
        connection = sqlite3.connect(other)
        try:
            connection.execute("CREATE INDEX unexpected_index ON schema_metadata(storage_kind)")
            connection.commit()
        finally:
            connection.close()
        self.assert_code("invalid_store", lambda: validate_clan_games_store(other))

    def test_foreign_key_and_integrity_contract(self) -> None:
        self.record()
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(
                "UPDATE player_scan_result SET scan_id = 'orphan' WHERE player_tag = '#AAA111'"
            )
            connection.commit()
        finally:
            connection.close()
        self.assert_code("invalid_store", lambda: validate_clan_games_store(self.path))

    def test_event_definition_insert_dedup_and_corrected_definition(self) -> None:
        first = self.record()
        later = scan(
            "fictional-scan-2",
            kind="periodic",
            started="2026-09-10T07:00:00Z",
            finished="2026-09-10T07:00:05Z",
            results=(
                successful(
                    value=2_000,
                    attempted="2026-09-10T07:00:01Z",
                    observed="2026-09-10T07:00:02Z",
                ),
            ),
        )
        second = self.record(later)
        corrected = fictional_event(end="2026-09-17T06:00:00Z")
        third_scan = scan(
            "fictional-scan-3",
            kind="final",
            started="2026-09-17T06:00:01Z",
            finished="2026-09-17T06:00:05Z",
            results=(
                successful(
                    value=3_000,
                    attempted="2026-09-17T06:00:02Z",
                    observed="2026-09-17T06:00:03Z",
                ),
            ),
        )
        third = self.record(third_scan, corrected)
        self.assertEqual(first.definition_fingerprint, second.definition_fingerprint)
        self.assertNotEqual(first.definition_fingerprint, third.definition_fingerprint)
        summary = summarize_clan_games_store(self.path)
        self.assertEqual(1, summary["event_count"])
        self.assertEqual(2, summary["definition_count"])
        connection = sqlite3.connect(self.path)
        try:
            definitions = connection.execute(
                "SELECT event_id, official_source_url, confirmed_at_utc "
                "FROM event_definition_snapshot ORDER BY end_at_utc"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(
            [
                ("fictional-alpha", SOURCE, "2026-08-20T12:00:00.000000Z"),
                ("fictional-alpha", SOURCE, "2026-08-20T12:00:00.000000Z"),
            ],
            definitions,
        )

    def test_event_definition_fingerprint_is_stable_and_excludes_recorded_time(self) -> None:
        first = event_definition_fingerprint(self.event)
        self.assertEqual(first, event_definition_fingerprint(fictional_event()))
        self.assertRegex(first, r"^[0-9a-f]{64}$")

    def test_event_id_mismatch_is_rejected_before_write(self) -> None:
        candidate = scan(event_id="fictional-other")
        self.assert_code(
            "invalid_event_definition", lambda: self.record(candidate)
        )
        self.assertEqual(0, summarize_clan_games_store(self.path)["scan_count"])

    def test_successful_scan_preserves_values_targets_and_counts(self) -> None:
        candidate = scan(
            results=(successful(), successful("#BBB222", 2_000))
        )
        self.record(candidate)
        summary = list_scan_summaries(self.path)[0]
        self.assertEqual((2, 2, 0, 0), tuple(summary[key] for key in (
            "requested_count", "successful_count", "failed_count", "skipped_count"
        )))
        rows = load_event_player_observations(self.path, self.event.event_id)
        self.assertEqual(["#AAA111", "#BBB222"], [row["player_tag"] for row in rows])
        self.assertEqual([1_000, 2_000], [row["cumulative_value"] for row in rows])
        self.assertEqual([50_000, 50_000], [row["achievement_target"] for row in rows])

    def test_partial_success_with_failure_has_no_zero_fill(self) -> None:
        failed = PlayerScanResult.failed(
            "#BBB222",
            result_code="api_transport_failure",
            attempted_at="2026-09-10T06:00:03Z",
        )
        self.record(scan(results=(successful(), failed)))
        summary = list_scan_summaries(self.path)[0]
        self.assertEqual("partial_success", summary["status"])
        self.assertEqual((2, 1, 1, 0), tuple(summary[key] for key in (
            "requested_count", "successful_count", "failed_count", "skipped_count"
        )))
        row = load_event_player_observations(self.path, self.event.event_id)[1]
        self.assertIsNone(row["cumulative_value"])
        self.assertIsNone(row["achievement_target"])

    def test_partial_success_with_skipped_has_no_zero_fill(self) -> None:
        skipped = PlayerScanResult.skipped(
            "#BBB222", result_code="skipped_after_systemic_failure"
        )
        self.record(scan(results=(successful(), skipped)))
        summary = list_scan_summaries(self.path)[0]
        self.assertEqual("partial_success", summary["status"])
        self.assertEqual(1, summary["skipped_count"])

    def test_failed_scan_can_contain_failed_and_skipped_rows(self) -> None:
        results = (
            PlayerScanResult.failed(
                "#AAA111",
                result_code="api_http_403",
                attempted_at="2026-09-10T06:00:01Z",
            ),
            PlayerScanResult.skipped(
                "#BBB222", result_code="skipped_after_systemic_failure"
            ),
        )
        self.record(scan(results=results, result_code="api_http_403"))
        summary = list_scan_summaries(self.path)[0]
        self.assertEqual("failed", summary["status"])
        self.assertEqual((0, 1, 1), tuple(summary[key] for key in (
            "successful_count", "failed_count", "skipped_count"
        )))

    def test_caller_supplied_count_or_status_mismatch_is_rejected(self) -> None:
        candidate = scan()
        self.assert_code(
            "invalid_scan", lambda: replace(candidate, requested_count=2)
        )
        self.assert_code(
            "invalid_scan", lambda: replace(candidate, status="partial_success")
        )
        self.assert_code(
            "invalid_scan", lambda: replace(candidate, result_code="partial_success")
        )

    def test_scan_identity_is_opaque_bounded_and_path_free(self) -> None:
        self.assertEqual("run:2026.09_1-abc", scan("run:2026.09_1-abc").scan_id)
        for scan_id in ("", "#AAA111", "path/to/scan", "scan id", "x" * 129):
            with self.subTest(scan_id=scan_id):
                self.assert_code("invalid_scan", lambda scan_id=scan_id: scan(scan_id))

    def test_success_missing_or_invalid_value_is_rejected(self) -> None:
        base = successful()
        for value in (None, -1, True, "1"):
            with self.subTest(value=value):
                self.assert_code(
                    "invalid_player_result",
                    lambda value=value: replace(base, cumulative_value=value),
                )

    def test_success_requires_confirmed_phase1_source_metadata(self) -> None:
        base = successful()
        for field_name, value in (
            ("source_kind", "other_source"),
            ("normalization_version", "other_version"),
        ):
            with self.subTest(field_name=field_name):
                self.assert_code(
                    "invalid_player_result",
                    lambda field_name=field_name, value=value: replace(
                        base, **{field_name: value}
                    ),
                )

    def test_failure_and_skip_shape_reject_observation_values(self) -> None:
        failed = PlayerScanResult.failed(
            "#AAA111", result_code="timeout", attempted_at="2026-09-10T06:00:01Z"
        )
        skipped = PlayerScanResult.skipped(
            "#BBB222", result_code="skipped_after_systemic_failure"
        )
        mutations = (
            lambda: replace(failed, cumulative_value=0),
            lambda: replace(failed, observed_at_utc="2026-09-10T06:00:02Z"),
            lambda: replace(skipped, achievement_target=0),
        )
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                self.assert_code("invalid_player_result", mutation)

    def test_empty_or_noncanonical_tag_and_duplicate_player_are_rejected(self) -> None:
        for tag in ("", "AAA111", "#aa111", "#A"):
            with self.subTest(tag=tag):
                self.assert_code(
                    "invalid_player_result",
                    lambda tag=tag: PlayerScanResult.failed(
                        tag,
                        result_code="timeout",
                        attempted_at="2026-09-10T06:00:01Z",
                    ),
                )
        self.assert_code(
            "invalid_player_result",
            lambda: scan(results=(successful(), successful())),
        )

    def test_exact_retry_and_shuffled_results_are_idempotent(self) -> None:
        first = successful("#AAA111", 1_000)
        second = successful("#BBB222", 2_000)
        candidate = scan(results=(first, second))
        initial = self.record(candidate)
        retry = self.record(scan(results=(second, first)))
        self.assertEqual("success", initial.result_code)
        self.assertEqual("no_change", retry.result_code)
        self.assertEqual(initial.scan_fingerprint, retry.scan_fingerprint)
        self.assertEqual(1, summarize_clan_games_store(self.path)["scan_count"])

    def test_transaction_failure_rolls_back_definition_scan_and_players(self) -> None:
        import clan_analytics.clan_games_history as module

        original = module._insert_definition

        def insert_then_fail(connection, event, fingerprint):
            original(connection, event, fingerprint)
            raise ClanGamesStoreError("write_failure", "fixture transaction failure")

        with patch(
            "clan_analytics.clan_games_history._insert_definition",
            side_effect=insert_then_fail,
        ):
            self.assert_code("write_failure", lambda: self.record())
        self.assertEqual(
            {
                "definition_count": 0,
                "scan_count": 0,
                "player_result_count": 0,
            },
            {
                key: summarize_clan_games_store(self.path)[key]
                for key in (
                    "definition_count",
                    "scan_count",
                    "player_result_count",
                )
            },
        )

    def test_bounded_database_lock_has_distinct_result_code(self) -> None:
        locker = sqlite3.connect(self.path)
        try:
            locker.execute("PRAGMA journal_mode = WAL")
            locker.execute("BEGIN IMMEDIATE")
            self.assert_code("locked", lambda: self.record())
        finally:
            locker.rollback()
            locker.close()

    def test_same_scan_id_changed_value_or_status_conflicts(self) -> None:
        self.record()
        changed_value = scan(results=(successful(value=2_000),))
        self.assert_code("scan_conflict", lambda: self.record(changed_value))
        changed_status = scan(
            results=(
                PlayerScanResult.failed(
                    "#AAA111",
                    result_code="timeout",
                    attempted_at="2026-09-10T06:00:01Z",
                ),
            ),
            result_code="timeout",
        )
        self.assert_code("scan_conflict", lambda: self.record(changed_status))

    def test_scan_fingerprint_is_deterministic_and_private_local_only(self) -> None:
        one = scan(results=(successful("#BBB222"), successful("#AAA111")))
        two = scan(results=(successful("#AAA111"), successful("#BBB222")))
        self.assertEqual(
            scan_content_fingerprint(self.event, one),
            scan_content_fingerprint(self.event, two),
        )

    def test_chronological_guard_and_independent_events(self) -> None:
        self.record()
        later = scan(
            "scan-later",
            started="2026-09-10T07:00:00Z",
            finished="2026-09-10T07:00:05Z",
            results=(successful(
                attempted="2026-09-10T07:00:01Z",
                observed="2026-09-10T07:00:02Z",
            ),),
        )
        self.record(later)
        for scan_id, started in (
            ("scan-equal", "2026-09-10T07:00:00Z"),
            ("scan-earlier", "2026-09-10T06:30:00Z"),
        ):
            candidate = scan(
                scan_id,
                started=started,
                finished="2026-09-10T07:00:04Z",
                results=(successful(
                    attempted="2026-09-10T07:00:01Z",
                    observed="2026-09-10T07:00:02Z",
                ),),
            )
            self.assert_code("out_of_order_scan", lambda candidate=candidate: self.record(candidate))
        other_event = fictional_event("fictional-beta")
        other = scan("other-1", event_id="fictional-beta")
        self.record(other, other_event)
        self.assertEqual(3, summarize_clan_games_store(self.path)["scan_count"])

    def test_all_scan_kinds_and_unknown_kind(self) -> None:
        for kind in ("baseline", "periodic", "final"):
            with self.subTest(kind=kind):
                self.assertEqual(kind, scan(kind=kind).scan_kind)
        self.assert_code("invalid_scan", lambda: scan(kind="manual"))

    def test_naive_and_out_of_range_timestamps_are_rejected(self) -> None:
        self.assert_code(
            "invalid_scan",
            lambda: scan(started="2026-09-10T06:00:00"),
        )
        self.assert_code(
            "invalid_player_result",
            lambda: scan(results=(successful(
                attempted="2026-09-10T05:59:59Z",
                observed="2026-09-10T06:00:02Z",
            ),)),
        )
        self.assert_code(
            "invalid_player_result",
            lambda: successful(
                attempted="2026-09-10T06:00:03Z",
                observed="2026-09-10T06:00:02Z",
            ),
        )

    def test_offset_timestamps_normalize_to_fixed_width_utc(self) -> None:
        value = scan(
            started="2026-09-10T09:00:00+03:00",
            finished="2026-09-10T09:00:05+03:00",
        )
        self.assertEqual("2026-09-10T06:00:00.000000Z", value.started_at_utc)
        self.assertEqual("2026-09-10T06:00:05.000000Z", value.finished_at_utc)

    def test_scan_may_cross_event_boundary_without_storage_rejection(self) -> None:
        boundary_event = fictional_event(end="2026-09-10T06:00:03Z")
        self.record(scan(), boundary_event)
        self.assertEqual(1, summarize_clan_games_store(self.path)["scan_count"])

    def test_tampered_fingerprint_counts_timestamps_and_identity_fail_validation(self) -> None:
        statements = (
            ("UPDATE collection_scan SET scan_fingerprint = 'bad'", ()),
            ("UPDATE collection_scan SET successful_count = 0, failed_count = 1", ()),
            ("UPDATE event_definition_snapshot SET recorded_at_utc = 'not-time'", ()),
            ("UPDATE event_definition_snapshot SET event_id = 'other'", ()),
        )
        for index, (statement, parameters) in enumerate(statements):
            with self.subTest(index=index):
                target = self.root / f"tampered-{index}.sqlite3"
                initialize_clan_games_store(target)
                record_clan_games_scan(target, self.event, scan())
                connection = sqlite3.connect(target)
                try:
                    connection.execute("PRAGMA ignore_check_constraints = ON")
                    connection.execute(statement, parameters)
                    connection.commit()
                finally:
                    connection.close()
                self.assert_code("invalid_store", lambda target=target: validate_clan_games_store(target))

    def test_read_apis_return_coverage_latest_and_deterministic_order(self) -> None:
        self.record(scan(results=(successful("#BBB222"), successful("#AAA111"))))
        later = scan(
            "scan-2",
            kind="final",
            started="2026-09-10T07:00:00Z",
            finished="2026-09-10T07:00:05Z",
            results=(PlayerScanResult.failed(
                "#AAA111", result_code="timeout", attempted_at="2026-09-10T07:00:01Z"
            ),),
            result_code="timeout",
        )
        self.record(later)
        self.assertEqual("scan-2", get_latest_scan(self.path, self.event.event_id)["scan_id"])
        self.assertEqual(1, len(get_scans_by_kind(self.path, self.event.event_id, "final")))
        rows = load_event_player_observations(self.path, self.event.event_id)
        self.assertEqual(
            [("fictional-scan-1", "#AAA111"), ("fictional-scan-1", "#BBB222"), ("scan-2", "#AAA111")],
            [(row["scan_id"], row["player_tag"]) for row in rows],
        )

    def test_safe_summary_contains_no_identity(self) -> None:
        self.record()
        rendered = json.dumps(summarize_clan_games_store(self.path), sort_keys=True)
        self.assertNotIn("#AAA111", rendered)
        self.assertNotIn("player_tag", rendered)

    def test_backup_is_standalone_valid_and_non_overwriting(self) -> None:
        self.record()
        backup = self.root / "backup.sqlite3"
        create_validated_clan_games_backup(self.path, backup)
        validate_clan_games_store(backup)
        self.assertFalse(Path(str(backup) + "-wal").exists())
        self.assertFalse(Path(str(backup) + "-shm").exists())
        self.assert_code(
            "backup_failure",
            lambda: create_validated_clan_games_backup(self.path, backup),
        )

    def test_backup_captures_valid_wal_source_without_sidecars(self) -> None:
        self.record()
        writer = sqlite3.connect(self.path)
        try:
            writer.execute("PRAGMA journal_mode = WAL")
            writer.execute(
                "UPDATE schema_metadata SET migration_state = migration_state"
            )
            writer.commit()
            backup = self.root / "wal-backup.sqlite3"
            create_validated_clan_games_backup(self.path, backup)
        finally:
            writer.close()
        validate_clan_games_store(backup)
        self.assertFalse(Path(str(backup) + "-wal").exists())
        self.assertFalse(Path(str(backup) + "-shm").exists())

    def test_backup_overwrite_preserves_old_destination_until_valid_replace(self) -> None:
        self.record()
        backup = self.root / "backup.sqlite3"
        create_validated_clan_games_backup(self.path, backup)
        old_bytes = backup.read_bytes()
        with patch(
            "clan_analytics.clan_games_history.validate_clan_games_store",
            side_effect=[
                None,
                ClanGamesStoreError("invalid_store", "fixture invalid"),
            ],
        ):
            self.assert_code(
                "backup_failure",
                lambda: create_validated_clan_games_backup(
                    self.path, backup, overwrite=True
                ),
            )
        self.assertEqual(old_bytes, backup.read_bytes())
        create_validated_clan_games_backup(self.path, backup, overwrite=True)
        validate_clan_games_store(backup)

    def test_backup_post_replace_validation_failure_restores_old_destination(self) -> None:
        self.record()
        backup = self.root / "backup.sqlite3"
        create_validated_clan_games_backup(self.path, backup)
        old_bytes = backup.read_bytes()
        with patch(
            "clan_analytics.clan_games_history.validate_clan_games_store",
            side_effect=[
                None,
                None,
                ClanGamesStoreError("invalid_store", "fixture invalid"),
            ],
        ):
            self.assert_code(
                "backup_failure",
                lambda: create_validated_clan_games_backup(
                    self.path, backup, overwrite=True
                ),
            )
        self.assertEqual(old_bytes, backup.read_bytes())
        validate_clan_games_store(backup)

    def test_corrupt_source_cannot_replace_existing_backup(self) -> None:
        self.record()
        backup = self.root / "backup.sqlite3"
        create_validated_clan_games_backup(self.path, backup)
        old_bytes = backup.read_bytes()
        corrupt = self.root / "corrupt.sqlite3"
        corrupt.write_bytes(b"corrupt")
        self.assert_code(
            "invalid_store",
            lambda: create_validated_clan_games_backup(
                corrupt, backup, overwrite=True
            ),
        )
        self.assertEqual(old_bytes, backup.read_bytes())

    def test_public_site_path_is_rejected(self) -> None:
        self.assert_code(
            "invalid_store",
            lambda: initialize_clan_games_store(
                self.root / "site" / "clan_games.sqlite3"
            ),
        )


class ClanGamesStorageBoundaryTests(unittest.TestCase):
    def test_module_import_does_not_create_production_store_or_directory(self) -> None:
        before_store = PRODUCTION_STORE.exists()
        before_directory = PRODUCTION_STORE.parent.exists()
        import clan_analytics.clan_games_history as module

        self.assertIsNotNone(module.SCHEMA_VERSION)
        self.assertEqual(before_store, PRODUCTION_STORE.exists())
        self.assertEqual(before_directory, PRODUCTION_STORE.parent.exists())
        self.assertFalse(PRODUCTION_STORE.exists())

    def test_storage_has_no_network_token_updater_or_public_surface(self) -> None:
        import clan_analytics.clan_games_history as module

        source = inspect.getsource(module)
        for forbidden in (
            "urlopen",
            "requests.",
            "http.client",
            "Authorization",
            "update_clan_site",
            "site/data",
            "event_points",
            "clan_total",
            "event_cap",
            "player_name",
        ):
            self.assertNotIn(forbidden, source)
        self.assertEqual(
            "data/clan_games/clan_games.v1.sqlite3", LOGICAL_DATABASE_PATH
        )

    def test_only_fictional_temp_databases_are_used_by_this_test_module(self) -> None:
        source = Path(__file__).read_text(encoding="utf-8")
        self.assertIn("TemporaryDirectory", source)
        forbidden_call = "initialize_clan_games_store" + "(PRODUCTION_STORE"
        self.assertNotIn(forbidden_call, source)
        self.assertFalse(PRODUCTION_STORE.exists())

    def test_audit_contract_does_not_persist_database(self) -> None:
        self.assertFalse(any((REPO_ROOT.parent / "runs").rglob("*.sqlite3")))


if __name__ == "__main__":
    unittest.main()
