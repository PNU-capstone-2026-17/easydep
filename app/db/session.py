from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

from app.db.models import Base


load_dotenv()

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def database_settings() -> dict[str, str]:
    return {
        "host": settings.db_host,
        "port": settings.db_port,
        "user": settings.db_user,
        "password": settings.db_password,
        "name": settings.db_name,
    }


def database_url(include_database: bool = True) -> str:
    settings = database_settings()
    password = quote_plus(settings["password"])
    database = settings["name"] if include_database else ""
    return (
        f"mysql+pymysql://{settings['user']}:{password}"
        f"@{settings['host']}:{settings['port']}/{database}?charset=utf8mb4"
    )


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(
            database_url(),
            pool_pre_ping=True,
            # Artifact generation holds a connection across long LLM calls, so
            # recycle well before MySQL's default 8h wait_timeout.
            pool_recycle=3600,
            pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
            max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            expire_on_commit=False,
        )
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session: commits on success, rolls back on failure."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create the database and tables if they do not exist yet."""
    settings = database_settings()
    bootstrap = create_engine(database_url(include_database=False), future=True)
    with bootstrap.connect() as connection:
        connection.execute(
            text(
                f"CREATE DATABASE IF NOT EXISTS `{settings['name']}` "
                "DEFAULT CHARACTER SET utf8mb4"
            )
        )
        connection.commit()
    bootstrap.dispose()

    Base.metadata.create_all(get_engine())
