from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
CLI = REPO_ROOT / "scripts" / "clan_games" / "manage_event_registry.py"
PRODUCTION_REGISTRY = (
    REPO_ROOT.parent / "data" / "clan_games" / "event_registry.v1.json"
)
sys.path.insert(0, str(SRC_ROOT))

from clan_analytics.clan_games_events import (  # noqa: E402
    REGISTRY_LOGICAL_PATH,
    ClanGamesEvent,
    EventRegistryError,
    get_active_event,
    get_event,
    get_upcoming_event,
    initialize_event_registry,
    list_events,
    load_event_registry,
    register_event,
    replace_event,
    validate_event_registry,
)


FICTIONAL_SOURCE = (
    "https://supercell.com/en/games/clashofclans/blog/news/"
    "fictional-clan-games-evidence/"
)
CONFIRMED = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def event(
    event_id: str = "fictional-alpha",
    *,
    start: str = "2026-09-10T09:00:00+03:00",
    end: str = "2026-09-16T09:00:00+03:00",
    source: str = FICTIONAL_SOURCE,
    confirmed: datetime | str = CONFIRMED,
) -> ClanGamesEvent:
    return ClanGamesEvent.create(
        event_id=event_id,
        start_at=start,
        end_at=end,
        official_source_url=source,
        confirmed_at=confirmed,
    )


class EventModelTests(unittest.TestCase):
    def test_valid_event_normalizes_offsets_and_is_immutable(self) -> None:
        value = event()
        self.assertEqual(value.start_at_utc, "2026-09-10T06:00:00.000000Z")
        self.assertEqual(value.end_at_utc, "2026-09-16T06:00:00.000000Z")
        self.assertEqual(value.confirmed_at_utc, "2026-08-20T12:00:00.000000Z")
        self.assertEqual(value.duration_seconds, 6 * 24 * 60 * 60)
        with self.assertRaises(Exception):
            value.event_id = "changed"  # type: ignore[misc]

    def test_naive_timestamps_are_rejected(self) -> None:
        for field in ("start_at", "end_at", "confirmed_at"):
            kwargs = {
                "event_id": "fictional-alpha",
                "start_at": "2026-09-10T06:00:00Z",
                "end_at": "2026-09-16T06:00:00Z",
                "official_source_url": FICTIONAL_SOURCE,
                "confirmed_at": "2026-08-20T12:00:00Z",
            }
            kwargs[field] = datetime(2026, 9, 1, 12, 0)
            with self.subTest(field=field), self.assertRaises(EventRegistryError) as caught:
                ClanGamesEvent.create(**kwargs)
            self.assertEqual(caught.exception.result_code, "invalid_event")

    def test_equal_or_reversed_window_is_rejected(self) -> None:
        for end in ("2026-09-10T06:00:00Z", "2026-09-09T06:00:00Z"):
            with self.subTest(end=end), self.assertRaises(EventRegistryError) as caught:
                event(start="2026-09-10T06:00:00Z", end=end)
            self.assertEqual(caught.exception.result_code, "invalid_event")

    def test_event_id_contract_is_bounded_and_not_date_semantic(self) -> None:
        self.assertEqual(event("simple_7-event").event_id, "simple_7-event")
        for invalid in (
            "",
            "Uppercase",
            "contains space",
            "-leading",
            "_leading",
            "a" * 65,
            "слово",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(EventRegistryError) as caught:
                event(invalid)
            self.assertEqual(caught.exception.result_code, "invalid_event")

    def test_only_proven_canonical_supercell_https_urls_are_accepted(self) -> None:
        valid = event(source="HTTPS://SUPERCELL.COM/evidence/page")
        self.assertEqual(valid.official_source_url, "https://supercell.com/evidence/page")
        invalid_urls = (
            "http://supercell.com/evidence",
            "https://www.supercell.com/evidence",
            "https://example.com/evidence",
            "https://user:password@supercell.com/evidence",
            "https://supercell.com:443/evidence",
            "https://supercell.com/evidence?token=fictional",
            "https://supercell.com/evidence#fragment",
            "https://localhost/evidence",
            "https://127.0.0.1/evidence",
        )
        for invalid in invalid_urls:
            with self.subTest(url=invalid), self.assertRaises(EventRegistryError) as caught:
                event(source=invalid)
            self.assertEqual(caught.exception.result_code, "invalid_official_source")

    def test_status_boundaries_are_start_inclusive_end_exclusive(self) -> None:
        value = event(
            start="2026-09-10T06:00:00Z", end="2026-09-11T06:00:00Z"
        )
        cases = (
            ("2026-09-10T05:59:59.999999Z", "upcoming"),
            ("2026-09-10T06:00:00.000000Z", "active"),
            ("2026-09-10T12:00:00Z", "active"),
            ("2026-09-11T06:00:00.000000Z", "ended"),
            ("2026-09-12T06:00:00Z", "ended"),
        )
        for as_of, expected in cases:
            with self.subTest(as_of=as_of):
                self.assertEqual(value.status(as_of), expected)


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "event_registry.v1.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def init(self, path: Path | None = None) -> Path:
        target = path or self.path
        result = initialize_event_registry(target)
        self.assertEqual(result.result_code, "registry_created")
        return target

    def test_initialization_is_empty_atomic_and_deterministic(self) -> None:
        self.init()
        self.assertEqual(
            self.path.read_text(encoding="utf-8"),
            '{\n  "events": [],\n  "schema_version": 1\n}\n',
        )
        self.assertEqual(load_event_registry(self.path).events, ())

    def test_initialization_is_idempotent_for_valid_existing_registry(self) -> None:
        self.init()
        original = self.path.read_bytes()
        result = initialize_event_registry(self.path)
        self.assertEqual(result.result_code, "no_change")
        self.assertEqual(self.path.read_bytes(), original)

    def test_invalid_existing_registry_is_not_overwritten(self) -> None:
        self.path.write_bytes(b"{not-json")
        original = self.path.read_bytes()
        with self.assertRaises(EventRegistryError) as caught:
            initialize_event_registry(self.path)
        self.assertEqual(caught.exception.result_code, "invalid_registry")
        self.assertEqual(self.path.read_bytes(), original)

    def test_missing_registry_has_controlled_result(self) -> None:
        with self.assertRaises(EventRegistryError) as caught:
            load_event_registry(self.path)
        self.assertEqual(caught.exception.result_code, "registry_not_found")

    def test_initialization_write_failure_leaves_no_registry_or_temp(self) -> None:
        with patch(
            "clan_analytics.clan_games_events.os.link",
            side_effect=OSError("fictional link failure"),
        ):
            with self.assertRaises(EventRegistryError) as caught:
                initialize_event_registry(self.path)
        self.assertEqual(caught.exception.result_code, "write_failure")
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_import_has_no_production_registry_side_effect(self) -> None:
        before = PRODUCTION_REGISTRY.exists()
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                f"import sys; sys.path.insert(0, {str(SRC_ROOT)!r}); import clan_analytics.clan_games_events",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(PRODUCTION_REGISTRY.exists(), before)

    def test_strict_registry_shape_version_and_event_fields(self) -> None:
        base = {
            "schema_version": 1,
            "events": [event().to_dict()],
        }
        invalid_values = []
        root_extra = copy.deepcopy(base)
        root_extra["extra"] = True
        invalid_values.append(root_extra)
        future = copy.deepcopy(base)
        future["schema_version"] = 2
        invalid_values.append(future)
        events_object = copy.deepcopy(base)
        events_object["events"] = {}
        invalid_values.append(events_object)
        event_extra = copy.deepcopy(base)
        event_extra["events"][0]["active"] = True
        invalid_values.append(event_extra)
        noncanonical = copy.deepcopy(base)
        noncanonical["events"][0]["start_at_utc"] = "2026-09-10T06:00:00Z"
        invalid_values.append(noncanonical)
        noncanonical_url = copy.deepcopy(base)
        noncanonical_url["events"][0]["official_source_url"] = (
            "HTTPS://SUPERCELL.COM/evidence"
        )
        invalid_values.append(noncanonical_url)
        for index, payload in enumerate(invalid_values):
            with self.subTest(index=index):
                self.path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(EventRegistryError) as caught:
                    load_event_registry(self.path)
                self.assertEqual(caught.exception.result_code, "invalid_registry")

    def test_first_and_second_nonoverlapping_events_sort_deterministically(self) -> None:
        self.init()
        later = event(
            "fictional-later",
            start="2026-10-10T00:00:00Z",
            end="2026-10-12T00:00:00Z",
        )
        earlier = event(
            "fictional-earlier",
            start="2026-09-10T00:00:00Z",
            end="2026-09-12T00:00:00Z",
        )
        self.assertEqual(register_event(self.path, later).result_code, "success")
        self.assertEqual(register_event(self.path, earlier).result_code, "success")
        registry = load_event_registry(self.path)
        self.assertEqual(
            [item.event_id for item in registry.events],
            ["fictional-earlier", "fictional-later"],
        )

    def test_exact_registration_retry_is_no_change_and_byte_stable(self) -> None:
        self.init()
        value = event()
        register_event(self.path, value)
        original = self.path.read_bytes()
        result = register_event(self.path, value)
        self.assertEqual(result.result_code, "no_change")
        self.assertEqual(self.path.read_bytes(), original)

    def test_registration_write_failure_preserves_original(self) -> None:
        self.init()
        original = self.path.read_bytes()
        with patch(
            "clan_analytics.clan_games_events._write_atomic_replace",
            side_effect=EventRegistryError("write_failure", "fictional write failure"),
        ):
            with self.assertRaises(EventRegistryError) as caught:
                register_event(self.path, event())
        self.assertEqual(caught.exception.result_code, "write_failure")
        self.assertEqual(self.path.read_bytes(), original)

    def test_duplicate_event_id_with_changed_fields_is_conflict(self) -> None:
        self.init()
        register_event(self.path, event())
        original = self.path.read_bytes()
        with self.assertRaises(EventRegistryError) as caught:
            register_event(
                self.path,
                event(end="2026-09-17T06:00:00Z"),
            )
        self.assertEqual(caught.exception.result_code, "duplicate_event_id")
        self.assertEqual(self.path.read_bytes(), original)

    def test_overlap_is_rejected_and_touching_boundaries_are_allowed(self) -> None:
        self.init()
        register_event(
            self.path,
            event(
                "fictional-first",
                start="2026-09-10T00:00:00Z",
                end="2026-09-12T00:00:00Z",
            ),
        )
        original = self.path.read_bytes()
        with self.assertRaises(EventRegistryError) as caught:
            register_event(
                self.path,
                event(
                    "fictional-overlap",
                    start="2026-09-11T00:00:00Z",
                    end="2026-09-13T00:00:00Z",
                ),
            )
        self.assertEqual(caught.exception.result_code, "event_overlap")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(
            register_event(
                self.path,
                event(
                    "fictional-touching",
                    start="2026-09-12T00:00:00Z",
                    end="2026-09-14T00:00:00Z",
                ),
            ).result_code,
            "success",
        )

    def test_active_and_upcoming_lookup_across_multiple_events(self) -> None:
        self.init()
        first = event(
            "fictional-first",
            start="2026-09-10T00:00:00Z",
            end="2026-09-12T00:00:00Z",
        )
        second = event(
            "fictional-second",
            start="2026-10-10T00:00:00Z",
            end="2026-10-12T00:00:00Z",
        )
        register_event(self.path, second)
        register_event(self.path, first)
        registry = load_event_registry(self.path)
        cases = (
            ("2026-09-09T00:00:00Z", None, "fictional-first"),
            ("2026-09-10T00:00:00Z", "fictional-first", "fictional-second"),
            ("2026-09-11T00:00:00Z", "fictional-first", "fictional-second"),
            ("2026-09-12T00:00:00Z", None, "fictional-second"),
            ("2026-10-10T00:00:00Z", "fictional-second", None),
            ("2026-10-12T00:00:00Z", None, None),
        )
        for as_of, active_id, upcoming_id in cases:
            with self.subTest(as_of=as_of):
                active = get_active_event(registry, as_of)
                upcoming = get_upcoming_event(registry, as_of)
                self.assertEqual(active.event_id if active else None, active_id)
                self.assertEqual(upcoming.event_id if upcoming else None, upcoming_id)

    def test_ended_events_are_retained_and_list_is_immutable(self) -> None:
        self.init()
        value = event()
        register_event(self.path, value)
        registry = load_event_registry(self.path)
        self.assertEqual(value.status("2027-01-01T00:00:00Z"), "ended")
        self.assertEqual(list_events(registry), (value,))
        self.assertEqual(get_event(registry, value.event_id), value)

    def test_explicit_replace_creates_validated_exact_backup(self) -> None:
        self.init()
        original_event = event()
        register_event(self.path, original_event)
        original_bytes = self.path.read_bytes()
        replacement = event(
            end="2026-09-17T06:00:00Z",
            confirmed=CONFIRMED + timedelta(days=1),
        )
        fixed_clock = lambda: datetime(2026, 8, 21, 12, 0, 0, 123456, tzinfo=timezone.utc)
        result = replace_event(
            self.path,
            replacement,
            explicit_replace=True,
            clock=fixed_clock,
        )
        self.assertEqual(result.result_code, "success")
        self.assertEqual(load_event_registry(self.path).events, (replacement,))
        backup = self.root / str(result.backup_logical_path)
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.read_bytes(), original_bytes)
        self.assertEqual(load_event_registry(backup).events, (original_event,))

    def test_replace_requires_explicit_intent_and_existing_id(self) -> None:
        self.init()
        original = event()
        register_event(self.path, original)
        original_bytes = self.path.read_bytes()
        with self.assertRaises(EventRegistryError) as caught:
            replace_event(self.path, event(end="2026-09-17T06:00:00Z"))
        self.assertEqual(caught.exception.result_code, "event_conflict")
        with self.assertRaises(EventRegistryError) as missing:
            replace_event(
                self.path,
                event("fictional-missing"),
                explicit_replace=True,
            )
        self.assertEqual(missing.exception.result_code, "replace_requires_existing")
        self.assertEqual(self.path.read_bytes(), original_bytes)

    def test_replacement_overlap_is_rejected_before_backup_or_write(self) -> None:
        self.init()
        first = event(
            "fictional-first",
            start="2026-09-10T00:00:00Z",
            end="2026-09-12T00:00:00Z",
        )
        second = event(
            "fictional-second",
            start="2026-09-15T00:00:00Z",
            end="2026-09-17T00:00:00Z",
        )
        register_event(self.path, first)
        register_event(self.path, second)
        original = self.path.read_bytes()
        overlapping = event(
            "fictional-second",
            start="2026-09-11T00:00:00Z",
            end="2026-09-16T00:00:00Z",
            confirmed=CONFIRMED + timedelta(days=1),
        )
        with self.assertRaises(EventRegistryError) as caught:
            replace_event(self.path, overlapping, explicit_replace=True)
        self.assertEqual(caught.exception.result_code, "event_overlap")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse((self.root / "backups").exists())

    def test_failed_replacement_keeps_original_byte_identical(self) -> None:
        self.init()
        register_event(self.path, event())
        original = self.path.read_bytes()
        replacement = event(
            end="2026-09-17T06:00:00Z",
            confirmed=CONFIRMED + timedelta(days=1),
        )
        with patch(
            "clan_analytics.clan_games_events._write_atomic_replace",
            side_effect=EventRegistryError("write_failure", "fictional write failure"),
        ):
            with self.assertRaises(EventRegistryError) as caught:
                replace_event(self.path, replacement, explicit_replace=True)
        self.assertEqual(caught.exception.result_code, "write_failure")
        self.assertEqual(self.path.read_bytes(), original)

    def test_backup_collision_fails_without_overwriting_backup_or_registry(self) -> None:
        self.init()
        register_event(self.path, event())
        fixed = lambda: datetime(2026, 8, 21, 12, 0, 0, 123456, tzinfo=timezone.utc)
        first_replacement = event(
            end="2026-09-17T06:00:00Z",
            confirmed=CONFIRMED + timedelta(days=1),
        )
        first_result = replace_event(
            self.path, first_replacement, explicit_replace=True, clock=fixed
        )
        backup = self.root / str(first_result.backup_logical_path)
        backup_bytes = backup.read_bytes()
        current_bytes = self.path.read_bytes()
        second_replacement = event(
            end="2026-09-18T06:00:00Z",
            confirmed=CONFIRMED + timedelta(days=2),
        )
        with self.assertRaises(EventRegistryError) as caught:
            replace_event(
                self.path, second_replacement, explicit_replace=True, clock=fixed
            )
        self.assertEqual(caught.exception.result_code, "backup_failure")
        self.assertEqual(backup.read_bytes(), backup_bytes)
        self.assertEqual(self.path.read_bytes(), current_bytes)

    def test_insertion_order_is_byte_independent(self) -> None:
        first_path = self.root / "first" / "event_registry.v1.json"
        second_path = self.root / "second" / "event_registry.v1.json"
        initialize_event_registry(first_path)
        initialize_event_registry(second_path)
        first = event(
            "fictional-first",
            start="2026-09-10T00:00:00Z",
            end="2026-09-12T00:00:00Z",
        )
        second = event(
            "fictional-second",
            start="2026-10-10T00:00:00Z",
            end="2026-10-12T00:00:00Z",
        )
        register_event(first_path, first)
        register_event(first_path, second)
        register_event(second_path, second)
        register_event(second_path, first)
        self.assertEqual(first_path.read_bytes(), second_path.read_bytes())

    def test_repeated_validation_is_read_only(self) -> None:
        self.init()
        register_event(self.path, event())
        original = self.path.read_bytes()
        first = validate_event_registry(self.path)
        second = validate_event_registry(self.path)
        self.assertEqual(first, second)
        self.assertEqual(self.path.read_bytes(), original)

    def test_unsorted_duplicate_and_overlapping_manual_payloads_fail_closed(self) -> None:
        first = event(
            "fictional-first",
            start="2026-09-10T00:00:00Z",
            end="2026-09-12T00:00:00Z",
        )
        second = event(
            "fictional-second",
            start="2026-10-10T00:00:00Z",
            end="2026-10-12T00:00:00Z",
        )
        payloads = [
            {"schema_version": 1, "events": [second.to_dict(), first.to_dict()]},
            {"schema_version": 1, "events": [first.to_dict(), first.to_dict()]},
            {
                "schema_version": 1,
                "events": [
                    first.to_dict(),
                    event(
                        "fictional-overlap",
                        start="2026-09-11T00:00:00Z",
                        end="2026-09-13T00:00:00Z",
                    ).to_dict(),
                ],
            },
        ]
        for index, payload in enumerate(payloads):
            with self.subTest(index=index):
                self.path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(EventRegistryError) as caught:
                    load_event_registry(self.path)
                self.assertEqual(caught.exception.result_code, "invalid_registry")


class RegistryCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "event_registry.v1.json"
        self.production_existed_before = PRODUCTION_REGISTRY.exists()

    def tearDown(self) -> None:
        self.assertEqual(PRODUCTION_REGISTRY.exists(), self.production_existed_before)
        self.temporary.cleanup()

    def run_cli(self, *args: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        command = [
            sys.executable,
            str(CLI),
            "--test-registry",
            str(self.path),
            *args,
        ]
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        rendered = result.stdout if result.returncode == 0 else result.stderr
        payload = json.loads(rendered.strip().splitlines()[-1])
        self.assertNotIn(str(self.path), result.stdout)
        self.assertNotIn(str(self.path), result.stderr)
        self.assertEqual(payload["registry"], "test-registry/event_registry.v1.json")
        return result, payload

    def event_args(self, *, end: str = "2026-09-16T06:00:00Z") -> tuple[str, ...]:
        return (
            "--event-id",
            "fictional-cli",
            "--start",
            "2026-09-10T06:00:00Z",
            "--end",
            end,
            "--official-source-url",
            FICTIONAL_SOURCE,
        )

    def test_cli_init_validate_register_status_list_and_exact_retry(self) -> None:
        result, payload = self.run_cli("init")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["result_code"], "registry_created")
        result, payload = self.run_cli("validate")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["event_count"], 0)
        result, payload = self.run_cli("register", *self.event_args())
        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["result_code"], "success")
        original = self.path.read_bytes()
        result, payload = self.run_cli("register", *self.event_args())
        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["result_code"], "no_change")
        self.assertEqual(self.path.read_bytes(), original)
        result, payload = self.run_cli(
            "status", "--as-of", "2026-09-11T00:00:00Z"
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["active_event"]["event_id"], "fictional-cli")
        self.assertIsNone(payload["upcoming_event"])
        result, payload = self.run_cli("list")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(payload["events"]), 1)

    def test_cli_replace_requires_flag_then_creates_backup(self) -> None:
        self.run_cli("init")
        self.run_cli("register", *self.event_args())
        original = self.path.read_bytes()
        result, payload = self.run_cli(
            "replace", *self.event_args(end="2026-09-17T06:00:00Z")
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(payload["result_code"], "event_conflict")
        self.assertEqual(self.path.read_bytes(), original)
        result, payload = self.run_cli(
            "replace",
            *self.event_args(end="2026-09-17T06:00:00Z"),
            "--confirm-replace",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["result_code"], "success")
        backups = list((self.root / "backups" / "event_registry").glob("*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)

    def test_cli_duplicate_conflict_and_invalid_source_have_safe_exit_codes(self) -> None:
        self.run_cli("init")
        self.run_cli("register", *self.event_args())
        result, payload = self.run_cli(
            "register", *self.event_args(end="2026-09-18T06:00:00Z")
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(payload["result_code"], "duplicate_event_id")
        result, payload = self.run_cli(
            "register",
            "--event-id",
            "fictional-other",
            "--start",
            "2026-10-10T06:00:00Z",
            "--end",
            "2026-10-11T06:00:00Z",
            "--official-source-url",
            "https://example.com/not-official",
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(payload["result_code"], "invalid_official_source")

    def test_cli_test_path_guard_rejects_noncanonical_filename_without_leak(self) -> None:
        bad_path = self.root / "arbitrary.json"
        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--test-registry",
                str(bad_path),
                "init",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(str(bad_path), result.stderr)
        payload = json.loads(result.stderr.strip().splitlines()[-1])
        self.assertEqual(payload["result_code"], "invalid_registry")
        self.assertFalse(bad_path.exists())

    def test_cli_and_module_do_not_reference_public_or_updater_paths(self) -> None:
        source = (REPO_ROOT / "src" / "clan_analytics" / "clan_games_events.py").read_text(
            encoding="utf-8"
        )
        cli_source = CLI.read_text(encoding="utf-8")
        for forbidden in (
            "site/data",
            "update_clan_site",
            "fetch_games_champion",
            "player_tag",
            "sqlite",
            "Authorization",
            "urlopen",
            "requests.",
            "http.client",
            "event_cap",
            "recurrence",
            "22-28",
            "delete",
        ):
            self.assertNotIn(forbidden, source)
            self.assertNotIn(forbidden, cli_source)
        self.assertEqual(REGISTRY_LOGICAL_PATH, "data/clan_games/event_registry.v1.json")


if __name__ == "__main__":
    unittest.main()
