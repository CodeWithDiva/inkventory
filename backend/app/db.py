from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

_connect_args: dict = {}
if settings.database_url.startswith("sqlite"):
    # SQLite is used from worker threads during generation jobs.
    _connect_args = {"check_same_thread": False, "timeout": 30}
    db_path = settings.database_url.removeprefix("sqlite:///")
    if db_path and db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(settings.database_url, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


BASE_DIR = Path(__file__).resolve().parent.parent


def init_db() -> None:
    """Bring the database schema up to date with Alembic migrations (backend/migrations)."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect, text

    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            # Several API workers start at once; let one migrate while the others wait.
            conn.execute(text("SELECT pg_advisory_xact_lock(727274)"))
        tables = set(inspect(conn).get_table_names())
        if "organizations" in tables and "alembic_version" not in tables:
            # Database created before migrations existed: it matches revision 0001
            # (after an in-place column added by early versions), so mark it as such.
            columns = {c["name"] for c in inspect(conn).get_columns("generation_jobs")}
            if "engine" not in columns:
                conn.execute(text("ALTER TABLE generation_jobs ADD COLUMN engine VARCHAR(120)"))
            cfg.attributes["connection"] = conn
            command.stamp(cfg, "0001")
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
