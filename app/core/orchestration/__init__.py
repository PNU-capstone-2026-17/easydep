"""Cross-agent workflow orchestration with lazy graph initialization."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_GRAPH_EXPORTS = {
    "build_orchestration_graph",
    "complete_design",
    "complete_implementation",
    "resume_workflow",
    "start_design_from_cached_requirements",
    "start_implementation_from_completed_design",
    "start_workflow",
}

__all__ = sorted(_GRAPH_EXPORTS)


def __getattr__(name: str) -> Any:
    """Load graph APIs only when used to keep package imports lightweight."""
    if name not in _GRAPH_EXPORTS:
        raise AttributeError(name)
    graph_module = import_module("app.core.orchestration.graph")
    return getattr(graph_module, name)
