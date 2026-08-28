"""요구사항 modeling stage의 공개 proposal·검증·repair·projection 계약을 검증한다.

모든 structured proposal과 semantic review는 test double로 주입한다. production
prompt 문자열이나 private helper를 읽지 않으며 실제 NIM/네트워크 호출은 없다.
"""

from __future__ import annotations

import ast
import inspect
import threading
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

from app.requirements.config import Settings
from app.requirements.contracts.state import AgentState
from app.requirements.knowledge import rules
from app.requirements.modeling import (
    diagram,
    refinement,
    relationships,
    specifications,
    use_cases,
    validation,
)
from app.requirements.orchestration import supervisor
from app.requirements.runtime import telemetry
from app.requirements.schemas import (
    Actor,
    ActorResult,
    AnalyzeResponse,
    ClarifyOnlyResult,
    Critique,
    ExpandedRequirementsResult,
    MainScenarioStep,
    RuleVerdict,
    UseCase,
    UseCaseResult,
    UseCaseSpec,
)

ROOT = Path(__file__).resolve().parent.parent
MODELING_DIR = ROOT / "app" / "requirements" / "modeling"


def test_requirements_llm_concurrency_default_matches_measured_nim_limit() -> None:
    """요구사항 LLM 병렬도 기본값은 NIM에서 실측한 8개 호출을 유지한다."""

    assert Settings.model_fields["spec_concurrency"].default == 8


def _actor_result() -> ActorResult:
    return ActorResult(actors=[
        Actor(
            name="Member",
            description="A registered service member.",
            sourceRefs=["R1"],
        )
    ])


def _use_case_result(*, traced: bool = True) -> UseCaseResult:
    return UseCaseResult(use_cases=[
        UseCase(
            name="Submit request",
            primary_actor="Member",
            goal="submit a service request",
            requirement_ids=["R1"] if traced else [],
        )
    ])


def _clean_spec(trigger: str = "Member submits a request") -> UseCaseSpec:
    return UseCaseSpec(
        preconditions=["The member is eligible."],
        trigger=trigger,
        main_scenario=[
            MainScenarioStep(
                step_number=1,
                sentence="Member submits a service request.",
                covered_req_ids=["R1"],
            )
        ],
        success_guarantee=[
            {"sentence": "The request is recorded.", "covered_req_ids": []}
        ],
    )


def _use_case_item(identifier: str, name: str) -> dict[str, object]:
    return {
        "id": identifier,
        "name": name,
        "primary_actor": "Member",
        "supporting_actors": [],
        "level": "user_goal",
        "goal": name,
        "requirement_ids": ["R1"],
        "nfr_ids": [],
    }


def test_refinement_public_services_accept_typed_proposals_and_preserve_patch_shape() -> None:
    """expansion과 refinement가 proposal 1회씩 accepted RR patch를 만든다."""

    schemas: list[type] = []

    def propose(schema, _messages):
        schemas.append(schema)
        if schema is ExpandedRequirementsResult:
            return ExpandedRequirementsResult(
                requirements=["Members shall submit service requests."]
            )
        return ClarifyOnlyResult.model_validate({
            "requirementDrafts": [{
                "text": "Members shall submit service requests.",
                "sourceRefs": ["RAW1"],
            }]
        })

    expanded = refinement.expand_requirements(
        {"raw_requirements": ["Build a request service."]},
        proposal_call=propose,
    )
    accepted = refinement.clarify(
        {
            "raw_requirements": ["Build a request service."],
            **expanded,
            "messages": [],
        },
        proposal_call=propose,
    )

    assert schemas == [ExpandedRequirementsResult, ClarifyOnlyResult]
    assert accepted == {
        "refined_requirements": ["Members shall submit service requests."],
        "constraint_links": [],
        "requirement_drafts": [{
            "text": "Members shall submit service requests.",
            "sourceRefs": ["RAW1"],
            "ref": "RR1",
        }],
        "requirement_source_issues": [],
        "phase": "clarify",
    }


def test_actor_and_use_case_public_services_preserve_calls_and_accepted_json() -> None:
    """actor와 use-case proposal은 정상 입력에서 각각 1회만 호출된다."""

    schemas: list[type] = []

    def propose(schema, _messages):
        schemas.append(schema)
        return _actor_result() if schema is ActorResult else _use_case_result()

    classified = [{"id": "R1", "text": "Members submit requests.", "type": "FR"}]
    actor_patch = use_cases.identify_actors(
        {"classified": classified}, proposal_call=propose
    )
    use_case_patch = use_cases.identify_use_cases(
        {"classified": classified, **actor_patch}, proposal_call=propose
    )

    assert schemas == [ActorResult, UseCaseResult]
    assert actor_patch == {
        "actors": [{
            "name": "Member",
            "description": "A registered service member.",
            "parent_actor": None,
            "source_refs": ["R1"],
        }],
        "phase": "actors",
    }
    assert use_case_patch["use_cases"] == [{
        "id": "UC1",
        "name": "Submit request",
        "primary_actor": "Member",
        "supporting_actors": [],
        "level": "user_goal",
        "goal": "submit a service request",
        "requirement_ids": ["R1"],
        "nfr_ids": [],
    }]


def test_step2_trace_audits_overlap_and_propagate_the_run_context(monkeypatch) -> None:
    """독립 requirement trace audit 두 건이 같은 run context에서 겹쳐 실행된다."""

    monkeypatch.setattr(use_cases.settings, "spec_concurrency", 2)
    barrier = threading.Barrier(2, timeout=3)
    lock = threading.Lock()
    audited: list[tuple[str, object]] = []

    def propose(schema, _messages):
        if schema is UseCaseResult:
            return UseCaseResult(use_cases=[
                UseCase(name="Browse", primary_actor="Member", goal="browse"),
                UseCase(name="Enroll", primary_actor="Member", goal="enroll"),
            ])
        with lock:
            index = len(audited)
            requirement_id, use_case_name = (("R1", "Browse"), ("R2", "Enroll"))[index]
            audited.append((requirement_id, telemetry.current_run()))
        barrier.wait()
        return schema.model_validate({
            "requirement_id": requirement_id,
            "realized_by_use_case_names": [use_case_name],
        })

    state: AgentState = {
        "classified": [
            {"id": "R1", "text": "Members browse offerings.", "type": "FR"},
            {"id": "R2", "text": "Members enroll in offerings.", "type": "FR"},
        ],
        "actors": [{
            "name": "Member",
            "description": "member",
            "parent_actor": None,
            "source_refs": ["R1", "R2"],
        }],
    }
    with telemetry.run_scope("modeling-step2") as stats:
        patch = use_cases.identify_use_cases(state, proposal_call=propose)

    assert [item["requirement_ids"] for item in patch["use_cases"]] == [["R1"], ["R2"]]
    assert len(audited) == 2
    assert all(run is stats for _requirement_id, run in audited)


def test_step3_generation_overlaps_preserves_order_and_uses_one_call_per_unit(
    monkeypatch,
) -> None:
    """명세 generation은 UC별 1회 호출을 병렬 실행하고 입력 순서로 취합한다."""

    monkeypatch.setattr(specifications.settings, "spec_concurrency", 3)
    barrier = threading.Barrier(3, timeout=3)
    lock = threading.Lock()
    observed_runs: list[object] = []

    def propose(_schema, _messages):
        with lock:
            observed_runs.append(telemetry.current_run())
        barrier.wait()
        return _clean_spec()

    use_case_items = [
        _use_case_item("UC1", "First"),
        _use_case_item("UC2", "Second"),
        _use_case_item("UC3", "Third"),
    ]
    state = {
        "use_cases": use_case_items,
        "classified": [{"id": "R1", "text": "Members submit requests.", "type": "FR"}],
        "actors": [],
    }
    with telemetry.run_scope("modeling-step3") as stats:
        patch = specifications.generate_specs(
            state,
            proposal_call=propose,
            review_call=lambda *_args, **_kwargs: validation.Review(),
        )

    assert [item["use_case_id"] for item in patch["use_case_specs"]] == [
        "UC1",
        "UC2",
        "UC3",
    ]
    assert len(observed_runs) == 3
    assert all(run is stats for run in observed_runs)


def test_specification_repair_stalls_after_unique_strategies_are_exhausted() -> None:
    """계속 같은 결함·후보면 두 고유 전략 뒤 명시적으로 정체된다."""
    calls = 0

    def propose(_schema, _messages):
        nonlocal calls
        calls += 1
        return _clean_spec(trigger="User clicks the submit button")

    item = specifications.generate_specification(
        _use_case_item("UC1", "Submit request"),
        {"R1": {"id": "R1", "text": "Members submit requests.", "type": "FR"}},
        [],
        proposal_call=propose,
        review_call=lambda *_args, **_kwargs: validation.Review(),
    )

    assert calls == 3
    assert item["repair_iters"] == 2
    assert item["repair_stopped"] == "stalled"
    assert item["issues"]


def test_relationship_repair_is_one_bounded_selection_rerun() -> None:
    """확정 relationship finding은 proposal 전체를 정확히 한 번만 재선택한다."""

    state = {
        "actors": [{
            "name": "Member",
            "description": "member",
            "parent_actor": None,
            "source_refs": ["R1"],
        }],
        "classified": [{"id": "R1", "text": "Shared validation.", "type": "FR"}],
        "use_cases": [
            _use_case_item("UC1", "First"),
            _use_case_item("UC2", "Second"),
        ],
        "use_case_specs": [
            {
                "use_case_id": identifier,
                "main_scenario": [{
                    "step_number": index,
                    "sentence": "System validates the request.",
                    "covered_req_ids": ["R1"],
                }],
                "issues": [],
                "semantic_status": validation.OK,
            }
            for index, identifier in enumerate(("UC1", "UC2"), 1)
        ],
    }
    proposal_calls = 0
    reviews = iter([
        validation.Review(findings=["[rel] independently confirmed defect"]),
        validation.Review(),
    ])

    def propose(schema, _messages):
        nonlocal proposal_calls
        proposal_calls += 1
        return schema()

    patch = relationships.identify_relationships(
        state,
        proposal_call=propose,
        review_call=lambda *_args, **_kwargs: next(reviews),
    )

    assert proposal_calls == 2
    assert patch["relationships"]["repair_iters"] == 1
    assert patch["relationships"]["repair_stopped"] == "clean"


def test_semantic_validator_voting_keeps_logical_and_physical_call_count(
    monkeypatch,
) -> None:
    """3표 validator는 physical 3회로 과반 finding 하나만 채택한다."""

    monkeypatch.setattr(validation.settings, "enable_semantic_validator", True)
    monkeypatch.setattr(validation.settings, "validator_per_rule", False)
    monkeypatch.setattr(validation.settings, "validator_votes", 3)
    rule_ids = [
        rule.id
        for rule in rules.judged_by(rules.WRITE_SPECIFICATIONS, rules.JUDGED_VALIDATOR)
    ]
    ballots = []
    for index in range(3):
        ballots.append(Critique(verdicts=[
            RuleVerdict(
                rule_id=rule_id,
                violated=rule_id == "spec.no-scope-creep" and index < 2,
                directive="remove invented scope"
                if rule_id == "spec.no-scope-creep" and index < 2
                else "",
            )
            for rule_id in rule_ids
        ]))
    calls = 0

    def propose(_schema, _messages):
        nonlocal calls
        result = ballots[calls]
        calls += 1
        return result

    monkeypatch.setattr(validation, "invoke_structured", propose)
    review = validation.review(
        rules.WRITE_SPECIFICATIONS,
        {"trigger": "Member submits a request."},
        prefix="semantic",
        source="spec.semantic_validator",
    )

    assert calls == 3
    assert {rules.rule_of(finding) for finding in review.findings} == {
        "spec.no-scope-creep"
    }


def test_supervisor_and_registry_preserve_the_downstream_rerun_scope(monkeypatch) -> None:
    """가장 상류 defect 하나만 선택하고 기존 cascade 순서를 유지한다."""

    from app.requirements import stage_registry

    actor_issue = f"[model] remove system actor {rules.tag_of('actors.sud-is-not-an-actor')}"
    spec_issue = f"[semantic] remove scope {rules.tag_of('spec.no-scope-creep')}"
    decision = supervisor.decide({
        "model_review": {
            "issues": [actor_issue],
            "semantic_status": validation.OK,
            "unexamined_rules": [],
        },
        "use_case_specs": [{
            "use_case_id": "UC1",
            "issues": [spec_issue],
            "repair_stopped": "clean",
        }],
        "relationships": {},
    })

    assert decision.owner == "actors"
    assert stage_registry.cascade_order() == (
        "actors",
        "use_cases",
        "coverage",
        "specs",
        "relationships",
        "diagram",
    )


def test_http_state_json_and_plantuml_shape_remain_compatible() -> None:
    """canonical stage 결과가 기존 HTTP/AgentState key와 PlantUML shape에 맞는다."""

    actors = [{
        "name": "Member",
        "description": "member",
        "parent_actor": None,
        "source_refs": ["R1"],
    }]
    modeled_use_cases = [_use_case_item("UC1", "Submit request")]
    relationship_patch = {
        "associations": [{"actor": "Member", "use_case_id": "UC1"}],
        "includes": [],
        "extends": [],
        "generalizations": [],
        "derived_use_cases": [],
    }
    plantuml = diagram.render_diagram({
        "actors": actors,
        "use_cases": modeled_use_cases,
        "relationships": relationship_patch,
    })["diagram"]
    response = AnalyzeResponse(
        thread_id="thread-modeling",
        phase="diagram",
        status="completed",
        actors=actors,
        use_cases=modeled_use_cases,
        relationships=relationship_patch,
        diagram=plantuml,
    ).model_dump(mode="json", exclude_none=True)

    assert {
        "actors",
        "use_cases",
        "use_case_specs",
        "relationships",
        "diagram",
    } <= set(AgentState.__annotations__)
    assert tuple(response) == (
        "thread_id",
        "phase",
        "status",
        "actors",
        "use_cases",
        "relationships",
        "diagram",
    )
    assert response["diagram"].startswith("@startuml\n")
    assert response["diagram"].endswith("\n@enduml")


def test_legacy_stage_imports_delegate_to_canonical_public_boundaries() -> None:
    """기존 step1–4 import는 독립 구현이 아니라 canonical service를 재노출한다."""

    from app.requirements.agent.steps import step1_requirements as legacy_step1
    from app.requirements.agent.steps import step2_usecases as legacy_step2
    from app.requirements.agent.steps import step3_specifications as legacy_step3
    from app.requirements.agent.steps import step4_diagram as legacy_step4

    assert legacy_step1.expand_requirements.__wrapped__ is refinement.expand_requirements
    assert legacy_step2.identify_use_cases.__wrapped__ is use_cases.identify_use_cases
    assert legacy_step3.generate_specs.__wrapped__ is specifications.generate_specs
    assert legacy_step4.identify_relationships.__wrapped__ is (
        relationships.identify_relationships
    )
    assert legacy_step4.render_diagram is diagram.render_diagram


def _contains_unbounded_annotation(annotation: object) -> bool:
    """annotation tree에 Any 또는 parameter 없는 dict가 있는지 판정한다."""

    if annotation is Any or annotation is dict:
        return True
    origin = get_origin(annotation)
    if origin is dict and not get_args(annotation):
        return True
    return any(_contains_unbounded_annotation(item) for item in get_args(annotation))


def test_modeling_import_direction_and_public_annotations_are_bounded() -> None:
    """modeling은 orchestration/downstream을 역참조하거나 Any/bare dict를 노출하지 않는다."""

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
    for path in sorted(MODELING_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
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
        refinement.expand_requirements,
        refinement.normalize_expansion,
        refinement.normalize_refinement,
        refinement.clarify,
        use_cases.normalize_actors,
        use_cases.normalize_use_cases,
        use_cases.identify_actors,
        use_cases.identify_use_cases,
        use_cases.review_model,
        specifications.normalize_specification,
        specifications.validate_specification,
        specifications.generate_specification,
        specifications.generate_specs,
        relationships.select_relationship_parts,
        relationships.identify_relationships,
        diagram.render_diagram,
    )
    for function in public_functions:
        assert not any(
            _contains_unbounded_annotation(annotation)
            for annotation in get_type_hints(function).values()
        ), function.__qualname__
        assert inspect.signature(function).return_annotation is not inspect.Signature.empty


def test_modeling_documentation_covers_the_operational_contract() -> None:
    """README가 입력·출력·부수효과·실패와 import 제한을 모두 기록한다."""

    readme = (MODELING_DIR / "README.md").read_text(encoding="utf-8")
    for heading in (
        "## 입력",
        "## 출력",
        "## 부수효과와 호출 범위",
        "## 사용하면 안 되는 import",
        "## 실패 조건",
        "## 호환 경계",
    ):
        assert heading in readme
    assert "숫자 상한 대신" in readme
    assert "별도의 dead-path 근거 없이 제거하지" in readme
