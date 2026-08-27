"""모듈식 4단계 워크플로의 지연 로딩 공개 API."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_CONTRACT_EXPORTS = {"RunRequest", "RunResult"}
_GRAPH_EXPORTS = {"get_run", "resume_run", "retry_failed_run", "run_batch", "start_run"}

__all__ = sorted(_CONTRACT_EXPORTS | _GRAPH_EXPORTS)


def __getattr__(name: str) -> Any:
    """하위 도구 실행이 전체 요구사항 그래프를 불필요하게 import하지 않게 한다."""
    if name in _CONTRACT_EXPORTS:
        return getattr(import_module("app.orchestration.contracts"), name)
    if name in _GRAPH_EXPORTS:
        return getattr(import_module("app.orchestration.graph"), name)
    raise AttributeError(name)
