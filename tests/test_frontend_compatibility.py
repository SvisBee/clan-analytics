from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class FrontendCompatibilityTests(unittest.TestCase):
    def run_node_test(self, relative_path: str) -> None:
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for the offline frontend contract test")
        result = subprocess.run(
            [node, "--test", str(REPO_ROOT / relative_path)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_node_current_war_compatibility_contract(self) -> None:
        self.run_node_test("tests/js/current-war-compatibility.test.js")

    def test_node_weekly_donations_contract(self) -> None:
        self.run_node_test("tests/js/donations-weekly-contract.test.js")


if __name__ == "__main__":
    unittest.main()
