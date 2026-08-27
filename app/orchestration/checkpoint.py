"""Small SQLite-backed LangGraph checkpoint adapter.

LangGraph's in-memory saver already owns the checkpoint semantics. This adapter
persists its serialized internal maps after every write, using only Python's
standard-library sqlite3 module. The database is trusted application state and
must not be replaced with an untrusted file.
"""

from __future__ import annotations

import pickle
import sqlite3
import threading
from collections import defaultdict
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

DEFAULT_CHECKPOINT_PATH = Path(".easydep/checkpoints/orchestration.sqlite3")


class SqliteMemorySaver(MemorySaver):
    """Persist a MemorySaver namespace as one transactional SQLite record."""

    def __init__(self, path: str | Path, store_id: str) -> None:
        self.path = Path(path)
        self.store_id = store_id
        self._lock = threading.RLock()
        super().__init__()
        self._initialize()
        self._load()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoint_stores (
                    store_id TEXT PRIMARY KEY,
                    payload BLOB NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def _load(self) -> None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM checkpoint_stores WHERE store_id = ?",
                (self.store_id,),
            ).fetchone()
            if row is None:
                return
            payload = pickle.loads(row[0])  # noqa: S301 - trusted local state only
            storage = defaultdict(lambda: defaultdict(dict))
            for thread_id, namespaces in payload.get("storage", {}).items():
                for namespace, checkpoints in namespaces.items():
                    storage[thread_id][namespace].update(checkpoints)
            self.storage = storage
            self.writes = defaultdict(dict, payload.get("writes", {}))
            self.blobs = dict(payload.get("blobs", {}))

    def _persist(self) -> None:
        with self._lock, self._connect() as connection:
            payload = pickle.dumps(
                {
                    "storage": {
                        thread_id: {
                            namespace: dict(checkpoints)
                            for namespace, checkpoints in namespaces.items()
                        }
                        for thread_id, namespaces in self.storage.items()
                    },
                    "writes": dict(self.writes),
                    "blobs": self.blobs,
                },
                protocol=pickle.HIGHEST_PROTOCOL,
            )
            connection.execute(
                """
                INSERT INTO checkpoint_stores (store_id, payload, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(store_id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (self.store_id, payload),
            )

    def put(self, config, checkpoint, metadata, new_versions):
        with self._lock:
            result = super().put(config, checkpoint, metadata, new_versions)
            self._persist()
            return result

    def put_writes(self, config, writes, task_id, task_path="") -> None:
        with self._lock:
            super().put_writes(config, writes, task_id, task_path)
            self._persist()

    def delete_thread(self, thread_id: str) -> None:
        with self._lock:
            super().delete_thread(thread_id)
            self._persist()

    def clear(self) -> None:
        """Delete this logical store without touching other graph stores."""
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM checkpoint_stores WHERE store_id = ?", (self.store_id,)
            )
        self.storage = defaultdict(lambda: defaultdict(dict))
        self.writes = defaultdict(dict)
        self.blobs = {}
