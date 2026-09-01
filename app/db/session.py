"""MySQL 연결과 SQLAlchemy transaction의 공통 생성·정리 규칙을 제공한다.

repository는 이 모듈의 `session_scope()`를 사용한다. 업무 코드가 제각각 Engine이나 Session을
만들지 않게 하여 connection pool 설정, commit과 rollback 동작을 한곳에서 유지한다.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

from app.db.models import Base


load_dotenv()

# Engine과 sessionmaker는 process 안에서 하나만 만든다. 요청마다 새 connection pool을 만들면
# 연결 수가 빠르게 늘고, 서로 다른 pool 설정 때문에 장애 원인을 추적하기 어려워진다.
_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def database_settings() -> dict[str, str]:
    """전역 설정에서 데이터베이스 접속값만 이름이 분명한 dict로 꺼낸다."""
    return {
        "host": settings.db_host,
        "port": settings.db_port,
        "user": settings.db_user,
        "password": settings.db_password,
        "name": settings.db_name,
    }


def database_url(include_database: bool = True) -> str:
    """SQLAlchemy가 이해하는 MySQL URL을 만든다.

    database 자체를 만들 때는 아직 DB 이름을 URL에 넣을 수 없으므로 `include_database=False`를
    사용한다. 비밀번호는 URL 예약 문자가 들어 있어도 안전하도록 percent-encoding한다.
    """
    settings = database_settings()
    password = quote_plus(settings["password"])
    database = settings["name"] if include_database else ""
    return (
        f"mysql+pymysql://{settings['user']}:{password}"
        f"@{settings['host']}:{settings['port']}/{database}?charset=utf8mb4"
    )


def get_engine() -> Engine:
    """공용 SQLAlchemy Engine을 처음 요청할 때 한 번만 만든다."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            database_url(),
            pool_pre_ping=True,
            # 산출물 생성은 긴 LLM 호출 사이에 DB 연결을 오래 보유할 수 있다. MySQL의 기본
            # wait_timeout에 닿기 훨씬 전에 연결을 교체해, 다음 query에서 끊긴 연결을 받지 않게 한다.
            pool_recycle=3600,
            pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
            max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """공용 Engine에 연결된 Session factory를 지연 생성한다."""
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
    """한 transaction을 열고 성공 시 commit, 실패 시 rollback한다.

    `yield` 안에서 발생한 원래 예외를 그대로 다시 던지므로 호출자는 DB 오류를 숨기지 않고
    처리할 수 있다. `finally`에서 항상 Session을 닫아 connection을 pool로 돌려준다.
    """
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
    """개발·단일 서버 실행에 필요한 database와 table이 없으면 만든다.

    첫 연결은 database 이름을 제외해야 한다. database를 만든 뒤 bootstrap Engine을 즉시
    폐기하고, 실제 table 생성은 공용 Engine으로 수행한다.
    """
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

    engine = get_engine()
    Base.metadata.create_all(engine)
