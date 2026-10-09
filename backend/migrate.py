"""Explicit migration entrypoint for deployment jobs and operators."""

from backend.config import Settings
from backend.db import Database

if __name__ == "__main__":
    Database(Settings().database_url).migrate()
