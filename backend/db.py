from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import Base


class Database:
    def __init__(self, url: str):
        options = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            options["connect_args"] = {"check_same_thread": False}
            if ":memory:" in url:
                options["poolclass"] = StaticPool
        self.engine = create_engine(url, **options)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self):
        with self.factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def migrate(self, testing=False):
        if testing:
            Base.metadata.create_all(self.engine)
            return
        from alembic import command
        from alembic.config import Config

        config = Config()
        config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
