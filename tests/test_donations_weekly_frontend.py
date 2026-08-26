from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class DonationsWeeklyFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (REPO_ROOT / "site" / "index.html").read_text(encoding="utf-8")
        cls.app = (REPO_ROOT / "site" / "assets" / "js" / "app.js").read_text(
            encoding="utf-8"
        )
        cls.contract = (
            REPO_ROOT / "site" / "assets" / "js" / "donations-weekly-contract.js"
        ).read_text(encoding="utf-8")

    def test_weekly_section_and_independent_states_exist(self) -> None:
        self.assertIn('id="donations"', self.html)
        self.assertIn("Недельные пожертвования", self.html)
        self.assertIn("data-donations-loading", self.html)
        self.assertIn("data-donations-error", self.html)
        self.assertIn("data-donations-content", self.html)
        self.assertIn("Данные о пожертвованиях временно недоступны.", self.html)

    def test_relative_resource_and_contract_are_loaded(self) -> None:
        self.assertIn("data/donations-weekly.json", self.app)
        self.assertNotIn("svisbee.github.io", self.app.lower())
        self.assertIn("assets/js/donations-weekly-contract.js", self.html)
        self.assertIn("donations-counter-schema-v2-20260825", self.html)

    def test_week_selector_summary_and_leaderboard_contract(self) -> None:
        self.assertIn('data-donations-selection="current"', self.html)
        self.assertIn('data-donations-selection="previous"', self.html)
        self.assertIn("Передано", self.html)
        self.assertIn("Получено", self.html)
        self.assertIn("Активных донатеров", self.html)
        for heading in ("№", "Игрок", "Передано", "Получено"):
            self.assertIn(heading, self.html)

    def test_weekly_failure_is_caught_without_calling_the_site_error_handler(self) -> None:
        loader = re.search(
            r"const loadWeeklyDonations = async \(collectedAt = null\) => \{(?P<body>.*?)\n\};",
            self.app,
            re.DOTALL,
        )
        self.assertIsNotNone(loader)
        body = loader.group("body")
        self.assertIn("try {", body)
        self.assertIn("catch (error)", body)
        self.assertNotIn("showError", body)
        self.assertLess(self.app.rfind("renderSite("), self.app.rfind("await loadWeeklyDonations(config.collected_at);"))

    def test_v2_raw_counter_wording_replaces_delta_lower_bound_wording(self) -> None:
        self.assertIn("Показываются последние значения игровых счётчиков", self.html)
        self.assertIn("Текущие показатели в игре", self.contract)
        self.assertIn("Последний снимок предыдущей календарной недели", self.contract)
        self.assertIn("Игровой момент сброса пока не подтверждён", self.contract)
        self.assertIn("раньше конца календарной недели", self.contract)
        self.assertNotIn("Последний зафиксированный итог", self.contract)
        self.assertNotIn("подтверждённый минимум", (self.html + self.contract).lower())
        self.assertIn("week.donations", self.app)
        self.assertIn("player.donations", self.app)
        self.assertNotIn("week.donations_confirmed", self.app)

    def test_frontend_does_not_reference_private_identity_fields(self) -> None:
        frontend = self.app + "\n" + self.contract
        forbidden = (
            "player_tag",
            "clan_tag",
            "internal_id",
            "player_id_internal",
            "payload_id",
            "observation_id",
            "fingerprint",
            "source_run_id",
        )
        for field in forbidden:
            with self.subTest(field=field):
                self.assertNotIn(field, frontend)
        self.assertNotRegex(frontend, r"nickname\s*(?:=>|:)\s*(?:identity|id)")


if __name__ == "__main__":
    unittest.main()
