from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
FIXTURES = Path(__file__).with_name("fixtures")
sys.path.insert(0, str(SRC_ROOT))

from clan_analytics.api.client import (  # noqa: E402
    OFFICIAL_API_BASE_URL,
    HttpResponse,
    build_request_url,
)
from clan_analytics.api.current_war_probe import (  # noqa: E402
    OFFICIAL_CURRENT_WAR_ENDPOINT_TEMPLATE,
    OUTPUT_FILENAMES,
    build_public_current_war_preview,
    main,
)
from clan_analytics.api.normalization import normalize_current_war  # noqa: E402


BASE_URL = OFFICIAL_API_BASE_URL
ENDPOINT_TEMPLATE = OFFICIAL_CURRENT_WAR_ENDPOINT_TEMPLATE
TOKEN_NAME = "COC_API_TOKEN"
FAKE_TOKEN = "fixture-secret-value"


def load_fixture():
    with (FIXTURES / "current_war.json").open(encoding="utf-8") as fixture:
        return json.load(fixture)


class FakeTransport:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.calls = []

    def get(self, url: str, **kwargs) -> HttpResponse:
        self.calls.append((url, kwargs))
        return self.response


class GuardEnvironment(dict):
    def get(self, key, default=None):
        raise AssertionError("dry-run must not read environment variables")


def response_for(payload=None, *, content_type="application/json; charset=utf-8"):
    body = json.dumps(load_fixture() if payload is None else payload).encode("utf-8")
    return HttpResponse(
        200,
        content_type,
        body,
        build_request_url(BASE_URL, ENDPOINT_TEMPLATE, "#DEMOCLAN"),
    )


class CurrentWarProjectionTests(unittest.TestCase):
    def test_detailed_fixture_builds_safe_member_metrics(self) -> None:
        normalized = normalize_current_war(
            load_fixture(),
            collected_at="2026-07-20T00:00:00Z",
            raw_source_reference="fixtures/current_war.json",
        )
        public = build_public_current_war_preview(normalized)
        self.assertEqual(public["state"], "inWar")
        self.assertEqual(public["participants"], 2)
        self.assertEqual(public["attacks_used"], 2)
        self.assertEqual(public["attacks_available"], 4)
        self.assertEqual(public["stars_earned"], 5)
        self.assertEqual(public["end_time"], "2026-07-20")
        self.assertEqual(public["members"][0]["war_position"], 1)
        self.assertEqual(public["members"][0]["nickname"], "Alpha")
        self.assertEqual(public["members"][0]["average_stars"], 2.5)
        self.assertEqual(public["members"][1]["war_position"], 2)

        rendered = json.dumps(public)
        self.assertNotIn("#DEMO", rendered)
        self.assertNotIn("#TARGET", rendered)
        self.assertNotIn("Fixture Opponent", rendered)
        self.assertNotIn("player_tag", rendered)

    def test_map_position_must_be_positive_and_unique(self) -> None:
        payload = load_fixture()
        payload["clan"]["members"][0]["mapPosition"] = 0
        with self.assertRaisesRegex(ValueError, "mapPosition must be one or greater"):
            normalize_current_war(
                payload,
                collected_at="2026-07-20T00:00:00Z",
                raw_source_reference="inline",
            )

        payload = load_fixture()
        payload["clan"]["members"][1]["mapPosition"] = 2
        with self.assertRaisesRegex(ValueError, "duplicate map positions"):
            normalize_current_war(
                payload,
                collected_at="2026-07-20T00:00:00Z",
                raw_source_reference="inline",
            )

    def test_not_in_war_response_is_valid_and_explicit(self) -> None:
        normalized = normalize_current_war(
            {"state": "notInWar"},
            collected_at="2026-07-20T00:00:00Z",
            raw_source_reference="inline",
        )
        public = build_public_current_war_preview(normalized)
        self.assertEqual(public["data_status"], "not_in_war")
        self.assertEqual(public["participants"], 0)
        self.assertEqual(public["members"], [])
        self.assertIsNone(public["attacks_available"])


class ClanCurrentWarProbeTests(unittest.TestCase):
    def arguments(self, output_dir: Path, *extra: str) -> list[str]:
        return [
            "--clan-tag",
            "#DEMOCLAN",
            "--token-env",
            TOKEN_NAME,
            "--output-dir",
            str(output_dir),
            "--timeout-seconds",
            "15",
            "--base-url",
            BASE_URL,
            "--endpoint-template",
            ENDPOINT_TEMPLATE,
            *extra,
        ]

    def run_main(
        self,
        arguments: list[str],
        *,
        allowed_root: Path,
        environ=None,
        transport=None,
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        exit_code = main(
            arguments,
            allowed_output_root=allowed_root,
            environ={} if environ is None else environ,
            transport=transport,
            stdout=stdout,
            stderr=stderr,
        )
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_dry_run_does_not_read_environment_or_call_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "run"
            transport = FakeTransport(response_for())
            result = self.run_main(
                self.arguments(target, "--dry-run"),
                allowed_root=root,
                environ=GuardEnvironment(),
                transport=transport,
            )
            self.assertEqual(result[0], 0)
            self.assertEqual(transport.calls, [])
            self.assertFalse(target.exists())
            self.assertIn("Requests planned: 1", result[1])
            self.assertIn("Network executed: no", result[1])

    def test_execute_calls_only_verified_current_war_url(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "run"
            transport = FakeTransport(response_for())
            result = self.run_main(
                self.arguments(target, "--confirm-api-contract"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
            )
            self.assertEqual(result[0], 0)
            self.assertEqual(len(transport.calls), 1)
            self.assertEqual(
                transport.calls[0][0],
                "https://api.clashofclans.com/v1/clans/%23DEMOCLAN/currentwar",
            )
            self.assertEqual(set(path.name for path in target.iterdir()), set(OUTPUT_FILENAMES))

    def test_output_contract_keeps_raw_internal_and_public_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "run"
            transport = FakeTransport(response_for())
            result = self.run_main(
                self.arguments(target, "--confirm-api-contract"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
            )
            self.assertEqual(result[0], 0)

            raw = json.loads((target / "raw_current_war_response.json").read_text())
            normalized = json.loads((target / "normalized_current_war.json").read_text())
            public = json.loads((target / "public_current_war_preview.json").read_text())
            metadata = json.loads((target / "probe_metadata.json").read_text())

            self.assertEqual(raw["clan"]["members"][0]["tag"], "#DEMO002")
            self.assertEqual(normalized["members"][0]["player_tag"], "#DEMO001")
            public_text = json.dumps(public)
            self.assertNotIn("#DEMO", public_text)
            self.assertNotIn("Fixture Opponent", public_text)
            self.assertEqual(metadata["request_count"], 1)
            self.assertEqual(metadata["redirects_followed"], 0)

    def test_not_in_war_response_writes_valid_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "run"
            transport = FakeTransport(response_for({"state": "notInWar"}))
            result = self.run_main(
                self.arguments(target, "--confirm-api-contract"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
            )
            self.assertEqual(result[0], 0)
            public = json.loads((target / "public_current_war_preview.json").read_text())
            self.assertEqual(public["data_status"], "not_in_war")
            self.assertEqual(public["members"], [])

    def test_token_value_never_appears_in_output_or_console(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "run"
            transport = FakeTransport(response_for())
            result = self.run_main(
                self.arguments(target, "--confirm-api-contract"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
            )
            self.assertEqual(result[0], 0)
            combined = result[1] + result[2]
            combined += "\n".join(
                path.read_text(encoding="utf-8")
                for path in target.iterdir()
            )
            self.assertNotIn(FAKE_TOKEN, combined)

    def test_missing_token_fails_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport = FakeTransport(response_for())
            result = self.run_main(
                self.arguments(root / "run", "--confirm-api-contract"),
                allowed_root=root,
                environ={},
                transport=transport,
            )
            self.assertEqual(result[0], 2)
            self.assertEqual(transport.calls, [])

    def test_wrong_endpoint_is_rejected_before_environment_and_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.arguments(root / "run", "--confirm-api-contract")
            arguments[arguments.index(ENDPOINT_TEMPLATE)] = "/clans/{clan_tag}/warlog"
            transport = FakeTransport(response_for())
            result = self.run_main(
                arguments,
                allowed_root=root,
                environ=GuardEnvironment(),
                transport=transport,
            )
            self.assertEqual(result[0], 2)
            self.assertEqual(transport.calls, [])

    def test_existing_output_fails_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "run"
            target.mkdir()
            transport = FakeTransport(response_for())
            result = self.run_main(
                self.arguments(target, "--confirm-api-contract"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
            )
            self.assertEqual(result[0], 2)
            self.assertEqual(transport.calls, [])

    def test_invalid_json_leaves_no_target_or_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "run"
            response = HttpResponse(
                200,
                "application/json",
                b"not-json",
                build_request_url(BASE_URL, ENDPOINT_TEMPLATE, "#DEMOCLAN"),
            )
            transport = FakeTransport(response)
            result = self.run_main(
                self.arguments(target, "--confirm-api-contract"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
            )
            self.assertEqual(result[0], 2)
            self.assertFalse(target.exists())
            self.assertEqual(list(root.glob(".current-war-probe-staging-*")), [])

    def test_token_cli_argument_is_rejected_without_echo(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        exit_code = main(
            ["--token", FAKE_TOKEN],
            stdout=stdout,
            stderr=stderr,
        )
        self.assertEqual(exit_code, 2)
        self.assertNotIn(FAKE_TOKEN, stdout.getvalue() + stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
