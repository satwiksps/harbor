"""Offline regressions for private Secret generation and accidental credential loss."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from local_settings import DEFAULTS, load_settings

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
        with tempfile.TemporaryDirectory(prefix="harbor-secret-test-") as directory:
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
        with tempfile.TemporaryDirectory(prefix="harbor-secret-test-") as directory:
            root = Path(directory)
            self.assertEqual(self.prepare(root).returncode, 0)
            before = (root / "k8s-app-secret.json").read_bytes()
            (root / "development.json").unlink()
            result = self.prepare(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Restore the private credential file", result.stderr)
            self.assertFalse((root / "development.json").exists())
            self.assertEqual(before, (root / "k8s-app-secret.json").read_bytes())

    def test_installation_settings_preserve_explicit_existing_identifiers(self):
        with tempfile.TemporaryDirectory(prefix="harbor-settings-test-") as directory:
            path = Path(directory) / "local-settings.json"
            self.assertEqual(load_settings(path), DEFAULTS)
            settings = {
                "clusterName": "existing-cluster",
                "namespace": "existing",
                "releaseName": "existing-release",
                "postgresUser": "existing",
                "postgresDatabase": "existing",
            }
            path.write_text(json.dumps(settings), encoding="utf-8")
            self.assertEqual(load_settings(path), settings)

    def test_invalid_installation_settings_fail_before_command_execution(self):
        with tempfile.TemporaryDirectory(prefix="harbor-settings-test-") as directory:
            path = Path(directory) / "local-settings.json"
            for invalid in ({"unknown": "harbor"}, {"namespace": ""}, {"clusterName": 1}, []):
                with self.subTest(invalid=invalid):
                    path.write_text(json.dumps(invalid), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_settings(path)


if __name__ == "__main__":
    unittest.main()
