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

from clan_analytics.api import (  # noqa: E402
    NormalizationError,
    build_public_war_log_summary,
    normalize_war_log,
)
from clan_analytics.api.client import (  # noqa: E402
    OFFICIAL_API_BASE_URL,
    HttpResponse,
    build_request_url,
)
from clan_analytics.api.war_log_probe import (  # noqa: E402
    OFFICIAL_WAR_LOG_ENDPOINT_TEMPLATE,
    OUTPUT_FILENAMES,
    main,
)


BASE_URL = OFFICIAL_API_BASE_URL
ENDPOINT_TEMPLATE = OFFICIAL_WAR_LOG_ENDPOINT_TEMPLATE
TOKEN_NAME = "COC_API_TOKEN"
FAKE_TOKEN = "fixture-secret-value"


def load_fixture():
    with (FIXTURES / "war_log.json").open(encoding="utf-8") as fixture:
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


class WarLogNormalizationTests(unittest.TestCase):
    def test_fixture_normalizes_in_response_order(self) -> None:
        normalized = normalize_war_log(
            load_fixture(),
            collected_at="2026-07-20T00:00:00Z",
            raw_source_reference="fixtures/war_log.json",
        )
        self.assertEqual(len(normalized.entries), 3)
        self.assertEqual(normalized.entries[0].result, "win")
        self.assertEqual(normalized.entries[0].clan.clan_tag, "#DEMOCLAN")
        self.assertEqual(normalized.entries[0].clan.destruction_percentage, 92.4)
        self.assertEqual(normalized.entries[1].clan.destruction_percentage, 80.0)
        self.assertIsNone(normalized.entries[2].result)

    def test_public_summary_contains_only_neutral_aggregates(self) -> None:
        normalized = normalize_war_log(
            load_fixture(),
            collected_at="2026-07-20T00:00:00Z",
            raw_source_reference="fixtures/war_log.json",
        )
        public = build_public_war_log_summary(normalized)
        self.assertEqual(public["wars_observed"], 3)
        self.assertEqual(
            public["date_range"],
            {"oldest": "2026-07-12", "newest": "2026-07-18"},
        )
        self.assertEqual(
            public["result_distribution"],
            [
                {"result": "lose", "wars": 1},
                {"result": "win", "wars": 1},
            ],
        )
        rendered = json.dumps(public)
        self.assertNotIn("#DEMOCLAN", rendered)
        self.assertNotIn("Example Clan", rendered)
        self.assertNotIn("Opponent", rendered)

    def test_empty_items_are_valid_and_explicit(self) -> None:
        normalized = normalize_war_log(
            {"items": []},
            collected_at="2026-07-20T00:00:00Z",
            raw_source_reference="inline",
        )
        public = build_public_war_log_summary(normalized)
        self.assertEqual(public["data_status"], "empty")
        self.assertEqual(public["wars_observed"], 0)

    def test_invalid_destruction_type_is_rejected(self) -> None:
        payload = load_fixture()
        payload["items"][0]["clan"]["destructionPercentage"] = "92.4"
        with self.assertRaisesRegex(
            NormalizationError,
            r"warLog\.items\[0\]\.clan\.destructionPercentage must be a number",
        ):
            normalize_war_log(
                payload,
                collected_at="2026-07-20T00:00:00Z",
                raw_source_reference="inline",
            )


class ClanWarLogProbeTests(unittest.TestCase):
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

    def test_execute_calls_only_verified_war_log_url(self) -> None:
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
                "https://api.clashofclans.com/v1/clans/%23DEMOCLAN/warlog",
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

            raw = json.loads((target / "raw_war_log_response.json").read_text())
            normalized = json.loads((target / "normalized_war_log.json").read_text())
            public = json.loads((target / "public_war_log_preview.json").read_text())
            metadata = json.loads((target / "probe_metadata.json").read_text())

            self.assertEqual(raw["items"][0]["clan"]["tag"], "#DEMOCLAN")
            self.assertEqual(normalized["entries"][0]["clan"]["clan_tag"], "#DEMOCLAN")
            public_text = json.dumps(public)
            self.assertNotIn("#DEMOCLAN", public_text)
            self.assertNotIn("Example Clan", public_text)
            self.assertEqual(metadata["request_count"], 1)
            self.assertEqual(metadata["redirects_followed"], 0)

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
            arguments[arguments.index(ENDPOINT_TEMPLATE)] = (
                "/clans/{clan_tag}/currentwar"
            )
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
            self.assertEqual(list(root.glob(".war-log-probe-staging-*")), [])

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
