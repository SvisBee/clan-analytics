"""Explicit entrypoint for the local clan roster probe."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from clan_analytics.api.client import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
