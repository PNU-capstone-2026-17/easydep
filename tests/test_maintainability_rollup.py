"""유지보수성 리팩터링의 최종 공개 계약과 경계 방향을 검증한다."""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

from app.design.services.api_spec import ApiSpecModel, build_openapi_from_model
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.deployment_diagram.bundle import hydrate_deployment_diagram_bundle
from app.design.services.deployment_diagram.models import WorkloadGraph
from app.design.services.deployment_diagram.provider_plantuml import (
    deployment_bundle_provisioning_puml,
)
from app.design.services.erd.plantuml import generate_erd_from_bce_json
from app.design.services.sequence_diagram import SequenceCollection
from app.design.services.sequence_diagram.plantuml import generate_sequence_from_model
from app.implementation.domain.implementation_ir import (
    parse_components,
    parse_erd_entities,
    parse_openapi_operations,
)
from app.requirements.schemas import AnalyzeResponse

_ROOT = Path(__file__).resolve().parents[1]
_STATE = (
    _ROOT
    / "evaluation/baselines/course-registration-cases/goldset/e1-aws"
    / "snapshots/deployment_diagram/state.json"
)


def test_frozen_contract_chain_reprojects_and_reaches_implementation() -> None:
    """한 current fixture의 requirements→implementation 외부 shape를 연속 검증한다."""
    state = json.loads(_STATE.read_text(encoding="utf-8"))

    response = AnalyzeResponse.model_validate(
        {
            "thread_id": "rollup",
            "phase": "diagram",
            "status": "completed",
            "requirements": state["classified"],
            "actors": state["actors"],
            "use_cases": state["use_cases"],
            "coverage": state["coverage"],
            "use_case_specs": state["use_case_specs"],
            "relationships": state["relationships"],
            "diagram": state["diagram"],
            "capability_contract": state["capability_contract"],
            "resource_spec": state["resource_spec"],
            "resource_intake": state["resource_intake"],
        }
    )
    assert response.requirements

    class_puml = generate_plantuml_from_bce_json(state["extracted_bce_classes"])
    assert class_puml == state["class_diagram_puml"]
    sequence = SequenceCollection.model_validate(state["sequence_diagram_model"])
    sequence_puml = generate_sequence_from_model(sequence.model_dump(mode="json"))
    assert sequence_puml.count("@startuml") == len(sequence.Diagrams)

    api_model = ApiSpecModel.model_validate(state["api_spec_model"])
    assert build_openapi_from_model(api_model) == state["api_spec"]
    assert generate_erd_from_bce_json(state["erd_bce_classes"]) == state["erd_puml"]

    workload = WorkloadGraph.model_validate(state["deployment_workload_graph"])
    assert workload.workloads
    hydrated = hydrate_deployment_diagram_bundle(state["deployment_diagram_bundle"])
    assert hydrated["deployment_workload_graph"] == state["deployment_workload_graph"]
    assert hydrated["deployment_plan"] == state["deployment_plan"]
    assert hydrated["deployment_resource_plan"] == state["deployment_resource_plan"]
    assert deployment_bundle_provisioning_puml(state["deployment_diagram_bundle"]) == (
        state["deployment_diagram_provisioning_puml"]
    )

    assert parse_components(class_puml)
    assert parse_openapi_operations(json.dumps(state["api_spec"], ensure_ascii=False))
    assert parse_erd_entities(state["erd_puml"])


def test_removed_namespaces_have_no_tracked_or_active_consumers() -> None:
    """app.core와 구 requirements orchestration 경로가 active 코드로 돌아오지 않는다."""
    tracked = subprocess.run(
        ["git", "ls-files", "--", "app/core"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert tracked == []

    legacy = (
        "app.requirements.agent.graph",
        "app.requirements.agent.subgraphs",
        "app.requirements.agent.supervisor",
        "app.requirements.agent.playbook",
        "app.requirements.agent.steps.feedback_gates",
        "app.requirements.api",
        "app.requirements.feedback",
        "app.requirements.runner",
        "app.requirements.session_store",
    )
    facades = {
        "app/requirements/agent/graph.py",
        "app/requirements/agent/subgraphs.py",
        "app/requirements/agent/supervisor.py",
        "app/requirements/agent/playbook.py",
        "app/requirements/agent/steps/feedback_gates.py",
        "app/requirements/api.py",
        "app/requirements/feedback.py",
        "app/requirements/runner.py",
    }
    offenders: list[str] = []
    for path in sorted((_ROOT / "app").rglob("*.py")):
        relative = path.relative_to(_ROOT).as_posix()
        if relative in facades:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [
                    node.module,
                    *(f"{node.module}.{alias.name}" for alias in node.names),
                ]
            offenders.extend(
                f"{relative}:{getattr(node, 'lineno', 0)}:{module}"
                for module in modules
                if module in legacy
            )
    assert offenders == []


def test_rollup_policy_documents_current_checkpoint_and_context_ownership() -> None:
    text = (_ROOT / "docs/maintainability-rollup.md").read_text(encoding="utf-8")
    assert "현재 checkpoint schema" in text
    assert "과거 MySQL checkpoint shape" in text
    assert "migration" in text
    for context in ("cloudkb", "requirements", "design", "orchestration", "implementation"):
        assert f"`app.{context}`" in text
