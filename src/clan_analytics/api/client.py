"""Fail-closed, single-request foundation for a future clan roster probe."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .normalization import (
    NormalizationError,
    build_composition_summary,
    build_public_roster,
    normalize_clan,
)


PROJECT_USER_AGENT = "ClashClanAnalytics-Probe/0.1"
DEFAULT_OUTPUT_ROOT = Path(r"D:\coc\runs\api_probe")
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 60
REQUESTS_PLANNED = 1
OUTPUT_FILENAMES = (
    "raw_clan_response.json",
    "probe_metadata.json",
    "normalized_clan.json",
    "public_roster_preview.json",
)
_TAG_PATTERN = re.compile(r"#[A-Z0-9]{3,20}")
_ENV_NAME_PATTERN = re.compile(r"[A-Z_][A-Z0-9_]{1,63}")
_PRIVATE_PUBLIC_KEYS = {
    "player_tag",
    "raw_source_reference",
    "source_timestamp",
    "collected_at",
    "telegram_username",
    "leadership_note",
    "review_status",
    "manual_flags",
    "consent_flags",
    "token",
    "token_env",
    "ip",
}


class ProbeError(ValueError):
    """A safe operator-facing failure without credential material."""


class _ProbeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ProbeError(f"invalid arguments: {message}")


@dataclass(frozen=True)
class ProbePlan:
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
    overwrite: bool


@dataclass(frozen=True)
class HttpResponse:
    status: int
    content_type: str
    body: bytes
    final_url: str


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class UrllibTransport:
    """Standard-library GET transport that never follows redirects or retries."""

    def get(
        self,
        url: str,
        *,
        token: str,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> HttpResponse:
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "User-Agent": PROJECT_USER_AGENT,
            },
            method="GET",
        )
        opener = build_opener(_NoRedirectHandler())
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        if int(content_length) > max_response_bytes:
                            raise ProbeError("response exceeds the maximum allowed size")
                    except ValueError as error:
                        raise ProbeError("response has an invalid Content-Length") from error
                body = response.read(max_response_bytes + 1)
                return HttpResponse(
                    status=response.status,
                    content_type=response.headers.get("Content-Type", ""),
                    body=body,
                    final_url=response.geturl(),
                )
        except HTTPError as error:
            if 300 <= error.code < 400:
                raise ProbeError("redirect response rejected") from None
            raise ProbeError(f"HTTP request failed with status {error.code}") from None
        except URLError:
            raise ProbeError("HTTP request failed") from None


def normalize_clan_tag(value: str) -> str:
    """Apply a conservative project syntax check, not an official API claim."""

    normalized = value.strip().upper()
    if normalized and not normalized.startswith("#"):
        normalized = f"#{normalized}"
    if not _TAG_PATTERN.fullmatch(normalized):
        raise ProbeError("clan tag must be # followed by 3 to 20 ASCII letters or digits")
    return normalized


def _validate_token_env(name: str) -> str:
    if not _ENV_NAME_PATTERN.fullmatch(name):
        raise ProbeError("token environment variable name is invalid")
    return name


def _validate_timeout(value: int) -> int:
    if not MIN_TIMEOUT_SECONDS <= value <= MAX_TIMEOUT_SECONDS:
        raise ProbeError(
            f"timeout must be between {MIN_TIMEOUT_SECONDS} and "
            f"{MAX_TIMEOUT_SECONDS} seconds"
        )
    return value


def _validate_base_url(value: str) -> tuple[str, str]:
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ProbeError("base URL must be an HTTPS origin")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProbeError("base URL must not contain credentials, query, or fragment")
    if parsed.path not in ("", "/"):
        raise ProbeError("base URL must not contain a path")
    hostname = parsed.hostname.lower()
    if hostname != "clashofclans.com" and not hostname.endswith(".clashofclans.com"):
        raise ProbeError("base URL host must belong to clashofclans.com")
    try:
        parsed_port = parsed.port
    except ValueError:
        raise ProbeError("base URL contains an invalid port") from None
    port = f":{parsed_port}" if parsed_port is not None else ""
    return f"https://{hostname}{port}", hostname


def _validate_endpoint_template(value: str) -> str:
    template = value.strip()
    if template.count("{clan_tag}") != 1:
        raise ProbeError("endpoint template must contain {clan_tag} exactly once")
    remainder = template.replace("{clan_tag}", "")
    if not template.startswith("/") or "{" in remainder or "}" in remainder:
        raise ProbeError("endpoint template must be an absolute path with one placeholder")
    if "\\" in template or "//" in template or ".." in template:
        raise ProbeError("endpoint template contains an unsafe path segment")
    if "?" in template or "#" in template:
        raise ProbeError("endpoint template must not contain query or fragment")
    return template


def _validate_output_dir(value: str, allowed_output_root: Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        raise ProbeError("output directory must be an absolute path")
    resolved = candidate.resolve(strict=False)
    root = allowed_output_root.resolve(strict=False)
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        raise ProbeError(f"output directory must be inside {root}") from None
    if not relative.parts:
        raise ProbeError("output directory must be a run-specific child directory")
    return resolved


def build_request_url(base_url: str, endpoint_template: str, clan_tag: str) -> str:
    encoded_tag = quote(clan_tag, safe="")
    endpoint = endpoint_template.replace("{clan_tag}", encoded_tag)
    request_url = urljoin(f"{base_url}/", endpoint.lstrip("/"))
    if urlsplit(request_url).hostname != urlsplit(base_url).hostname:
        raise ProbeError("request URL escaped the configured host")
    return request_url


def build_probe_plan(
    arguments: argparse.Namespace, *, allowed_output_root: Path = DEFAULT_OUTPUT_ROOT
) -> ProbePlan:
    clan_tag = normalize_clan_tag(arguments.clan_tag)
    token_env = _validate_token_env(arguments.token_env)
    timeout_seconds = _validate_timeout(arguments.timeout_seconds)
    base_url, target_host = _validate_base_url(arguments.base_url)
    endpoint_template = _validate_endpoint_template(arguments.endpoint_template)
    output_dir = _validate_output_dir(arguments.output_dir, allowed_output_root)
    return ProbePlan(
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
        overwrite=arguments.overwrite,
    )


def _content_type_is_json(value: str) -> bool:
    media_type = value.partition(";")[0].strip().lower()
    return media_type == "application/json" or media_type.endswith("+json")


def _parse_response(response: HttpResponse, plan: ProbePlan, token: str) -> Mapping[str, Any]:
    if response.status != 200:
        raise ProbeError(f"HTTP request failed with status {response.status}")
    final_url = urlsplit(response.final_url)
    configured_url = urlsplit(plan.base_url)
    try:
        final_origin = (final_url.scheme, final_url.hostname, final_url.port)
        configured_origin = (
            configured_url.scheme,
            configured_url.hostname,
            configured_url.port,
        )
    except ValueError:
        raise ProbeError("response URL has an invalid origin") from None
    if final_origin != configured_origin:
        raise ProbeError("response URL origin differs from the configured origin")
    if not _content_type_is_json(response.content_type):
        raise ProbeError("response Content-Type is not JSON")
    if len(response.body) > MAX_RESPONSE_BYTES:
        raise ProbeError("response exceeds the maximum allowed size")
    if token.encode("utf-8") in response.body:
        raise ProbeError("response rejected because it contains credential material")
    try:
        decoded = response.body.decode("utf-8")
        payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProbeError("response is not valid UTF-8 JSON") from None
    if not isinstance(payload, Mapping):
        raise ProbeError("response JSON root must be an object")
    return payload


def _assert_public_projection(value: Any) -> None:
    if isinstance(value, Mapping):
        forbidden = _PRIVATE_PUBLIC_KEYS.intersection(value)
        if forbidden:
            raise ProbeError("public projection contains a private field")
        for nested in value.values():
            _assert_public_projection(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_public_projection(nested)
    elif isinstance(value, str) and value.startswith("#"):
        raise ProbeError("public projection contains a game tag")


def _write_json(path: Path, value: Any, *, overwrite: bool) -> None:
    mode = "w" if overwrite else "x"
    with path.open(mode, encoding="utf-8", newline="\n") as output:
        json.dump(value, output, ensure_ascii=False, indent=2, sort_keys=True)
        output.write("\n")


def _write_outputs(
    plan: ProbePlan,
    *,
    response: HttpResponse,
    normalized: Any,
    public_preview: Mapping[str, Any],
    collected_at: str,
) -> None:
    if plan.output_dir.exists() and not plan.overwrite:
        raise ProbeError("output directory already exists; use --overwrite explicitly")
    plan.output_dir.mkdir(parents=True, exist_ok=plan.overwrite)
    if not plan.overwrite:
        collisions = [name for name in OUTPUT_FILENAMES if (plan.output_dir / name).exists()]
        if collisions:
            raise ProbeError("an output file already exists; use --overwrite explicitly")

    raw_path = plan.output_dir / "raw_clan_response.json"
    raw_mode = "wb" if plan.overwrite else "xb"
    with raw_path.open(raw_mode) as raw_output:
        raw_output.write(response.body)

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
    _write_json(plan.output_dir / "probe_metadata.json", metadata, overwrite=plan.overwrite)
    _write_json(
        plan.output_dir / "normalized_clan.json",
        asdict(normalized),
        overwrite=plan.overwrite,
    )
    _write_json(
        plan.output_dir / "public_roster_preview.json",
        public_preview,
        overwrite=plan.overwrite,
    )


def execute_probe(
    plan: ProbePlan,
    *,
    environ: Mapping[str, str],
    transport: Any | None = None,
) -> None:
    if not plan.contract_confirmed:
        raise ProbeError("execute mode requires --confirm-api-contract")
    if "placeholder" in plan.base_url.lower() or "unverified" in plan.endpoint_template.lower():
        raise ProbeError("execute mode rejects placeholder API contract values")
    if plan.output_dir.exists() and not plan.overwrite:
        raise ProbeError("output directory already exists; use --overwrite explicitly")
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
        normalized = normalize_clan(
            payload,
            collected_at=collected_at,
            raw_source_reference="raw_clan_response.json",
        )
    except NormalizationError as error:
        raise ProbeError(f"response normalization failed: {error}") from None
    public_preview = {
        **build_public_roster(normalized),
        "composition": build_composition_summary(normalized),
    }
    _assert_public_projection(public_preview)
    _write_outputs(
        plan,
        response=response,
        normalized=normalized,
        public_preview=public_preview,
        collected_at=collected_at,
    )


def _print_dry_run(plan: ProbePlan, output: TextIO) -> None:
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
        description="Prepare or execute one fail-closed clan roster probe."
    )
    parser.add_argument("--clan-tag", required=True)
    parser.add_argument("--token-env", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--timeout-seconds", required=True, type=int)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--endpoint-template", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-api-contract", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    transport: Any | None = None,
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
        plan = build_probe_plan(parsed, allowed_output_root=allowed_output_root)
        if plan.dry_run:
            _print_dry_run(plan, stdout)
            return 0
        execute_probe(
            plan,
            environ=os.environ if environ is None else environ,
            transport=transport,
        )
        print("Probe: PASS", file=stdout)
        print(f"Requests executed: {REQUESTS_PLANNED}", file=stdout)
        print(f"Output: {plan.output_dir}", file=stdout)
        return 0
    except ProbeError as error:
        print(f"Error: {error}", file=stderr)
        return 2
