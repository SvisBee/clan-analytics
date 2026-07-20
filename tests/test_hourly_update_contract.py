from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class HourlyUpdateContractTests(unittest.TestCase):
    def test_schema_v1_preflight_precedes_all_probe_invocations(self) -> None:
        script = (REPO_ROOT / "scripts" / "update" / "update_clan_site.ps1").read_text(encoding="utf-8")
        preflight = script.index("Test-HistorySchemaPreflight")
        roster_probe = script.index("Collecting current clan roster")
        config_load = script.index("Get-Content -LiteralPath $LocalConfigPath")
        self.assertLess(preflight, config_load)
        self.assertLess(preflight, roster_probe)
        self.assertIn("validate_war_history.py", script)

    def test_current_war_contract_is_required_and_syntax_checked(self) -> None:
        script = (REPO_ROOT / "scripts" / "update" / "update_clan_site.ps1").read_text(encoding="utf-8")
        contract = "site\\assets\\js\\current-war-contract.js"
        self.assertTrue((REPO_ROOT / "site" / "assets" / "js" / "current-war-contract.js").is_file())
        self.assertGreaterEqual(script.count(contract), 3)
        self.assertIn("Current-war contract syntax check", script)


if __name__ == "__main__":
    unittest.main()
