"""요구사항 orchestration의 공개 경계와 유지보수 규칙을 검증한다."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[1]
_PACKAGE = _ROOT / "app" / "requirements" / "orchestration"
_MODULES = (
    "service.py",
    "feedback.py",
    "feedback_gates.py",
    "graph.py",
    "persistence.py",
    "subgraphs.py",
    "supervisor.py",
)


@pytest.mark.parametrize("filename", _MODULES)
def test_orchestration_modules_do_not_reverse_depend_on_downstream_contexts(
    filename: str,
) -> None:
    """조율 계층이 design·implementation이나 단계 private helper를 소유하지 않는다."""
    tree = ast.parse((_PACKAGE / filename).read_text(encoding="utf-8"))
    forbidden_modules: list[str] = []
    private_stage_imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            forbidden_modules.extend(
                alias.name
                for alias in node.names
                if alias.name.startswith(("app.design", "app.implementation"))
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith(("app.design", "app.implementation")):
                forbidden_modules.append(module)
            if module.startswith(
                ("app.requirements.modeling", "app.requirements.resources")
            ):
                private_stage_imports.extend(
                    alias.name for alias in node.names if alias.name.startswith("_")
                )

    assert forbidden_modules == []
    assert private_stage_imports == []


def test_only_current_checkpoint_schema_is_supported() -> None:
    """과거 checkpoint shape 전용 facade·parser·migration fallback을 되살리지 않는다."""
    assert not (_ROOT / "app" / "requirements" / "session_store.py").exists()

    tree = ast.parse((_PACKAGE / "persistence.py").read_text(encoding="utf-8"))
    suspicious = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and any(token in node.name.lower() for token in ("legacy", "migrat", "fallback"))
    ]
    assert suspicious == []

    active_imports = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (_ROOT / "app" / "requirements").rglob("*.py")
    )
    assert "app.requirements.session_store" not in active_imports
