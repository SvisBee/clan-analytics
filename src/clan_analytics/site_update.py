"""Build a transactional, public-only site data update from three probe runs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from .api.current_war_probe import build_public_current_war_preview
from .api.normalization import (
    build_composition_summary,
    build_public_roster,
    build_public_war_log_summary,
    normalize_clan,
    normalize_current_war,
    normalize_war_log,
)
from .history import build_public_war_history, detailed_wars, empty_history, merge_war_history


PUBLIC_FILENAMES = (
    "roster.json",
    "current-war.json",
    "war-log.json",
    "war-history.json",
    "site-config.json",
)
_FORBIDDEN_KEYS = {
    "player_tag",
    "clan_tag",
    "raw_source_reference",
    "source_timestamp",
    "authorization",
    "token",
    "api_token",
}
_TAG_PATTERN = re.compile(r"#[A-Z0-9]{3,20}")


class SiteUpdateError(ValueError):
    """Raised when probe inputs or generated public files are unsafe."""


@dataclass(frozen=True)
class ProbePayload:
    raw: Mapping[str, Any]
    metadata: Mapping[str, Any]


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SiteUpdateError(f"invalid JSON: {path}") from error


def _load_probe(run_dir: Path, *, raw_name: str) -> ProbePayload:
    raw = _read_json(run_dir / raw_name)
    metadata = _read_json(run_dir / "probe_metadata.json")
    if not isinstance(raw, Mapping) or not isinstance(metadata, Mapping):
        raise SiteUpdateError(f"invalid probe objects in {run_dir}")
    if metadata.get("request_count") != 1:
        raise SiteUpdateError(f"probe request count is not one: {run_dir}")
    if metadata.get("response_status") != 200:
        raise SiteUpdateError(f"probe response status is not 200: {run_dir}")
    if metadata.get("redirects_followed") != 0:
        raise SiteUpdateError(f"probe followed redirects: {run_dir}")
    collected_at = metadata.get("collected_at")
    if not isinstance(collected_at, str) or not collected_at:
        raise SiteUpdateError(f"probe collected_at is missing: {run_dir}")
    return ProbePayload(raw=raw, metadata=metadata)


def _load_existing(path: Path, default: Any) -> Any:
    return _read_json(path) if path.is_file() else default


def _safe_badge_url(raw_clan: Mapping[str, Any]) -> str | None:
    badges = raw_clan.get("badgeUrls")
    if not isinstance(badges, Mapping):
        return None
    for key in ("large", "medium", "small"):
        value = badges.get(key)
        if not isinstance(value, str):
            continue
        parsed = urlsplit(value)
        if parsed.scheme == "https" and parsed.netloc and not parsed.username and not parsed.password:
            return value
    return None


def _scan_public(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            if key_text.casefold() in _FORBIDDEN_KEYS:
                raise SiteUpdateError(f"forbidden public key at {path}.{key_text}")
            _scan_public(nested, f"{path}.{key_text}")
        return
    if isinstance(value, list):
        for index, nested in enumerate(value):
            _scan_public(nested, f"{path}[{index}]")
        return
    if isinstance(value, str):
        lowered = value.casefold()
        if "authorization" in lowered or "bearer " in lowered or "coc_api_token" in lowered:
            raise SiteUpdateError(f"forbidden public string at {path}")
        if _TAG_PATTERN.fullmatch(value.strip()):
            raise SiteUpdateError(f"game tag leaked into public output at {path}")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def build_site_update(
    *,
    roster_run: Path,
    current_war_run: Path,
    war_log_run: Path,
    existing_history_path: Path,
    existing_site_data_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build proposed public files and next internal history without applying them."""

    if output_dir.exists():
        raise SiteUpdateError("output directory already exists")

    roster_probe = _load_probe(roster_run, raw_name="raw_clan_response.json")
    current_probe = _load_probe(
        current_war_run, raw_name="raw_current_war_response.json"
    )
    war_log_probe = _load_probe(war_log_run, raw_name="raw_war_log_response.json")

    clan = normalize_clan(
        roster_probe.raw,
        collected_at=str(roster_probe.metadata["collected_at"]),
        raw_source_reference="raw_clan_response.json",
    )
    current_war = normalize_current_war(
        current_probe.raw,
        collected_at=str(current_probe.metadata["collected_at"]),
        raw_source_reference="raw_current_war_response.json",
    )
    war_log = normalize_war_log(
        war_log_probe.raw,
        collected_at=str(war_log_probe.metadata["collected_at"]),
        raw_source_reference="raw_war_log_response.json",
    )

    history = (
        _read_json(existing_history_path)
        if existing_history_path.is_file()
        else empty_history()
    )
    if not isinstance(history, Mapping):
        raise SiteUpdateError("existing history must be an object")
    next_history, history_changed = merge_war_history(history, current_war)
    wars = detailed_wars(next_history)

    roster = {
        **build_public_roster(clan, wars),
        "composition": build_composition_summary(clan),
    }
    current_war_public = build_public_current_war_preview(current_war)
    war_log_public = build_public_war_log_summary(war_log)
    war_history_public = build_public_war_history(next_history)

    existing_roster = _load_existing(existing_site_data_dir / "roster.json", None)
    existing_current = _load_existing(existing_site_data_dir / "current-war.json", None)
    existing_war_log = _load_existing(existing_site_data_dir / "war-log.json", None)
    existing_history_public = _load_existing(
        existing_site_data_dir / "war-history.json", None
    )
    existing_config = _load_existing(existing_site_data_dir / "site-config.json", {})
    if not isinstance(existing_config, Mapping):
        existing_config = {}

    changed = {
        "roster": roster != existing_roster,
        "current_war": current_war_public != existing_current,
        "war_log": war_log_public != existing_war_log,
        "war_history": war_history_public != existing_history_public,
    }

    badge_url = _safe_badge_url(roster_probe.raw)
    config = {
        "badge_url": badge_url,
        "collected_at": (
            roster_probe.metadata["collected_at"]
            if changed["roster"] or existing_config.get("collected_at") is None
            else existing_config.get("collected_at")
        ),
        "current_war_collected_at": (
            current_probe.metadata["collected_at"]
            if changed["current_war"]
            or existing_config.get("current_war_collected_at") is None
            else existing_config.get("current_war_collected_at")
        ),
        "war_log_collected_at": (
            war_log_probe.metadata["collected_at"]
            if changed["war_log"]
            or existing_config.get("war_log_collected_at") is None
            else existing_config.get("war_log_collected_at")
        ),
    }

    public_files = {
        "roster.json": roster,
        "current-war.json": current_war_public,
        "war-log.json": war_log_public,
        "war-history.json": war_history_public,
        "site-config.json": config,
    }
    for name, payload in public_files.items():
        _scan_public(payload, f"$.{name}")

    public_dir = output_dir / "site-data"
    for name in PUBLIC_FILENAMES:
        _write_json(public_dir / name, public_files[name])
    _write_json(output_dir / "history-next.json", next_history)

    config_changed = config != existing_config
    summary = {
        "schema_version": 1,
        "members": len(roster["members"]),
        "current_war_state": current_war_public["state"],
        "current_war_participants": current_war_public["participants"],
        "history_wars": war_history_public["wars_observed"],
        "history_changed": history_changed,
        "public_changed": {
            **changed,
            "site_config": config_changed,
        },
        "public_change_count": sum(changed.values()) + int(config_changed),
    }
    _write_json(output_dir / "summary.json", summary)
    return summary
