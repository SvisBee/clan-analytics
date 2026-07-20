"""Fail-closed, single-request probe for the official clan war log."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

from .client import (
    DEFAULT_OUTPUT_ROOT,
    MAX_RESPONSE_BYTES,
    OFFICIAL_API_BASE_URL,
    HttpResponse,
    ProbeError,
    UrllibTransport,
    _OutputFilesystem,
    _ProbeArgumentParser,
    _assert_public_projection,
    _parse_response,
    _serialize_json,
    _validate_base_url,
    _validate_output_dir,
    _validate_timeout,
    _validate_token_env,
    build_request_url,
    normalize_clan_tag,
)
from .normalization import (
    NormalizationError,
    build_public_war_log_summary,
    normalize_war_log,
)


OFFICIAL_WAR_LOG_ENDPOINT_TEMPLATE = "/clans/{clan_tag}/warlog"
REQUESTS_PLANNED = 1
OUTPUT_FILENAMES = (
    "raw_war_log_response.json",
    "probe_metadata.json",
    "normalized_war_log.json",
    "public_war_log_preview.json",
)


@dataclass(frozen=True)
class WarLogProbePlan:
    clan_tag: str
    token_env: str
    output_dir: Path
    timeout_seconds: int
    base_url: str
    endpoint_template: str
    request_url: str
    target_host: str
    dry_run: bool
    contract_confirmed: bool


def build_war_log_probe_plan(
    arguments: argparse.Namespace,
    *,
    allowed_output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> WarLogProbePlan:
    clan_tag = normalize_clan_tag(arguments.clan_tag)
    token_env = _validate_token_env(arguments.token_env)
    timeout_seconds = _validate_timeout(arguments.timeout_seconds)
    base_url, target_host = _validate_base_url(arguments.base_url)
    endpoint_template = arguments.endpoint_template.strip()

    if endpoint_template != OFFICIAL_WAR_LOG_ENDPOINT_TEMPLATE:
        raise ProbeError("war-log endpoint template is not the verified official value")

    output_dir = _validate_output_dir(arguments.output_dir, allowed_output_root)
    return WarLogProbePlan(
        clan_tag=clan_tag,
        token_env=token_env,
        output_dir=output_dir,
        timeout_seconds=timeout_seconds,
        base_url=base_url,
        endpoint_template=endpoint_template,
        request_url=build_request_url(base_url, endpoint_template, clan_tag),
        target_host=target_host,
        dry_run=arguments.dry_run,
        contract_confirmed=arguments.confirm_api_contract,
    )


def _new_staging_path(target: Path) -> Path:
    path = target.parent / f".war-log-probe-staging-{uuid.uuid4().hex}"
    if path.exists():
        raise ProbeError("output staging path collision")
    return path


def _assert_staging_path(path: Path, target: Path) -> None:
    prefix = ".war-log-probe-staging-"
    suffix = path.name.removeprefix(prefix)
    if (
        path == target
        or path.parent.resolve(strict=False) != target.parent.resolve(strict=False)
        or not path.name.startswith(prefix)
        or not re.fullmatch(r"[0-9a-f]{32}", suffix)
    ):
        raise ProbeError("unsafe output staging path")


def _cleanup_staging(
    filesystem: _OutputFilesystem,
    staging: Path | None,
    target: Path,
) -> str | None:
    if staging is None or not staging.exists():
        return None
    try:
        _assert_staging_path(staging, target)
        filesystem.remove_tree(staging)
    except (OSError, ProbeError):
        return "output staging cleanup failed"
    return None


def _validate_staging(
    staging: Path,
    *,
    expected_raw_payload: Mapping[str, Any],
) -> None:
    if not staging.is_dir():
        raise ProbeError("output staging directory is missing")

    entries = list(staging.iterdir())
    if (
        {entry.name for entry in entries} != set(OUTPUT_FILENAMES)
        or len(entries) != len(OUTPUT_FILENAMES)
    ):
        raise ProbeError("output staging file set is invalid")
    if any(not entry.is_file() or entry.is_symlink() for entry in entries):
        raise ProbeError("output staging contains a non-regular file")

    try:
        parsed = {
            name: json.loads((staging / name).read_text(encoding="utf-8"))
            for name in OUTPUT_FILENAMES
        }
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ProbeError("output staging contains invalid JSON") from None

    if parsed["raw_war_log_response.json"] != expected_raw_payload:
        raise ProbeError("output staging raw response mismatch")

    metadata = parsed["probe_metadata.json"]
    if not isinstance(metadata, Mapping):
        raise ProbeError("output staging metadata is invalid")
    if metadata.get("request_count") != 1 or metadata.get("redirects_followed") != 0:
        raise ProbeError("output staging request metadata is invalid")

    _assert_public_projection(parsed["public_war_log_preview.json"])


def _write_outputs(
    plan: WarLogProbePlan,
    *,
    response: HttpResponse,
    raw_payload: Mapping[str, Any],
    normalized: Any,
    public_preview: Mapping[str, Any],
    collected_at: str,
    filesystem: _OutputFilesystem,
) -> None:
    if plan.output_dir.exists():
        raise ProbeError("output directory already exists")

    metadata = {
        "collected_at": collected_at,
        "method": "GET",
        "request_count": REQUESTS_PLANNED,
        "target_host": plan.target_host,
        "endpoint_template": plan.endpoint_template,
        "request_url": plan.request_url,
        "timeout_seconds": plan.timeout_seconds,
        "response_status": response.status,
        "response_content_type": response.content_type,
        "response_bytes": len(response.body),
        "redirects_followed": 0,
    }

    _assert_public_projection(public_preview)
    files = {
        "raw_war_log_response.json": response.body,
        "probe_metadata.json": _serialize_json(metadata),
        "normalized_war_log.json": _serialize_json(asdict(normalized)),
        "public_war_log_preview.json": _serialize_json(public_preview),
    }
    if set(files) != set(OUTPUT_FILENAMES):
        raise ProbeError("output file set is invalid")

    staging: Path | None = None
    try:
        filesystem.ensure_parent(plan.output_dir.parent)
        if plan.output_dir.exists():
            raise ProbeError("output directory already exists")

        staging = _new_staging_path(plan.output_dir)
        _assert_staging_path(staging, plan.output_dir)
        filesystem.create_directory(staging)

        for name in OUTPUT_FILENAMES:
            filesystem.write_exclusive(staging / name, files[name])

        _validate_staging(staging, expected_raw_payload=raw_payload)
        filesystem.rename(staging, plan.output_dir)
    except ProbeError as error:
        cleanup_error = _cleanup_staging(filesystem, staging, plan.output_dir)
        if cleanup_error:
            raise ProbeError(f"{error}; {cleanup_error}") from None
        raise
    except OSError:
        cleanup_error = _cleanup_staging(filesystem, staging, plan.output_dir)
        message = "output transaction failed"
        if cleanup_error:
            message = f"{message}; {cleanup_error}"
        raise ProbeError(message) from None


def execute_war_log_probe(
    plan: WarLogProbePlan,
    *,
    environ: Mapping[str, str],
    transport: Any | None = None,
    filesystem: _OutputFilesystem | None = None,
) -> None:
    if not plan.contract_confirmed:
        raise ProbeError("execute mode requires --confirm-api-contract")
    if plan.base_url != OFFICIAL_API_BASE_URL:
        raise ProbeError("execute mode requires the official API base URL")
    if plan.endpoint_template != OFFICIAL_WAR_LOG_ENDPOINT_TEMPLATE:
        raise ProbeError("execute mode requires the official war-log endpoint")
    if plan.output_dir.exists():
        raise ProbeError("output directory already exists")

    token = environ.get(plan.token_env)
    if not token:
        raise ProbeError(f"token environment variable {plan.token_env} is not set")

    active_transport = transport if transport is not None else UrllibTransport()
    try:
        response = active_transport.get(
            plan.request_url,
            token=token,
            timeout_seconds=plan.timeout_seconds,
            max_response_bytes=MAX_RESPONSE_BYTES,
        )
    except ProbeError:
        raise
    except Exception:
        raise ProbeError("HTTP request failed") from None

    payload = _parse_response(response, plan, token)
    collected_at = datetime.now(timezone.utc).isoformat()

    try:
        normalized = normalize_war_log(
            payload,
            collected_at=collected_at,
            raw_source_reference="raw_war_log_response.json",
        )
    except NormalizationError as error:
        raise ProbeError(f"response normalization failed: {error}") from None

    public_preview = build_public_war_log_summary(normalized)
    _assert_public_projection(public_preview)

    _write_outputs(
        plan,
        response=response,
        raw_payload=payload,
        normalized=normalized,
        public_preview=public_preview,
        collected_at=collected_at,
        filesystem=filesystem if filesystem is not None else _OutputFilesystem(),
    )


def _print_dry_run(plan: WarLogProbePlan, output: TextIO) -> None:
    print("Mode: dry-run", file=output)
    print("Method: GET", file=output)
    print(f"Requests planned: {REQUESTS_PLANNED}", file=output)
    print(f"Token source: environment variable {plan.token_env}", file=output)
    print("Token value: [REDACTED]", file=output)
    print(f"Target host: {plan.target_host}", file=output)
    print(f"Endpoint template: {plan.endpoint_template}", file=output)
    print(f"Request URL: {plan.request_url}", file=output)
    print(f"Output: {plan.output_dir}", file=output)
    print(f"Timeout seconds: {plan.timeout_seconds}", file=output)
    print("Network executed: no", file=output)


def _build_parser() -> argparse.ArgumentParser:
    parser = _ProbeArgumentParser(
        description="Prepare or execute one fail-closed clan war-log probe."
    )
    parser.add_argument("--clan-tag", required=True)
    parser.add_argument("--token-env", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--timeout-seconds", required=True, type=int)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--endpoint-template", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-api-contract", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    transport: Any | None = None,
    filesystem: _OutputFilesystem | None = None,
    allowed_output_root: Path = DEFAULT_OUTPUT_ROOT,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--token" in arguments or any(value.startswith("--token=") for value in arguments):
        print("Error: token values are not accepted; use --token-env", file=stderr)
        return 2

    try:
        parsed = _build_parser().parse_args(arguments)
        plan = build_war_log_probe_plan(
            parsed,
            allowed_output_root=allowed_output_root,
        )
        if plan.dry_run:
            _print_dry_run(plan, stdout)
            return 0

        execute_war_log_probe(
            plan,
            environ=os.environ if environ is None else environ,
            transport=transport,
            filesystem=filesystem,
        )
        print("War log probe: PASS", file=stdout)
        print(f"Requests executed: {REQUESTS_PLANNED}", file=stdout)
        print(f"Output: {plan.output_dir}", file=stdout)
        return 0
    except ProbeError as error:
        print(f"Error: {error}", file=stderr)
        return 2
