"""설계 산출물 입력 계약 — 스키마와 참조 검증.

여기 테스트가 고정하는 것은 계약의 세 규칙이다.

    조인은 명시 id로만        이름 퍼지 조인 없음 — 못 가리키면 걸린다
    오타는 조용히 죽지 않는다  additionalProperties=false — 칸 이름이 틀리면 그 자리에서
    한 번에 다 보여준다        문제는 예외가 아니라 목록
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from appkb.contract import validate_design

_EXAMPLE = Path(__file__).resolve().parent.parent / "appkb" / "examples" / "order-demo.json"


@pytest.fixture()
def design() -> dict:
    return json.loads(_EXAMPLE.read_text(encoding="utf-8"))


def test_the_shipped_example_is_valid(design) -> None:
    """예제가 깨져 있으면 계약 문서 전체가 신뢰를 잃는다."""
    assert validate_design(design) == []


# --- 규칙 1: 조인은 명시 id로만 ------------------------------------------------

def test_dangling_component_ref_is_caught(design) -> None:
    design["artifacts"][0]["componentId"] = "no-such-component"
    problems = validate_design(design)
    assert any("no-such-component" in p for p in problems)


def test_er_owner_is_required_not_guessed(design) -> None:
    """소유자가 없으면 DB 토폴로지(공유 vs 분리)를 정할 수 없다 — 그때 공유 DB를
    기본값으로 두는 것이 정확히 '모르는 값을 채우는' 실패라, 아예 필수다."""
    del design["artifacts"][1]["entities"][0]["ownerComponentId"]
    problems = validate_design(design)
    assert any("ownerComponentId" in p for p in problems)


def test_sequence_participant_must_point_at_exactly_one_thing(design) -> None:
    """componentId도 externalId도 actor도 아니면 **누군지 모른다** — 짐작하지 않는다."""
    seq = design["artifacts"][3]
    seq["participants"].append({"id": "ghost", "name": "정체불명"})
    seq["messages"].append({"from": "ghost", "to": "api", "async": False})
    problems = validate_design(design)
    assert any("정확히 하나" in p for p in problems)


def test_participant_pointing_at_two_things_is_also_wrong(design) -> None:
    seq = design["artifacts"][3]
    seq["participants"][1]["actor"] = True  # componentId도 있는데 actor까지
    problems = validate_design(design)
    assert any("정확히 하나" in p for p in problems)


def test_message_endpoints_must_be_participants(design) -> None:
    design["artifacts"][3]["messages"][0]["from"] = "nobody"
    assert any("nobody" in p for p in validate_design(design))


# --- 규칙 2: 오타는 조용히 죽지 않는다 -----------------------------------------

def test_typo_in_field_name_fails_loudly(design) -> None:
    """`comonentId` 오타가 조용히 무시되면 그 요소가 미매칭으로 빠지는데
    왜 빠졌는지 아무도 모른다 — additionalProperties=false가 그 자리에서 잡는다."""
    artifact = design["artifacts"][0]
    artifact["comonentId"] = artifact.pop("componentId")
    problems = validate_design(design)
    assert problems and all(p.startswith("[스키마]") for p in problems)


def test_component_id_charset_matches_cloud_name_rules(design) -> None:
    """id는 리소스 이름 씨앗이다 — 클라우드 이름 규칙과 어긋난 문자를 처음부터 안 받는다."""
    design["components"][0]["id"] = "Order_API!"
    assert any("[스키마]" in p for p in validate_design(design))


def test_rendering_extras_go_into_meta_not_new_fields(design) -> None:
    """렌더링 좌표 같은 우리가 안 읽는 정보의 자리는 meta다 — 계약은 좁게, 탈출구는 명시적으로."""
    design["components"][0]["meta"] = {"x": 120, "y": 40, "color": "#fff"}
    assert validate_design(design) == []


# --- 규칙 3 + 경계 -------------------------------------------------------------

def test_problems_come_as_a_list_not_first_error(design) -> None:
    """상류는 에이전트다 — 첫 오류에서 멈추면 고치고 다시를 반복한다."""
    design["artifacts"][0]["componentId"] = "ghost-1"
    design["artifacts"][2]["classes"][0]["componentId"] = "ghost-2"
    problems = validate_design(design)
    assert len(problems) >= 2


def test_requirements_are_optional_but_not_defaulted(design) -> None:
    """requirements가 없어도 계약 위반이 아니다 — 그때 비용 조인을 못 한다고 답할
    책임은 composer(P3)에 있지, 여기서 기본 리전을 채워 주지 않는다."""
    del design["requirements"]
    assert validate_design(design) == []


def test_openapi_2_is_rejected(design) -> None:
    """Swagger 2.0을 조용히 받으면 securitySchemes 위치가 달라 인증 신호를 놓친다."""
    design["artifacts"][0]["openapi"]["openapi"] = "2.0"
    assert any("3.x" in p for p in validate_design(design))


def test_duplicate_ids_between_components_and_externals(design) -> None:
    design["externals"].append({"id": "order-api", "name": "충돌"})
    assert any("겹친다" in p for p in validate_design(design))


@pytest.mark.parametrize("index,kind,break_it,expect", [
    (0, "openapi", lambda a: a.pop("componentId"), "componentId"),
    (1, "er", lambda a: a["entities"][0].pop("ownerComponentId"), "ownerComponentId"),
    (2, "class", lambda a: a["classes"][0].pop("componentId"), "componentId"),
    (3, "sequence", lambda a: a["messages"][0].pop("async"), "async"),
])
def test_oneof_error_names_the_actual_missing_field(design, index, kind, break_it, expect) -> None:
    """**oneOf가 세부를 삼키면 안 된다.** 실측에서 ownerComponentId 누락이
    "not valid under any of the given schemas"에 묻혔고, best_match 휴리스틱은
    엉뚱한 분기를 골랐다 — kind 판별자로 분기를 직접 고른다. 이 테스트는
    `_KIND_BRANCH`가 스키마의 oneOf 순서와 함께 움직이는 것도 겸해서 고정한다."""
    artifact = design["artifacts"][index]
    assert artifact["kind"] == kind
    break_it(artifact)
    problems = validate_design(design)
    assert any(expect in p for p in problems), problems


def test_schema_errors_come_before_reference_noise(design) -> None:
    """모양이 틀렸으면 참조 검증은 소음이다 — 스키마 문제만 먼저 보여준다."""
    design["schemaVersion"] = "2"
    design["artifacts"][0]["componentId"] = "ghost"
    problems = validate_design(design)
    assert all(p.startswith("[스키마]") for p in problems)
