"""Offline regressions for private Secret generation and accidental credential loss."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PREPARE = Path(__file__).with_name("prepare-secrets.py")


class SecretPreparationTests(unittest.TestCase):
    def prepare(self, directory):
        return subprocess.run(
            [sys.executable, str(PREPARE), "--output-dir", str(directory)],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_repeated_preparation_preserves_credentials(self):
        with tempfile.TemporaryDirectory(prefix="commerce-secret-test-") as directory:
            root = Path(directory)
            self.assertEqual(self.prepare(root).returncode, 0)
            before = (root / "development.json").read_bytes()
            result = self.prepare(root)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(before, (root / "development.json").read_bytes())
            credentials = json.loads(before)
            manifest = json.loads((root / "k8s-app-secret.json").read_text())
            self.assertEqual(
                manifest["stringData"]["session_secret"], credentials["session_secret"]
            )
            self.assertNotIn(credentials["session_secret"], result.stdout + result.stderr)

    def test_lost_credential_file_does_not_replace_existing_manifests(self):
        with tempfile.TemporaryDirectory(prefix="commerce-secret-test-") as directory:
            root = Path(directory)
            self.assertEqual(self.prepare(root).returncode, 0)
            before = (root / "k8s-app-secret.json").read_bytes()
            (root / "development.json").unlink()
            result = self.prepare(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Restore the private credential file", result.stderr)
            self.assertFalse((root / "development.json").exists())
            self.assertEqual(before, (root / "k8s-app-secret.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
