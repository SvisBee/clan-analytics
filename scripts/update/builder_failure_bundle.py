"""Preserve exact site-builder inputs locally and replay them without network."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_BUNDLE_BYTES = 80 * 1024 * 1024
PUBLIC_NAMES = ("roster.json", "current-war.json", "war-log.json", "war-history.json", "site-config.json")


class BundleError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy(source: Path, destination: Path, logical_name: str, relative_filename: str, total: list[int]) -> dict[str, object]:
    if not source.is_file():
        raise BundleError(f"required builder input is missing: {logical_name}")
    size = source.stat().st_size
    if size > MAX_FILE_BYTES or total[0] + size > MAX_BUNDLE_BYTES:
        raise BundleError(f"builder input exceeds capture limit: {logical_name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    total[0] += size
    return {"logical_name": logical_name, "relative_filename": relative_filename, "sha256": _sha256(destination), "byte_size": size}


def _paths(workspace: Path, run_id: str) -> tuple[Path, Path]:
    root = workspace / "local" / "diagnostics" / "builder_failure"
    return root / ".pending" / run_id, root / run_id


def prepare(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace_root).resolve()
    pending, final = _paths(workspace, args.run_id)
    if pending.exists() or final.exists():
        raise BundleError("diagnostic bundle run ID already exists")
    pending.mkdir(parents=True)
    total = [0]
    artifacts: list[dict[str, object]] = []
    inputs = [
        (Path(args.roster_run) / "raw_clan_response.json", "inputs/roster/raw_clan_response.json", "roster_response"),
        (Path(args.roster_run) / "probe_metadata.json", "inputs/roster/probe_metadata.json", "roster_metadata"),
        (Path(args.current_war_run) / "raw_current_war_response.json", "inputs/current-war/raw_current_war_response.json", "current_war_response"),
        (Path(args.current_war_run) / "probe_metadata.json", "inputs/current-war/probe_metadata.json", "current_war_metadata"),
        (Path(args.war_log_run) / "raw_war_log_response.json", "inputs/war-log/raw_war_log_response.json", "war_log_response"),
        (Path(args.war_log_run) / "probe_metadata.json", "inputs/war-log/probe_metadata.json", "war_log_metadata"),
        (Path(args.history_path), "workspace/data/war_history/history.json", "existing_history"),
    ]
    for source, relative, logical in inputs:
        artifacts.append(_copy(source, pending / relative, logical, relative, total))
    manual = workspace / "data" / "manual" / "war_evidence" / "linked_manual_evidence.json"
    if manual.is_file():
        relative = "workspace/data/manual/war_evidence/linked_manual_evidence.json"
        artifacts.append(_copy(manual, pending / relative, "manual_overlay", relative, total))
    site_data = Path(args.site_data_dir)
    for name in PUBLIC_NAMES:
        source = site_data / name
        if source.is_file():
            relative = f"workspace/repo/site/data/{name}"
            artifacts.append(_copy(source, pending / relative, f"site_data_{name}", relative, total))
    manifest = {
        "schema_version": SCHEMA_VERSION, "run_id": args.run_id,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository_head": args.repository_head, "builder_entrypoint": "scripts/update/build_site_update.py",
        "python_version": platform.python_version(), "artifact_count": len(artifacts), "artifacts": artifacts,
        "builder_process_exit_code": None, "builder_result_code": None, "exception_type": None,
        "safe_error_path": None, "replay_supported": True,
    }
    (pending / "reproduction-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def finalize(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace_root).resolve()
    pending, final = _paths(workspace, args.run_id)
    if not pending.is_dir():
        raise BundleError("pending diagnostic bundle is missing")
    if args.success:
        shutil.rmtree(pending)
        return 0
    if final.exists():
        raise BundleError("persistent diagnostic bundle already exists")
    manifest_path = pending / "reproduction-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    safe_text = args.safe_error_text or ""
    path_match = re.search(r"history\.wars\[\d+\](?:\.[A-Za-z_]+)+", safe_text)
    type_match = re.search(r"(?:clan_analytics\.history\.)?([A-Za-z]+Error)", safe_text)
    manifest.update(builder_process_exit_code=args.process_exit_code, builder_result_code="builder_failure",
                    exception_type=type_match.group(1) if type_match else None,
                    safe_error_path=path_match.group(0) if path_match else None)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    final.parent.mkdir(parents=True, exist_ok=True)
    pending.replace(final)
    return 0


def verify(bundle: Path) -> dict[str, object]:
    manifest = json.loads((bundle / "reproduction-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise BundleError("unsupported bundle schema")
    for item in manifest.get("artifacts", []):
        path = bundle / str(item["relative_filename"])
        if not path.is_file() or path.stat().st_size != item["byte_size"] or _sha256(path) != item["sha256"]:
            raise BundleError(f"bundle artifact verification failed: {item['logical_name']}")
    return manifest


def replay(args: argparse.Namespace) -> int:
    bundle = Path(args.bundle).resolve()
    verify(bundle)
    repo = Path(__file__).resolve().parents[2]
    workspace = bundle / "workspace"
    with tempfile.TemporaryDirectory(prefix="clan-builder-replay-") as temporary:
        command = [sys.executable, str(repo / "scripts/update/build_site_update.py"),
            "--roster-run", str(bundle / "inputs/roster"), "--current-war-run", str(bundle / "inputs/current-war"),
            "--war-log-run", str(bundle / "inputs/war-log"), "--history-path", str(workspace / "data/war_history/history.json"),
            "--site-data-dir", str(workspace / "repo/site/data"), "--output-dir", str(Path(temporary) / "proposal")]
        result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        safe_path = re.search(r"history\.wars\[\d+\](?:\.[A-Za-z_]+)+", result.stderr)
        print(json.dumps({"status": "reproduced_failure", "process_exit_code": result.returncode,
                          "safe_error_path": safe_path.group(0) if safe_path else None}, sort_keys=True))
    else:
        print(json.dumps({"status": "success", "process_exit_code": 0}, sort_keys=True))
    return result.returncode


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    sub = result.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    for name in ("workspace_root", "run_id", "repository_head", "roster_run", "current_war_run", "war_log_run", "history_path", "site_data_dir"):
        prep.add_argument("--" + name.replace("_", "-"), required=True)
    prep.set_defaults(func=prepare)
    done = sub.add_parser("finalize")
    done.add_argument("--workspace-root", required=True); done.add_argument("--run-id", required=True)
    done.add_argument("--success", action="store_true"); done.add_argument("--process-exit-code", type=int, default=0)
    done.add_argument("--safe-error-text", default=""); done.set_defaults(func=finalize)
    rep = sub.add_parser("replay"); rep.add_argument("--bundle", required=True); rep.set_defaults(func=replay)
    return result


def main() -> int:
    try:
        args = parser().parse_args()
        return args.func(args)
    except (BundleError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"builder failure bundle error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
