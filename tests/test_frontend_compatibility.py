from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class FrontendCompatibilityTests(unittest.TestCase):
    def test_node_current_war_compatibility_contract(self) -> None:
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for the offline frontend contract test")
        result = subprocess.run(
            [node, "--test", str(REPO_ROOT / "tests" / "js" / "current-war-compatibility.test.js")],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
