from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import Base


class Database:
    def __init__(self, url: str):
        options = {"pool_pre_ping": True}
        parsed = make_url(url)
        if parsed.get_backend_name() == "postgresql":
            # Bound failed connects, pool starvation and lock contention. Preserve
            # options such as a caller's search_path while enforcing request limits.
            configured_options = parsed.query.get("options", "")
            options.update(
                pool_timeout=5,
                connect_args={
                    "connect_timeout": 5,
                    "options": (
                        f"{configured_options} -c statement_timeout=15000 -c lock_timeout=5000"
                    ).strip(),
                    "keepalives": 1,
                    "keepalives_idle": 10,
                    "keepalives_interval": 5,
                    "keepalives_count": 2,
                    "tcp_user_timeout": 10000,
                },
            )
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
