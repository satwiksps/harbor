"""Explicit targets for deployed checks, with unchanged local defaults.

Set HARBOR_URL, HARBOR_STORE_URL, HARBOR_TEST_CREDENTIALS and
HARBOR_TEST_OUTPUT_DIR before starting a harness. The credentials setting points
to a private JSON file, never a credential value. Reports and screenshots use
separate subdirectories beneath the output directory. Keep both paths ignored.
"""

import os
from pathlib import Path


def console_url(default="http://harbor.localhost:8080"):
    return os.getenv("HARBOR_URL", default).rstrip("/")


def store_url():
    return os.getenv("HARBOR_STORE_URL", "http://store.localhost:8080").rstrip("/")


def credentials_path(root):
    return Path(os.getenv("HARBOR_TEST_CREDENTIALS", str(root / ".secrets/development.json")))


def private_path(root, name):
    # A deployed member/key file must stay beside that deployment's credentials.
    return credentials_path(root).parent / name


def output_path(root, *parts):
    return Path(os.getenv("HARBOR_TEST_OUTPUT_DIR", str(root / ".runtime"))).joinpath(*parts)
