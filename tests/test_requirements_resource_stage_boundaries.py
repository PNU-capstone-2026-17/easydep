"""요구사항 resource stage의 공개 호출·상태·JSON 계약을 검증한다.

구조화 proposal은 주입한 test double로만 실행하며 실제 NIM·환율·검색
네트워크를 사용하지 않는다. prompt 문구와 private normalization helper는 검사하지
않고 accepted state patch와 관찰 가능한 호출 수만 고정한다.
"""

from __future__ import annotations

import ast
import inspect
import threading
from pathlib import Path
from typing import cast, get_type_hints

from app.requirements.contracts.request import AnalyzeRequest
from app.requirements.contracts.state import AgentState
from app.requirements.resources import (
    capability_extraction,
    cloud_inputs,
    input_registry,
    service,
)
from app.requirements.runtime import telemetry
from app.requirements.schemas import (
    AnalyzeResponse,
    CloudConstraintExtraction,
    DeploymentNeed,
    DeploymentNeedsResult,
)

ROOT = Path(__file__).resolve().parent.parent
RESOURCE_DIR = ROOT / "app" / "requirements" / "resources"
CANONICAL_STAGE_MODULES = (
    capability_extraction,
    cloud_inputs,
    service,
)


def test_decision_questions_use_the_current_depkb_condition_contract() -> None:
    """DepKB decision은 존재하지 않는 legacy detail 대신 current condition을 사용한다."""

    questions = input_registry.asks_for("gcp", ("vm",))
    decision = next(item for item in questions if item.id.startswith("decision.gcp."))

    assert decision.question.startswith("How should subnet be selected for nic?")
    assert "custom에선 필수" in decision.question


def test_capability_public_seam_preserves_sample_call_count_and_output_shape() -> None:
    """capability proposal은 지정 표본 수만큼만 호출하고 기존 alias JSON을 낸다."""

    requirement = "Application data must survive VM replacement."
    calls: list[tuple[list[dict], int]] = []

    def propose(listing: list[dict], seed: int) -> DeploymentNeedsResult:
        calls.append((listing, seed))
        return DeploymentNeedsResult(
            deploymentNeeds={
                "persistent_block_storage": DeploymentNeed(
                    role="Keep application data across VM replacement",
                    required=True,
                    requirementIds=["NFR1"],
                    evidenceSpans=[requirement],
                    origin="explicit",
                )
            }
        )

    result = capability_extraction.derive_deployment_needs(
        {"classified": [{"id": "NFR1", "text": requirement, "type": "NFR"}]},
        sample_count=3,
        proposal_call=propose,
    )

    assert len(calls) == 3
    assert len({seed for _listing, seed in calls}) == 3
    assert all(listing == [{"id": "NFR1", "text": requirement, "type": "NFR"}]
               for listing, _seed in calls)
    assert set(result) == {"deployment_needs", "capability_contract", "phase"}
    assert result["phase"] == "deployment_needs"
    need = result["deployment_needs"]["persistent_block_storage"]
    assert need["requirementIds"] == ["NFR1"]
    assert need["dependencyCapabilityIds"] == ["persistent-block-storage"]
    assert need["decision"] == "accepted"
    contract = result["capability_contract"]
    assert set(contract) == {"schemaVersion", "capabilities", "questions"}
    assert contract["schemaVersion"] == "CapabilityContract/v1"


def test_resource_proposal_is_called_once_then_cached_projection_calls_zero(
    monkeypatch,
) -> None:
    """resource proposal 1회와 cached accepted projection 0회 계약을 고정한다."""

    monkeypatch.setattr(service.settings, "resource_agent_llm", True)
    constraints = "Deploy to AWS in Seoul with a monthly budget of at most 100 USD."
    calls: list[str] = []

    def propose(briefing: str) -> CloudConstraintExtraction:
        calls.append(briefing)
        return CloudConstraintExtraction(
            provider="aws",
            provider_evidence="AWS",
            region_as_written="Seoul",
            region_evidence="Seoul",
            monthly_budget_amount=100,
            monthly_budget_currency="USD",
            monthly_budget_evidence="100 USD",
            understanding="AWS Seoul, up to 100 USD per month.",
        )

    state = cast(
        AgentState,
        {"classified": [], "resource_constraints_text": constraints},
    )
    intermediate = service.extract_resource_constraints(state, proposal_call=propose)

    assert len(calls) == 1
    assert constraints in calls[0]
    extraction = cast(
        dict[str, object], intermediate["resource_constraint_extraction"]
    )
    assert extraction["status"] == "completed"

    def unexpected(_briefing: str) -> CloudConstraintExtraction:
        raise AssertionError("cached extraction must not call the proposal adapter")

    result = service.build_resource_spec(
        {**state, **intermediate},
        proposal_call=unexpected,
    )

    assert result["resource_spec"] == {
        "schemaVersion": "4",
        "workloads": ["vm"],
        "provider": "aws",
        "regionAsWritten": "Seoul",
        "region": "ap-northeast-2",
        "monthlyBudgetUSD": 100.0,
    }
    intake = cast(dict[str, object], result["resource_intake"])
    assert set(intake) == {
        "draft",
        "valid",
        "errors",
        "questions",
        "understanding",
        "provenance",
        "rejected",
        "trace",
    }
    assert intake["valid"] is True


def test_disabled_resource_extraction_has_zero_proposal_calls(monkeypatch) -> None:
    """resource LLM 비활성은 호출 없이 disabled 중간 shape를 보존한다."""

    monkeypatch.setattr(service.settings, "resource_agent_llm", False)
    calls = 0

    def unexpected(_briefing: str) -> CloudConstraintExtraction:
        nonlocal calls
        calls += 1
        raise AssertionError("disabled extraction must not call the proposal adapter")

    result = service.extract_resource_constraints(
        cast(
            AgentState,
            {"classified": [], "resource_constraints_text": "Deploy to AWS."},
        ),
        proposal_call=unexpected,
    )

    assert calls == 0
    assert result == {
        "resource_constraint_extraction": {
            "status": "disabled",
            "degraded": (
                "The resource constraint LLM is disabled; no constraints were extracted."
            ),
        }
    }


def test_cloud_input_public_seam_overlaps_branches_and_propagates_context() -> None:
    """2-thread 분기와 ContextVar 전파, 결정론 merge shape를 함께 검증한다."""

    barrier = threading.Barrier(2, timeout=3)
    observed_runs: list[object] = []
    lock = threading.Lock()

    def deployment(_state: AgentState) -> dict[str, object]:
        with lock:
            observed_runs.append(telemetry.current_run())
        barrier.wait()
        return {
            "deployment_needs": {"ingress": {}},
            "capability_contract": {
                "schemaVersion": "CapabilityContract/v1",
                "capabilities": [],
                "questions": [],
            },
        }

    def constraints(_state: AgentState) -> dict[str, object]:
        with lock:
            observed_runs.append(telemetry.current_run())
        barrier.wait()
        return {
            "resource_constraint_extraction": {
                "status": "completed",
                "result": {},
            }
        }

    with telemetry.run_scope("resource-parallel") as stats:
        result = cloud_inputs.analyze_cloud_inputs(
            {"classified": []},
            deployment_call=deployment,
            constraint_call=constraints,
        )

    assert observed_runs == [stats, stats]
    assert result == {
        "deployment_needs": {"ingress": {}},
        "capability_contract": {
            "schemaVersion": "CapabilityContract/v1",
            "capabilities": [],
            "questions": [],
        },
        "resource_constraint_extraction": {
            "status": "completed",
            "result": {},
        },
        "phase": "cloud_inputs",
    }


def test_request_state_and_api_resource_json_shape_is_preserved() -> None:
    """resource 경계 이동이 AnalyzeRequest·AgentState·AnalyzeResponse JSON을 바꾸지 않는다."""

    request = AnalyzeRequest.model_validate(
        {
            "requirements": ["Users can enroll."],
            "resource_constraints_text": "Deploy to AWS in Seoul.",
            "cloud_constraints": {"provider": "aws", "region": "Seoul"},
        }
    )
    assert request.model_dump(mode="json", exclude_none=True) == {
        "requirements": ["Users can enroll."],
        "cloud_constraints": {
            "provider": "aws",
            "region": "Seoul",
            "monthly_budget_currency": "USD",
        },
        "resource_constraints_text": "Deploy to AWS in Seoul.",
    }
    assert {
        "deployment_needs",
        "capability_contract",
        "resource_constraint_extraction",
        "resource_intake",
        "resource_spec",
    } <= set(AgentState.__annotations__)

    response = AnalyzeResponse(
        thread_id="thread-1",
        phase="resource_spec",
        status="completed",
        deployment_needs={"ingress": {"decision": "accepted"}},
        capability_contract={
            "schemaVersion": "CapabilityContract/v1",
            "capabilities": [],
            "questions": [],
        },
        resource_spec={"schemaVersion": "4", "workloads": ["vm"]},
        resource_intake={"valid": True, "questions": []},
    )
    assert response.model_dump(mode="json", exclude_none=True) == {
        "thread_id": "thread-1",
        "phase": "resource_spec",
        "status": "completed",
        "deployment_needs": {"ingress": {"decision": "accepted"}},
        "capability_contract": {
            "schemaVersion": "CapabilityContract/v1",
            "capabilities": [],
            "questions": [],
        },
        "resource_spec": {"schemaVersion": "4", "workloads": ["vm"]},
        "resource_intake": {"valid": True, "questions": []},
    }


def test_legacy_resource_imports_delegate_to_canonical_public_boundaries() -> None:
    """기존 Python import가 독립 구현을 만들지 않고 canonical service를 가리킨다."""

    from app.requirements import capability_contract as legacy_contract
    from app.requirements.agent.steps import resource_tools as legacy_tools
    from app.requirements.agent.steps import step_cloud as legacy_capability
    from app.requirements.agent.steps import step_cloud_inputs as legacy_cloud_inputs
    from app.requirements.agent.steps import step_resource as legacy_resource
    from app.requirements.resources import capability_contract, tools

    assert legacy_capability.derive_deployment_needs.__wrapped__ is (
        capability_extraction.derive_deployment_needs
    )
    assert legacy_cloud_inputs.analyze_cloud_inputs.__wrapped__ is (
        cloud_inputs.analyze_cloud_inputs
    )
    assert legacy_resource.extract_resource_constraints.__wrapped__ is (
        service.extract_resource_constraints
    )
    assert legacy_resource.build_resource_spec.__wrapped__ is service.build_resource_spec
    assert legacy_contract.decide is capability_contract.decide
    assert legacy_contract.CalibrationPoint is capability_contract.CalibrationPoint
    assert legacy_tools.resolve_region is tools.resolve_region
    assert legacy_tools.convert_to_usd is tools.convert_to_usd


def test_resource_stage_import_direction_and_public_annotations_are_bounded() -> None:
    """canonical stage가 상위 실행·하류 영역을 역참조하거나 Any/bare dict를 노출하지 않는다."""

    forbidden = (
        "app.requirements.agent",
        "app.requirements.api",
        "app.requirements.runner",
        "app.requirements.session_store",
        "app.repositories",
        "app.design",
        "app.implementation",
        "app.orchestration",
        "app.workspace",
    )
    offenders: list[str] = []
    for path in sorted(RESOURCE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            offenders.extend(
                f"{path.name}:{module}"
                for module in modules
                if module.startswith(forbidden)
            )
    assert offenders == []

    public_functions = (
        capability_extraction.derive_deployment_needs,
        cloud_inputs.analyze_cloud_inputs,
        service.extract_resource_constraints,
        service.build_resource_spec,
    )
    for function in public_functions:
        signature = inspect.signature(function)
        hints = get_type_hints(function)
        assert signature.return_annotation is not dict
        assert "typing.Any" not in repr(hints)
        assert all(parameter.annotation is not dict for parameter in signature.parameters.values())


def test_resource_boundary_documentation_covers_operational_contract() -> None:
    """README가 단계 경계와 보존 정책을 누락하지 않는다."""

    readme = (RESOURCE_DIR / "README.md").read_text(encoding="utf-8")
    for heading in (
        "## 입력",
        "## 출력",
        "## 부수효과와 호출 범위",
        "## 사용하면 안 되는 import",
        "## 실패 조건",
        "## 보존 중인 tool-agent 경로",
    ):
        assert heading in readme
    assert "호출되지 않는다는 사실만으로 삭제하지 않는다" in readme
