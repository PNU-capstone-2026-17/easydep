"""Export the report-case enrollment-count feedback as reproducible evidence.

The before model is the restored e1-aws report checkpoint.  The after model and
conversation record come from one completed Workspace API revision command.
Focused diagrams are deterministic projections of those complete saved models;
this script does not ask an LLM to redraw or summarize either model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.db.models import WorkspaceCommand
from app.db.session import session_scope
from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.class_diagram.scenario import build_scenario_index
from app.design.services.common.plantuml import render_plantuml
from app.design.services.sequence_diagram.plantuml import generate_sequence_from_model
from app.design.services.sequence_diagram.projection import (
    project_sequence_model,
    sequence_findings,
)
from app.repositories import artifact_repository
from app.workspace.repository import command_dict, get_command

DEFAULT_OUTPUT = (
    ROOT
    / "docs"
    / "final-report-feedback-evidence"
    / "report-case-class-enrollment-count"
)
SOURCE_STATE = (
    ROOT
    / "artifacts"
    / "checkpoint-e2e"
    / "current"
    / "e1-aws"
    / "chain"
    / "snapshots"
    / "class_diagram"
    / "state.json"
)
API_BASE = "http://127.0.0.1:8100"
APP_ID = "f6c0853d-1c7c-48a8-86f2-9703402e9268"
REVIEW_COMMAND_ID = "17a3f4a7-eabf-44cf-b979-2575c5644721"
SUCCESS_COMMAND_ID = "2f95a641-6922-459f-8756-03b9d7c0c9fa"
USER_FEEDBACK = (
    "CourseOffering increases the enrollment count when a registration is added, "
    "but it does not decrease the count when a registration is removed. Add the "
    "corresponding decrease operation and apply it to every relevant removal flow. "
    "Preserve the existing increment behavior."
)
USER_FEEDBACK_KO = (
    "CourseOffering는 수강 등록이 추가될 때 등록 인원을 증가시키지만, 등록이 제거될 "
    "때는 인원을 감소시키지 않습니다. 이에 대응하는 감소 연산을 추가하고 관련된 모든 "
    "등록 제거 흐름에 적용하세요. 기존 증가 동작은 유지하세요."
)
REQUEST_BODY = {
    "action": "message",
    "text": USER_FEEDBACK,
    "action_id": REVIEW_COMMAND_ID,
    "context": {"artifact_stage": "class_diagram"},
}
EXPECTED_TARGET_INSTRUCTIONS = {
    "class_diagram:CourseOffering": (
        "Add operation decrementEnrolledCount() returning void to the "
        "CourseOffering class."
    ),
    "class_diagram:UC3:main:1": (
        "Insert a call to CourseOffering::decrementEnrolledCount() before the "
        "existing call to CourseOffering::incrementEnrolledCount() in the UC3 swap flow."
    ),
    "class_diagram:UC5:main:1": (
        "Insert a call to CourseOffering::decrementEnrolledCount() after the call "
        "to Registration::deleteById(id:UUID) in the UC5 drop flow."
    ),
}


def api_json(path: str) -> Any:
    with urllib.request.urlopen(f"{API_BASE}{path}", timeout=30) as response:
        return json.load(response)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def class_named(model: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [item for item in model["Classes"] if item.get("className") == name]
    if len(matches) != 1:
        raise ValueError(f"Expected one class named {name}, found {len(matches)}.")
    return matches[0]


def collaboration_named(model: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [
        item
        for item in model["Collaborations"]
        if item.get("collaborationId") == name
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one collaboration named {name}, found {len(matches)}."
        )
    return matches[0]


def operation_named(class_item: dict[str, Any], name: str) -> dict[str, Any] | None:
    matches = [item for item in class_item["operations"] if item.get("name") == name]
    if len(matches) > 1:
        raise ValueError(f"Duplicate operation {name} in {class_item['className']}.")
    return matches[0] if matches else None


def call_operations(collaboration: dict[str, Any]) -> list[str]:
    return [str(item.get("receiverOperationId") or "") for item in collaboration["calls"]]


def verify_existing_calls_preserved(
    before_collaboration: dict[str, Any],
    after_collaboration: dict[str, Any],
) -> None:
    """Allow only position-derived identifier remaps around the inserted call."""

    before_calls = before_collaboration["calls"]
    after_calls = [
        call
        for call in after_collaboration["calls"]
        if call.get("receiverOperationId")
        != "CourseOffering::decrementEnrolledCount()"
    ]
    if len(before_calls) != len(after_calls):
        raise AssertionError("An existing collaboration call was added or removed.")
    id_map = {
        str(old.get("callId") or ""): str(new.get("callId") or "")
        for old, new in zip(before_calls, after_calls, strict=True)
    }

    def remap_reference(value: object) -> object:
        if not isinstance(value, str):
            return value
        for old_id, new_id in id_map.items():
            if value == old_id or value.startswith(f"{old_id}#"):
                return f"{new_id}{value[len(old_id):]}"
        return value

    for old, new in zip(before_calls, after_calls, strict=True):
        expected = deepcopy(old)
        expected["callId"] = id_map[str(old.get("callId") or "")]
        if "parentCallId" in expected:
            expected["parentCallId"] = remap_reference(expected["parentCallId"])
        for binding in expected.get("argumentBindings") or []:
            if "sourceRef" in binding:
                binding["sourceRef"] = remap_reference(binding["sourceRef"])
        if expected != new:
            raise AssertionError(
                "An existing call changed beyond position-derived references: "
                f"{old.get('callId')}"
            )


def canonical_semantic_model(model: dict[str, Any]) -> dict[str, Any]:
    """Normalize old type spellings and ignore storage-only stable identifiers."""

    normalized = BCEModel.model_validate(model).model_dump(by_alias=True)
    for class_item in normalized["Classes"]:
        for operation in class_item["operations"]:
            operation["stableId"] = None
    for collaboration in normalized["Collaborations"]:
        for call in collaboration["calls"]:
            call["stableId"] = None
    return normalized


def changed_named_items(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    key: str,
) -> list[str]:
    after_by_name = {str(item[key]): item for item in after}
    return [
        str(item[key])
        for item in before
        if item != after_by_name.get(str(item[key]))
    ]


def command_history() -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(WorkspaceCommand)
                .where(WorkspaceCommand.app_id == APP_ID)
                .order_by(WorkspaceCommand.created_at)
            )
        )
        return [command_dict(row) for row in rows]


def focused_class_source(model: dict[str, Any]) -> str:
    offering = deepcopy(class_named(model, "CourseOffering"))
    return generate_plantuml_from_bce_json(
        {
            "Classes": [offering],
            "DataTypes": [],
            "Relationships": [],
            "Collaborations": [],
        }
    )


def focused_sequence_source(
    sequence: dict[str, Any], use_case_id: str
) -> tuple[dict[str, Any], str]:
    diagram = next(
        item for item in sequence["Diagrams"] if item["use_case_id"] == use_case_id
    )
    return diagram, generate_sequence_from_model(diagram)


def render_source(source: str, stem: Path) -> None:
    write_text(stem.with_suffix(".puml"), source)
    stem.with_suffix(".svg").write_bytes(render_plantuml(source, "svg"))
    stem.with_suffix(".png").write_bytes(render_plantuml(source, "png"))


def verify(
    source_state: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    workspace: dict[str, Any],
    feedback_command: dict[str, Any],
    feedback_events: list[dict[str, Any]],
    before_sequence: dict[str, Any],
    after_sequence: dict[str, Any],
) -> dict[str, Any]:
    if before != source_state["extracted_bce_classes"]:
        raise AssertionError("Workspace before model differs from the report checkpoint.")
    if workspace["command"]["command_id"] != SUCCESS_COMMAND_ID:
        raise AssertionError("The successful command is no longer current.")
    if workspace["command"]["status"] != "AWAITING_INPUT":
        raise AssertionError("The feedback command did not return to review.")
    if feedback_command["status"] != "AWAITING_INPUT":
        raise AssertionError("The persisted feedback command is not awaiting review.")
    if not any(
        event.get("actor") == "user" and event.get("text") == USER_FEEDBACK
        for event in feedback_events
    ):
        raise AssertionError("The exact original user feedback event is missing.")

    expected_targets = [
        "class_diagram:CourseOffering",
        "class_diagram:UC3:main:1",
        "class_diagram:UC5:main:1",
    ]
    interpretation = feedback_command["payload"]["revision_interpretation"]
    if interpretation["targets"] != expected_targets:
        raise AssertionError(f"Unexpected interpreted targets: {interpretation['targets']}")
    if [item["target"] for item in interpretation["target_instructions"]] != expected_targets:
        raise AssertionError("The per-target LLM decomposition is incomplete.")
    actual_instructions = {
        item["target"]: item["instruction"]
        for item in interpretation["target_instructions"]
    }
    if actual_instructions != EXPECTED_TARGET_INSTRUCTIONS:
        raise AssertionError(
            f"The stored LLM instructions changed: {actual_instructions}"
        )

    before_offering = class_named(before, "CourseOffering")
    after_offering = class_named(after, "CourseOffering")
    if operation_named(before_offering, "decrementEnrolledCount") is not None:
        raise AssertionError("The before model already has the decrease operation.")
    decrement = operation_named(after_offering, "decrementEnrolledCount")
    if decrement is None:
        raise AssertionError("The after model lacks the decrease operation.")
    if decrement["parameters"] or decrement["returnType"] != "void":
        raise AssertionError("The decrease operation signature is unexpected.")
    if decrement["stepRefs"] != ["UC3:main:4", "UC5:main:3"]:
        raise AssertionError("The decrease operation trace refs are unexpected.")
    before_shell = {key: value for key, value in before_offering.items() if key != "operations"}
    after_shell = {key: value for key, value in after_offering.items() if key != "operations"}
    if before_shell != after_shell:
        raise AssertionError("CourseOffering changed outside its operation list.")
    if after_offering["operations"][:-1] != before_offering["operations"]:
        raise AssertionError("An existing CourseOffering operation changed or moved.")
    if after_offering["operations"][-1] != decrement:
        raise AssertionError("The decrease operation was not the sole appended operation.")

    before_increment = operation_named(before_offering, "incrementEnrolledCount")
    after_increment = operation_named(after_offering, "incrementEnrolledCount")
    if before_increment is None or after_increment is None:
        raise AssertionError("The existing increment operation was not preserved.")
    increment_fields = ("name", "parameters", "returnType", "stepRefs")
    if any(before_increment[key] != after_increment[key] for key in increment_fields):
        raise AssertionError("The existing increment operation contract changed.")

    semantic_before = canonical_semantic_model(before)
    semantic_after = canonical_semantic_model(after)
    changed_classes = changed_named_items(
        semantic_before["Classes"], semantic_after["Classes"], "className"
    )
    changed_collaborations = changed_named_items(
        semantic_before["Collaborations"],
        semantic_after["Collaborations"],
        "collaborationId",
    )
    if changed_classes != ["CourseOffering"]:
        raise AssertionError(f"Unexpected changed classes: {changed_classes}")
    if changed_collaborations != ["UC3:main:1", "UC5:main:1"]:
        raise AssertionError(
            f"Unexpected changed collaborations: {changed_collaborations}"
        )
    if semantic_before["DataTypes"] != semantic_after["DataTypes"]:
        raise AssertionError("Data types changed.")
    if semantic_before["Relationships"] != semantic_after["Relationships"]:
        raise AssertionError("Relationships changed.")

    expected_calls = {
        "UC3:main:1": {
            "before": [
                "StudentBoundary::swapCourseRegistration(request:SwapRequest)",
                "RegistrationControl::performSwap(request:SwapRequest)",
                "Student::isAuthorizedForSwap()",
                "CourseOffering::isEligibleForSwap(studentId:uuid)",
                "Registration::existsForStudentOffering(studentId:uuid,offeringId:uuid)",
                "Registration::deleteById(id:uuid)",
                "Registration::create(studentId:uuid,offeringId:uuid,registrationDate:localdatetime)",
                "CourseOffering::incrementEnrolledCount()",
            ],
            "after": [
                "StudentBoundary::swapCourseRegistration(request:SwapRequest)",
                "RegistrationControl::performSwap(request:SwapRequest)",
                "Student::isAuthorizedForSwap()",
                "CourseOffering::isEligibleForSwap(studentId:uuid)",
                "Registration::existsForStudentOffering(studentId:uuid,offeringId:uuid)",
                "Registration::deleteById(id:uuid)",
                "Registration::create(studentId:uuid,offeringId:uuid,registrationDate:localdatetime)",
                "CourseOffering::decrementEnrolledCount()",
                "CourseOffering::incrementEnrolledCount()",
            ],
        },
        "UC5:main:1": {
            "before": [
                "StudentBoundary::dropRegistration(request:DropRequest)",
                "RegistrationControl::processDrop(request:DropRequest)",
                "Student::isAuthorizedForRegistration()",
                "Registration::findById(id:uuid)",
                "Registration::deleteById(id:uuid)",
            ],
            "after": [
                "StudentBoundary::dropRegistration(request:DropRequest)",
                "RegistrationControl::processDrop(request:DropRequest)",
                "Student::isAuthorizedForRegistration()",
                "Registration::findById(id:uuid)",
                "Registration::deleteById(id:uuid)",
                "CourseOffering::decrementEnrolledCount()",
            ],
        },
    }
    canonical_before = canonical_semantic_model(before)
    canonical_after = canonical_semantic_model(after)
    for collaboration_id, expected in expected_calls.items():
        before_collaboration = collaboration_named(before, collaboration_id)
        after_collaboration = collaboration_named(after, collaboration_id)
        actual_before = call_operations(before_collaboration)
        actual_after = call_operations(after_collaboration)
        if actual_before != expected["before"] or actual_after != expected["after"]:
            raise AssertionError(f"Unexpected call order for {collaboration_id}.")
        verify_existing_calls_preserved(before_collaboration, after_collaboration)

    for model in (canonical_before, canonical_after):
        for collaboration in model["Collaborations"]:
            call_ids = {call["callId"] for call in collaboration["calls"]}
            if any(
                call["parentCallId"] is not None
                and call["parentCallId"] not in call_ids
                for call in collaboration["calls"]
            ):
                raise AssertionError(
                    f"Broken parent call ref in {collaboration['collaborationId']}."
                )

    if sequence_findings(before_sequence) or sequence_findings(after_sequence):
        raise AssertionError("A focused sequence source has a projection finding.")

    raw_changed_classes = changed_named_items(
        before["Classes"], after["Classes"], "className"
    )
    raw_changed_collaborations = changed_named_items(
        before["Collaborations"], after["Collaborations"], "collaborationId"
    )
    if raw_changed_classes != ["CourseOffering"]:
        raise AssertionError(f"Unexpected raw changed classes: {raw_changed_classes}")
    if raw_changed_collaborations != ["UC3:main:1", "UC5:main:1"]:
        raise AssertionError(
            f"Unexpected raw changed collaborations: {raw_changed_collaborations}"
        )
    return {
        "report_checkpoint_matches_workspace_before": True,
        "workspace_command_status": feedback_command["status"],
        "original_user_event_preserved": True,
        "llm_selected_targets": expected_targets,
        "semantic_changed_classes": changed_classes,
        "semantic_changed_collaborations": changed_collaborations,
        "raw_changed_classes": raw_changed_classes,
        "raw_changed_collaborations": raw_changed_collaborations,
        "raw_nonsemantic_difference_note": None,
        "decrement_operation": decrement,
        "increment_operation_preserved": True,
        "existing_calls_preserved_except_position_refs": True,
        "sequence_projection_findings": {"before": [], "after": []},
    }


def conversation_document(feedback_command: dict[str, Any]) -> dict[str, Any]:
    translations = {
        "class_diagram:CourseOffering": (
            "CourseOffering 클래스에 void를 반환하는 decrementEnrolledCount() 연산을 "
            "추가한다."
        ),
        "class_diagram:UC3:main:1": (
            "UC3 수강 교환 흐름에서 기존 CourseOffering::incrementEnrolledCount() 호출 "
            "앞에 CourseOffering::decrementEnrolledCount() 호출을 삽입한다."
        ),
        "class_diagram:UC5:main:1": (
            "UC5 수강 취소 흐름에서 Registration::deleteById(id:UUID) 호출 뒤에 "
            "CourseOffering::decrementEnrolledCount() 호출을 삽입한다."
        ),
    }
    instructions = feedback_command["payload"]["revision_interpretation"][
        "target_instructions"
    ]
    return {
        "interaction_type": "user_initiated_direct_feedback",
        "choice_prompt": None,
        "choice_options": [],
        "choice_note_ko": (
            "이 사례는 사용자가 수정 의견을 먼저 입력한 직접 피드백이므로 에이전트가 "
            "제시한 선택지 원문은 없다."
        ),
        "feedback_reason_ko": (
            "등록 추가 흐름에는 enrolledCount 증가가 있지만 등록 제거 흐름에는 대응하는 "
            "감소가 없어, 교환 또는 취소 후 저장된 등록 수와 강좌의 등록 인원이 달라질 수 "
            "있기 때문에 피드백을 전달하였다."
        ),
        "user_feedback": {
            "original_en": USER_FEEDBACK,
            "translation_ko": USER_FEEDBACK_KO,
        },
        "llm_interpretation": {
            "requested_effect_original_en": feedback_command["payload"][
                "revision_interpretation"
            ]["requested_effect"],
            "targets": feedback_command["payload"]["revision_interpretation"][
                "targets"
            ],
            "target_instructions": [
                {
                    "target": item["target"],
                    "original_en": item["instruction"],
                    "translation_ko": translations[item["target"]],
                }
                for item in instructions
            ],
            "patch_intents": feedback_command["payload"][
                "revision_interpretation"
            ]["patch_intents"],
        },
        "assistant_result_original_en": (
            feedback_command.get("result") or {}
        ).get("message"),
        "assistant_result_translation_ko": (
            "검증된 설계 요소 3개와 추적으로 연결된 산출물만 수정했습니다. 결과를 "
            "검토하거나 다음 단계로 진행하세요."
        ),
        "command_id": SUCCESS_COMMAND_ID,
    }


def report_text() -> str:
    return f"""# 피드백을 통한 클래스와 상호작용 설계 수정

수강신청 애플리케이션의 초기 설계에는 등록을 추가한 뒤 `CourseOffering.incrementEnrolledCount()`를 호출하는 동작이 있었지만, 등록 제거에 대응하는 감소 연산은 없었다. 사용자는 다음 문장을 클래스 다이어그램 검토 화면에 직접 입력하였다.

**사용자 피드백 원문**

> {USER_FEEDBACK}

**한국어 번역**

> {USER_FEEDBACK_KO}

이 사례는 사용자가 먼저 의견을 입력한 직접 피드백이므로 에이전트가 제시한 선택지는 없다. 사용자는 `CourseOffering`만 지목했으며 UC 번호, 흐름 단계, 호출 ID는 입력하지 않았다.

대화 LLM은 자연어를 수정 요청으로 분류한 뒤 서로 다른 업무 표현을 찾을 검색어를 만들었다. 읽기 전용 검색 도구는 현재 클래스 모델, 유스케이스 명세, RTM(Requirements Traceability Matrix, 요구사항 추적표)을 조회해 후보를 반환하였다. LLM은 이 유한한 후보에서 `CourseOffering`, UC3 `Swap Course Registration`, UC5 `Drop Course Registration`을 골라 하나의 요청을 세 개의 최소 수정으로 분해하였다.

| 대상 | 에이전트가 분해한 지시 원문 | 한국어 번역 |
|---|---|---|
| `CourseOffering` | {EXPECTED_TARGET_INSTRUCTIONS['class_diagram:CourseOffering']} | CourseOffering 클래스에 void를 반환하는 decrementEnrolledCount() 연산을 추가한다. |
| UC3 | {EXPECTED_TARGET_INSTRUCTIONS['class_diagram:UC3:main:1']} | UC3 수강 교환 흐름에서 기존 incrementEnrolledCount() 호출 앞에 decrementEnrolledCount() 호출을 삽입한다. |
| UC5 | {EXPECTED_TARGET_INSTRUCTIONS['class_diagram:UC5:main:1']} | UC5 수강 취소 흐름에서 Registration::deleteById(id:UUID) 호출 뒤에 decrementEnrolledCount() 호출을 삽입한다. |

## 피드백 처리 흐름

1. Workspace API는 사용자 문장을 고치지 않고 이벤트와 명령에 저장한다.
2. 대화 LLM은 문장을 수정 요청으로 분류하고 관련 요소 검색어를 만든다. 검색어는 조회 조건일 뿐 수정 권한은 아니다.
3. 검색 도구는 이름이 일치하는 `CourseOffering`과 관련 클래스·협업을 찾고 RTM의 `flow_step` 연결을 읽는다. `flow_step`은 설계 요소가 어느 유스케이스 단계에서 사용되는지를 나타내는 단계 ID 목록이다. 이 사례의 `UC3:main:4`는 UC3 주 흐름 4단계, `UC5:main:3`은 UC5 주 흐름 3단계를 뜻한다.
4. LLM은 검색 결과 안에서만 수정 대상을 선택하고 대상별 영문 지시와 구조화 패치를 생성한다. 후보에 없는 참조는 코드가 제거한다. 실제 출력은 위 표와 [원본 명령 JSON](raw/command-successful-feedback.json)에 보존하였다.
5. 수정 계획기는 세 대상이 현재 클래스 산출물에 존재하고 같은 설계 단계가 소유하는지 확인한다. 이어 실행 권한을 이 세 대상에 한정한다.
6. 결정론적 패치 실행기는 `CourseOffering`에 연산을 하나 추가하고 UC3·UC5 협업에 호출을 하나씩 삽입한다. 클래스 다이어그램 전체를 LLM으로 다시 생성하지 않는다.
7. 코드는 연산 시그니처, 단계 추적, 호출 순서, 부모 호출, 인자 출처와 수정 범위를 검사한다. 세 패치는 한 작업 상태에 적용되며 하나라도 실패하면 새 버전을 저장하지 않는다. 모두 통과한 뒤에만 클래스 산출물 버전 2를 저장하고 검토 상태로 돌아간다.
8. 이 자료의 시퀀스 다이어그램은 저장된 before·after 클래스 협업을 시스템의 결정론적 투영기로 변환한 것이다. 그림을 다시 작성하거나 보정하는 데 LLM을 사용하지 않았다.

역할을 나누면 LLM은 자연어 해석과 후보 선택을 맡고, 검색·권한 검사·패치 적용·저장은 코드가 맡는다. UC3와 UC5는 코드에 고정된 대상이 아니라 실제 LLM이 검색 후보에서 선택한 결과다. 중간 검색어는 최종 명령에 저장되지 않으므로 보고서에서는 원문처럼 재구성하지 않았다.

## before와 after

| 구분 | 클래스 설계 | UC3 교환 흐름 | UC5 수강 취소 흐름 |
|---|---|---|---|
| before | `incrementEnrolledCount()`만 존재 | `deleteById()` → `create()` → `incrementEnrolledCount()` | `findById()` → `deleteById()` |
| after | 매개변수 없이 `void`를 반환하는 `decrementEnrolledCount()` 추가 | `deleteById()` → `create()` → `decrementEnrolledCount()` → `incrementEnrolledCount()` | `findById()` → `deleteById()` → `decrementEnrolledCount()` |

추가된 연산의 추적 참조는 `UC3:main:4`, `UC5:main:3`이다. 기존 `incrementEnrolledCount()` 연산과 기존 호출의 상대 순서는 유지되었다. 전체 JSON 비교 결과 변경된 클래스는 `CourseOffering` 하나이고, 변경된 협업은 UC3과 UC5뿐이었다. 새 연산과 두 호출 외에 다른 클래스, 협업, 데이터 타입, 관계는 바뀌지 않았다.

그림 X와 그림 Y는 변경된 `CourseOffering` 클래스만 보여준다. 그림 Z 계열은 영향을 받은 UC3과 UC5만 보여준다. 각 그림은 저장된 전체 before·after JSON에서 해당 부분을 추출해 시스템 렌더러로 생성하였다.

UC3의 실제 LLM 지시는 감소 호출을 기존 증가 호출 앞에 두도록 지정했다. 따라서 저장된 after에서도 삭제와 새 등록 생성 뒤, 기존 증가 직전에 감소가 삽입되었다. 현재 협업 모델은 기존 강좌와 새 강좌의 객체 인스턴스를 구분하지 않고 클래스 연산 단위로 호출을 저장하므로 두 호출은 모두 `CourseOffering` 참가자로 표시된다. 보고서에서는 이 한계를 감추기 위해 순서를 임의로 바꾸지 않았다.

## 사용 파일

- [before 전체 클래스 산출물](raw/before-class-diagram-v1-api.json)
- [after 전체 클래스 산출물](raw/after-class-diagram-v2-api.json)
- [피드백 원문이 포함된 Workspace 이벤트](raw/successful-feedback-events.json)
- [LLM 대상 분해가 포함된 명령](raw/command-successful-feedback.json)
- [검증 결과](verification.json)
- [before 클래스 확대 이미지](diagrams/class-courseoffering-before.png)
- [after 클래스 확대 이미지](diagrams/class-courseoffering-after.png)
- [UC3 before 시퀀스 이미지](diagrams/sequence-uc3-before.png)
- [UC3 after 시퀀스 이미지](diagrams/sequence-uc3-after.png)
- [UC5 before 시퀀스 이미지](diagrams/sequence-uc5-before.png)
- [UC5 after 시퀀스 이미지](diagrams/sequence-uc5-after.png)
"""


def readme_text(verification: dict[str, Any]) -> str:
    targets = ", ".join(f"`{item}`" for item in verification["llm_selected_targets"])
    return f"""# 보고서 사례 클래스 피드백 증거

이 폴더는 보고서 사례 `e1-aws`의 클래스 모델을 before로 복원하고, 현재 Workspace API에 자연어 피드백 한 문장을 전달하여 얻은 after를 보존한다. 원본 DOCX와 체크포인트는 수정하지 않았다.

## 실행 식별자

- Workspace 앱 ID: `{APP_ID}`
- before 검토 명령 ID: `{REVIEW_COMMAND_ID}`
- 성공한 피드백 명령 ID: `{SUCCESS_COMMAND_ID}`
- 원본 상태 SHA-256: `{sha256_file(SOURCE_STATE)}`

사용자 원문은 `raw/successful-feedback-events.json`에, LLM이 검색 결과를 바탕으로 분해한 세 지시는 `raw/command-successful-feedback.json`에 저장돼 있다. 선택된 수정 대상은 {targets}이다. 이 사례는 직접 피드백형이므로 에이전트 질문이나 선택지는 없었다.

## 원본과 투영 파일

- `raw/before-class-diagram-v1-api.json`, `raw/after-class-diagram-v2-api.json`: API가 반환한 전체 before/after 클래스 산출물
- `raw/before-sequence-projection.json`, `raw/after-sequence-projection.json`: 전체 클래스 산출물에서 결정론적으로 만든 전체 시퀀스 모델
- `raw/all-workspace-commands.json`: 이 복원 앱에 저장된 전체 명령 기록
- `raw/current-workspace-events.json`: 서버 재시작 뒤 메모리에 남은 Workspace 이벤트
- `diagrams/class-courseoffering-*`: 변경된 클래스만 추린 결정론적 렌더링
- `diagrams/sequence-uc3-*`, `diagrams/sequence-uc5-*`: 변경된 두 유스케이스만 추린 결정론적 렌더링
- `conversation.json`: 원문, 한국어 번역, 피드백 이유와 대상별 LLM 지시
- `report-text.md`: 보고서에 옮길 본문
- `verification.json`: 범위 및 추적 검증 결과
- `manifest.json`: 증거 파일별 SHA-256

## 비교 시 주의점

API 원문 JSON과 의미 정규화 결과 모두에서 변경된 클래스는 `CourseOffering`, 변경된 협업은 UC3과 UC5뿐이다. 삽입 위치 뒤의 호출은 위치 기반 `callId`, `parentCallId`, `sourceRef`가 새 위치에 맞게 다시 계산될 수 있다. 그 외 기존 호출의 receiver, 단계 추적, 인자 바인딩과 부모 구조는 유지되는지 `verification.json` 생성 과정에서 검사한다.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()

    source_state = json.loads(SOURCE_STATE.read_text(encoding="utf-8"))
    before_response = api_json(
        f"/api/apps/{APP_ID}/stages/class_diagram/versions/1"
    )
    after_response = api_json(
        f"/api/apps/{APP_ID}/stages/class_diagram/versions/2"
    )
    workspace = api_json(f"/api/workspace/apps/{APP_ID}")
    before = before_response["content"]
    after = after_response["content"]

    feedback_command = get_command(SUCCESS_COMMAND_ID)
    review_command = get_command(REVIEW_COMMAND_ID)
    if feedback_command is None or review_command is None:
        raise RuntimeError("One or more evidence commands are missing.")
    feedback_events = [
        item
        for item in workspace["events"]
        if item.get("command_id") == SUCCESS_COMMAND_ID
    ]

    current_state = artifact_repository.load_state(APP_ID)
    if current_state.get("extracted_bce_classes") != after:
        raise AssertionError("The current graph class model differs from artifact version 2.")
    scenario = build_scenario_index(source_state)
    before_sequence = project_sequence_model(
        scenario,
        BCEModel.model_validate(before),
        str(source_state.get("class_diagram_puml") or ""),
    ).model_dump()
    after_sequence = project_sequence_model(
        scenario,
        BCEModel.model_validate(after),
        str(current_state.get("class_diagram_puml") or ""),
    ).model_dump()

    verification = verify(
        source_state,
        before,
        after,
        workspace,
        feedback_command,
        feedback_events,
        before_sequence,
        after_sequence,
    )

    raw = output / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE_STATE, raw / "report-checkpoint-class-diagram-state.json")
    write_json(raw / "workspace-api-request.json", REQUEST_BODY)
    write_json(raw / "before-class-diagram-v1-api.json", before_response)
    write_json(raw / "after-class-diagram-v2-api.json", after_response)
    write_json(raw / "before-sequence-projection.json", before_sequence)
    write_json(raw / "after-sequence-projection.json", after_sequence)
    write_json(raw / "command-restored-class-review.json", review_command)
    write_json(raw / "command-successful-feedback.json", feedback_command)
    write_json(raw / "all-workspace-commands.json", command_history())
    write_json(raw / "current-workspace-events.json", workspace["events"])
    write_json(raw / "successful-feedback-events.json", feedback_events)

    diagrams = output / "diagrams"
    render_source(
        focused_class_source(before), diagrams / "class-courseoffering-before"
    )
    render_source(
        focused_class_source(after), diagrams / "class-courseoffering-after"
    )
    for use_case_id in ("UC3", "UC5"):
        before_diagram, before_puml = focused_sequence_source(
            before_sequence, use_case_id
        )
        after_diagram, after_puml = focused_sequence_source(
            after_sequence, use_case_id
        )
        write_json(
            raw / f"before-sequence-{use_case_id.lower()}-focus.json",
            before_diagram,
        )
        write_json(
            raw / f"after-sequence-{use_case_id.lower()}-focus.json",
            after_diagram,
        )
        render_source(
            before_puml, diagrams / f"sequence-{use_case_id.lower()}-before"
        )
        render_source(after_puml, diagrams / f"sequence-{use_case_id.lower()}-after")

    write_json(output / "conversation.json", conversation_document(feedback_command))
    write_json(output / "verification.json", verification)
    write_text(output / "report-text.md", report_text())
    write_text(output / "README.md", readme_text(verification))

    manifest_files = sorted(
        path for path in output.rglob("*") if path.is_file() and path.name != "manifest.json"
    )
    manifest = {
        "app_id": APP_ID,
        "review_command_id": REVIEW_COMMAND_ID,
        "successful_feedback_command_id": SUCCESS_COMMAND_ID,
        "source": SOURCE_STATE.relative_to(ROOT).as_posix(),
        "source_sha256": sha256_file(SOURCE_STATE),
        "files": [
            {
                "path": path.relative_to(output).as_posix(),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
            for path in manifest_files
        ],
        "verification": verification,
    }
    write_json(output / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
