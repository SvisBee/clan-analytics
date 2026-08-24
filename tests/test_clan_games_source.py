from __future__ import annotations

import copy
import inspect
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SRC_ROOT))

from clan_analytics.api.clan_games import (  # noqa: E402
    GAMES_CHAMPION_NORMALIZATION_VERSION,
    GamesChampionSourceError,
    build_player_request_url,
    fetch_games_champion,
    normalize_games_champion_profile,
)
from clan_analytics.api.client import (  # noqa: E402
    HttpResponse,
    ProbeHttpError,
    ProbeTimeoutError,
    ProbeTransportError,
    UrllibTransport,
)


PLAYER_TAG = "#DEMO123"
PRIVATE_TEST_TAG = "#PRIVATE_PLAYER_A"
PRIVATE_TEST_NAME = "Secret Player Name"
FAKE_TOKEN = "fictional-token-never-used-on-network"
OBSERVED = datetime(2026, 8, 24, 15, 30, 12, 345678, tzinfo=timezone.utc)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def valid_profile() -> dict:
    return fixture("player_profile_games_champion_valid.json")


def response_for(payload=None, *, content_type="application/json") -> HttpResponse:
    value = valid_profile() if payload is None else payload
    body = value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
    return HttpResponse(
        status=200,
        content_type=content_type,
        body=body,
        final_url=build_player_request_url(PLAYER_TAG),
    )


class FakeTransport:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs) -> HttpResponse:
        self.calls.append((url, kwargs))
        return self.response


class FailingTransport:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def get(self, url: str, **kwargs) -> HttpResponse:
        self.calls += 1
        raise self.error


class FailingOpener:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def open(self, request, timeout):
        raise self.error


class GamesChampionNormalizerTests(unittest.TestCase):
    def normalize(self, payload: dict | None = None):
        return normalize_games_champion_profile(
            valid_profile() if payload is None else payload,
            player_tag_internal=PLAYER_TAG,
            observed_at_utc=OBSERVED,
        )

    def assert_code(self, expected: str, payload: dict) -> GamesChampionSourceError:
        with self.assertRaises(GamesChampionSourceError) as caught:
            self.normalize(payload)
        self.assertEqual(caught.exception.result_code, expected)
        return caught.exception

    def test_valid_profile_normalizes_minimal_internal_model(self) -> None:
        snapshot = self.normalize()
        self.assertEqual(snapshot.player_tag_internal, PLAYER_TAG)
        self.assertEqual(snapshot.value, 12500)
        self.assertEqual(snapshot.target, 50000)
        self.assertEqual(snapshot.observed_at_utc, "2026-08-24T15:30:12.345678Z")
        self.assertEqual(snapshot.source_kind, "official_player_profile")
        self.assertEqual(snapshot.schema_version, 1)
        self.assertEqual(
            snapshot.normalization_version, GAMES_CHAMPION_NORMALIZATION_VERSION
        )
        self.assertNotIn("Secret", repr(snapshot))
        self.assertNotIn(PLAYER_TAG, repr(snapshot))

    def test_exact_name_is_required_without_array_position_assumption(self) -> None:
        payload = valid_profile()
        payload["achievements"][1]["name"] = "games champion"
        error = self.assert_code("games_champion_missing", payload)
        self.assertNotIn(PRIVATE_TEST_NAME, str(error))

    def test_missing_achievement_has_controlled_code(self) -> None:
        self.assert_code(
            "games_champion_missing",
            fixture("player_profile_games_champion_missing.json"),
        )

    def test_duplicate_achievement_fails_closed(self) -> None:
        self.assert_code(
            "invalid_player_schema",
            fixture("player_profile_games_champion_duplicate.json"),
        )

    def test_invalid_value_types_and_negative_fail_closed(self) -> None:
        for invalid in ("12500", 12500.0, True, -1, None):
            with self.subTest(invalid=invalid):
                payload = valid_profile()
                payload["achievements"][1]["value"] = invalid
                self.assert_code("games_champion_invalid", payload)

    def test_invalid_target_types_and_negative_fail_closed(self) -> None:
        for invalid in ("50000", 50000.0, True, -1, None):
            with self.subTest(invalid=invalid):
                payload = valid_profile()
                payload["achievements"][1]["target"] = invalid
                self.assert_code("games_champion_invalid", payload)

    def test_invalid_achievements_container_fails_closed(self) -> None:
        for invalid in (None, {}, "achievements"):
            with self.subTest(invalid=invalid):
                payload = valid_profile()
                payload["achievements"] = invalid
                self.assert_code("invalid_player_schema", payload)
        payload = valid_profile()
        del payload["achievements"]
        self.assert_code("invalid_player_schema", payload)

    def test_malformed_achievement_entry_fails_closed(self) -> None:
        for invalid in (None, {}, {"name": 123}):
            with self.subTest(invalid=invalid):
                payload = valid_profile()
                payload["achievements"].insert(1, invalid)
                self.assert_code("invalid_player_schema", payload)

    def test_unknown_extra_fields_are_ignored(self) -> None:
        first = self.normalize()
        payload = valid_profile()
        payload["newTopLevel"] = {"future": True}
        payload["achievements"][1]["newAchievementField"] = [1, 2, 3]
        self.assertEqual(self.normalize(payload), first)

    def test_naive_timestamp_is_rejected(self) -> None:
        with self.assertRaises(GamesChampionSourceError) as caught:
            normalize_games_champion_profile(
                valid_profile(),
                player_tag_internal=PLAYER_TAG,
                observed_at_utc=datetime(2026, 8, 24, 15, 30),
            )
        self.assertEqual(caught.exception.result_code, "invalid_player_schema")

    def test_offset_timestamp_is_canonical_fixed_width_utc(self) -> None:
        value = datetime(
            2026, 8, 24, 18, 30, 12, 345678, tzinfo=timezone(timedelta(hours=3))
        )
        snapshot = normalize_games_champion_profile(
            valid_profile(),
            player_tag_internal=PLAYER_TAG,
            observed_at_utc=value,
        )
        self.assertEqual(snapshot.observed_at_utc, "2026-08-24T15:30:12.345678Z")

    def test_normalization_is_deterministic(self) -> None:
        self.assertEqual(self.normalize(), self.normalize(copy.deepcopy(valid_profile())))

    def test_response_identity_mismatch_fails_without_leakage(self) -> None:
        payload = valid_profile()
        payload["tag"] = "#OTHER999"
        error = self.assert_code("invalid_player_schema", payload)
        self.assertNotIn(PLAYER_TAG, str(error))
        self.assertNotIn("#OTHER999", str(error))

    def test_stars_and_text_contract_is_validated_but_not_stored(self) -> None:
        for key, invalid in (
            ("stars", True),
            ("stars", 4),
            ("info", None),
            ("completionInfo", 123),
        ):
            with self.subTest(key=key, invalid=invalid):
                payload = valid_profile()
                payload["achievements"][1][key] = invalid
                self.assert_code("games_champion_invalid", payload)
        snapshot = self.normalize()
        self.assertFalse(hasattr(snapshot, "achievement_stars"))
        self.assertFalse(hasattr(snapshot, "info"))
        self.assertFalse(hasattr(snapshot, "completion_info"))


class GamesChampionClientTests(unittest.TestCase):
    def fetch(self, transport):
        return fetch_games_champion(
            PLAYER_TAG,
            token=FAKE_TOKEN,
            transport=transport,
            clock=lambda: OBSERVED,
        )

    def test_player_request_url_encodes_hash_and_uses_official_endpoint(self) -> None:
        url = build_player_request_url(PLAYER_TAG)
        self.assertEqual(
            url, "https://api.clashofclans.com/v1/players/%23DEMO123"
        )

    def test_success_executes_exactly_one_request_and_returns_safe_result(self) -> None:
        transport = FakeTransport(response_for())
        snapshot, safe = self.fetch(transport)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(snapshot.value, 12500)
        self.assertEqual(safe.status, "success")
        self.assertEqual(safe.result_code, "success")
        self.assertEqual(safe.http_status, 200)
        self.assertEqual(safe.value_validation_status, "valid")
        self.assertEqual(safe.target_validation_status, "valid")
        self.assertNotIn(PLAYER_TAG, json.dumps(safe.to_dict()))
        self.assertNotIn(PRIVATE_TEST_NAME, json.dumps(safe.to_dict()))

    def test_http_403_has_systemic_code_and_vpn_hint(self) -> None:
        error = self.assert_fetch_error(FailingTransport(ProbeHttpError(403)))
        self.assertEqual(error.result_code, "api_http_403")
        self.assertEqual(error.http_status, 403)
        self.assertEqual(
            error.to_safe_result().operator_hint_code, "enable_approved_vpn"
        )

    def test_other_http_status_uses_existing_health_equivalent(self) -> None:
        error = self.assert_fetch_error(FailingTransport(ProbeHttpError(429)))
        self.assertEqual(error.result_code, "api_http_other")
        self.assertEqual(error.http_status, 429)

    def test_timeout_is_distinct_from_transport_failure(self) -> None:
        error = self.assert_fetch_error(
            FailingTransport(ProbeTimeoutError("fixture timeout"))
        )
        self.assertEqual(error.result_code, "timeout")

    def test_transport_failure_uses_existing_health_equivalent(self) -> None:
        error = self.assert_fetch_error(
            FailingTransport(ProbeTransportError("fixture transport failure"))
        )
        self.assertEqual(error.result_code, "api_transport_failure")

    def test_default_transport_classifies_http_403(self) -> None:
        error = HTTPError(
            build_player_request_url(PLAYER_TAG), 403, "Forbidden", {}, None
        )
        with patch(
            "clan_analytics.api.client.build_opener",
            return_value=FailingOpener(error),
        ):
            caught = self.assert_default_fetch_error()
        self.assertEqual(caught.result_code, "api_http_403")

    def test_default_transport_classifies_timeout(self) -> None:
        with patch(
            "clan_analytics.api.client.build_opener",
            return_value=FailingOpener(URLError(TimeoutError("fixture timeout"))),
        ):
            caught = self.assert_default_fetch_error()
        self.assertEqual(caught.result_code, "timeout")

    def test_default_transport_classifies_other_transport_failure(self) -> None:
        with patch(
            "clan_analytics.api.client.build_opener",
            return_value=FailingOpener(URLError(OSError("fixture transport"))),
        ):
            caught = self.assert_default_fetch_error()
        self.assertEqual(caught.result_code, "api_transport_failure")

    def test_unexpected_transport_exception_is_safe(self) -> None:
        error = self.assert_fetch_error(FailingTransport(RuntimeError(FAKE_TOKEN)))
        self.assertEqual(error.result_code, "unexpected_error")
        self.assertNotIn(FAKE_TOKEN, str(error))

    def test_unexpected_clock_exception_is_safe(self) -> None:
        with self.assertRaises(GamesChampionSourceError) as caught:
            fetch_games_champion(
                PLAYER_TAG,
                token=FAKE_TOKEN,
                transport=FakeTransport(response_for()),
                clock=lambda: (_ for _ in ()).throw(RuntimeError(PRIVATE_TEST_NAME)),
            )
        self.assertEqual(caught.exception.result_code, "unexpected_error")
        self.assertNotIn(PRIVATE_TEST_NAME, str(caught.exception))

    def test_malformed_transport_response_is_safe(self) -> None:
        transport = FakeTransport(response_for())
        transport.response = object()
        error = self.assert_fetch_error(transport)
        self.assertEqual(error.result_code, "unexpected_error")

    def test_invalid_json_has_distinct_code(self) -> None:
        error = self.assert_fetch_error(FakeTransport(response_for(b"not-json")))
        self.assertEqual(error.result_code, "invalid_json")

    def test_invalid_content_type_is_safe_schema_failure(self) -> None:
        error = self.assert_fetch_error(
            FakeTransport(response_for(content_type="text/html"))
        )
        self.assertEqual(error.result_code, "invalid_player_schema")

    def test_normalization_failure_preserves_safe_metadata(self) -> None:
        payload = fixture("player_profile_games_champion_missing.json")
        error = self.assert_fetch_error(FakeTransport(response_for(payload)))
        safe = error.to_safe_result().to_dict()
        self.assertEqual(error.result_code, "games_champion_missing")
        self.assertEqual(safe["http_status"], 200)
        self.assertEqual(safe["observed_at_utc"], "2026-08-24T15:30:12.345678Z")
        self.assertNotIn(PLAYER_TAG, json.dumps(safe))
        self.assertNotIn(PRIVATE_TEST_NAME, json.dumps(safe))

    def test_invalid_private_test_identity_never_appears_in_error(self) -> None:
        with self.assertRaises(GamesChampionSourceError) as caught:
            fetch_games_champion(
                PRIVATE_TEST_TAG,
                token=FAKE_TOKEN,
                transport=FakeTransport(response_for()),
                clock=lambda: OBSERVED,
            )
        self.assertNotIn(PRIVATE_TEST_TAG, str(caught.exception))
        self.assertNotIn(PRIVATE_TEST_TAG, json.dumps(caught.exception.to_safe_result().to_dict()))

    def test_client_has_no_raw_persistence_or_debug_dump_surface(self) -> None:
        import clan_analytics.api.clan_games as module

        source = inspect.getsource(module)
        for forbidden in (
            "write_text(",
            "write_bytes(",
            "open(",
            "raw_profile",
            "debug_dump",
            "site/data",
        ):
            self.assertNotIn(forbidden, source)

    def test_updater_remains_three_probe_flow_without_player_endpoint(self) -> None:
        updater = (REPO_ROOT / "scripts" / "update" / "update_clan_site.ps1").read_text(
            encoding="utf-8-sig"
        )
        self.assertNotIn("/players/", updater)
        self.assertNotIn("fetch_games_champion", updater)
        self.assertNotIn("GamesChampion", updater)
        # Each existing probe appears once in required-file preflight and once
        # in its single normal-flow invocation.
        self.assertEqual(updater.count("run_clan_roster_probe.ps1"), 2)
        self.assertEqual(updater.count("run_clan_current_war_probe.ps1"), 2)
        self.assertEqual(updater.count("run_clan_war_log_probe.ps1"), 2)

    def test_client_owns_no_retry_or_concurrency_policy(self) -> None:
        import clan_analytics.api.clan_games as module

        source = inspect.getsource(module)
        for forbidden in (
            "ThreadPool",
            "ProcessPool",
            "asyncio",
            "retry",
            "sleep(",
        ):
            self.assertNotIn(forbidden, source)

    def assert_fetch_error(self, transport) -> GamesChampionSourceError:
        with self.assertRaises(GamesChampionSourceError) as caught:
            self.fetch(transport)
        rendered = str(caught.exception)
        self.assertNotIn(PLAYER_TAG, rendered)
        self.assertNotIn(PRIVATE_TEST_NAME, rendered)
        self.assertNotIn(FAKE_TOKEN, rendered)
        return caught.exception

    def assert_default_fetch_error(self) -> GamesChampionSourceError:
        with self.assertRaises(GamesChampionSourceError) as caught:
            fetch_games_champion(
                PLAYER_TAG,
                token=FAKE_TOKEN,
                transport=UrllibTransport(),
                clock=lambda: OBSERVED,
            )
        self.assertNotIn(PLAYER_TAG, str(caught.exception))
        self.assertNotIn(FAKE_TOKEN, str(caught.exception))
        return caught.exception


if __name__ == "__main__":
    unittest.main()
