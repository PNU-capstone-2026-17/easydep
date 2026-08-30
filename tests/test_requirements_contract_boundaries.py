"""요구사항 요청·상태 contract의 canonical shape을 검증한다.

공개 Pydantic·TypedDict 경계만 사용하며 endpoint, graph state 저장소,
private serializer의 구현 모양에는 결합하지 않는다.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from app.requirements.contracts import request, state

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = ROOT / "app" / "requirements" / "contracts"


def test_stage_registry_is_importable_first_in_a_fresh_process() -> None:
    """canonical registry를 먼저 import해도 agent package 순환 import가 없다."""

    completed = subprocess.run(  # noqa: S603 - 고정된 현재 Python interpreter만 실행
        [
            sys.executable,
            "-X",
            "utf8",
            "-c",
            (
                "from app.requirements import stage_registry; "
                "assert stage_registry.PIPELINE"
            ),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_analyze_request_public_shape_and_normalization_are_preserved() -> None:
    """canonical request는 기존 HTTP input field 순서와 정규화를 유지한다."""

    assert tuple(request.AnalyzeRequest.model_fields) == (
        "requirements",
        "cloud_constraints",
        "deployment_preferences",
        "resource_constraints_text",
        "answer",
        "edit",
        "resource_answers",
        "thread_id",
        "feedback_gates",
        "app_id",
    )

    value = request.AnalyzeRequest.model_validate(
        {
            "requirements": ["Users can sign in."],
            "cloud_constraints": {
                "provider": "aws",
                "region": " Seoul ",
                "monthly_budget_currency": "usd",
            },
        }
    )

    assert value.model_dump(exclude_none=True) == {
        "requirements": ["Users can sign in."],
        "cloud_constraints": {
            "provider": "aws",
            "region": "Seoul",
            "monthly_budget_currency": "USD",
        },
    }


def test_agent_state_public_key_order_is_preserved() -> None:
    """checkpoint·graph consumer가 사용하는 state key 목록을 고정한다."""

    assert tuple(state.AgentState.__annotations__) == (
        "messages",
        "raw_requirements",
        "expanded_requirements",
        "expanded_source_refs",
        "refined_requirements",
        "requirement_drafts",
        "requirement_source_issues",
        "constraint_links",
        "classified",
        "phase",
        "deployment_needs",
        "capability_contract",
        "capability_answers",
        "resource_constraints_text",
        "initial_cloud_constraints",
        "resource_constraint_extraction",
        "resource_answers",
        "resource_intake",
        "resource_spec",
        "actors",
        "use_cases",
        "constraint_applicability",
        "coverage",
        "traceability",
        "model_review",
        "use_case_specs",
        "spec_report",
        "relationships",
        "relationship_report",
        "diagram",
        "gate_route",
        "redo_rounds",
        "redo_history",
        "stage_feedback",
        "redo_route",
    )


def test_contract_modules_do_not_import_execution_or_downstream_layers() -> None:
    """typed contract가 agent runtime·API·세션·하류 구현을 역참조하지 않는다."""

    forbidden = (
        "app.requirements.agent",
        "app.requirements.api",
        "app.requirements.runtime",
        "app.requirements.session_store",
        "app.design",
        "app.implementation",
    )
    offenders: list[str] = []
    for path in sorted(CONTRACTS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.append(node.module)
        offenders.extend(
            f"{path.name}:{module}"
            for module in imported
            if module.startswith(forbidden)
        )

    assert offenders == []
