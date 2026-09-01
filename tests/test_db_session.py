"""Database bootstrap safety and destructive development reset tests."""

from __future__ import annotations

from typing import Any

import pytest

from app.db import session as db_session


class _FakeConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: Any) -> None:
        self.statements.append(str(statement))


class _FakeTransaction:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    def __enter__(self) -> _FakeConnection:
        return self.connection

    def __exit__(self, *_args: Any) -> None:
        return None


class _FakeEngine:
    def __init__(self) -> None:
        self.connection = _FakeConnection()
        self.disposed = False

    def begin(self) -> _FakeTransaction:
        return _FakeTransaction(self.connection)

    def dispose(self) -> None:
        self.disposed = True


@pytest.mark.parametrize(
    "name",
    ["mysql", "information_schema", "performance_schema", "sys", "easydep-test"],
)
def test_protected_or_unsafe_database_names_are_rejected(name: str) -> None:
    with pytest.raises(ValueError):
        db_session._validated_database_name(name)


def test_reset_option_drops_database_before_recreating_current_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap = _FakeEngine()
    application = _FakeEngine()
    engines = iter([bootstrap, application])
    created_urls: list[str] = []
    created_tables_on: list[Any] = []

    def fake_create_engine(url: str, **_kwargs: Any) -> _FakeEngine:
        created_urls.append(url)
        return next(engines)

    monkeypatch.setattr(db_session.settings, "db_schema_reset_on_start", True)
    monkeypatch.setattr(
        db_session,
        "database_settings",
        lambda: {
            "host": "127.0.0.1",
            "port": 3306,
            "user": "root",
            "password": "secret",
            "name": "easydep",
        },
    )
    monkeypatch.setattr(db_session, "create_engine", fake_create_engine)
    monkeypatch.setattr(db_session, "_engine", None)
    monkeypatch.setattr(db_session, "_session_factory", object())
    monkeypatch.setattr(
        db_session.Base.metadata,
        "create_all",
        lambda engine: created_tables_on.append(engine),
    )

    db_session.init_db()

    assert bootstrap.connection.statements == [
        "DROP DATABASE IF EXISTS `easydep`",
        "CREATE DATABASE IF NOT EXISTS `easydep` DEFAULT CHARACTER SET utf8mb4",
    ]
    assert bootstrap.disposed is True
    assert created_tables_on == [application]
    assert db_session._session_factory is None
    assert created_urls[0].endswith("@127.0.0.1:3306/?charset=utf8mb4")
    assert created_urls[1].endswith("@127.0.0.1:3306/easydep?charset=utf8mb4")


def test_normal_start_never_drops_existing_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap = _FakeEngine()
    application = _FakeEngine()
    engines = iter([bootstrap, application])

    monkeypatch.setattr(db_session.settings, "db_schema_reset_on_start", False)
    monkeypatch.setattr(
        db_session,
        "database_settings",
        lambda: {
            "host": "127.0.0.1",
            "port": 3306,
            "user": "root",
            "password": "secret",
            "name": "easydep",
        },
    )
    monkeypatch.setattr(
        db_session,
        "create_engine",
        lambda *_args, **_kwargs: next(engines),
    )
    monkeypatch.setattr(db_session, "_engine", None)
    monkeypatch.setattr(db_session.Base.metadata, "create_all", lambda _engine: None)

    db_session.init_db()

    assert bootstrap.connection.statements == [
        "CREATE DATABASE IF NOT EXISTS `easydep` DEFAULT CHARACTER SET utf8mb4"
    ]

