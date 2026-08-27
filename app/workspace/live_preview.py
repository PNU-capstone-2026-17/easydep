"""Versionless, process-local previews for an active workspace command."""
from __future__ import annotations

from dataclasses import dataclass, replace
from threading import RLock
from time import monotonic

TERMINAL_TTL_SECONDS = 600.0


@dataclass(frozen=True)
class LivePreview:
    app_id: str
    command_id: str
    stage: str
    revision: int
    puml: str
    phase: str
    unit: str
    completed: int
    total: int
    image_svg: bytes | None = None
    expires_at: float | None = None


class LivePreviewStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str], LivePreview] = {}
        self._lock = RLock()

    @staticmethod
    def _key(app_id: str, command_id: str, stage: str) -> tuple[str, str, str]:
        return app_id, command_id, stage

    def _remove_expired(self) -> None:
        now = monotonic()
        for key, item in list(self._items.items()):
            if item.expires_at is not None and item.expires_at <= now:
                self._items.pop(key, None)

    def publish(
        self,
        *,
        app_id: str,
        command_id: str,
        stage: str,
        puml: str,
        phase: str,
        unit: str = "",
        completed: int = 0,
        total: int = 0,
    ) -> LivePreview:
        if not puml.strip():
            raise ValueError("A live preview requires non-empty PlantUML.")
        key = self._key(app_id, command_id, stage)
        with self._lock:
            self._remove_expired()
            current = self._items.get(key)
            item = LivePreview(
                app_id=app_id,
                command_id=command_id,
                stage=stage,
                revision=(current.revision + 1 if current else 1),
                puml=puml,
                phase=phase,
                unit=unit,
                completed=max(0, completed),
                total=max(0, total),
            )
            self._items[key] = item
            return item

    def get(self, app_id: str, command_id: str, stage: str) -> LivePreview | None:
        with self._lock:
            self._remove_expired()
            return self._items.get(self._key(app_id, command_id, stage))

    def cache_svg(
        self, app_id: str, command_id: str, stage: str, revision: int, image: bytes,
    ) -> None:
        key = self._key(app_id, command_id, stage)
        with self._lock:
            current = self._items.get(key)
            if current is not None and current.revision == revision:
                self._items[key] = replace(current, image_svg=image)

    def mark_terminal(self, app_id: str, command_id: str) -> None:
        with self._lock:
            deadline = monotonic() + TERMINAL_TTL_SECONDS
            for key, item in list(self._items.items()):
                if item.app_id == app_id and item.command_id == command_id:
                    self._items[key] = replace(item, expires_at=deadline)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


live_previews = LivePreviewStore()
