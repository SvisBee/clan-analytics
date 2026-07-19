from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from clan_analytics.api.client import (  # noqa: E402
    MAX_RESPONSE_BYTES,
    HttpResponse,
    ProbeError,
    _OutputFilesystem,
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


class ObservingFilesystem(_OutputFilesystem):
    def __init__(self) -> None:
        self.staging_ready = False

    def rename(self, source: Path, destination: Path) -> None:
        if source.name.startswith(".clan-probe-staging-"):
            if destination.exists():
                raise AssertionError("target appeared before staging publication")
            self.staging_ready = sorted(path.name for path in source.iterdir()) == sorted(
                (
                    "raw_clan_response.json",
                    "probe_metadata.json",
                    "normalized_clan.json",
                    "public_roster_preview.json",
                )
            )
        super().rename(source, destination)


class FailingWriteFilesystem(_OutputFilesystem):
    def __init__(self, fail_at: int) -> None:
        self.fail_at = fail_at
        self.write_count = 0

    def write_exclusive(self, path: Path, content: bytes) -> None:
        self.write_count += 1
        if self.write_count == self.fail_at:
            raise OSError("injected write failure")
        super().write_exclusive(path, content)


class FailingPublishFilesystem(_OutputFilesystem):
    def rename(self, source: Path, destination: Path) -> None:
        if source.name.startswith(".clan-probe-staging-"):
            raise OSError("injected publication failure")
        super().rename(source, destination)


class FailingBackupCleanupFilesystem(_OutputFilesystem):
    def remove_tree(self, path: Path) -> None:
        if path.name.startswith(".clan-probe-backup-"):
            raise OSError("injected backup cleanup failure")
        super().remove_tree(path)


class FailingRollbackFilesystem(_OutputFilesystem):
    def rename(self, source: Path, destination: Path) -> None:
        if source.name.startswith((".clan-probe-staging-", ".clan-probe-backup-")):
            raise OSError("injected publication or recovery failure")
        super().rename(source, destination)


class ExtraStagingFileFilesystem(_OutputFilesystem):
    def __init__(self) -> None:
        self.write_count = 0

    def write_exclusive(self, path: Path, content: bytes) -> None:
        super().write_exclusive(path, content)
        self.write_count += 1
        if self.write_count == 4:
            (path.parent / "unexpected.json").write_text("{}", encoding="utf-8")


class InvalidStagingJsonFilesystem(_OutputFilesystem):
    def write_exclusive(self, path: Path, content: bytes) -> None:
        super().write_exclusive(path, content)
        if path.name == "raw_clan_response.json":
            path.write_bytes(b"not-json")


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
        filesystem=None,
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        exit_code = main(
            arguments,
            allowed_output_root=allowed_root,
            environ={} if environ is None else environ,
            transport=transport,
            filesystem=filesystem,
            stdout=stdout,
            stderr=stderr,
        )
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def transaction_paths(self, root: Path, kind: str) -> list[Path]:
        return list(root.glob(f".clan-probe-{kind}-*"))

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
            before = marker.read_bytes()
            transport = FakeTransport(response_for())
            result = self.run_main(
                self.arguments(target, "--confirm-api-contract"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
            )
            self.assertEqual(result[0], 2)
            self.assertEqual(marker.read_bytes(), before)
            self.assertEqual(transport.calls, [])
            self.assertEqual(self.transaction_paths(root, "staging"), [])

    def test_new_run_is_published_only_after_complete_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "run"
            filesystem = ObservingFilesystem()
            transport = FakeTransport(response_for())
            result = self.run_main(
                self.arguments(target, "--confirm-api-contract"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
                filesystem=filesystem,
            )
            self.assertEqual(result[0], 0)
            self.assertTrue(filesystem.staging_ready)
            self.assertEqual(len(transport.calls), 1)
            self.assertEqual(len(list(target.iterdir())), 4)
            self.assertEqual(self.transaction_paths(root, "staging"), [])
            self.assertEqual(self.transaction_paths(root, "backup"), [])

    def test_second_file_write_failure_leaves_no_partial_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "run"
            transport = FakeTransport(response_for())
            result = self.run_main(
                self.arguments(target, "--confirm-api-contract"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
                filesystem=FailingWriteFilesystem(2),
            )
            self.assertEqual(result[0], 2)
            self.assertFalse(target.exists())
            self.assertEqual(self.transaction_paths(root, "staging"), [])
            self.assertEqual(len(transport.calls), 1)

    def test_fourth_file_write_failure_leaves_no_partial_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "run"
            transport = FakeTransport(response_for())
            result = self.run_main(
                self.arguments(target, "--confirm-api-contract"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
                filesystem=FailingWriteFilesystem(4),
            )
            self.assertEqual(result[0], 2)
            self.assertFalse(target.exists())
            self.assertEqual(self.transaction_paths(root, "staging"), [])
            self.assertEqual(len(transport.calls), 1)

    def test_serialization_failure_happens_before_filesystem_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "run"
            transport = FakeTransport(response_for())
            with patch(
                "clan_analytics.api.client._serialize_json",
                side_effect=ProbeError("output JSON serialization failed"),
            ):
                result = self.run_main(
                    self.arguments(target, "--confirm-api-contract"),
                    allowed_root=root,
                    environ={TOKEN_NAME: FAKE_TOKEN},
                    transport=transport,
                )
            self.assertEqual(result[0], 2)
            self.assertFalse(target.exists())
            self.assertEqual(self.transaction_paths(root, "staging"), [])
            self.assertEqual(len(transport.calls), 1)
            self.assertNotIn(FAKE_TOKEN, result[1] + result[2])

    def test_overwrite_publishes_complete_new_run_and_removes_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "run"
            target.mkdir()
            (target / "marker.txt").write_text("old", encoding="utf-8")
            transport = FakeTransport(response_for())
            result = self.run_main(
                self.arguments(target, "--confirm-api-contract", "--overwrite"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
            )
            self.assertEqual(result[0], 0)
            self.assertFalse((target / "marker.txt").exists())
            self.assertEqual(len(list(target.iterdir())), 4)
            self.assertEqual(self.transaction_paths(root, "staging"), [])
            self.assertEqual(self.transaction_paths(root, "backup"), [])
            self.assertEqual(len(transport.calls), 1)

    def test_overwrite_staging_failure_preserves_old_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "run"
            target.mkdir()
            marker = target / "marker.txt"
            marker.write_bytes(b"old-output")
            transport = FakeTransport(response_for())
            result = self.run_main(
                self.arguments(target, "--confirm-api-contract", "--overwrite"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
                filesystem=FailingWriteFilesystem(2),
            )
            self.assertEqual(result[0], 2)
            self.assertEqual(marker.read_bytes(), b"old-output")
            self.assertEqual(self.transaction_paths(root, "staging"), [])
            self.assertEqual(self.transaction_paths(root, "backup"), [])
            self.assertEqual(len(transport.calls), 1)

    def test_publication_failure_restores_old_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "run"
            target.mkdir()
            marker = target / "marker.txt"
            marker.write_bytes(b"old-output")
            transport = FakeTransport(response_for())
            result = self.run_main(
                self.arguments(target, "--confirm-api-contract", "--overwrite"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
                filesystem=FailingPublishFilesystem(),
            )
            self.assertEqual(result[0], 2)
            self.assertEqual(marker.read_bytes(), b"old-output")
            self.assertEqual(self.transaction_paths(root, "staging"), [])
            self.assertEqual(self.transaction_paths(root, "backup"), [])
            self.assertEqual(len(transport.calls), 1)

    def test_backup_cleanup_failure_keeps_new_target_and_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "run"
            target.mkdir()
            (target / "marker.txt").write_bytes(b"old-output")
            transport = FakeTransport(response_for())
            result = self.run_main(
                self.arguments(target, "--confirm-api-contract", "--overwrite"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
                filesystem=FailingBackupCleanupFilesystem(),
            )
            backups = self.transaction_paths(root, "backup")
            self.assertEqual(result[0], 2)
            self.assertEqual(len(list(target.iterdir())), 4)
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "marker.txt").read_bytes(), b"old-output")
            self.assertIn("backup cleanup failed", result[2])
            self.assertEqual(len(transport.calls), 1)

    def test_rollback_failure_reports_recovery_failure_and_keeps_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "run"
            target.mkdir()
            (target / "marker.txt").write_bytes(b"old-output")
            transport = FakeTransport(response_for())
            result = self.run_main(
                self.arguments(target, "--confirm-api-contract", "--overwrite"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
                filesystem=FailingRollbackFilesystem(),
            )
            backups = self.transaction_paths(root, "backup")
            self.assertEqual(result[0], 2)
            self.assertFalse(target.exists())
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "marker.txt").read_bytes(), b"old-output")
            self.assertEqual(self.transaction_paths(root, "staging"), [])
            self.assertIn("output recovery failed", result[2])
            self.assertNotIn(FAKE_TOKEN, result[1] + result[2])
            self.assertEqual(len(transport.calls), 1)

    def test_extra_staging_file_blocks_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "run"
            transport = FakeTransport(response_for())
            result = self.run_main(
                self.arguments(target, "--confirm-api-contract"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
                filesystem=ExtraStagingFileFilesystem(),
            )
            self.assertEqual(result[0], 2)
            self.assertFalse(target.exists())
            self.assertEqual(self.transaction_paths(root, "staging"), [])
            self.assertEqual(len(transport.calls), 1)

    def test_invalid_staging_json_blocks_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "run"
            transport = FakeTransport(response_for())
            result = self.run_main(
                self.arguments(target, "--confirm-api-contract"),
                allowed_root=root,
                environ={TOKEN_NAME: FAKE_TOKEN},
                transport=transport,
                filesystem=InvalidStagingJsonFilesystem(),
            )
            self.assertEqual(result[0], 2)
            self.assertFalse(target.exists())
            self.assertEqual(self.transaction_paths(root, "staging"), [])
            self.assertEqual(len(transport.calls), 1)

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
