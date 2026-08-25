from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from clan_analytics.site_update import _scan_public  # noqa: E402
from clan_analytics.donations_weekly_public import (  # noqa: E402
    validate_public_weekly_donations,
)


class PublicSiteDataTests(unittest.TestCase):
    def test_committed_public_json_is_single_safe_document(self) -> None:
        expected = {
            "roster.json": dict,
            "current-war.json": dict,
            "war-log.json": dict,
            "war-history.json": dict,
            "site-config.json": dict,
        }
        optional_until_first_builder_publication = {
            "donations-weekly.json": dict,
        }
        actual_names = {path.name for path in (REPO_ROOT / "site" / "data").glob("*.json")}
        required_names = set(expected)
        released_names = required_names | set(optional_until_first_builder_publication)
        self.assertIn(actual_names, (required_names, released_names))
        if "donations-weekly.json" in actual_names:
            expected.update(optional_until_first_builder_publication)
        for name, root_type in expected.items():
            with self.subTest(name=name):
                raw = (REPO_ROOT / "site" / "data" / name).read_bytes()
                text = raw.decode("utf-8")
                self.assertFalse(any(marker in text for marker in (
                    "Exit code:", "Wall time:", "Output:", "Traceback", "PowerShell",
                )))
                payload = json.loads(text)
                self.assertIsInstance(payload, root_type)
                _scan_public(payload, f"$.{name}")
                if name == "donations-weekly.json":
                    if payload.get("schema_version") == 2:
                        validate_public_weekly_donations(payload)
                    else:
                        self.assertEqual(1, payload.get("schema_version"))
                        self.assertEqual("confirmed_lower_bound", payload.get("metric_semantics"))


if __name__ == "__main__":
    unittest.main()
