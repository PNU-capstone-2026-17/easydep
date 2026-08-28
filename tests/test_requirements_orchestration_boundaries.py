"""요구사항 orchestration의 공개 경계와 유지보수 규칙을 검증한다."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

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


def test_legacy_imports_reexport_canonical_public_objects() -> None:
    """호환 경로가 orchestration 구현의 사본을 만들지 않는다."""
    from app.requirements import api as legacy_api
    from app.requirements import feedback as legacy_feedback
    from app.requirements import runner as legacy_runner
    from app.requirements.agent import graph as legacy_graph
    from app.requirements.agent import subgraphs as legacy_subgraphs
    from app.requirements.agent import supervisor as legacy_supervisor
    from app.requirements.agent.steps import feedback_gates as legacy_gates
    from app.requirements.orchestration import (
        api,
        feedback,
        feedback_gates,
        graph,
        runner,
        subgraphs,
        supervisor,
    )

    pairs = (
        (legacy_api.analyze_endpoint, api.analyze_endpoint),
        (legacy_api.persist_analysis, api.persist_analysis),
        (legacy_feedback.apply_feedback, feedback.apply_feedback),
        (legacy_feedback.apply_feedback_upto, feedback.apply_feedback_upto),
        (legacy_runner.run_pipeline, runner.run_pipeline),
        (legacy_runner.persist_run, runner.persist_run),
        (legacy_graph.build_graph, graph.build_graph),
        (legacy_graph.start_analysis, graph.start_analysis),
        (legacy_graph.resume_analysis, graph.resume_analysis),
        (legacy_subgraphs.build_stage, subgraphs.build_stage),
        (legacy_supervisor.decide, supervisor.decide),
        (legacy_gates.gate_requirements, feedback_gates.gate_requirements),
    )
    assert all(legacy is canonical for legacy, canonical in pairs)


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
    violations: list[str] = []
    for function in public_functions:
        signature = inspect.signature(function)
        annotations = [
            parameter.annotation for parameter in signature.parameters.values()
        ] + [signature.return_annotation]
        if inspect.Signature.empty in annotations:
            violations.append(f"{function.__module__}.{function.__name__}: missing")
        for annotation in annotations:
            if annotation in (dict, "dict", "Any"):
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


def test_orchestration_readme_records_the_operational_contract() -> None:
    """조율 경계의 입력·출력·부수효과·실패와 금지 의존성을 문서화한다."""
    text = (_PACKAGE / "README.md").read_text(encoding="utf-8")
    for heading in (
        "## 입력",
        "## 출력",
        "## 부수효과와 호출 범위",
        "## 금지 의존성",
        "## 실패 조건",
        "## 호환 경계",
    ):
        assert heading in text
    assert "PIPELINE" in text
    assert "과거 requirements checkpoint shape 전용 MySQL parser" in text
    assert "실제 NIM 호출을 하지 않는다" in text
