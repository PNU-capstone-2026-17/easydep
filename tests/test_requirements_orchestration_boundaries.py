"""요구사항 orchestration의 공개 경계와 유지보수 규칙을 검증한다."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import get_type_hints

import pytest

_ROOT = Path(__file__).parents[1]
_PACKAGE = _ROOT / "app" / "requirements" / "orchestration"
_MODULES = (
    "api.py",
    "feedback.py",
    "feedback_gates.py",
    "graph.py",
    "persistence.py",
    "runner.py",
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


def test_public_orchestration_signatures_do_not_add_bare_dict_or_any() -> None:
    """새 공개 seam은 현재 state·JSON 값의 경계를 annotation으로 드러낸다."""
    from app.requirements.orchestration import (
        api,
        feedback,
        feedback_gates,
        graph,
        runner,
        subgraphs,
        supervisor,
    )

    public_functions = (
        api.persist_analysis,
        api.analyze_endpoint,
        feedback.classify_feedback,
        feedback.resolve_intent,
        feedback.apply_feedback_upto,
        feedback.apply_feedback,
        feedback_gates.route_gate,
        feedback_gates.gate_requirements,
        feedback_gates.gate_use_cases,
        feedback_gates.gate_specs,
        feedback_gates.gate_relationships,
        graph.build_graph,
        graph.start_analysis,
        graph.resume_analysis,
        graph.result_payload,
        runner.load_input,
        runner.load_state,
        runner.run_pipeline,
        runner.persist_run,
        subgraphs.build_stage,
        subgraphs.build_stage_subgraphs,
        supervisor.decide,
        supervisor.blocking_issues,
        supervisor.supervise_for,
    )
    def contains_unbounded(annotation: object) -> bool:
        """중첩 generic과 forward annotation까지 bare dict·Any를 찾는다."""

        from typing import Any, get_args, get_origin

        if annotation in (dict, Any, "dict", "Any"):
            return True
        if isinstance(annotation, str):
            return "Any" in annotation or annotation.strip() == "dict"
        origin = get_origin(annotation)
        return (origin is dict and not get_args(annotation)) or any(
            contains_unbounded(item) for item in get_args(annotation)
        )

    violations: list[str] = []
    for function in public_functions:
        signature = inspect.signature(function)
        hints = get_type_hints(function)
        annotations = [
            hints.get(parameter.name, parameter.annotation)
            for parameter in signature.parameters.values()
        ] + [hints.get("return", signature.return_annotation)]
        if inspect.Signature.empty in annotations:
            violations.append(f"{function.__module__}.{function.__name__}: missing")
        for annotation in annotations:
            if contains_unbounded(annotation):
                violations.append(
                    f"{function.__module__}.{function.__name__}: {annotation!r}"
                )

    assert violations == []


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
