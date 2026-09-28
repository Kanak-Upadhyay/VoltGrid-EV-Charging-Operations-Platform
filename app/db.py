import os
import re
import threading
from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings

engine: Engine | None = None
SessionLocal: sessionmaker[Session] | None = None
_seeded = False
_ready_lock = threading.Lock()

_DB_NAME = re.compile(r"^[A-Za-z0-9_]+$")


def _ensure_mysql_database(url: str) -> None:
    parsed = make_url(url)
    if not parsed.drivername.startswith("mysql"):
        return
    dbname = parsed.database
    if not dbname or not _DB_NAME.match(dbname):
        raise RuntimeError("MySQL database name must be alphanumeric")
    server = create_engine(parsed.set(database=None), pool_pre_ping=True)
    try:
        with server.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{dbname}`"))
    finally:
        server.dispose()


def init_db(url: str | None = None, force: bool = False) -> None:
    global engine, SessionLocal
    if engine is not None and not force:
        return
    settings = get_settings()
    url = url or settings.database_url
    kwargs: dict = {}
    if url.startswith("sqlite"):
        database_path = make_url(url).database
        if database_path and database_path not in (":memory:", ""):
            from pathlib import Path

            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["poolclass"] = StaticPool
    else:
        _ensure_mysql_database(url)
        kwargs["pool_pre_ping"] = True
        if settings.mysql_ssl:
            import ssl

            kwargs["connect_args"] = {"ssl": ssl.create_default_context()}
    engine = create_engine(url, **kwargs)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    from app.models import Base

    Base.metadata.create_all(engine)


def new_session() -> Session:
    if SessionLocal is None:
        raise RuntimeError("Database is not initialized")
    return SessionLocal()


def ensure_ready() -> None:
    """Create tables and demo data. Vercel does not keep a local MySQL server."""
    global _seeded
    with _ready_lock:
        init_db()
        if get_settings().seed_on_startup and not _seeded:
            from app.seed import seed

            db = new_session()
            try:
                seed(db)
            finally:
                db.close()
            _seeded = True


def get_db() -> Iterator[Session]:
    ensure_ready()
    session = new_session()
    try:
        yield session
    finally:
        session.close()


def mysql_lock(statement, session: Session):
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "mysql":
        return statement.with_for_update()
    return statement
