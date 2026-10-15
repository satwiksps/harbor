"""Shared local installation identifiers for maintenance and verification scripts."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {
    "clusterName": "harbor",
    "namespace": "harbor",
    "releaseName": "harbor",
    "postgresUser": "harbor",
    "postgresDatabase": "harbor",
}


def load_settings(path=None):
    path = Path(path) if path is not None else ROOT / ".runtime/local-settings.json"
    overrides = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if not isinstance(overrides, dict) or set(overrides) - DEFAULTS.keys():
        raise ValueError("Unknown local installation settings")
    if any(
        not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", value)
        for value in overrides.values()
    ):
        raise ValueError("Invalid local installation identifier")
    return {**DEFAULTS, **overrides}


SETTINGS = load_settings()
CONTEXT = "k3d-" + SETTINGS["clusterName"]
NAMESPACE = SETTINGS["namespace"]
RELEASE = SETTINGS["releaseName"]
POSTGRES_USER = SETTINGS["postgresUser"]
POSTGRES_DATABASE = SETTINGS["postgresDatabase"]
KUBE = ["kubectl", "--context", CONTEXT, "-n", NAMESPACE]
