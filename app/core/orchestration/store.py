"""Small dependency-free SQLite store for resumable orchestration state."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_RUN_STORE = Path(".easydep/orchestration/runs.sqlite3")


class RunStore:
    def __init__(self, path: str | Path = DEFAULT_RUN_STORE) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def save(self, run_id: str, state: dict[str, Any]) -> None:
        encoded = json.dumps(state, ensure_ascii=False, default=str)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs(run_id, state_json) VALUES (?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (run_id, encoded),
            )

    def load(self, run_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown orchestration run: {run_id}")
        value = json.loads(row[0])
        if not isinstance(value, dict):
            raise TypeError(f"Stored orchestration state is invalid: {run_id}")
        return value
