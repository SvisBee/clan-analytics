from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from clan_analytics.api.client import (  # noqa: E402
    MAX_RESPONSE_BYTES,
    HttpResponse,
    ProbeError,
    build_request_url,
    main,
    normalize_clan_tag,
)


BASE_URL = "https://fixture.clashofclans.com"
ENDPOINT_TEMPLATE = "/fixture/clans/{clan_tag}"
TOKEN_NAME = "COC_API_TOKEN"
FAKE_TOKEN = "fixture-secret-value"
VALID_RESPONSE = {
    "tag": "#DEMOCLAN",
    "name": "Example Clan",
    "clanLevel": 10,
    "memberList": [
        {
            "tag": "#DEMO001",
            "name": "Demo Player 01",
            "role": "member",
            "townHallLevel": 16,
        }
    ],
}


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


class FailingTransport:
    def get(self, url: str, **kwargs) -> HttpResponse:
        raise RuntimeError(FAKE_TOKEN)


def response_for(
    payload=VALID_RESPONSE,
    *,
    content_type: str = "application/json; charset=utf-8",
    final_url: str | None = None,
) -> HttpResponse:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return HttpResponse(200, content_type, body, final_url or build_request_url(
        BASE_URL, ENDPOINT_TEMPLATE, "#DEMOCLAN"
    ))


class ClanRosterProbeTests(unittest.TestCase):
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

    def assert_argument_value_redacted(
        self,
        arguments: list[str],
        *,
        allowed_root: Path,
    ) -> None:
        transport = FakeTransport(response_for())
        result = self.run_main(
            arguments,
            allowed_root=allowed_root,
            transport=transport,
        )
        self.assertNotEqual(result[0], 0)
        self.assertNotIn(FAKE_TOKEN, result[1])
        self.assertNotIn(FAKE_TOKEN, result[2])
        self.assertEqual(transport.calls, [])

    def test_dry_run_does_not_read_environment_or_call_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport = FakeTransport(response_for())
            target = root / "run"
            result = self.run_main(
                self.arguments(target, "--dry-run"),
                allowed_root=root,
                environ=GuardEnvironment(),
                transport=transport,
            )
            self.assertEqual(result[0], 0)
            self.assertEqual(transport.calls, [])
            self.assertFalse(target.exists())
            self.assertIn("Network executed: no", result[1])

    def test_token_value_never_appears_in_stdout_or_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport = FakeTransport(response_for())
            result = self.run_main(
                self.arguments(root / "run", "--confirm-api-contract"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
            )
            self.assertEqual(result[0], 0)
            self.assertNotIn(FAKE_TOKEN, result[1] + result[2])

    def test_token_value_cli_argument_is_rejected_without_echo(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        transport = FakeTransport(response_for())
        exit_code = main(
            ["--token", FAKE_TOKEN],
            transport=transport,
            stdout=stdout,
            stderr=stderr,
        )
        self.assertEqual(exit_code, 2)
        self.assertNotIn(FAKE_TOKEN, stdout.getvalue())
        self.assertNotIn(FAKE_TOKEN, stderr.getvalue())
        self.assertIn("--token-env", stderr.getvalue())
        self.assertEqual(transport.calls, [])

    def test_unknown_api_token_option_does_not_echo_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assert_argument_value_redacted(
                self.arguments(root / "run", "--api-token", FAKE_TOKEN),
                allowed_root=root,
            )

    def test_unknown_credential_option_does_not_echo_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assert_argument_value_redacted(
                self.arguments(root / "run", "--credential", FAKE_TOKEN),
                allowed_root=root,
            )

    def test_invalid_integer_does_not_echo_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.arguments(root / "run")
            arguments[arguments.index("15")] = FAKE_TOKEN
            self.assert_argument_value_redacted(arguments, allowed_root=root)

    def test_extra_positional_argument_does_not_echo_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assert_argument_value_redacted(
                self.arguments(root / "run", FAKE_TOKEN),
                allowed_root=root,
            )

    def test_unexpected_transport_error_does_not_expose_token(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self.run_main(
                self.arguments(root / "run", "--confirm-api-contract"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=FailingTransport(),
            )
            self.assertEqual(result[0], 2)
            self.assertNotIn(FAKE_TOKEN, result[1] + result[2])
            self.assertIn("HTTP request failed", result[2])

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
            self.assertIn("is not set", result[2])

    def test_execute_rejects_placeholder_contract_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport = FakeTransport(response_for())
            arguments = self.arguments(root / "run", "--confirm-api-contract")
            arguments[arguments.index(BASE_URL)] = "https://api-placeholder.clashofclans.com"
            arguments[arguments.index(ENDPOINT_TEMPLATE)] = "/UNVERIFIED/{clan_tag}"
            result = self.run_main(
                arguments,
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
            )
            self.assertEqual(result[0], 2)
            self.assertEqual(transport.calls, [])
            self.assertIn("placeholder API contract", result[2])

    def test_clan_tag_is_normalized(self) -> None:
        self.assertEqual(normalize_clan_tag(" demo123 "), "#DEMO123")

    def test_clan_tag_hash_is_url_encoded(self) -> None:
        url = build_request_url(BASE_URL, ENDPOINT_TEMPLATE, "#DEMOCLAN")
        self.assertIn("%23DEMOCLAN", url)
        self.assertNotIn("#DEMOCLAN", url)

    def test_invalid_clan_tag_is_rejected(self) -> None:
        for value in ("", "#A", "#BAD TAG", "#BAD/123"):
            with self.subTest(value=value), self.assertRaises(ProbeError):
                normalize_clan_tag(value)

    def test_timeout_is_required_by_parser(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = self.arguments(root / "run", "--dry-run")
            arguments[arguments.index("#DEMOCLAN")] = FAKE_TOKEN
            index = arguments.index("--timeout-seconds")
            del arguments[index : index + 2]
            result = self.run_main(arguments, allowed_root=root)
            self.assertEqual(result[0], 2)
            self.assertIn("--timeout-seconds", result[2])
            self.assertNotIn(FAKE_TOKEN, result[1] + result[2])

    def test_timeout_range_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for value in ("0", "61"):
                arguments = self.arguments(root / f"run-{value}", "--dry-run")
                arguments[arguments.index("15")] = value
                result = self.run_main(arguments, allowed_root=root)
                self.assertEqual(result[0], 2)
                self.assertIn("timeout must be between", result[2])

    def test_output_path_inside_repo_is_rejected(self) -> None:
        result = self.run_main(
            self.arguments(REPO_ROOT / "probe-output", "--dry-run"),
            allowed_root=Path(r"D:\coc\runs\api_probe"),
        )
        self.assertEqual(result[0], 2)

    def test_output_path_inside_site_is_rejected(self) -> None:
        result = self.run_main(
            self.arguments(REPO_ROOT / "site" / "data" / "probe", "--dry-run"),
            allowed_root=Path(r"D:\coc\runs\api_probe"),
        )
        self.assertEqual(result[0], 2)

    def test_existing_output_is_not_overwritten_without_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "run"
            target.mkdir()
            marker = target / "marker.txt"
            marker.write_text("preserve", encoding="utf-8")
            transport = FakeTransport(response_for())
            result = self.run_main(
                self.arguments(target, "--confirm-api-contract"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
            )
            self.assertEqual(result[0], 2)
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(transport.calls, [])

    def test_cross_host_response_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport = FakeTransport(
                response_for(final_url="https://unexpected.example/response")
            )
            result = self.run_main(
                self.arguments(root / "run", "--confirm-api-contract"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
            )
            self.assertEqual(result[0], 2)
            self.assertIn("origin differs", result[2])

    def test_non_json_content_type_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport = FakeTransport(response_for(content_type="text/html"))
            result = self.run_main(
                self.arguments(root / "run", "--confirm-api-contract"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
            )
            self.assertEqual(result[0], 2)
            self.assertIn("Content-Type", result[2])

    def test_response_that_echoes_token_is_rejected_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "run"
            transport = FakeTransport(response_for({"echo": FAKE_TOKEN}))
            result = self.run_main(
                self.arguments(target, "--confirm-api-contract"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
            )
            self.assertEqual(result[0], 2)
            self.assertNotIn(FAKE_TOKEN, result[1] + result[2])
            self.assertFalse(target.exists())

    def test_oversized_response_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport = FakeTransport(response_for(b"{" + b" " * MAX_RESPONSE_BYTES + b"}"))
            result = self.run_main(
                self.arguments(root / "run", "--confirm-api-contract"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
            )
            self.assertEqual(result[0], 2)
            self.assertIn("maximum allowed size", result[2])

    def test_malformed_json_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport = FakeTransport(response_for(b"not-json"))
            result = self.run_main(
                self.arguments(root / "run", "--confirm-api-contract"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
            )
            self.assertEqual(result[0], 2)
            self.assertIn("valid UTF-8 JSON", result[2])

    def test_success_uses_exactly_one_get_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport = FakeTransport(response_for())
            result = self.run_main(
                self.arguments(root / "run", "--confirm-api-contract"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
            )
            self.assertEqual(result[0], 0)
            self.assertEqual(len(transport.calls), 1)
            self.assertEqual(transport.calls[0][1]["timeout_seconds"], 15)

    def test_public_output_excludes_private_fields(self) -> None:
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
            public = json.loads(
                (target / "public_roster_preview.json").read_text(encoding="utf-8")
            )
            rendered = json.dumps(public)
            for forbidden in (
                "player_tag",
                "#DEMO001",
                "telegram",
                "token",
                "leadership",
                "consent",
            ):
                self.assertNotIn(forbidden, rendered)

    def test_success_writes_only_the_four_documented_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "run"
            result = self.run_main(
                self.arguments(target, "--confirm-api-contract"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=FakeTransport(response_for()),
            )
            self.assertEqual(result[0], 0)
            self.assertEqual(
                sorted(path.name for path in target.iterdir()),
                [
                    "normalized_clan.json",
                    "probe_metadata.json",
                    "public_roster_preview.json",
                    "raw_clan_response.json",
                ],
            )

    def test_runtime_has_no_hardcoded_token_value(self) -> None:
        sources = [
            SRC_ROOT / "clan_analytics" / "api" / "client.py",
            REPO_ROOT / "scripts" / "api" / "probe_clan_roster.py",
        ]
        for source in sources:
            text = source.read_text(encoding="utf-8")
            self.assertNotIn(FAKE_TOKEN, text)
            self.assertNotIn("COC_API_TOKEN=", text)


if __name__ == "__main__":
    unittest.main()
