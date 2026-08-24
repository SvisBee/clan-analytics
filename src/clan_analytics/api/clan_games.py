"""Official player-profile source for the Games Champion cumulative counter."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Callable, Mapping
from urllib.parse import quote

from .client import (
    MAX_RESPONSE_BYTES,
    OFFICIAL_API_BASE_URL,
    HttpResponse,
    ProbeError,
    ProbeHttpError,
    ProbeInvalidJsonError,
    ProbeTimeoutError,
    ProbeTransportError,
    UrllibTransport,
    _validate_timeout,
    parse_official_json_response,
)


GAMES_CHAMPION_NAME = "Games Champion"
GAMES_CHAMPION_NORMALIZATION_VERSION = "games_champion_v1"
GAMES_CHAMPION_SOURCE_KIND = "official_player_profile"
OFFICIAL_PLAYER_ENDPOINT_TEMPLATE = "/players/{player_tag}"
DEFAULT_TIMEOUT_SECONDS = 15
_TAG_PATTERN = re.compile(r"#[A-Z0-9]{3,20}")
_RESULT_CODES = {
    "success",
    "api_http_403",
    "api_http_other",
    "api_transport_failure",
    "timeout",
    "invalid_json",
    "invalid_player_schema",
    "games_champion_missing",
    "games_champion_invalid",
    "unexpected_error",
}


@dataclass(frozen=True)
class GamesChampionSnapshot:
    """Internal-only normalized observation candidate.

    The stable player identity is intentionally hidden from ``repr`` and is not
    part of the safe result projection.
    """

    player_tag_internal: str = field(repr=False)
    value: int
    target: int
    observed_at_utc: str
    source_kind: str = GAMES_CHAMPION_SOURCE_KIND
    schema_version: int = 1
    normalization_version: str = GAMES_CHAMPION_NORMALIZATION_VERSION


@dataclass(frozen=True)
class GamesChampionSafeResult:
    """Identity-free operational result safe for bounded health reporting."""

    status: str
    result_code: str
    observed_at_utc: str | None
    duration_ms: int
    http_status: int | None
    value_validation_status: str
    target_validation_status: str
    normalization_version: str = GAMES_CHAMPION_NORMALIZATION_VERSION
    operator_hint_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "result_code": self.result_code,
            "observed_at_utc": self.observed_at_utc,
            "duration_ms": self.duration_ms,
            "http_status": self.http_status,
            "value_validation_status": self.value_validation_status,
            "target_validation_status": self.target_validation_status,
            "normalization_version": self.normalization_version,
            "operator_hint_code": self.operator_hint_code,
        }


class GamesChampionSourceError(ValueError):
    """Typed source failure whose message and safe projection omit identity."""

    def __init__(
        self,
        result_code: str,
        safe_message: str,
        *,
        observed_at_utc: str | None = None,
        duration_ms: int = 0,
        http_status: int | None = None,
    ) -> None:
        if result_code not in _RESULT_CODES or result_code == "success":
            raise ValueError("invalid Games Champion result code")
        self.result_code = result_code
        self.safe_message = safe_message
        self.observed_at_utc = observed_at_utc
        self.duration_ms = duration_ms
        self.http_status = http_status
        super().__init__(safe_message)

    def to_safe_result(self) -> GamesChampionSafeResult:
        return GamesChampionSafeResult(
            status="failed",
            result_code=self.result_code,
            observed_at_utc=self.observed_at_utc,
            duration_ms=self.duration_ms,
            http_status=self.http_status,
            value_validation_status="not_validated",
            target_validation_status="not_validated",
            operator_hint_code=(
                "enable_approved_vpn"
                if self.result_code == "api_http_403"
                else None
            ),
        )


def _normalize_player_tag(value: str) -> str:
    if not isinstance(value, str):
        raise GamesChampionSourceError(
            "invalid_player_schema", "internal player identity is invalid"
        )
    normalized = value.strip().upper()
    if normalized and not normalized.startswith("#"):
        normalized = f"#{normalized}"
    if not _TAG_PATTERN.fullmatch(normalized):
        raise GamesChampionSourceError(
            "invalid_player_schema", "internal player identity is invalid"
        )
    return normalized


def _canonical_utc(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise GamesChampionSourceError(
            "invalid_player_schema", "observation timestamp must be timezone-aware"
        )
    try:
        offset = value.utcoffset()
    except (OverflowError, ValueError):
        offset = None
    if offset is None:
        raise GamesChampionSourceError(
            "invalid_player_schema", "observation timestamp must be timezone-aware"
        )
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def build_player_request_url(player_tag_internal: str) -> str:
    """Build the one verified official player endpoint without logging identity."""

    player_tag = _normalize_player_tag(player_tag_internal)
    encoded = quote(player_tag, safe="")
    return f"{OFFICIAL_API_BASE_URL}/players/{encoded}"


def _required_nonnegative_integer(
    achievement: Mapping[str, Any], key: str
) -> int:
    value = achievement.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GamesChampionSourceError(
            "games_champion_invalid",
            "Games Champion contains an invalid numeric field",
        )
    return value


def normalize_games_champion_profile(
    profile: Mapping[str, Any],
    *,
    player_tag_internal: str,
    observed_at_utc: datetime,
) -> GamesChampionSnapshot:
    """Extract exactly one Games Champion achievement from an in-memory profile."""

    player_tag = _normalize_player_tag(player_tag_internal)
    observed_at = _canonical_utc(observed_at_utc)
    if not isinstance(profile, Mapping):
        raise GamesChampionSourceError(
            "invalid_player_schema", "official player profile must be an object"
        )
    response_tag = profile.get("tag")
    if not isinstance(response_tag, str) or response_tag != player_tag:
        raise GamesChampionSourceError(
            "invalid_player_schema", "official player profile identity mismatch"
        )
    achievements = profile.get("achievements")
    if not isinstance(achievements, list):
        raise GamesChampionSourceError(
            "invalid_player_schema", "official player achievements must be an array"
        )

    matches: list[Mapping[str, Any]] = []
    for item in achievements:
        if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
            raise GamesChampionSourceError(
                "invalid_player_schema", "official player achievement entry is invalid"
            )
        if item["name"] == GAMES_CHAMPION_NAME:
            matches.append(item)
    if not matches:
        raise GamesChampionSourceError(
            "games_champion_missing", "Games Champion achievement is missing"
        )
    if len(matches) != 1:
        raise GamesChampionSourceError(
            "invalid_player_schema", "Games Champion achievement is duplicated"
        )

    achievement = matches[0]
    value = _required_nonnegative_integer(achievement, "value")
    target = _required_nonnegative_integer(achievement, "target")
    stars = achievement.get("stars")
    if isinstance(stars, bool) or not isinstance(stars, int) or not 0 <= stars <= 3:
        raise GamesChampionSourceError(
            "games_champion_invalid", "Games Champion stars field is invalid"
        )
    if not isinstance(achievement.get("info"), str) or not isinstance(
        achievement.get("completionInfo"), str
    ):
        raise GamesChampionSourceError(
            "games_champion_invalid", "Games Champion text fields are invalid"
        )

    return GamesChampionSnapshot(
        player_tag_internal=player_tag,
        value=value,
        target=target,
        observed_at_utc=observed_at,
    )


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def _source_error(
    result_code: str,
    safe_message: str,
    *,
    started: float,
    observed_at_utc: str | None = None,
    http_status: int | None = None,
) -> GamesChampionSourceError:
    return GamesChampionSourceError(
        result_code,
        safe_message,
        observed_at_utc=observed_at_utc,
        duration_ms=_duration_ms(started),
        http_status=http_status,
    )


def fetch_games_champion(
    player_tag_internal: str,
    *,
    token: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    transport: Any | None = None,
    clock: Callable[[], datetime] | None = None,
) -> tuple[GamesChampionSnapshot, GamesChampionSafeResult]:
    """Fetch once, normalize in memory, and return separate private/safe values."""

    started = perf_counter()
    if not isinstance(token, str) or not token:
        raise _source_error(
            "unexpected_error", "API credential is unavailable", started=started
        )
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
        raise _source_error(
            "unexpected_error", "request timeout is invalid", started=started
        )
    try:
        timeout = _validate_timeout(timeout_seconds)
        request_url = build_player_request_url(player_tag_internal)
    except ProbeError:
        raise _source_error(
            "unexpected_error", "request configuration is invalid", started=started
        ) from None
    except GamesChampionSourceError:
        raise

    active_transport = transport if transport is not None else UrllibTransport()
    try:
        response: HttpResponse = active_transport.get(
            request_url,
            token=token,
            timeout_seconds=timeout,
            max_response_bytes=MAX_RESPONSE_BYTES,
        )
    except ProbeHttpError as error:
        code = "api_http_403" if error.status_code == 403 else "api_http_other"
        raise _source_error(
            code,
            "Clash API rejected the player request",
            started=started,
            http_status=error.status_code,
        ) from None
    except ProbeTimeoutError:
        raise _source_error(
            "timeout", "Clash API player request timed out", started=started
        ) from None
    except ProbeTransportError:
        raise _source_error(
            "api_transport_failure",
            "Clash API player request did not complete",
            started=started,
        ) from None
    except ProbeError:
        raise _source_error(
            "api_transport_failure",
            "Clash API player request did not complete",
            started=started,
        ) from None
    except Exception:
        raise _source_error(
            "unexpected_error", "unexpected player source failure", started=started
        ) from None

    try:
        received = (clock or (lambda: datetime.now(timezone.utc)))()
        observed_at = _canonical_utc(received)
    except Exception:
        raise _source_error(
            "unexpected_error",
            "response observation timestamp is invalid",
            started=started,
            http_status=getattr(response, "status", None),
        ) from None
    try:
        profile = parse_official_json_response(
            response,
            base_url=OFFICIAL_API_BASE_URL,
            token=token,
        )
    except ProbeHttpError as error:
        code = "api_http_403" if error.status_code == 403 else "api_http_other"
        raise _source_error(
            code,
            "Clash API rejected the player request",
            started=started,
            observed_at_utc=observed_at,
            http_status=error.status_code,
        ) from None
    except ProbeInvalidJsonError:
        raise _source_error(
            "invalid_json",
            "official player response is invalid JSON",
            started=started,
            observed_at_utc=observed_at,
            http_status=response.status,
        ) from None
    except ProbeError:
        raise _source_error(
            "invalid_player_schema",
            "official player response failed safety validation",
            started=started,
            observed_at_utc=observed_at,
            http_status=response.status,
        ) from None
    except Exception:
        raise _source_error(
            "unexpected_error",
            "unexpected player response failure",
            started=started,
            observed_at_utc=observed_at,
            http_status=getattr(response, "status", None),
        ) from None

    try:
        snapshot = normalize_games_champion_profile(
            profile,
            player_tag_internal=player_tag_internal,
            observed_at_utc=received,
        )
    except GamesChampionSourceError as error:
        raise _source_error(
            error.result_code,
            error.safe_message,
            started=started,
            observed_at_utc=observed_at,
            http_status=response.status,
        ) from None
    except Exception:
        raise _source_error(
            "unexpected_error",
            "unexpected player normalization failure",
            started=started,
            observed_at_utc=observed_at,
            http_status=response.status,
        ) from None

    safe_result = GamesChampionSafeResult(
        status="success",
        result_code="success",
        observed_at_utc=snapshot.observed_at_utc,
        duration_ms=_duration_ms(started),
        http_status=response.status,
        value_validation_status="valid",
        target_validation_status="valid",
    )
    return snapshot, safe_result
