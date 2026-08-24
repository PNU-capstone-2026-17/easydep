"""결정론 검출기 — 규칙 하나에 검출기 하나.

## 무엇을 보는가: 그림이 아니라 모델

검사 대상은 렌더된 PlantUML이 아니라 **BCE 모델**이다. 이유가 셋이다.

  1. **렌더는 정보를 지운다.** `plantuml.py`의 `sanitize_*`가 `Payment Service!`를
     `Payment_Service_`로 만들고, 중괄호를 괄호로 바꾼다. 그림만 보면 LLM이 무엇을 냈는지
     알 수 없다 — 중화된 뒤를 보는 검사는 중화가 숨긴 것을 못 본다.
  2. **지적이 가리킬 이름이 모델에 있다.** 지적의 `location`은 `className`인데, 이것은
     `subgraphs.py`의 `CLASS_DIAGRAM_SPEC.elements`가 쓰는 이름이고 `rtm.py`의 element와도
     같다. 그래야 "class_diagram:Order를 고쳐줘"가 통한다.
  3. **고칠 대상이 모델이다.** 재생성은 `revise_bce_classes`가 모델을 편집한다. 그림을
     보고 지적해 놓고 모델을 고치라고 하면 좌표가 어긋난다.

## 사정거리

여기 있는 것은 **결정론으로 참인 것만**이다. 이름이 중복인지, 참조가 실제로 존재하는지,
스테레오타입 조합이 금지된 것인지. **의미 판정은 여기 없다** — "이 명사가 정말 Entity인가",
"MSS 스텝이 실제로 표현됐는가"는 사람이나 LLM 검증자의 몫이고, 그 규칙은 아직 없다
(`rules.unjudged_defects()`가 비어 있는 것이 지금의 사실이다).

## 검사 순서가 뜻을 갖는다

`stereotype_is_bce`가 `communication_rules`보다 먼저 돈다. 스테레오타입을 못 읽으면 통신
규칙 세 개가 전부 **무판정**이 되는데, 무판정은 겉보기에 통과와 같다. 순서가 그 조용한
실패를 드러낸다: 스테레오타입이 깨졌다는 지적이 먼저 나온다.

## 위반이 가려질 수 있다 — 그리고 그래도 된다

뒤의 검출기가 앞의 것이 이미 지적한 자리를 건너뛰므로, **결함 하나가 다른 결함을 가린다.**
예: `Order`의 스테레오타입이 `Repository`(BCE 밖)이면 `OrderForm → Order`가 Boundary-Entity
직결인지 판정할 수 없어 건너뛴다. 스테레오타입을 `Entity`로 고치고 나서야 그 링크 위반이
드러난다.

의도한 것이다. 한 실수를 지적 둘로 세면 위반 수가 부풀고, 재생성이 실제로 하나를 고쳤는데도
수가 안 줄어 버려진다(`no_improvement`). 대신 **고칠 때마다 새 위반이 나타날 수 있다**는
성질이 생기는데, 그래도 루프는 반드시 멈춘다 — 채택 조건이 "위반 수가 **줄어야** 한다"라
가려졌던 것이 드러나 수가 안 줄면 그 자리에서 멈추고 남은 것을 사람에게 보고한다.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.design import rtm
from app.design.knowledge import rules
from app.design.services.class_diagram.plantuml import RELATION_SYMBOLS, sanitize_class_name
from app.design.services.common import fields, multiplicity
from app.design.services.erd import mapping
from app.design.services.sequence_diagram.methods import (
    method_call_signature,
    method_name,
    method_return_type,
    normalize_return_type,
)

#: BCE 세 분류. 소문자로 비교한다 — 모델은 `<<Control>>`, `Control`, `control`을 섞어 낸다.
BOUNDARY = "boundary"
CONTROL = "control"
ENTITY = "entity"
BCE_STEREOTYPES = (BOUNDARY, CONTROL, ENTITY)

#: PascalCase 식별자: 대문자로 시작하고, 영숫자만, 밑줄·공백·기호 없음.
_PASCAL_CASE = re.compile(r"^[A-Z][A-Za-z0-9]*$")


@dataclass(frozen=True)
class Finding:
    """규칙 위반 하나. **어느 규칙인지를 들고 다닌다.**"""

    rule_id: str
    message: str
    #: 모델 안의 위치(클래스 이름, 관계 표현). 전체에 대한 지적이면 None.
    location: str | None = None

    def as_issue(self) -> str:
        """상태·게이트·저장소에 실리는 한 줄. 꼬리표로 근거가 함께 간다."""
        head = f"{self.location}: {self.message}" if self.location else self.message
        return f"{head} {rules.tag_of(self.rule_id)}"


# ---------------------------------------------------------------------------
# 모델 읽기 도우미
# ---------------------------------------------------------------------------
def _classes(model: dict[str, Any]) -> list[dict]:
    return [c for c in (model.get("Classes") or []) if isinstance(c, dict)]


def _relationships(model: dict[str, Any]) -> list[dict]:
    return [r for r in (model.get("Relationships") or []) if isinstance(r, dict)]


def _stereotype_of(class_item: dict) -> str:
    """`<<Control>>`·`Control`·`control` 을 전부 `control` 로 읽는다.

    렌더러의 `sanitize_stereotype`이 하는 정규화와 같은 관대함이어야 한다. 여기서 더
    엄격하면 그림에는 멀쩡히 나오는 것을 결함이라 부르게 된다.
    """
    raw = str(class_item.get("stereotype", ""))
    return raw.replace("<", "").replace(">", "").strip().lower()


def _relation_label(relationship: dict) -> str:
    """관계 하나를 가리키는 이름. 관계에는 id가 없어서 조합으로 가리킨다."""
    return "{} -> {}".format(
        relationship.get("source") or "?", relationship.get("target") or "?"
    )


# ---------------------------------------------------------------------------
# 검출기
# ---------------------------------------------------------------------------
def _dangling_endpoints(model: dict, rule_id: str, consequence: str) -> list[Finding]:
    """관계의 양 끝이 선언된 클래스인가 — **판정은 한 벌, 문구는 스테이지별로.**

    같은 결함이 두 산출물에서 **정반대로** 나타나기 때문에 문구를 나눈다. 클래스
    다이어그램에서는 PlantUML이 그 이름으로 유령 클래스를 *만들고*, ERD에서는 사상이
    그 관계를 *버린다*. 하나는 없던 것이 생기고 하나는 있던 것이 사라진다 — 고치는
    사람에게는 전혀 다른 이야기다.

    그렇다고 검출기를 두 벌 쓰면 갈라진다. 그래서 로직은 여기 하나이고, 부르는 쪽이
    자기 규칙 id와 결과 설명을 준다.
    """
    declared = {c.get("className") for c in _classes(model) if c.get("className")}
    found: list[Finding] = []
    for relationship in _relationships(model):
        label = _relation_label(relationship)
        for end in ("source", "target"):
            name = relationship.get(end)
            if name and name not in declared:
                found.append(
                    Finding(rule_id, f"{end} '{name}'가 Classes에 없음 — {consequence}", label)
                )
    return found


def relationship_endpoints(model: dict, state: dict) -> list[Finding]:
    """관계의 양 끝이 선언된 클래스인가.

    **가장 값나가는 검사다.** 매달린 끝은 조용히 실패한다:

      - PlantUML은 오류를 내지 않고 그 이름으로 **빈 클래스를 하나 만든다**(실측:
        선언 1개 + 매달린 끝 1개 → `-syntax`가 `(2 entities)`를 보고하고 통과).
      - 구현 단계의 `parse_design_classes`는 `class X <<S>> {…}` 선언만 정규식으로 읽으므로
        그 유령을 **못 본다**. 그런데 같은 파일의 `parse_relations`는 관계 줄에서 식별자를
        긁어 가므로 **유령을 본다**. 그래서 `design_context.py`의 `relation_context`가
        "존재하지 않는 클래스를 가리키는 관계 줄"을 코드 생성 프롬프트에 실어 보낸다.

    즉 문법 검증도, 하류 파서도 이것을 막지 못한다. 여기서 막아야 한다.
    """
    return _dangling_endpoints(
        model,
        "class.relationship-endpoints-exist",
        "그림에 그 이름의 빈 클래스가 생긴다",
    )


def usecase_ids(model: dict, state: dict) -> list[Finding]:
    """`use_case_ids`가 입력 유스케이스의 실제 id인가 (환각 참조).

    판정은 `rtm.upstream_names`에서 나온다 — 추적표가 사후에 하는 판정과 **같은 것**이고,
    두 벌이면 갈라진다.

    ⚠ 입력에 유스케이스 id가 하나도 없으면 검사하지 않는다. 그때는 **모든** id가 unknown이
    되는데, 그건 "LLM이 지어냈다"가 아니라 "대조할 상류가 없다"는 뜻이다. 대조할 것이
    없는데 전건 위반을 내면 재생성이 고칠 수 없는 지적으로 예산만 태운다.
    """
    rule_id = "class.usecase-ids-exist"
    known = rtm.upstream_names(state).get("use_case") or set()
    if not known:
        return []

    found: list[Finding] = []
    for class_item in _classes(model):
        name = class_item.get("className") or "?"
        for ref in class_item.get("use_case_ids") or []:
            if ref and ref not in known:
                found.append(
                    Finding(rule_id, f"입력에 없는 유스케이스 id '{ref}'", name)
                )
    return found


def _broken_stereotypes(model: dict, rule_id: str, consequence: str = "") -> list[Finding]:
    """스테레오타입이 Boundary/Control/Entity 중 하나인가 — 판정 한 벌, 문구는 스테이지별.

    `_dangling_endpoints`와 같은 이유로 공유한다: 판정은 같고 **결과가 다르다**. 클래스
    다이어그램에서는 통신 규칙이 무판정이 되고, ERD에서는 그 표와 관계가 사라진다.
    """
    tail = f" — {consequence}" if consequence else ""
    found: list[Finding] = []
    for class_item in _classes(model):
        name = class_item.get("className") or "?"
        stereotype = _stereotype_of(class_item)
        if not stereotype:
            found.append(Finding(rule_id, f"스테레오타입 없음{tail}", name))
        elif stereotype not in BCE_STEREOTYPES:
            found.append(
                Finding(rule_id, f"BCE 밖의 스테레오타입 '{stereotype}'{tail}", name)
            )
    return found


def stereotype_is_bce(model: dict, state: dict) -> list[Finding]:
    """**통신 규칙보다 먼저 돈다.** 이게 깨지면 아래 세 규칙이 무판정이 되고, 무판정은
    겉보기에 통과와 같다.
    """
    return _broken_stereotypes(model, "class.stereotype-is-bce")


#: 금지된 (source 스테레오타입, target 스테레오타입) 조합 → (규칙 id, 왜 안 되는지).
#:
#: 방향이 뜻을 갖는 것과 안 갖는 것이 섞여 있다. Boundary-Entity 와 Boundary-Boundary 는
#: **연결 자체가** 금지라 양방향을 다 적고, Entity→Control/Boundary 는 **개시**가 금지라
#: 한 방향만 적는다 — Control→Entity 는 정상이다.
_FORBIDDEN_LINKS: dict[tuple[str, str], tuple[str, str]] = {
    (BOUNDARY, ENTITY): (
        "class.no-boundary-entity-link",
        "Boundary와 Entity를 직접 이었다 — 사이에 Control이 있어야 한다",
    ),
    (ENTITY, BOUNDARY): (
        "class.no-boundary-entity-link",
        "Entity와 Boundary를 직접 이었다 — 사이에 Control이 있어야 한다",
    ),
    (BOUNDARY, BOUNDARY): (
        "class.no-boundary-boundary-link",
        "Boundary끼리 직접 이었다 — Boundary는 액터 또는 Control과 통신한다",
    ),
    (ENTITY, CONTROL): (
        "class.entity-does-not-initiate",
        "Entity가 Control을 향해 관계를 시작했다 — Entity는 행위를 개시하지 않는다",
    ),
}


def communication_rules(model: dict, state: dict) -> list[Finding]:
    """BCE 통신 규칙 위반 (Boundary↔Entity, Boundary↔Boundary, Entity의 개시).

    Entity→Boundary는 `_FORBIDDEN_LINKS`에서 **직결 금지** 쪽으로 잡히므로 개시 규칙에서
    또 세지 않는다. 같은 관계 하나가 지적 둘이 되면 재생성이 하나를 고치고도 수가 안
    줄어 `no_improvement`로 멈춘다.

    스테레오타입이 BCE 밖이거나 양 끝이 선언되지 않은 관계는 **건너뛴다.** 그건 각각
    `stereotype_is_bce`와 `relationship_endpoints`가 이미 지적했고, 여기서 또 세면 한
    결함이 여러 지적이 된다.
    """
    stereotype_by_name = {
        c["className"]: _stereotype_of(c)
        for c in _classes(model)
        if c.get("className")
    }

    found: list[Finding] = []
    for relationship in _relationships(model):
        source = stereotype_by_name.get(relationship.get("source"))
        target = stereotype_by_name.get(relationship.get("target"))
        if source not in BCE_STEREOTYPES or target not in BCE_STEREOTYPES:
            continue
        violation = _FORBIDDEN_LINKS.get((source, target))
        if violation:
            rule_id, message = violation
            found.append(Finding(rule_id, message, _relation_label(relationship)))
    return found


def relationship_type_known(model: dict, state: dict) -> list[Finding]:
    """관계의 종류가 렌더러가 아는 다섯 중 하나인가.

    모르는 값은 그림에서 단순 연관(`-->`)이 되고, ERD 사상에서는 구조적 연관으로 세지지
    않아 관계가 통째로 사라진다. **판정 기준을 여기 다시 적지 않고** 렌더러의 표를
    그대로 쓴다 — 두 벌이면 표를 늘릴 때 판정이 안 따라온다.
    """
    rule_id = "class.relationship-type-known"
    found: list[Finding] = []
    for relationship in _relationships(model):
        kind = str(relationship.get("type") or "")
        if kind and kind not in RELATION_SYMBOLS:
            found.append(
                Finding(rule_id, f"모르는 관계 종류 '{kind}'", _relation_label(relationship))
            )
    return found


def entity_association_multiplicity(model: dict, state: dict) -> list[Finding]:
    """Entity 사이의 **구조적** 관계가 양끝 다중도를 갖고 있는가.

    행위 링크(Boundary·Control이 낀 것)와 상속은 세지 않는다. 전자는 다중도를 가질 것이
    아니고, 후자는 일반화라 UML에서도 다중도를 달지 않는다.

    스테레오타입이 BCE 밖이거나 끝이 선언되지 않은 관계도 건너뛴다 — 그건
    `stereotype_is_bce`와 `relationship_endpoints`가 이미 지적했고, 여기서 또 세면 한
    결함이 여러 지적이 된다.
    """
    rule_id = "class.entity-association-multiplicity"
    stereotype_by_name = {
        c["className"]: _stereotype_of(c) for c in _classes(model) if c.get("className")
    }

    found: list[Finding] = []
    for relationship in _relationships(model):
        if str(relationship.get("type") or "Association") not in mapping.STRUCTURAL_TYPES:
            continue
        ends = (relationship.get("source"), relationship.get("target"))
        if any(stereotype_by_name.get(end) != ENTITY for end in ends):
            continue
        label = _relation_label(relationship)
        for side in ("source", "target"):
            value = str(relationship.get(f"{side}Multiplicity") or "").strip()
            # **판정을 사상과 같은 함수로 한다.** 두 벌이면 검출기는 통과시키는데 사상은
            # 못 옮기는 어긋남이 나고, 그러면 아무 지적 없이 선이 사라진다.
            if multiplicity.is_known(value):
                continue
            # 안 적은 것과 못 읽는 것을 구별해서 말한다 — 고치는 쪽이 할 일이 다르다.
            found.append(
                Finding(rule_id, f"{side} 다중도가 없음", label)
                if not value
                else Finding(
                    rule_id,
                    f"{side} 다중도 '{value}'는 아는 표기가 아님 "
                    f"(쓸 수 있는 것: {', '.join(multiplicity.CANONICAL)})",
                    label,
                )
            )
    return found


def _parameter_items(signature: str) -> list[str]:
    """Read comma-separated parameters while preserving generic type commas."""
    inside = signature.partition("(")[2].rpartition(")")[0]
    if not inside:
        return []
    values: list[str] = []
    start = 0
    depth = 0
    for index, character in enumerate(inside):
        if character == "<":
            depth += 1
        elif character == ">":
            depth = max(0, depth - 1)
        elif character == "," and depth == 0:
            values.append(inside[start:index])
            start = index + 1
    values.append(inside[start:])
    return values


def method_parameters_typed(model: dict, state: dict) -> list[Finding]:
    """BCE methods that declare inputs make their names and types usable downstream."""
    rule_id = "class.method-parameters-typed"
    found: list[Finding] = []
    for class_item in _classes(model):
        class_name = str(class_item.get("className") or "?")
        for raw_method in class_item.get("methods") or []:
            raw_text = str(raw_method)
            signature = method_call_signature(raw_text)
            if not signature:
                continue
            seen: set[str] = set()
            invalid = False
            for item in _parameter_items(signature):
                name, separator, type_name = item.partition(":")
                name = name.strip()
                if (
                    not separator
                    or not name
                    or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                    or not type_name.strip()
                    or name in seen
                ):
                    invalid = True
                    break
                seen.add(name)
            if invalid:
                found.append(
                    Finding(
                        rule_id,
                        f"메서드 '{raw_text}'의 매개변수는 중복 없이 'name : Type' 형식이어야 함",
                        class_name,
                    )
                )
    return found


def fields_typed(model: dict, state: dict) -> list[Finding]:
    """Require a Java type on every declared BCE field.

    The downstream BCE generator cannot represent an untyped PlantUML
    attribute as valid Java (its legacy parser turns it into ``void``), and an
    untyped field also cannot be mapped consistently to persistence code.
    """
    rule_id = "class.fields-typed"
    found: list[Finding] = []
    for class_item in _classes(model):
        class_name = str(class_item.get("className") or "?")
        for raw_field in class_item.get("fields") or []:
            field_name, field_type = fields.split_field(str(raw_field))
            if field_name and not field_type:
                found.append(
                    Finding(
                        rule_id,
                        f"{class_name}.{field_name}: 필드 타입이 선언되지 않음 — 'name : Type' 형식이 필요함",
                        class_name,
                    )
                )
    return found


_CONTROL_OUTCOME_PREFIXES = (
    "authenticate", "authorize", "calculate", "check", "create", "find", "generate",
    "get", "initiate", "list", "process", "register", "search", "select", "show",
    "validate", "view",
)


def control_outcome_return_contract(model: dict, state: dict) -> list[Finding]:
    """Result-like Control verbs must state whether they return a value or are void."""
    rule_id = "class.control-outcome-return-contract"
    found: list[Finding] = []
    for class_item in _classes(model):
        if _stereotype_of(class_item) != CONTROL:
            continue
        class_name = str(class_item.get("className") or "?")
        for raw_method in class_item.get("methods") or []:
            raw_text = str(raw_method)
            name = method_name(raw_text)
            if (
                name.startswith(_CONTROL_OUTCOME_PREFIXES)
                and method_return_type(raw_text) is None
            ):
                found.append(
                    Finding(
                        rule_id,
                        f"결과·판정 성격의 Control 메서드 '{raw_text}'에 ': ReturnType' 또는 ': void' 계약이 없음",
                        class_name,
                    )
                )
    return found


def names_unique(model: dict, state: dict) -> list[Finding]:
    """클래스 이름이 유일한가 — **렌더 후 기준으로.**

    `sanitize_class_name`을 통과시킨 뒤 비교하는 것이 요점이다. `Payment Service!`와
    `Payment_Service_`는 모델에서는 다른 이름이지만 그림에서는 한 클래스가 된다. 원본만
    비교하면 그림에서 두 클래스가 합쳐진 것을 아무도 못 본다.
    """
    rule_id = "class.names-unique"
    seen: dict[str, str] = {}
    found: list[Finding] = []
    for class_item in _classes(model):
        name = class_item.get("className")
        if not name:
            continue
        rendered = sanitize_class_name(name)
        first = seen.get(rendered)
        if first is None:
            seen[rendered] = name
        elif first == name:
            found.append(Finding(rule_id, "같은 이름의 클래스가 둘 이상", name))
        else:
            found.append(
                Finding(
                    rule_id,
                    f"'{first}'와 렌더 후 같은 이름('{rendered}')이 된다",
                    name,
                )
            )
    return found


def name_pascal_case(model: dict, state: dict) -> list[Finding]:
    """클래스 이름이 PascalCase 식별자인가."""
    rule_id = "class.name-pascal-case"
    found: list[Finding] = []
    for class_item in _classes(model):
        name = class_item.get("className")
        if not name:
            found.append(Finding(rule_id, "className이 비어 있음"))
        elif not _PASCAL_CASE.match(name):
            found.append(Finding(rule_id, "PascalCase 식별자가 아님", name))
    return found


def usecase_coverage(model: dict, state: dict) -> list[Finding]:
    """입력의 모든 유스케이스가 최소 한 클래스에 붙잡혔는가.

    유스케이스를 통째로 빠뜨리는 것은 다이어그램이 조금 부실한 것이 아니다 — 설계
    다섯 장이 전부 이 모델에서 나오므로, 여기서 빠진 기능은 **설계 전체에서 사라진다.**

    ⚠ `usecase_ids`와 같은 이유로, 입력에 id가 없으면 검사하지 않는다.
    """
    rule_id = "class.covers-use-cases"
    known = rtm.upstream_names(state).get("use_case") or set()
    if not known:
        return []

    claimed = {
        ref
        for class_item in _classes(model)
        for ref in (class_item.get("use_case_ids") or [])
        if ref
    }
    return [
        Finding(rule_id, f"유스케이스 '{uc}'를 가리키는 클래스가 없음")
        for uc in sorted(known - claimed)
    ]


# ---------------------------------------------------------------------------
# ERD 검출기 — 두 층을 본다
# ---------------------------------------------------------------------------
# 앞의 검출기들은 전부 BCE 모델 하나만 봤다. ERD는 그럴 수 없다: "이 테이블에 기본키가
# 있나", "이 외래키가 실재 테이블을 가리키나"는 **사상 결과**에 대한 질문이고, BCE에는
# 테이블도 키도 없다.
#
# 그래서 여기 있는 것들은 `mapping.build_logical_model()`이 낸 논리 데이터 모델을 받는다.
# 그림을 파싱하지 않는 이유는 앞의 것들과 같다 — 렌더는 정보를 지우고, 우리가 방금 만든
# 문자열을 되읽는 것은 아무것도 확인하지 않는다.
def _tables(logical: dict) -> list[dict]:
    return [t for t in (logical.get("Tables") or []) if isinstance(t, dict)]


def erd_relationship_endpoints(model: dict, logical: dict) -> list[Finding]:
    return _dangling_endpoints(
        model, "erd.relationship-endpoints-exist", "그 관계가 ERD에서 통째로 사라진다"
    )


def erd_stereotype_is_bce(model: dict, logical: dict) -> list[Finding]:
    """`erd_has_entity`보다 **먼저** 돈다 — 딱지가 전부 깨져 표가 0개가 되면 원인은
    하나인데 지적이 둘 나온다. 원인 쪽을 먼저 보여준다.
    """
    return _broken_stereotypes(model, "erd.stereotype-is-bce", "ERD에서 빠진다")


def erd_entity_name_usable(model: dict, logical: dict) -> list[Finding]:
    """딱지가 Entity인 것만 본다 — Boundary·Control은 표가 안 되므로 이름이 비어도 ERD에
    영향이 없고, 그건 클래스 다이어그램 쪽이 볼 일이다.
    """
    rule_id = "erd.entity-name-usable"
    found: list[Finding] = []
    for class_item in _classes(model):
        if not fields.is_entity(class_item):
            continue
        name = str(class_item.get("className") or "")
        if not name.strip():
            found.append(Finding(rule_id, "className이 비어 있음 — 'UnknownEntity'가 된다"))
        elif not fields.sanitize_entity_name(name).strip("_"):
            found.append(
                Finding(rule_id, f"'{name}'은 전부 기호라 표 이름이 될 수 없음", name)
            )
    return found


def erd_has_entity(model: dict, logical: dict) -> list[Finding]:
    """표가 하나라도 있는가.

    재생성이 모델을 **비워서** 위반을 없애는 길도 이것이 막는다 — 비우면 이 위반이 새로
    생겨 위반 수가 안 줄고 후보가 버려진다. ERD 스펙에는 `elements`가 없어
    `_is_degenerate`가 그 함정을 못 막으므로 막는 것은 이 규칙 하나다.
    """
    if _tables(logical):
        return []
    return [Finding("erd.has-entity", "<<Entity>> 클래스가 하나도 없어 ERD가 비어 있다")]


#: 사상 못 한 사유 → **사람이 읽을 말.** 빠지면 `str(reason)`이 그대로 나가서
#: `multiple-inheritance` 같은 영어 슬러그가 게이트 화면에 뜬다.
#:
#: **모듈 수준에 있는 것이 요점이다.** 사유는 `mapping.py`에서 늘어나고 문구는 여기서
#: 붙는데, 함수 안에 숨어 있으면 둘이 갈라진 것을 아무도 못 본다. 밖에 있으면
#: `tests/test_erd_check.py`가 `mapping`의 `UNMAPPED_*` 상수를 전수해 이 표와 대조한다.
UNMAPPED_PROSE: dict[str, str] = {
    mapping.UNMAPPED_MULTIPLICITY: "다중도가 없어 사상하지 못했다",
    # `{type}`는 사상이 담아 둔 **실제 관계 종류**로 바뀐다(아래 함수). 이 사유는
    # `Dependency`뿐 아니라 구조적이지 않은 종류 전부를 받으므로, 문구에 종류를 박아 두면
    # `Realization`을 적은 모델에게 "Dependency로 이었다"고 **없는 말을 하게 된다.**
    mapping.UNMAPPED_DEPENDENCY: "Entity 둘을 {type}로 이었다 — 데이터 관계가 아니다",
    mapping.UNMAPPED_MULTIPLE_INHERITANCE: (
        "부모가 둘 이상이다 — 관계형에는 다중 상속이 없어 하나도 옮기지 않았다. "
        "부모를 하나로 줄이거나, 나머지는 연관으로 바꿔라"
    ),
    mapping.UNMAPPED_INHERITANCE_CYCLE: (
        "상속이 순환한다 — 어느 행도 먼저 만들 수 없어 하나도 옮기지 않았다"
    ),
    mapping.UNMAPPED_DUPLICATE_JUNCTION: (
        "같은 두 Entity를 잇는 다대다가 둘 이상이다 — 연결 테이블 이름이 같아져 "
        "둘째는 옮기지 않았다. 하나로 합치거나, 둘을 구별해야 한다면 그 관계를 "
        "Entity로 승격시켜 양쪽과 각각 관계를 맺어라"
    ),
}


def erd_relationships_mapped(model: dict, logical: dict) -> list[Finding]:
    """옮기지 못한 관계가 남아 있는가 — **그림에 없는 관계가 모델에 있다.**

    `{type}`을 사상이 담아 둔 실제 종류로 바꾼다. **`.format`이 아니라 `.replace`다** —
    문구는 사람이 쓰는 자유 텍스트라 언젠가 중괄호가 들어가고, 그때 `.format`은 검사
    노드를 통째로 터뜨린다. 자리표시자가 없는 문구에서 `.replace`는 아무 일도 안 한다.
    """
    return [
        Finding(
            "erd.relationship-mapped",
            UNMAPPED_PROSE.get(item.get("reason"), str(item.get("reason"))).replace(
                "{type}", str(item.get("type") or "Dependency")
            ),
            "{} -> {}".format(item.get("source", "?"), item.get("target", "?")),
        )
        for item in (logical.get("Unmapped") or [])
    ]


def erd_composition_owner(model: dict, logical: dict) -> list[Finding]:
    """**BCE 층에서 본다** — 논리 모델에는 한쪽으로 정리된 결과만 남아 모순이 안 보인다."""
    rule_id = "erd.composition-owner-is-mandatory"
    entities = {c.get("className") for c in _classes(model) if fields.is_entity(c)}

    found: list[Finding] = []
    for relationship in _relationships(model):
        if str(relationship.get("type") or "") != "Composition":
            continue
        if not {relationship.get("source"), relationship.get("target")} <= entities:
            continue
        # source 가 전체(whole)이고 target 이 부분(part)이다 — 사상이 그렇게 읽는다.
        owner = multiplicity.normalize(relationship.get("sourceMultiplicity"))
        if not owner or owner == "1":
            # 빈 값은 다중도 자체가 없는 것이라 `erd.relationship-mapped`가 이미 말한다.
            continue
        found.append(
            Finding(
                rule_id,
                f"합성인데 전체 쪽이 '{owner}'다 — 합성은 부분이 전체 없이 존재할 수 없다는 "
                "뜻이라 전체 쪽은 '1'이어야 한다. 관계 종류를 Association으로 바꾸거나 "
                "다중도를 '1'로 고쳐라",
                _relation_label(relationship),
            )
        )
    return found


def erd_mandatory_reference_cycle(model: dict, logical: dict) -> list[Finding]:
    """필수 외래키만 따라가 **제자리로 돌아오는 고리**를 찾는다. 자기 참조도 고리다.

    고리마다 **지적 하나**를 낸다. 간선마다 내면 한 실수가 여러 지적이 되어, 재생성이
    하나를 고쳐도 위반 수가 안 줄고 수정본이 통째로 버려진다.
    """
    rule_id = "erd.no-mandatory-reference-cycle"
    edges: dict[str, set[str]] = {}
    for table in _tables(logical):
        name = str(table.get("name") or "")
        edges[name] = {
            str(c["references"])
            for c in table.get("columns") or []
            if c.get("references") and c.get("mandatory")
        }

    found: list[Finding] = []
    reported: set[frozenset[str]] = set()
    for start in edges:
        path: list[str] = []

        def walk(node: str) -> list[str] | None:
            if node == start and path:
                return [*path, start]
            if node in path:
                return None
            path.append(node)
            for nxt in sorted(edges.get(node, ())):
                cycle = walk(nxt)
                if cycle:
                    return cycle
            path.pop()
            return None

        for first in sorted(edges[start]):
            cycle = walk(first)
            if not cycle:
                continue
            key = frozenset(cycle)
            if key in reported:
                break
            reported.add(key)
            found.append(
                Finding(
                    rule_id,
                    "필수 외래키가 " + " → ".join([start, *cycle]) + " 로 제자리에 돌아온다 "
                    "— 이 고리에는 첫 행을 넣을 수 없다. 한 곳의 다중도를 '0..1'로 바꾸거나 "
                    "관계를 다시 보라",
                    start,
                )
            )
            break
    return found


def erd_identifier_fields(model: dict, logical: dict) -> list[Finding]:
    """**없는 필드**를 가리키는 것과 있지만 **키가 될 수 없는** 필드(다중값)를 가리키는
    것을 구별해 말한다 — 고치는 쪽이 할 일이 다르다. BCE 층에서 본다(논리 모델에는 이미
    대리키로 떨어진 결과만 남아 있다).
    """
    rule_id = "erd.identifier-fields-exist"
    found: list[Finding] = []
    for class_item in _classes(model):
        if not fields.is_entity(class_item):
            continue
        name = class_item.get("className") or "?"
        declared = {
            field: raw_type
            for field, raw_type in (
                fields.split_field(f) for f in class_item.get("fields") or [] if str(f).strip()
            )
            if field
        }
        for wanted in class_item.get("identifier") or []:
            match = next(
                (d for d in declared if fields.squash(str(wanted)) == fields.squash(d)), None
            )
            if match is None:
                found.append(
                    Finding(rule_id, f"identifier '{wanted}'가 이 Entity의 필드에 없음", name)
                )
            elif fields.is_collection(declared[match]):
                found.append(
                    Finding(
                        rule_id,
                        f"identifier '{wanted}'는 다중값 필드라 키가 될 수 없음 — "
                        "제1정규화로 자식 표에 가므로 이 표에 칸이 안 남는다",
                        name,
                    )
                )
    return found


def erd_surrogate_key_collides(model: dict, logical: dict) -> list[Finding]:
    """사상이 표시해 둔 충돌(`surrogateCollidesWith`)을 지적으로 옮긴다."""
    return [
        Finding(
            "erd.surrogate-key-collides",
            f"우리가 붙이는 대리키와 이름이 같은 필드가 있다 — 선언한 '{collision}'이 밀려난다. "
            "식별자라면 identifier에 적고, 아니라면 이름을 바꿔라",
            str(table.get("name") or "?"),
        )
        for table in _tables(logical)
        if (collision := table.get("surrogateCollidesWith"))
    ]


def erd_table_names_unique(model: dict, logical: dict) -> list[Finding]:
    """테이블 이름이 유일한가 — **사상이 만든 이름(연결 표·1NF 자식)까지 세어서.**"""
    rule_id = "erd.table-names-unique"
    seen: set[str] = set()
    found: list[Finding] = []
    for table in _tables(logical):
        name = str(table.get("name") or "")
        if name in seen:
            kind = table.get("origin", {}).get("kind", "class")
            found.append(Finding(rule_id, f"테이블 이름이 겹친다 (출처: {kind})", name))
        seen.add(name)
    return found


# 기본키 유무와 외래키 참조를 보는 검출기는 **일부러 없다.** 사상이 그 둘을 구성에 의해
# 보장하므로 어떤 모델로도 위반이 안 나오고, 그러면 그 검출기의 "0건"은 아무 정보가
# 아니다. 불변식은 `tests/test_erd_mapping.py`가 지킨다 — 사상이 깨지면 그쪽이 운다.


def erd_entity_typed_field_needs_relationship(model: dict, logical: dict) -> list[Finding]:
    """필드 타입이 Entity인데 그 둘 사이에 관계가 없다 — **사상이 조용히 버리는 자리다.**

    `member : Member`나 `lines : List<OrderLine>`는 컬럼이 아니다. 그 사실을 들고 가는
    것은 관계이고(`mapping.py`의 사상표), 그래서 사상은 컬럼을 안 만든다. 관계까지 없으면
    **모델이 적은 링크가 산출물 어디에도 안 남는다** — 컬럼도 자식 표도 관계선도 없고
    `Unmapped`에도 안 들어간다. 드러날 자리가 여기뿐이다.

    한동안 컬렉션은 버려지고 스칼라는 가짜 컬럼이 됐다(`member : MEMBER` — SQL 타입도
    아닌 것이 하류 DDL까지 갔다). 지금은 둘 다 안 만들고, 둘 다 여기서 말한다.

    **이름이 아니라 타입을 본다.** `erd.fk-from-field-name`이 금지하는 추측과 다른
    일이라는 것은 `fields.names_an_entity`의 docstring에 적어 두었다.
    """
    rule_id = "erd.entity-typed-field-needs-relationship"
    entities = {
        c.get("className"): c for c in _classes(model)
        if fields.is_entity(c) and c.get("className")
    }
    linked: set[frozenset[str]] = {
        frozenset((str(r.get("source")), str(r.get("target"))))
        for r in _relationships(model)
    }

    found: list[Finding] = []
    for name, class_item in entities.items():
        for raw in class_item.get("fields") or []:
            field_name, raw_type = fields.split_field(raw)
            if not field_name or not raw_type:
                continue
            target = fields.referenced_entity(raw_type, entities)
            # 자기 자신을 가리키는 필드도 관계를 요구한다 — 자기 참조는 정상적인 관계다.
            if not target or frozenset((name, target)) in linked:
                continue
            collection = fields.is_collection(raw_type)
            found.append(
                Finding(
                    rule_id,
                    f"'{field_name} : {raw_type}'가 Entity '{target}'를 가리키는데 둘 사이에 "
                    "관계가 없다 — Entity 타입 필드는 컬럼이 되지 않으므로 이대로면 "
                    "ERD에 아무것도 안 남는다. "
                    + (
                        f"'{name}'과 '{target}'을 다중도와 함께 관계로 적어라"
                        if collection
                        else f"'{name}'과 '{target}'을 관계로 적거나, 참조가 아니라면 "
                        "필드 타입을 자료형으로 바꿔라"
                    ),
                    name,
                )
            )
    return found


#: `memberId` · `member_id` · `MemberID` 처럼 뒤에 붙은 식별자 접미사.
_ID_SUFFIX = re.compile(r"[_\s]*id$", re.IGNORECASE)


def erd_reference_like_fields(model: dict, logical: dict) -> list[Finding]:
    """**좁게 건다.** 필드가 `<X>Id` 꼴이고, `X`가 실재 Entity이고, 그 둘 사이에 관계가
    하나도 없을 때만 센다. 관계가 이미 있으면 일부러 적어 둔 칸일 수 있고, 그때 지적하면
    고칠 것이 없는 지적으로 재생성 예산만 태운다.

    **타입이 이미 Entity를 말하는 필드는 건너뛴다** —
    `erd.entity-typed-field-needs-relationship`이 그쪽을 맡는다. `member : Member`처럼
    이름과 타입이 둘 다 걸리는 필드가 있어서, 안 비키면 실수 하나가 지적 둘이 되어
    재생성이 하나를 고쳐도 위반 수가 안 줄고 수정본이 통째로 버려진다.
    """
    rule_id = "erd.field-looks-like-reference"
    entities = {
        c.get("className"): c for c in _classes(model)
        if fields.is_entity(c) and c.get("className")
    }
    linked: set[frozenset[str]] = {
        frozenset((str(r.get("source")), str(r.get("target"))))
        for r in _relationships(model)
    }

    found: list[Finding] = []
    for name, class_item in entities.items():
        for raw in class_item.get("fields") or []:
            field_name, raw_type = fields.split_field(raw)
            if fields.referenced_entity(raw_type, entities):
                continue
            stem = _ID_SUFFIX.sub("", field_name).strip()
            if not stem or fields.squash(stem) == fields.squash(name):
                continue
            target = next(
                (e for e in entities if fields.squash(e) == fields.squash(stem)), None
            )
            if target and frozenset((name, target)) not in linked:
                found.append(
                    Finding(
                        rule_id,
                        f"'{field_name}'가 Entity '{target}'를 이름으로 가리킨다 — "
                        "관계로 적어야 외래키가 된다",
                        name,
                    )
                )
    return found


#: ERD 검출기. 클래스 쪽과 시그니처가 다르다 — 둘째 인자가 상태가 아니라 **논리 데이터
#: 모델**이다. 같은 이름으로 두면 "상태를 받는다"고 읽히고, 실제로 상태를 넘기는 실수가
#: 조용히 통과한다(dict라 아무 오류도 안 난다).
#:
#: **순서가 뜻을 갖는다**: 구조(끝점·딱지·이름) → 산출물이 있는가 → 관계가 옮겨졌는가
#: → 키 → 이름 신호. 구조가 먼저인 이유는 그것이 깨지면 뒤의 것들이 **볼 것이 없어져
#: 조용해지기** 때문이다 — 딱지가 깨져 표가 사라지면 그 표에 대한 어떤 검사도 안 돈다.
ERD_DETECTORS: dict[str, Callable[[dict, dict], list[Finding]]] = {
    "erd_relationship_endpoints": erd_relationship_endpoints,
    "erd_stereotype_is_bce": erd_stereotype_is_bce,
    "erd_entity_name_usable": erd_entity_name_usable,
    "erd_has_entity": erd_has_entity,
    "erd_relationships_mapped": erd_relationships_mapped,
    "erd_composition_owner": erd_composition_owner,
    "erd_mandatory_reference_cycle": erd_mandatory_reference_cycle,
    "erd_identifier_fields": erd_identifier_fields,
    "erd_surrogate_key_collides": erd_surrogate_key_collides,
    "erd_table_names_unique": erd_table_names_unique,
    # 타입 신호가 이름 신호보다 **먼저**다. 앞엣것은 모델이 적은 자료형을 읽는 것이고
    # 뒤엣것은 이름에서 짐작하는 것이라, 둘 다 걸리는 필드에서는 앞엣것이 맡는다.
    "erd_entity_typed_field_needs_relationship": erd_entity_typed_field_needs_relationship,
    "erd_reference_like_fields": erd_reference_like_fields,
}


def erd_findings(model: dict, state: dict) -> list[Finding]:
    """ERD 모델 하나에 대한 결정론 검증 전부.

    `state`를 받지만 **안 쓴다.** 시그니처가 `DesignArtifactSpec.check`의 것이라 그대로
    맞추고, 상류 대조가 필요한 ERD 규칙은 지금 없다 — ERD가 참조하는 유스케이스는 클래스
    다이어그램에서 이미 판정됐다.

    사상을 여기서 한 번 돌린다. 렌더가 다시 돌리므로 두 번 도는 셈인데, 순수 함수라
    결과가 같고 캐시를 두면 "언제 무효화하나"가 새 문제가 된다.
    """
    logical = mapping.build_logical_model(model or {})
    found: list[Finding] = []
    for detect in ERD_DETECTORS.values():
        found.extend(detect(model or {}, logical))
    return found


#: 검출기 이름 → 구현. 이름은 `rules.Rule.detector`가 가리키는 그것이다.
#: 양방향으로 맞물려 있어야 한다 — 선언만 있고 구현이 없거나, 구현만 있고 아무 규칙도
#: 안 쓰는 검출기가 있으면 테스트가 실패한다.
#:
#: **순서가 뜻을 갖는다**: 참조 무결성 → 스테레오타입 → 통신 규칙 → 형태 → 커버리지.
#: 뒤의 검출기들이 앞의 것이 이미 지적한 것을 건너뛰므로(중복 지적 방지), 순서가 곧
#: 어느 지적이 살아남는가다.
CLASS_DIAGRAM_DETECTORS: dict[str, Callable[[dict, dict], list[Finding]]] = {
    "relationship_endpoints": relationship_endpoints,
    "usecase_ids": usecase_ids,
    "stereotype_is_bce": stereotype_is_bce,
    "communication_rules": communication_rules,
    "relationship_type_known": relationship_type_known,
    "entity_association_multiplicity": entity_association_multiplicity,
    "method_parameters_typed": method_parameters_typed,
    "fields_typed": fields_typed,
    "control_outcome_return_contract": control_outcome_return_contract,
    "names_unique": names_unique,
    "name_pascal_case": name_pascal_case,
    "usecase_coverage": usecase_coverage,
}


def class_diagram_findings(model: dict, state: dict) -> list[Finding]:
    """BCE 모델 하나에 대한 결정론 검증 전부.

    `state`가 필요한 이유는 `usecase_ids`·`usecase_coverage`가 입력 유스케이스 명세를
    봐야 해서다 — 모델만으로는 "지어낸 id"와 "정당한 id"를 구별할 수 없다.
    """
    found: list[Finding] = []
    for detect in CLASS_DIAGRAM_DETECTORS.values():
        found.extend(detect(model or {}, state or {}))
    return found


# ---------------------------------------------------------------------------
# Sequence diagram and API specification detectors
# ---------------------------------------------------------------------------
def _class_names_from_puml(state: dict) -> set[str]:
    return set(re.findall(r"(?m)^\s*class\s+([A-Za-z_]\w*)\b", state.get("class_diagram_puml", "")))


def _known_use_case_ids(state: dict) -> set[str]:
    return rtm.upstream_names(state).get("use_case") or set()


def _known_flow_step_ids(state: dict) -> set[str]:
    """요구사항 명세의 주·확장 흐름 단계를 안정적인 참조 ID로 펼친다."""
    spec = state.get("usecase_spec") or {}
    if not isinstance(spec, dict):
        return set()
    result: set[str] = set()
    for use_case in spec.get("use_case_specs") or []:
        if not isinstance(use_case, dict):
            continue
        use_case_id = str(use_case.get("use_case_id") or "").strip()
        if not use_case_id:
            continue
        for step in use_case.get("main_scenario") or []:
            number = step.get("step_number") if isinstance(step, dict) else None
            if number is not None:
                result.add(f"{use_case_id}:main:{number}")
        for extension in use_case.get("extensions") or []:
            if not isinstance(extension, dict):
                continue
            label = str(extension.get("label") or "").strip()
            for step in extension.get("handling_steps") or []:
                sub_step = str(step.get("sub_step") or "").strip() if isinstance(step, dict) else ""
                if label and sub_step:
                    result.add(f"{use_case_id}:extension:{label}:{sub_step}")
    return result


def _flow_step_sentence(step: dict) -> str:
    return str(step.get("sentence") or step.get("description") or "").strip()


def _is_unresolved_step(step: dict) -> bool:
    if str(step.get("status") or "").strip().lower() == "unresolved":
        return True
    sentence = _flow_step_sentence(step).lower()
    return any(
        marker in sentence
        for marker in ("todo", "tbd", "what do we do", "to be decided", "미정", "결정 필요")
    )


def _unresolved_flow_step_ids(state: dict) -> set[str]:
    result: set[str] = set()
    spec = state.get("usecase_spec") or {}
    if not isinstance(spec, dict):
        return result
    for use_case in spec.get("use_case_specs") or []:
        if not isinstance(use_case, dict):
            continue
        use_case_id = str(use_case.get("use_case_id") or "").strip()
        for step in use_case.get("main_scenario") or []:
            if isinstance(step, dict) and _is_unresolved_step(step):
                result.add(f"{use_case_id}:main:{step.get('step_number')}")
        for extension in use_case.get("extensions") or []:
            if not isinstance(extension, dict):
                continue
            label = str(extension.get("label") or "").strip()
            for step in extension.get("handling_steps") or []:
                if isinstance(step, dict) and _is_unresolved_step(step):
                    result.add(f"{use_case_id}:extension:{label}:{step.get('sub_step')}")
    return result


def _message_fragments(message: dict) -> list[dict]:
    """새 fragment 경로를 읽고, 옛 group/condition 저장본도 한 레벨로 해석한다."""
    fragments = message.get("fragments")
    if isinstance(fragments, list):
        return [item for item in fragments if isinstance(item, dict)]
    group = str(message.get("group") or "").strip()
    condition = str(message.get("condition") or "").strip()
    if not group and not condition:
        return []
    return [{"id": f"legacy:{group}:{condition}", "type": group, "branch": "main", "condition": condition}]


def _participant_id(participant: dict) -> str:
    """메시지가 참조하는 참가자 ID. 옛 저장본은 name을 alias로 간주한다."""
    return str(participant.get("alias") or participant.get("name") or "").strip()


def _class_methods_from_state(state: dict) -> dict[str, set[str]]:
    """BCE 모델에서 클래스별 메서드 집합을 뽑는다.

    메서드 이름은 비교를 위해 **괄호와 앞뒤 공백을 벗긴** 형태로 정규화한다.
    BCE 모델의 `methods`는 `list[str]`이고, 각 원소는 `"registerMember()"` 같은
    자유 텍스트다. 시퀀스 메시지의 `label`도 같은 형태이므로 정규화된 이름으로
    대조한다.
    """
    classes = (state.get("extracted_bce_classes") or {}).get("Classes", [])
    result: dict[str, set[str]] = {}
    for c in classes:
        name = c.get("className")
        if not name:
            continue
        methods: set[str] = set()
        for m in c.get("methods") or []:
            normalized = _normalize_method_name(str(m).strip())
            if normalized:
                methods.add(normalized)
        result[name] = methods
    return result


def _normalize_method_name(raw: str) -> str:
    """메서드 이름을 비교 가능한 형태로 정규화한다.

    BCE 모델의 메서드(`"+ registerMember(name: String): void"`)와 시퀀스 메시지의
    라벨(`"registerMember()"`)을 대조하려면 양쪽을 같은 형태로 만들어야 한다.
    가시성 기호(`+`, `-`, `#`, `~`)와 반환 타입(`: Type`), 매개변수 목록 안의
    내용을 전부 벗기고, 메서드 **이름만** 소문자로 남긴다.
    """
    # 가시성 기호 제거
    raw = re.sub(r'^[+\-#~]\s*', '', raw)
    # 괄호 이전의 이름만 추출
    match = re.match(r'([A-Za-z_]\w*)', raw)
    return match.group(1).lower() if match else raw.lower().strip()


def sequence_participants(model: dict, state: dict) -> list[Finding]:
    declared = {_participant_id(item) for item in model.get("Participants", []) if _participant_id(item)}
    found: list[Finding] = []
    for message in model.get("Messages", []):
        source, target = str(message.get("source", "")).strip(), str(message.get("target", "")).strip()
        label = f"{source} -> {target}".strip()
        if source not in declared:
            found.append(Finding("sequence.message-participants-exist", f"source '{source}'가 Participants에 없음", label))
        if target not in declared:
            found.append(Finding("sequence.message-participants-exist", f"target '{target}'가 Participants에 없음", label))
    return found


def sequence_bce_flow(model: dict, state: dict) -> list[Finding]:
    kinds = {_participant_id(item): str(item.get("kind", "")).strip().lower() for item in model.get("Participants", [])}
    allowed = {
        ("actor", "boundary"),
        ("boundary", "control"),
        ("control", "boundary"),
        ("control", "control"),
        ("control", "entity"),
        ("control", "database"),
        ("entity", "entity"),
        ("entity", "database"),
    }
    found: list[Finding] = []
    for message in model.get("Messages", []):
        if str(message.get("type", "sync")).lower() in {"return", "activate", "deactivate"}:
            continue
        source, target = str(message.get("source", "")).strip(), str(message.get("target", "")).strip()
        source_kind, target_kind = kinds.get(source), kinds.get(target)
        if source == target and source_kind and source_kind != "actor":
            continue
        if source_kind and target_kind and (source_kind, target_kind) not in allowed:
            found.append(Finding("sequence.message-bce-flow", f"{source_kind} → {target_kind} 호출은 BCE 흐름을 위반함", f"{source} -> {target}"))
    return found


def sequence_traceability(model: dict, state: dict) -> list[Finding]:
    classes, use_cases = _class_names_from_puml(state), _known_use_case_ids(state)
    flow_steps = _known_flow_step_ids(state)
    found: list[Finding] = []
    for participant in model.get("Participants", []):
        source_class, name = str(participant.get("source_class", "")).strip(), str(participant.get("name", "?")).strip()
        if source_class and source_class not in classes:
            found.append(Finding("sequence.references-exist", f"클래스 다이어그램에 없는 source_class '{source_class}'", name))
    if use_cases:
        for message in model.get("Messages", []):
            for use_case in message.get("use_case_ids", []):
                if use_case and use_case not in use_cases:
                    found.append(Finding("sequence.references-exist", f"입력에 없는 유스케이스 id '{use_case}'", f"{message.get('source', '?')} -> {message.get('target', '?')}"))
            for step_id in message.get("step_ids", []):
                if step_id and flow_steps and step_id not in flow_steps:
                    found.append(Finding("sequence.references-exist", f"입력에 없는 흐름 단계 id '{step_id}'", f"{message.get('source', '?')} -> {message.get('target', '?')}"))
    return found


def sequence_participant_classes(model: dict, state: dict) -> list[Finding]:
    """비-액터 참가자가 클래스 다이어그램에 실재하는 클래스인가.

    `sequence_traceability`는 추적 필드(`source_class`)만 본다 — 그 필드가 비어 있으면
    지적하지 않는다. 그런데 참가자 `name` 자체가 클래스 다이어그램에 없는 이름이면,
    그 참가자에 매달린 메시지가 전부 유령 상호작용이 된다. 여기서 잡는다.

    액터는 유스케이스 명세에서 오므로 클래스 목록에 없는 것이 정상이다 — 건너뛴다.
    """
    rule_id = "sequence.participant-classes-exist"
    classes = _class_names_from_puml(state)
    if not classes:
        return []  # 클래스 다이어그램이 없으면 대조할 것이 없다

    found: list[Finding] = []
    for participant in model.get("Participants", []):
        kind = str(participant.get("kind", "")).strip().lower()
        if kind == "actor":
            continue
        name = str(participant.get("name", "")).strip()
        if not name:
            continue
        # source_class가 있으면 그것으로 대조, 없으면 name으로 대조
        class_ref = str(participant.get("source_class", "")).strip() or name
        if class_ref not in classes:
            found.append(
                Finding(rule_id, f"클래스 다이어그램에 없는 참가자 '{name}' (대응 클래스 '{class_ref}')", name)
            )
    return found


def sequence_message_methods(model: dict, state: dict) -> list[Finding]:
    """메시지 라벨이 target 클래스의 실제 메서드인가.

    시퀀스 다이어그램의 메시지 라벨은 "호출되는 오퍼레이션"이다. 클래스 다이어그램에서
    해당 클래스의 `methods`에 정의되지 않은 오퍼레이션을 호출하면 설계가 불일치한다.

    라벨이 비어 있으면 건너뛴다 — 라벨 없는 메시지는 이름을 안 단 것이지 없는 메서드를
    부른 것이 아니다. return 타입 메시지도 건너뛴다 — 응답은 호출이 아니다.

    대조는 **전체 호출 시그니처 수준**이다. BCE 모델의 메서드가
    `"registerMember(name: String): void"`이면 메시지도 `"registerMember(name: String)"`
    이어야 한다. 반환 타입과 가시성만 비교에서 제외한다.
    """
    rule_id = "sequence.message-labels-match-methods"
    classes = (state.get("extracted_bce_classes") or {}).get("Classes", [])
    class_methods = {
        str(class_item.get("className")): {
            signature
            for raw_method in class_item.get("methods") or []
            if (signature := method_call_signature(str(raw_method)))
        }
        for class_item in classes
        if class_item.get("className")
    }
    if not class_methods:
        return []  # BCE 모델이 없으면 대조할 것이 없다

    # 참가자 이름 → 대응 클래스 매핑 (source_class가 있으면 그것, 없으면 name)
    participant_to_class: dict[str, str] = {}
    for participant in model.get("Participants", []):
        name = _participant_id(participant)
        kind = str(participant.get("kind", "")).strip().lower()
        if kind == "actor" or not name:
            continue
        class_ref = str(participant.get("source_class", "")).strip() or name
        participant_to_class[name] = class_ref

    found: list[Finding] = []
    for message in model.get("Messages", []):
        if str(message.get("type", "sync")).lower() not in {"sync", "async", "self"}:
            continue
        label = str(message.get("label", "")).strip()
        if not label:
            continue
        target = str(message.get("target", "")).strip()
        source = str(message.get("source", "")).strip()
        target_class = participant_to_class.get(target)
        if not target_class:
            continue  # 액터이거나 매핑이 없다 — 다른 검출기가 잡는다

        methods = class_methods.get(target_class)
        if methods is None:
            continue  # 클래스 자체가 BCE에 없다 — participant_classes 검출기가 잡는다

        normalized_label = method_call_signature(label)
        if normalized_label and normalized_label not in methods:
            location = f"{source} -> {target} : {label}"
            found.append(
                Finding(
                    rule_id,
                    f"'{target_class}' 클래스에 '{label}' 메서드가 정의되어 있지 않음",
                    location,
                )
            )
    return found


def api_path_parameters(model: dict, state: dict) -> list[Finding]:
    found: list[Finding] = []
    for endpoint in model.get("Endpoints", []):
        path = str(endpoint.get("path", ""))
        expected = set(re.findall(r"\{([^{}]+)\}", path))
        actual = {str(item.get("name", "")).strip() for item in endpoint.get("path_params", []) if item.get("name")}
        if expected != actual:
            found.append(Finding("api.path-parameters-match", f"경로 변수 {sorted(expected)}와 path_params {sorted(actual)}가 일치하지 않음", f"{endpoint.get('method', 'get').upper()} {path}"))
    return found


def api_operations_present(model: dict, state: dict) -> list[Finding]:
    """Require a generated API model to contain an implementable operation.

    The implementation pipeline always runs OpenAPI Generator, which rejects a
    schema-only document.  More importantly, a schema without an operation does
    not realize any user-visible system behaviour.  Treat this as a design-model
    defect so the normal API repair loop can regenerate grounded endpoints before
    implementation starts.
    """
    endpoints = model.get("Endpoints") if isinstance(model, dict) else None
    if isinstance(endpoints, list) and any(isinstance(endpoint, dict) for endpoint in endpoints):
        return []
    return [
        Finding(
            "api.operations-present",
            "구현 가능한 API operation이 없음 — 유스케이스·BCE Control·시퀀스 호출에 근거한 endpoint를 생성해야 함",
        )
    ]


def api_schema_references(model: dict, state: dict) -> list[Finding]:
    schemas = {str(item.get("name", "")).strip() for item in model.get("Schemas", []) if item.get("name")}
    found: list[Finding] = []
    for endpoint in model.get("Endpoints", []):
        location = f"{endpoint.get('method', 'get').upper()} {endpoint.get('path', '')}"
        references = [endpoint.get("request_schema", "")] + [item.get("schema_name", "") for item in endpoint.get("responses", [])]
        for reference in references:
            if reference and str(reference).strip() not in schemas:
                found.append(Finding("api.schema-references-exist", f"Schemas에 없는 참조 '{reference}'", location))
    return found


def api_operation_ids(model: dict, state: dict) -> list[Finding]:
    seen: set[str] = set()
    found: list[Finding] = []
    for endpoint in model.get("Endpoints", []):
        operation_id = str(endpoint.get("operation_id", "")).strip()
        location = f"{endpoint.get('method', 'get').upper()} {endpoint.get('path', '')}"
        if not operation_id:
            found.append(Finding("api.operation-ids-unique", "operation_id가 비어 있음", location))
        elif operation_id in seen:
            found.append(Finding("api.operation-ids-unique", f"중복 operation_id '{operation_id}'", location))
        seen.add(operation_id)
    return found


def api_traceability(model: dict, state: dict) -> list[Finding]:
    classes, use_cases = _class_names_from_puml(state), _known_use_case_ids(state)
    found: list[Finding] = []
    for endpoint in model.get("Endpoints", []):
        location = f"{endpoint.get('method', 'get').upper()} {endpoint.get('path', '')}"
        for source_class in endpoint.get("source_classes", []):
            if source_class and source_class not in classes:
                found.append(Finding("api.references-exist", f"클래스 다이어그램에 없는 source_class '{source_class}'", location))
        if use_cases:
            for use_case in endpoint.get("use_case_ids", []):
                if use_case and use_case not in use_cases:
                    found.append(Finding("api.references-exist", f"입력에 없는 유스케이스 id '{use_case}'", location))
    return found


def _api_location(endpoint: dict) -> str:
    return f"{endpoint.get('method', 'get').upper()} {endpoint.get('path', '')}"


def _control_method_contracts(state: dict) -> dict[str, dict[str, dict[str, object]]]:
    """Return exact BCE Control method contracts keyed by class then method name."""
    controls: dict[str, dict[str, dict[str, object]]] = {}
    for class_item in (state.get("extracted_bce_classes") or {}).get("Classes", []):
        stereotype = str(class_item.get("stereotype", "")).replace("<", "").replace(">", "").strip().lower()
        if stereotype != "control":
            continue
        class_name = str(class_item.get("className") or "").strip()
        if not class_name:
            continue
        methods: dict[str, dict[str, object]] = {}
        for raw in class_item.get("methods") or []:
            text = str(raw)
            signature = method_call_signature(text)
            if not signature:
                continue
            match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\((.*)\)$", signature)
            if not match:
                continue
            parameters: list[tuple[str, str]] = []
            raw_parameters = match.group(2).strip()
            if raw_parameters:
                for value in raw_parameters.split(","):
                    parameter = value.strip()
                    name, separator, type_name = parameter.partition(":")
                    if not separator or not name.strip() or not type_name.strip():
                        # The sequence/class contract validator reports the
                        # malformed declaration itself. Do not pretend it is
                        # a callable API target here.
                        parameters = []
                        break
                    parameters.append((name.strip(), type_name.strip()))
            methods[match.group(1)] = {
                "signature": signature,
                "parameters": tuple(parameters),
                "returnType": method_return_type(text),
            }
        controls[class_name] = methods
    return controls


def _normalise_contract_type(value: str) -> str:
    """Normalise the small type vocabulary shared by API and BCE models."""
    token = re.sub(r"\s+", "", value or "").removesuffix("?").lower()
    aliases = {
        "string": "string", "str": "string",
        "integer": "integer", "int": "integer", "long": "integer", "short": "integer",
        "float": "number", "double": "number", "bigdecimal": "number", "number": "number",
        "bool": "boolean", "boolean": "boolean",
    }
    return aliases.get(token, token)


def _contract_types_compatible(actual: str, expected: str) -> bool:
    """Treat inbound request DTO wrappers as the represented domain type."""
    actual_normalized = _normalise_contract_type(actual)
    expected_normalized = _normalise_contract_type(expected)
    if actual_normalized == expected_normalized:
        return True
    # Java date/time values are serialized as ISO strings on the HTTP wire.
    # Treating them as incompatible would reject a valid JSON representation.
    if actual_normalized == "string" and (
        expected_normalized.startswith("java.time.")
        or expected_normalized in {"localdate", "localdatetime", "instant"}
    ):
        return True
    # Only inbound DTO conventions may stand in for a Control parameter.
    # Accepting ``Response`` here would hide a directionally-invalid mapping.
    suffixes = ("createrequest", "updaterequest", "request", "dto")
    def base(value: str) -> str:
        lowered = value.lower()
        for suffix in suffixes:
            if lowered.endswith(suffix) and len(lowered) > len(suffix):
                return lowered[: -len(suffix)]
        return lowered
    return base(actual_normalized) == base(expected_normalized)


def _request_value_types(endpoint: dict, schemas: dict[str, dict]) -> dict[str, str]:
    """Enumerate the only request values an API binding may consume."""
    values: dict[str, str] = {}
    for prefix, key in (("$path.", "path_params"), ("$query.", "query_params")):
        for parameter in endpoint.get(key, []) or []:
            if not isinstance(parameter, dict):
                continue
            name = str(parameter.get("name") or "").strip()
            if name:
                values[prefix + name] = str(parameter.get("type") or "string")
    request_schema = str(endpoint.get("request_schema") or "").strip()
    if request_schema:
        values["$body"] = request_schema
        for field in schemas.get(request_schema, {}).get("fields", []) or []:
            if not isinstance(field, dict):
                continue
            name = str(field.get("name") or "").strip()
            if name:
                values[f"$body.{name}"] = str(field.get("type") or "string")
    return values


def _binding(endpoint: dict) -> dict | None:
    value = endpoint.get("control_binding")
    return value if isinstance(value, dict) else None


def api_control_binding(model: dict, state: dict) -> list[Finding]:
    """Ensure every API operation has a real, reviewable Control target."""
    controls = _control_method_contracts(state)
    found: list[Finding] = []
    for endpoint in model.get("Endpoints", []) or []:
        if not isinstance(endpoint, dict):
            continue
        location = _api_location(endpoint)
        binding = _binding(endpoint)
        if binding is None:
            found.append(Finding(
                "api.control-binding-exists",
                "endpoint에 control_binding이 없음 — 구현할 BCE Control과 메서드를 명시해야 함",
                location,
            ))
            continue
        control = str(binding.get("control") or "").strip()
        method = str(binding.get("method") or "").strip()
        if not control or control not in controls:
            found.append(Finding(
                "api.control-binding-exists",
                f"Control '{control or '<empty>'}'이 BCE의 <<Control>> 클래스로 존재하지 않음",
                location,
            ))
            continue
        if not method or method not in controls[control]:
            found.append(Finding(
                "api.control-binding-exists",
                f"{control}에 '{method or '<empty>'}' 메서드가 정의되어 있지 않음",
                location,
            ))
            continue
        source_classes = {
            str(item).strip() for item in endpoint.get("source_classes", []) or [] if str(item).strip()
        }
        if control not in source_classes:
            found.append(Finding(
                "api.control-binding-exists",
                f"control_binding의 {control}이 source_classes에 추적되지 않음",
                location,
            ))
    return found


def api_control_arguments(model: dict, state: dict) -> list[Finding]:
    """Reject implicit, missing, or type-incompatible HTTP-to-Control values."""
    controls = _control_method_contracts(state)
    schemas = {
        str(item.get("name") or "").strip(): item
        for item in model.get("Schemas", []) or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    found: list[Finding] = []
    for endpoint in model.get("Endpoints", []) or []:
        if not isinstance(endpoint, dict):
            continue
        binding = _binding(endpoint)
        if binding is None:
            continue
        control = str(binding.get("control") or "").strip()
        method = str(binding.get("method") or "").strip()
        contract = controls.get(control, {}).get(method)
        if contract is None:
            continue  # api_control_binding owns unknown targets.
        location = _api_location(endpoint)
        expected = dict(contract["parameters"])
        supplied: dict[str, str] = {}
        duplicate: set[str] = set()
        for argument in binding.get("arguments", []) or []:
            if not isinstance(argument, dict):
                continue
            name = str(argument.get("name") or "").strip()
            source = str(argument.get("source") or "").strip()
            if name in supplied:
                duplicate.add(name)
            elif name:
                supplied[name] = source
        if duplicate:
            found.append(Finding(
                "api.control-arguments-match",
                f"Control 인자 {sorted(duplicate)}가 둘 이상 매핑됨",
                location,
            ))
        if set(supplied) != set(expected):
            found.append(Finding(
                "api.control-arguments-match",
                f"{control}.{method} 인자 {sorted(expected)}와 바인딩 인자 {sorted(supplied)}가 일치하지 않음",
                location,
            ))
        available = _request_value_types(endpoint, schemas)
        for name, source in supplied.items():
            if source not in available:
                found.append(Finding(
                    "api.control-arguments-match",
                    f"'{name}'의 원천 '{source or '<empty>'}'이 선언된 path/query/body 값이 아님",
                    location,
                ))
                continue
            if name not in expected:
                continue
            actual_type = _normalise_contract_type(available[source])
            expected_type = _normalise_contract_type(expected[name])
            if not _contract_types_compatible(actual_type, expected_type):
                found.append(Finding(
                    "api.control-arguments-match",
                    f"'{name}'의 원천 타입 {available[source]}이 Control 파라미터 타입 {expected[name]}과 호환되지 않음",
                    location,
                ))
    return found


def api_control_outcomes(model: dict, state: dict) -> list[Finding]:
    """Require a named Control result for every documented HTTP result."""
    controls = _control_method_contracts(state)
    found: list[Finding] = []
    for endpoint in model.get("Endpoints", []) or []:
        if not isinstance(endpoint, dict):
            continue
        binding = _binding(endpoint)
        if binding is None:
            continue
        control = str(binding.get("control") or "").strip()
        method = str(binding.get("method") or "").strip()
        contract = controls.get(control, {}).get(method)
        if contract is None:
            continue
        location = _api_location(endpoint)
        documented = {
            int(item.get("status"))
            for item in endpoint.get("responses", []) or []
            if isinstance(item, dict) and str(item.get("status", "")).isdigit()
        }
        outcomes: dict[int, str] = {}
        duplicate: set[int] = set()
        for outcome in binding.get("outcomes", []) or []:
            if not isinstance(outcome, dict) or not str(outcome.get("status", "")).isdigit():
                continue
            status = int(outcome["status"])
            if status in outcomes:
                duplicate.add(status)
            outcomes[status] = str(outcome.get("outcome") or "").strip()
        if documented != set(outcomes) or any(not value for value in outcomes.values()) or duplicate:
            found.append(Finding(
                "api.control-outcomes-cover-responses",
                f"문서화한 응답 {sorted(documented)}과 Control outcome {sorted(outcomes)}이 일치하지 않거나 outcome 이름이 비어 있음",
                location,
            ))
        for response in endpoint.get("responses", []) or []:
            if not isinstance(response, dict):
                continue
            status = int(response.get("status", 0) or 0)
            if 200 <= status < 300 and status != 204 and not str(response.get("schema_name") or "").strip():
                found.append(Finding(
                    "api.control-outcomes-cover-responses",
                    f"{status} 성공 응답에 schema_name이 없어 생성 코드가 Object 응답으로 약화됨",
                    location,
                ))
                break
        needs_result = any(status != 204 for status in documented)
        return_type = _normalise_contract_type(str(contract.get("returnType") or ""))
        if needs_result and return_type in {"", "void", "object", "any", "map", "dict"}:
            found.append(Finding(
                "api.control-outcomes-cover-responses",
                f"{control}.{method}의 반환 타입 '{contract.get('returnType') or '<none>'}'은 문서화한 결과를 구분할 수 없음",
                location,
            ))
    return found


def _sequence_diagrams_for_api(state: dict) -> list[dict]:
    model = state.get("sequence_diagram_model") or {}
    diagrams = model.get("Diagrams") if isinstance(model, dict) else None
    if isinstance(diagrams, list):
        return [item for item in diagrams if isinstance(item, dict)]
    return [model] if isinstance(model, dict) else []


def api_control_sequence(model: dict, state: dict) -> list[Finding]:
    """Prove the claimed API target is present in an actual sequence path."""
    controls = _control_method_contracts(state)
    diagrams = _sequence_diagrams_for_api(state)
    found: list[Finding] = []
    for endpoint in model.get("Endpoints", []) or []:
        if not isinstance(endpoint, dict):
            continue
        binding = _binding(endpoint)
        if binding is None:
            continue
        control = str(binding.get("control") or "").strip()
        method = str(binding.get("method") or "").strip()
        contract = controls.get(control, {}).get(method)
        if contract is None:
            continue
        expected_signature = str(contract["signature"])
        endpoint_use_cases = {
            str(item).strip() for item in endpoint.get("use_case_ids", []) or [] if str(item).strip()
        }
        matches = False
        for diagram in diagrams:
            participant_classes = {
                _participant_id(participant): str(
                    participant.get("source_class") or participant.get("name") or ""
                ).strip()
                for participant in diagram.get("Participants", []) or []
                if isinstance(participant, dict)
            }
            for message in diagram.get("Messages", []) or []:
                if not isinstance(message, dict) or str(message.get("type", "sync")).lower() not in {"sync", "async", "self"}:
                    continue
                if participant_classes.get(str(message.get("target") or "").strip()) != control:
                    continue
                if method_call_signature(str(message.get("label") or "")) != expected_signature:
                    continue
                message_use_cases = {
                    str(item).strip() for item in message.get("use_case_ids", []) or [] if str(item).strip()
                }
                if endpoint_use_cases and not (endpoint_use_cases & message_use_cases):
                    continue
                matches = True
                break
            if matches:
                break
        if not matches:
            found.append(Finding(
                "api.control-call-in-sequence",
                f"{control}.{expected_signature} 호출이 이 endpoint의 시퀀스 흐름에 없음",
                _api_location(endpoint),
            ))
    return found


def sequence_initial_entry(model: dict, state: dict) -> list[Finding]:
    """첫 번째 메시지는 반드시 Actor → Boundary 호출이어야 함.

    사용자가 시스템에 접근할 때 Control이나 Entity로 직접 진입하는 잘못된 상호작용을
    방지한다. 첫 번째 비-return 메시지를 기준으로 판정한다.
    """
    rule_id = "sequence.initial-message-entry"
    kinds = {
        _participant_id(p): str(p.get("kind", "")).strip().lower()
        for p in model.get("Participants", [])
    }

    first_msg = None
    for msg in model.get("Messages", []):
        if str(msg.get("type", "sync")).lower() not in {"return", "activate", "deactivate"}:
            first_msg = msg
            break

    if not first_msg:
        return []

    source = str(first_msg.get("source", "")).strip()
    target = str(first_msg.get("target", "")).strip()
    source_kind = kinds.get(source, "")
    target_kind = kinds.get(target, "")

    if source_kind != "actor" or target_kind != "boundary":
        location = f"{source} -> {target}"
        return [
            Finding(
                rule_id,
                f"시퀀스 다이어그램의 최초 호출은 Actor → Boundary이어야 함 (현재: {source_kind or source} → {target_kind or target})",
                location,
            )
        ]
    return []


def _uses_explicit_call_links(model: dict) -> bool:
    return any(
        "call_id" in message or "reply_to" in message
        for message in model.get("Messages", [])
        if isinstance(message, dict)
    )


def _explicit_calls(model: dict) -> dict[str, tuple[int, dict]]:
    return {
        str(message.get("call_id") or "").strip(): (index, message)
        for index, message in enumerate(model.get("Messages", []))
        if str(message.get("type", "sync")).lower() in {"sync", "async", "self"}
        and str(message.get("call_id") or "").strip()
    }


def sequence_call_return_links(model: dict, state: dict) -> list[Finding]:
    """새 모델의 호출 ID와 반환 reply_to가 정확히 한 호출을 연결하는가."""
    if not _uses_explicit_call_links(model):
        return []
    rule_id = "sequence.call-return-links"
    found: list[Finding] = []
    calls: dict[str, tuple[int, dict]] = {}
    reply_counts: dict[str, int] = {}
    for index, message in enumerate(model.get("Messages", [])):
        message_type = str(message.get("type", "sync")).strip().lower()
        source = str(message.get("source") or "").strip()
        target = str(message.get("target") or "").strip()
        location = f"{source} -> {target} : {message.get('label', '')}"
        if message_type in {"sync", "async", "self"}:
            call_id = str(message.get("call_id") or "").strip()
            if str(message.get("reply_to") or "").strip():
                found.append(Finding(rule_id, "호출 메시지에는 reply_to를 지정할 수 없음", location))
            if not call_id:
                found.append(Finding(rule_id, "호출 메시지의 call_id가 비어 있음", location))
            elif call_id in calls:
                found.append(Finding(rule_id, f"call_id '{call_id}'가 중복됨", location))
            else:
                calls[call_id] = (index, message)
        elif message_type == "return":
            reply_to = str(message.get("reply_to") or "").strip()
            if str(message.get("call_id") or "").strip():
                found.append(Finding(rule_id, "return 메시지에는 call_id를 지정할 수 없음", location))
            if not reply_to:
                found.append(Finding(rule_id, "return 메시지의 reply_to가 비어 있음", location))
                continue
            linked = calls.get(reply_to)
            if linked is None:
                found.append(Finding(rule_id, f"선행 호출 ID '{reply_to}'가 존재하지 않음", location))
                continue
            _, call = linked
            reply_counts[reply_to] = reply_counts.get(reply_to, 0) + 1
            if reply_counts[reply_to] > 1:
                found.append(Finding(rule_id, f"호출 '{reply_to}'에 반환이 둘 이상 연결됨", location))
            if (
                str(call.get("source") or "").strip() != target
                or str(call.get("target") or "").strip() != source
            ):
                found.append(Finding(rule_id, f"호출 '{reply_to}'과 반환 방향이 일치하지 않음", location))
    return found


def sequence_boundary_operation_direction(model: dict, state: dict) -> list[Finding]:
    """Boundary 호출이 입력/출력 방향의 소유권을 지키는지 검사한다."""
    rule_id = "sequence.boundary-operation-direction"
    kinds = {
        _participant_id(item): str(item.get("kind", "")).strip().lower()
        for item in model.get("Participants", [])
    }
    output_prefixes = (
        "display", "show", "render", "prompt", "notify", "send", "return", "respond",
    )
    found: list[Finding] = []
    for message in model.get("Messages", []):
        if str(message.get("type", "sync")).lower() not in {"sync", "async"}:
            continue
        source = str(message.get("source") or "").strip()
        target = str(message.get("target") or "").strip()
        if kinds.get(source) != "actor" or kinds.get(target) != "boundary":
            if kinds.get(source) != "control" or kinds.get(target) != "boundary":
                continue
            signature = method_call_signature(str(message.get("label") or ""))
            method_name = signature.partition("(")[0].lower()
            if method_name.startswith(output_prefixes):
                continue
            found.append(
                Finding(
                    rule_id,
                    f"Control이 Boundary 입력 오퍼레이션 '{signature}'을 출력처럼 호출함",
                    f"{source} -> {target} : {message.get('label', '')}",
                )
            )
            continue
        signature = method_call_signature(str(message.get("label") or ""))
        method_name = signature.partition("(")[0].lower()
        if method_name.startswith(output_prefixes):
            found.append(
                Finding(
                    rule_id,
                    f"Actor가 Boundary 출력 오퍼레이션 '{signature}'을 입력 이벤트처럼 호출함",
                    f"{source} -> {target} : {message.get('label', '')}",
                )
            )
    return found


def sequence_unmatched_returns(model: dict, state: dict) -> list[Finding]:
    """소비할 선행 호출 없이 독립적으로 존재하는 return 메시지 감지.

    하나의 호출은 최대 하나의 return만 소비할 수 있다. 호출을 반환 시점에 제거하여
    선행 호출 없는 반환뿐 아니라 한 호출에 여러 반환이 붙는 LLM 환각도 차단한다.
    """
    if _uses_explicit_call_links(model):
        return []
    rule_id = "sequence.unmatched-return-message"
    found: list[Finding] = []
    pending_calls: list[tuple[str, str]] = []

    for msg in model.get("Messages", []):
        m_type = str(msg.get("type", "sync")).lower()
        source = str(msg.get("source", "")).strip()
        target = str(msg.get("target", "")).strip()

        if m_type == "return":
            call_index = next(
                (
                    index
                    for index in range(len(pending_calls) - 1, -1, -1)
                    if pending_calls[index] == (target, source)
                ),
                None,
            )
            if call_index is None:
                location = f"{source} --> {target}"
                found.append(
                    Finding(
                        rule_id,
                        f"선행 호출 없이 고립된 return 메시지 ({source} → {target})",
                        location,
                    )
                )
            else:
                pending_calls.pop(call_index)
        elif m_type in {"sync", "async", "self"}:
            pending_calls.append((source, target))

    return found


def sequence_async_returns(model: dict, state: dict) -> list[Finding]:
    """fire-and-forget 비동기 호출에 연결된 반환 메시지를 검출한다."""
    rule_id = "sequence.async-call-has-no-return"
    if _uses_explicit_call_links(model):
        calls = _explicit_calls(model)
        found: list[Finding] = []
        for message in model.get("Messages", []):
            if str(message.get("type", "")).lower() != "return":
                continue
            linked = calls.get(str(message.get("reply_to") or "").strip())
            if linked is None:
                continue
            call = linked[1]
            if str(call.get("type", "sync")).lower() == "async":
                found.append(
                    Finding(
                        rule_id,
                        f"비동기 호출 '{call.get('label', '')}'은 반환 메시지를 가질 수 없음",
                        f"{message.get('source', '')} --> {message.get('target', '')}",
                    )
                )
        return found
    pending_calls: list[dict] = []
    found: list[Finding] = []
    for message in model.get("Messages", []):
        message_type = str(message.get("type", "sync")).strip().lower()
        source = str(message.get("source") or "").strip()
        target = str(message.get("target") or "").strip()
        if message_type in {"sync", "async", "self"}:
            pending_calls.append(message)
            continue
        if message_type != "return":
            continue
        call_index = next(
            (
                index
                for index in range(len(pending_calls) - 1, -1, -1)
                if str(pending_calls[index].get("source") or "").strip() == target
                and str(pending_calls[index].get("target") or "").strip() == source
            ),
            None,
        )
        if call_index is None:
            continue
        call = pending_calls.pop(call_index)
        if str(call.get("type", "sync")).strip().lower() == "async":
            found.append(
                Finding(
                    rule_id,
                    f"비동기 호출 '{call.get('label', '')}'은 반환 메시지를 가질 수 없음",
                    f"{source} --> {target}",
                )
            )
    return found


def sequence_return_values_match_methods(model: dict, state: dict) -> list[Finding]:
    """반환 라벨이 대응 호출 메서드의 클래스 선언 반환 타입과 같은가."""
    rule_id = "sequence.return-label-matches-method-return"
    participant_classes = {
        _participant_id(participant): str(
            participant.get("source_class") or participant.get("name") or ""
        ).strip()
        for participant in model.get("Participants", [])
        if str(participant.get("kind", "")).strip().lower() != "actor"
    }
    signatures: dict[str, dict[str, set[str]]] = {}
    for class_item in (state.get("extracted_bce_classes") or {}).get("Classes", []):
        class_name = str(class_item.get("className") or "").strip()
        if not class_name:
            continue
        by_method: dict[str, set[str]] = {}
        for raw_method in class_item.get("methods") or []:
            signature = method_call_signature(str(raw_method))
            if not signature:
                continue
            return_type = method_return_type(str(raw_method))
            if return_type:
                by_method.setdefault(signature, set()).add(return_type)
            else:
                by_method.setdefault(signature, set())
        signatures[class_name] = by_method

    explicit = _uses_explicit_call_links(model)
    calls_by_id = _explicit_calls(model) if explicit else {}
    pending_calls: list[dict] = []
    found: list[Finding] = []
    for message in model.get("Messages", []):
        message_type = str(message.get("type", "sync")).strip().lower()
        source = str(message.get("source") or "").strip()
        target = str(message.get("target") or "").strip()
        if not explicit and message_type in {"sync", "self"}:
            pending_calls.append(message)
            continue
        if message_type != "return":
            continue

        label = str(message.get("label") or "").strip()
        location = f"{source} --> {target} : {label or '<empty>'}"
        if not label:
            found.append(Finding(rule_id, "return 메시지의 결과 라벨이 비어 있음", location))
            continue

        if explicit:
            linked = calls_by_id.get(str(message.get("reply_to") or "").strip())
            if linked is None:
                continue
            call = linked[1]
        else:
            call_index = next(
                (
                    index
                    for index in range(len(pending_calls) - 1, -1, -1)
                    if str(pending_calls[index].get("source") or "").strip() == target
                    and str(pending_calls[index].get("target") or "").strip() == source
                ),
                None,
            )
            if call_index is None:
                continue  # 고립 반환은 sequence_unmatched_returns가 맡는다
            call = pending_calls.pop(call_index)
        class_name = participant_classes.get(str(call.get("target") or "").strip())
        called_method = method_call_signature(str(call.get("label") or ""))
        if not class_name or not called_method:
            continue
        class_signatures = signatures.get(class_name)
        if class_signatures is None or called_method not in class_signatures:
            continue  # 참가자/메서드 소유권 검출기가 맡는다

        declared = class_signatures[called_method]
        non_void = {
            return_type
            for return_type in declared
            if normalize_return_type(return_type) != "void"
        }
        if not non_void:
            found.append(
                Finding(
                    rule_id,
                    f"'{class_name}.{call.get('label', '')}'에 반환 타입이 선언되지 않았거나 void임",
                    location,
                )
            )
            continue
        if normalize_return_type(label) not in {
            normalize_return_type(return_type) for return_type in non_void
        }:
            found.append(
                Finding(
                    rule_id,
                    f"return 라벨 '{label}'이 '{class_name}.{call.get('label', '')}'의 "
                    f"반환 타입 {sorted(non_void)}와 일치하지 않음",
                    location,
                )
            )
    return found


def sequence_nonvoid_calls_have_returns(model: dict, state: dict) -> list[Finding]:
    """반환값이 선언된 동기 호출마다 정확히 하나의 반환 메시지가 있는가."""
    rule_id = "sequence.nonvoid-call-requires-return"
    participant_classes = {
        _participant_id(participant): str(
            participant.get("source_class") or participant.get("name") or ""
        ).strip()
        for participant in model.get("Participants", [])
        if str(participant.get("kind", "")).strip().lower() != "actor"
    }
    contracts: dict[str, dict[str, str]] = {}
    for class_item in (state.get("extracted_bce_classes") or {}).get("Classes", []):
        class_name = str(class_item.get("className") or "").strip()
        if not class_name:
            continue
        contracts[class_name] = {
            signature: return_type
            for raw_method in class_item.get("methods") or []
            if (signature := method_call_signature(str(raw_method)))
            and (return_type := method_return_type(str(raw_method)))
        }

    explicit = _uses_explicit_call_links(model)
    if explicit:
        returned_ids = {
            str(message.get("reply_to") or "").strip()
            for message in model.get("Messages", [])
            if str(message.get("type", "")).lower() == "return"
        }
        pending_calls = [
            message
            for message in model.get("Messages", [])
            if str(message.get("type", "sync")).lower() in {"sync", "self"}
            and str(message.get("call_id") or "").strip() not in returned_ids
        ]
    else:
        pending_calls = []
        for message in model.get("Messages", []):
            message_type = str(message.get("type", "sync")).strip().lower()
            source = str(message.get("source") or "").strip()
            target = str(message.get("target") or "").strip()
            if message_type in {"sync", "self"}:
                pending_calls.append(message)
                continue
            if message_type != "return":
                continue
            call_index = next(
                (
                    index
                    for index in range(len(pending_calls) - 1, -1, -1)
                    if str(pending_calls[index].get("source") or "").strip() == target
                    and str(pending_calls[index].get("target") or "").strip() == source
                ),
                None,
            )
            if call_index is not None:
                pending_calls.pop(call_index)

    found: list[Finding] = []
    for call in pending_calls:
        target = str(call.get("target") or "").strip()
        class_name = participant_classes.get(target)
        signature = method_call_signature(str(call.get("label") or ""))
        return_type = contracts.get(class_name or "", {}).get(signature)
        if not return_type or normalize_return_type(return_type) == "void":
            continue  # 잘못된 클래스/메서드는 소유권 검출기가 맡는다.
        source = str(call.get("source") or "").strip()
        found.append(
            Finding(
                rule_id,
                f"반환 타입 '{return_type}'을 선언한 호출 '{class_name}.{signature}'에 return 메시지가 없음",
                f"{source} -> {target} : {call.get('label', '')}",
            )
        )
    return found


def _method_parameters(signature: str) -> dict[str, str]:
    inside = signature.partition("(")[2].rpartition(")")[0]
    if not inside:
        return {}
    values: list[str] = []
    start = 0
    depth = 0
    for index, character in enumerate(inside):
        if character == "<":
            depth += 1
        elif character == ">":
            depth = max(0, depth - 1)
        elif character == "," and depth == 0:
            values.append(inside[start:index])
            start = index + 1
    values.append(inside[start:])
    result: dict[str, str] = {}
    for value in values:
        name, separator, type_name = value.partition(":")
        if separator and name and type_name:
            result[name] = type_name
    return result


def sequence_argument_data_flow(model: dict, state: dict) -> list[Finding]:
    """새 호출 모델의 매개변수 타입과 값 출처가 선행 데이터 흐름에 근거하는가."""
    if not _uses_explicit_call_links(model):
        return []
    rule_id = "sequence.argument-data-flow"
    participant_classes = {
        _participant_id(participant): str(
            participant.get("source_class") or participant.get("name") or ""
        ).strip()
        for participant in model.get("Participants", [])
        if str(participant.get("kind", "")).strip().lower() != "actor"
    }
    contracts: dict[str, dict[str, tuple[dict[str, str], str | None]]] = {}
    for class_item in (state.get("extracted_bce_classes") or {}).get("Classes", []):
        class_name = str(class_item.get("className") or "").strip()
        if not class_name:
            continue
        contracts[class_name] = {
            signature: (_method_parameters(signature), method_return_type(str(raw_method)))
            for raw_method in class_item.get("methods") or []
            if (signature := method_call_signature(str(raw_method)))
        }

    calls = _explicit_calls(model)
    known_steps = _known_flow_step_ids(state)
    found: list[Finding] = []
    for call_id, (call_index, call) in calls.items():
        target = str(call.get("target") or "").strip()
        class_name = participant_classes.get(target, "")
        signature = method_call_signature(str(call.get("label") or ""))
        contract = contracts.get(class_name, {}).get(signature)
        if contract is None:
            continue
        expected, _ = contract
        raw_bindings = [
            binding for binding in call.get("arguments") or [] if isinstance(binding, dict)
        ]
        bindings = {
            str(binding.get("parameter") or "").strip(): binding for binding in raw_bindings
        }
        location = f"{call.get('source', '')} -> {target} : {call.get('label', '')}"
        binding_names = [str(binding.get("parameter") or "").strip() for binding in raw_bindings]
        duplicates = sorted({name for name in binding_names if name and binding_names.count(name) > 1})
        if duplicates:
            found.append(
                Finding(
                    rule_id,
                    f"호출 '{call_id}'의 인자 {duplicates}가 둘 이상 바인딩됨",
                    location,
                )
            )
        if set(bindings) != set(expected):
            found.append(
                Finding(
                    rule_id,
                    f"호출 '{call_id}'의 인자 {sorted(bindings)}가 메서드 매개변수 {sorted(expected)}와 일치하지 않음",
                    location,
                )
            )
        for parameter, binding in bindings.items():
            if parameter not in expected:
                continue
            bound_type = str(binding.get("type") or "").strip()
            if normalize_return_type(bound_type) != normalize_return_type(expected[parameter]):
                found.append(
                    Finding(
                        rule_id,
                        f"인자 '{parameter}' 타입 '{bound_type}'이 선언 타입 '{expected[parameter]}'과 일치하지 않음",
                        location,
                    )
                )
            source_kind = str(binding.get("source_kind") or "").strip()
            source_ref = str(binding.get("source_ref") or "").strip()
            if source_kind == "input" and known_steps and source_ref not in known_steps:
                found.append(Finding(rule_id, f"입력 원천 단계 '{source_ref}'가 명세에 없음", location))
            if source_kind != "call_result":
                continue
            source_call = calls.get(source_ref)
            if source_call is None or source_call[0] >= call_index:
                found.append(Finding(rule_id, f"선행 호출 결과 '{source_ref}'가 존재하지 않음", location))
                continue
            result_call = source_call[1]
            result_owner = str(result_call.get("source") or "").strip()
            consumer = str(call.get("source") or "").strip()
            if result_owner != consumer:
                found.append(
                    Finding(
                        rule_id,
                        f"호출 결과 '{source_ref}'는 '{result_owner}'에게 반환됐으므로 "
                        f"명시적 전달 없이 '{consumer}'가 사용할 수 없음",
                        location,
                    )
                )
            result_class = participant_classes.get(str(result_call.get("target") or "").strip(), "")
            result_signature = method_call_signature(str(result_call.get("label") or ""))
            result_contract = contracts.get(result_class, {}).get(result_signature)
            result_type = result_contract[1] if result_contract else None
            if not result_type or normalize_return_type(result_type) != normalize_return_type(bound_type):
                found.append(
                    Finding(
                        rule_id,
                        f"호출 결과 '{source_ref}' 타입 '{result_type or '<none>'}'이 인자 '{parameter}' 타입 '{bound_type}'과 일치하지 않음",
                        location,
                    )
                )
    return found


def _flow_step_records(state: dict) -> list[tuple[str, str]]:
    """검증 가능한 흐름 단계 ID와 원문을 명세 순서대로 펼친다."""
    records: list[tuple[str, str]] = []
    spec = state.get("usecase_spec") or {}
    if not isinstance(spec, dict):
        return records
    for use_case in spec.get("use_case_specs") or []:
        if not isinstance(use_case, dict):
            continue
        use_case_id = str(use_case.get("use_case_id") or "").strip()
        if not use_case_id:
            continue
        for step in use_case.get("main_scenario") or []:
            if isinstance(step, dict) and step.get("step_number") is not None:
                records.append(
                    (f"{use_case_id}:main:{step.get('step_number')}", _flow_step_sentence(step))
                )
        for extension in use_case.get("extensions") or []:
            if not isinstance(extension, dict):
                continue
            label = str(extension.get("label") or "").strip()
            for step in extension.get("handling_steps") or []:
                if isinstance(step, dict) and label and step.get("sub_step"):
                    records.append(
                        (
                            f"{use_case_id}:extension:{label}:{step.get('sub_step')}",
                            _flow_step_sentence(step),
                        )
                    )
    return records


def sequence_actor_step_involvement(model: dict, state: dict) -> list[Finding]:
    """액터가 수행한다고 적힌 단계를 무관한 시스템 호출로 덮지 못하게 한다."""
    rule_id = "sequence.actor-step-involvement"
    actors = {
        _participant_id(participant): str(participant.get("name") or "").strip().lower()
        for participant in model.get("Participants", [])
        if str(participant.get("kind") or "").strip().lower() == "actor"
    }
    if not actors:
        return []
    actor_subjects = {name for name in actors.values() if name}
    actor_subjects.update({"user", "the user"})
    participant_classes = {
        _participant_id(participant): str(
            participant.get("source_class") or participant.get("name") or ""
        ).strip()
        for participant in model.get("Participants", [])
        if str(participant.get("kind") or "").strip().lower() != "actor"
    }
    class_method_counts = {
        str(item.get("className") or "").strip(): len(
            [method for method in item.get("methods") or [] if method_call_signature(str(method))]
        )
        for item in (state.get("extracted_bce_classes") or {}).get("Classes", [])
        if str(item.get("className") or "").strip()
    }
    unresolved = _unresolved_flow_step_ids(state)
    found: list[Finding] = []
    claimed_main_calls: dict[tuple[str, str], tuple[str, str, set[int]]] = {}
    for step_id, sentence in _flow_step_records(state):
        if step_id in unresolved or not sentence:
            continue
        lowered = sentence.lower().lstrip(" '-\"")
        if not any(
            lowered == subject
            or lowered.startswith(subject + " ")
            or lowered.startswith(subject + "'")
            for subject in actor_subjects
        ):
            continue
        indexed_messages = [
            (index, message)
            for index, message in enumerate(model.get("Messages", []))
            if step_id in {str(value).strip() for value in message.get("step_ids") or []}
            and str(message.get("type", "sync")).lower() in {"sync", "async", "self"}
        ]
        if not indexed_messages:
            continue  # coverage detector owns an entirely absent step.
        actor_messages = [
            (index, message)
            for index, message in indexed_messages
            if str(message.get("source") or "").strip() in actors
        ]
        if not actor_messages:
            found.append(
                Finding(
                    rule_id,
                    f"액터가 수행하는 단계 '{sentence}'에 액터가 시작하는 호출이 없음",
                    step_id,
                )
            )
            continue
        if ":main:" not in step_id:
            continue
        call_keys: dict[tuple[str, str], set[int]] = {}
        for index, message in actor_messages:
            key = (
                str(message.get("target") or "").strip(),
                method_call_signature(str(message.get("label") or "")),
            )
            if key[1]:
                call_keys.setdefault(key, set()).add(index)
        # One interaction may intentionally trace to multiple adjacent specification
        # steps. Only separate messages that reuse the same operation are suspicious.
        reused_by_distinct_messages = call_keys and all(
            key in claimed_main_calls
            and claimed_main_calls[key][2].isdisjoint(indexes)
            for key, indexes in call_keys.items()
        )
        # Reusing the only operation exposed by a Boundary is not evidence of a
        # fabricated trace. Health probes and metric collection are common examples.
        has_alternative_operation = any(
            class_method_counts.get(participant_classes.get(target, ""), 0) > 1
            for target, _ in call_keys
        )
        if reused_by_distinct_messages and (
            not class_method_counts or has_alternative_operation
        ):
            prior_steps = sorted({claimed_main_calls[key][0] for key in call_keys})
            found.append(
                Finding(
                    rule_id,
                    f"서로 다른 메인 액터 행동 '{sentence}'이 이미 단계 {prior_steps}에서 "
                    "사용한 동일 Boundary 호출로 커버됨",
                    step_id,
                )
            )
        for key, indexes in call_keys.items():
            claimed_main_calls.setdefault(key, (step_id, sentence, indexes))
    return found


def sequence_causal_call_chain(model: dict, state: dict) -> list[Finding]:
    """비-액터 호출 주체가 앞선 호출을 통해 먼저 도달 가능한 상태인가."""
    rule_id = "sequence.causal-call-chain"
    kinds = {
        _participant_id(participant): str(participant.get("kind", "")).strip().lower()
        for participant in model.get("Participants", [])
    }
    reached = {alias for alias, kind in kinds.items() if kind == "actor"}
    if not reached:
        return []  # 액터가 없는 부분 모델은 인과 시작점을 판정할 수 없다.

    found: list[Finding] = []
    reported: set[str] = set()
    for message in model.get("Messages", []):
        if str(message.get("type", "sync")).strip().lower() not in {"sync", "async", "self"}:
            continue
        source = str(message.get("source") or "").strip()
        target = str(message.get("target") or "").strip()
        if source not in reached:
            if source not in reported:
                found.append(
                    Finding(
                        rule_id,
                        f"'{source}'가 선행 호출로 활성화되기 전에 호출을 시작함",
                        f"{source} -> {target} : {message.get('label', '')}",
                    )
                )
                reported.add(source)
            continue
        reached.add(target)
    return found


def sequence_usecase_coverage(model: dict, state: dict) -> list[Finding]:
    """가능하면 모든 주·확장 단계를, 옛 입력이면 유스케이스 단위 커버리지를 검사한다."""
    rule_id = "sequence.usecase-step-coverage"
    diagram_use_case_id = str(model.get("use_case_id") or "").strip()
    all_flow_steps = _known_flow_step_ids(state)
    flow_steps = all_flow_steps - _unresolved_flow_step_ids(state)
    if diagram_use_case_id:
        all_flow_steps = {
            step_id
            for step_id in all_flow_steps
            if step_id.startswith(f"{diagram_use_case_id}:")
        }
        flow_steps = {
            step_id
            for step_id in flow_steps
            if step_id.startswith(f"{diagram_use_case_id}:")
        }
    if flow_steps:
        covered_steps = {
            str(step_id).strip()
            for message in model.get("Messages", [])
            if str(message.get("type", "sync")).lower() not in {"activate", "deactivate"}
            for step_id in message.get("step_ids", [])
            if step_id
        }
        # A step that is explicitly retained as unresolved is not silently
        # missing.  It remains a review finding below, but must not also make
        # the entire UC look as though no diagram was generated.
        covered_steps.update(
            str(item.get("step_id") or "").strip()
            for item in model.get("UnresolvedSteps", []) or []
            if isinstance(item, dict) and item.get("step_id")
        )
        return [
            Finding(rule_id, f"시퀀스 다이어그램에 반영되지 않은 흐름 단계 id '{step_id}'", step_id)
            for step_id in sorted(flow_steps - covered_steps)
        ]
    if all_flow_steps:
        return []  # 이 다이어그램의 알려진 단계가 모두 unresolved인 경우다.

    use_cases = _known_use_case_ids(state)
    if diagram_use_case_id:
        use_cases = {diagram_use_case_id}
    if not use_cases:
        return []

    covered: set[str] = set()
    for msg in model.get("Messages", []):
        for uc_id in msg.get("use_case_ids", []):
            if uc_id:
                covered.add(str(uc_id).strip())

    uncovered = use_cases - covered
    if not uncovered:
        return []

    found: list[Finding] = []
    for uc_id in sorted(uncovered):
        found.append(
            Finding(
                rule_id,
                f"시퀀스 다이어그램에 반영되지 않은 유스케이스 id '{uc_id}'",
                uc_id,
            )
        )
    return found


def sequence_step_operation_distinctness(model: dict, state: dict) -> list[Finding]:
    """Reject one Boundary input being used for distinct actor actions.

    ``step_ids`` are traceability references, not proof that a call explains the
    step.  Reusing the same receiver operation for the actor request, a seat
    check, persistence and a response lets a minimal diagram pass structural
    coverage while saying almost nothing about the workflow. System-internal
    steps are different: one Control operation may validly validate, persist,
    and return a result as part of one command. Their method name alone cannot
    prove that they are separate user-visible operations. Restrict this rule to
    Actor -> Boundary input calls; the actor-step detector supplies the same
    semantic guard and prevents a repeated generic entry operation from hiding
    distinct user requests.
    """
    rule_id = "sequence.step-operation-distinctness"
    use_case_id = str(model.get("use_case_id") or "").strip()
    kinds = {
        _participant_id(participant): str(participant.get("kind") or "").strip().lower()
        for participant in model.get("Participants", []) or []
        if isinstance(participant, dict)
    }
    calls: dict[str, set[str]] = {}
    for message in model.get("Messages") or []:
        if not isinstance(message, dict):
            continue
        if str(message.get("type", "sync")).strip().lower() not in {"sync", "async", "self"}:
            continue
        signature = method_call_signature(str(message.get("label") or ""))
        if not signature:
            continue
        source = str(message.get("source") or "").strip()
        target = str(message.get("target") or "").strip()
        if kinds.get(source) != "actor" or kinds.get(target) != "boundary":
            continue
        main_steps = {
            str(step_id).strip()
            for step_id in message.get("step_ids") or []
            if (
                str(step_id).startswith(f"{use_case_id}:main:")
                if use_case_id
                else ":main:" in str(step_id)
            )
        }
        if main_steps:
            calls.setdefault(signature, set()).update(main_steps)
    return [
        Finding(
            rule_id,
            f"서로 다른 사용자 입력 단계 {sorted(step_ids)}가 동일한 Boundary 호출 '{signature}'으로만 표현됨",
            signature,
        )
        for signature, step_ids in sorted(calls.items())
        if len(step_ids) > 1
    ]


def sequence_unresolved_steps(model: dict, state: dict) -> list[Finding]:
    """Keep unresolved requirement or method-mapping steps visible for review."""
    rule_id = "sequence.unresolved-usecase-step"
    diagram_use_case_id = str(model.get("use_case_id") or "").strip()
    unresolved = _unresolved_flow_step_ids(state)
    if diagram_use_case_id:
        unresolved = {
            step_id for step_id in unresolved if step_id.startswith(f"{diagram_use_case_id}:")
        }
    found = [
        Finding(
            rule_id,
            f"행동이 결정되지 않은 요구사항 단계 '{step_id}'는 시퀀스 생성 전에 보완해야 함",
            step_id,
        )
        for step_id in sorted(unresolved)
    ]
    known = {finding.location for finding in found}
    for item in model.get("UnresolvedSteps", []) or []:
        if not isinstance(item, dict):
            continue
        step_id = str(item.get("step_id") or "").strip()
        if not step_id or step_id in known:
            continue
        reason = str(item.get("reason") or "grounded method selection failed").strip()
        found.append(
            Finding(
                rule_id,
                f"흐름 단계 '{step_id}'의 클래스 메서드를 확정하지 못함: {reason}",
                step_id,
            )
        )
    return found


def _main_step_number(step_id: str, use_case_id: str) -> int | None:
    match = re.fullmatch(rf"{re.escape(use_case_id)}:main:(\d+)", step_id)
    return int(match.group(1)) if match else None


def sequence_flow_order(model: dict, state: dict) -> list[Finding]:
    """주 흐름 순서와 확장 흐름의 분기 위치가 명세와 일치하는가."""
    rule_id = "sequence.flow-order"
    use_case_id = str(model.get("use_case_id") or "").strip()
    if not use_case_id:
        return []
    messages = model.get("Messages", [])
    found: list[Finding] = []
    last_main = -1
    main_positions: dict[int, list[int]] = {}
    for index, message in enumerate(messages):
        numbers = sorted([
            number
            for step_id in message.get("step_ids") or []
            if (number := _main_step_number(str(step_id), use_case_id)) is not None
        ])
        for number in numbers:
            main_positions.setdefault(number, []).append(index)
            if number < last_main:
                found.append(
                    Finding(
                        rule_id,
                        f"주 흐름 단계 {number}가 단계 {last_main} 뒤에 배치됨",
                        f"{message.get('source', '')} -> {message.get('target', '')} : {message.get('label', '')}",
                    )
                )
            last_main = max(last_main, number)

    use_case = next(
        (
            item
            for item in (state.get("usecase_spec") or {}).get("use_case_specs") or []
            if str(item.get("use_case_id") or "").strip() == use_case_id
        ),
        None,
    )
    if not isinstance(use_case, dict):
        return found
    extension_anchors: dict[str, int] = {}
    for extension in use_case.get("extensions") or []:
        if not isinstance(extension, dict):
            continue
        label = str(extension.get("label") or "").strip()
        branch_step = extension.get("branch_step")
        if branch_step is None:
            match = re.match(r"(\d+)", label)
            branch_step = int(match.group(1)) if match else None
        if label and isinstance(branch_step, int):
            extension_anchors[label] = branch_step

    for label, branch_step in extension_anchors.items():
        positions = [
            index
            for index, message in enumerate(messages)
            if any(
                str(step_id).startswith(f"{use_case_id}:extension:{label}:")
                for step_id in message.get("step_ids") or []
            )
        ]
        if not positions:
            continue
        if branch_step not in main_positions:
            found.append(
                Finding(
                    rule_id,
                    f"확장 흐름 '{label}'의 분기 기준인 주 흐름 단계 {branch_step}가 없어 배치 위치를 검증할 수 없음",
                    f"{use_case_id}:extension:{label}",
                )
            )
            continue
        branch_end = max(main_positions[branch_step])
        later_main = [
            index
            for number, indexes in main_positions.items()
            if number > branch_step
            for index in indexes
        ]
        next_main = min(later_main) if later_main else len(messages)
        if min(positions) <= branch_end or max(positions) >= next_main:
            found.append(
                Finding(
                    rule_id,
                    f"확장 흐름 '{label}'가 분기 단계 {branch_step} 직후에 배치되지 않음",
                    f"{use_case_id}:extension:{label}",
                )
            )
    return found


def sequence_fragment_condition_consistency(model: dict, state: dict) -> list[Finding]:
    """복합 조각(group)과 조건문(condition) 간의 무결성 검사.

    group(alt/loop/opt)이 선언되었으면 condition설명이 필수이며, 반대로 group이
    없으면 condition만 독립적으로 유령 기입되어선 안 된다.
    """
    rule_id = "sequence.fragment-condition-consistency"
    found: list[Finding] = []

    definitions: dict[str, tuple[str, str]] = {}
    branches: dict[str, set[str]] = {}
    branch_conditions: dict[str, dict[str, set[str]]] = {}
    fragment_step_ids: dict[str, set[str]] = {}
    explicit_fragment_ids: set[str] = set()
    branch_positions: dict[str, dict[str, list[int]]] = {}
    for message_index, msg in enumerate(model.get("Messages", [])):
        source = str(msg.get("source", "")).strip()
        target = str(msg.get("target", "")).strip()
        label = str(msg.get("label", "")).strip()
        location = f"{source} -> {target} : {label}"
        for fragment in _message_fragments(msg):
            fragment_id = str(fragment.get("id") or "").strip()
            group = str(fragment.get("type") or "").strip()
            branch = str(fragment.get("branch") or "").strip()
            condition = str(fragment.get("condition") or "").strip()
            if not fragment_id or group not in {"alt", "opt", "loop"} or not condition:
                found.append(Finding(rule_id, "fragment의 id/type/condition이 완전하지 않음", location))
                continue
            branches.setdefault(fragment_id, set()).add(branch)
            branch_conditions.setdefault(fragment_id, {}).setdefault(branch, set()).add(
                " ".join(condition.lower().split())
            )
            fragment_step_ids.setdefault(fragment_id, set()).update(
                str(step_id).strip()
                for step_id in msg.get("step_ids") or []
                if str(step_id).strip()
            )
            branch_positions.setdefault(fragment_id, {}).setdefault(branch, []).append(
                message_index
            )
            if isinstance(msg.get("fragments"), list):
                explicit_fragment_ids.add(fragment_id)
            if branch == "else" and group != "alt":
                found.append(Finding(rule_id, "else branch는 alt fragment에서만 허용됨", location))
            prior = definitions.get(fragment_id)
            if prior and prior[0] != group:
                found.append(Finding(rule_id, f"fragment id '{fragment_id}'가 서로 다른 type을 사용함", location))
            definitions.setdefault(fragment_id, (group, condition))

    for fragment_id, (group, _) in definitions.items():
        if (
            fragment_id in explicit_fragment_ids
            and group == "alt"
            and branches.get(fragment_id) != {"main", "else"}
        ):
            found.append(
                Finding(
                    rule_id,
                    f"alt fragment '{fragment_id}'는 main과 else branch를 모두 가져야 함; 단일 조건은 opt를 사용해야 함",
                    fragment_id,
                )
            )
            continue
        positions = branch_positions.get(fragment_id, {})
        conditions = branch_conditions.get(fragment_id, {})
        extension_refs = {
            (match.group(1), match.group(2))
            for step_id in fragment_step_ids.get(fragment_id, set())
            if (
                match := re.fullmatch(
                    r"([^:]+):extension:([^:]+):[^:]+",
                    step_id,
                )
            )
        }
        extension_conditions = {
            " ".join(
                str(extension.get("condition") or "").rstrip(":").lower().split()
            )
            for use_case in (state.get("usecase_spec") or {}).get("use_case_specs") or []
            if isinstance(use_case, dict)
            for extension in use_case.get("extensions") or []
            if isinstance(extension, dict)
            and (
                str(use_case.get("use_case_id") or "").strip(),
                str(extension.get("label") or "").strip(),
            )
            in extension_refs
        }
        all_conditions = {
            value for values in conditions.values() for value in values
        }
        if (
            group == "alt"
            and len(extension_refs) == 1
            and extension_conditions & all_conditions
            and not any(
                ":main:" in step_id
                for step_id in fragment_step_ids.get(fragment_id, set())
            )
        ):
            found.append(
                Finding(
                    rule_id,
                    f"extension trigger만 표현한 fragment '{fragment_id}'는 alt가 아니라 opt여야 함",
                    fragment_id,
                )
            )
        unstable_branches = [
            branch for branch, values in conditions.items() if len(values) > 1
        ]
        if unstable_branches:
            found.append(
                Finding(
                    rule_id,
                    f"fragment '{fragment_id}'의 branch 조건이 메시지마다 달라짐: {sorted(unstable_branches)}",
                    fragment_id,
                )
            )
        if (
            group == "alt"
            and conditions.get("main")
            and conditions.get("else")
            and conditions["main"] & conditions["else"]
        ):
            found.append(
                Finding(
                    rule_id,
                    f"alt fragment '{fragment_id}'의 main과 else 조건이 동일해 상호 배타적이지 않음",
                    fragment_id,
                )
            )
        if (
            group == "alt"
            and positions.get("main")
            and positions.get("else")
            and min(positions["else"]) < min(positions["main"])
        ):
            found.append(
                Finding(
                    rule_id,
                    f"alt fragment '{fragment_id}'의 else branch가 main branch보다 먼저 나타남",
                    fragment_id,
                )
            )

    return found


def sequence_database_access_discipline(model: dict, state: dict) -> list[Finding]:
    """데이터베이스(database) 직접 접근 주체 규약 검사.

    Database 계층으로의 직접 접근은 Control 또는 Entity 계층에서만 허용되며,
    Actor나 Boundary 계층에서 DB를 직접 호출하는 것은 아키텍처 위반이다.
    """
    rule_id = "sequence.database-access-discipline"
    kinds = {
        _participant_id(p): str(p.get("kind", "")).strip().lower()
        for p in model.get("Participants", [])
    }
    found: list[Finding] = []

    for msg in model.get("Messages", []):
        if str(msg.get("type", "sync")).lower() in {"return", "activate", "deactivate"}:
            continue
        source = str(msg.get("source", "")).strip()
        target = str(msg.get("target", "")).strip()
        source_kind = kinds.get(source, "")
        target_kind = kinds.get(target, "")

        if target_kind == "database" and source_kind in ("actor", "boundary"):
            location = f"{source} -> {target}"
            found.append(
                Finding(
                    rule_id,
                    f"'{source_kind}' 계층({source})에서 데이터베이스({target})를 직접 호출함 (Control/Entity를 거쳐야 함)",
                    location,
                )
            )

    return found


def sequence_self_call_method_validation(model: dict, state: dict) -> list[Finding]:
    """자기 자신 호출(Self-Call) 오퍼레이션 검증.

    source == target 인 셀프 메시지가 발생할 때, 해당 호출 오퍼레이션이 정당하게
    선언되어 있는지 및 라벨 기입 여부를 검사한다.
    """
    rule_id = "sequence.self-call-method-validation"
    found: list[Finding] = []

    for msg in model.get("Messages", []):
        if str(msg.get("type", "sync")).lower() not in {"sync", "async", "self"}:
            continue
        source = str(msg.get("source", "")).strip()
        target = str(msg.get("target", "")).strip()
        label = str(msg.get("label", "")).strip()

        if source and source == target:
            if not label:
                location = f"{source} -> {target}"
                found.append(
                    Finding(
                        rule_id,
                        f"자기 자신({source})을 호출하는 메시지의 라벨(오퍼레이션명)이 비어 있음",
                        location,
                    )
                )

    return found


def sequence_orphan_participant_detection(model: dict, state: dict) -> list[Finding]:
    """메시지가 단 하나도 없는 고립된 참가자(Orphan Participant) 감지.

    Participants 목록에는 선언되어 있으나 전체 Messages 중 단 한 번도 source 나 target으로
    참여하지 않는 불필요한 유령 참가자를 탐지한다.
    """
    # A review-only UC intentionally retains its actor/Boundary so the rendered
    # note has real design context.  Those declarations are not ghost
    # participants and must not turn the explanatory diagram into a hidden one.
    if any(
        isinstance(item, dict)
        for item in model.get("UnresolvedSteps", []) or []
    ):
        return []

    rule_id = "sequence.orphan-participant-detection"
    active_participants: set[str] = set()

    for msg in model.get("Messages", []):
        source = str(msg.get("source", "")).strip()
        target = str(msg.get("target", "")).strip()
        if source:
            active_participants.add(source)
        if target:
            active_participants.add(target)

    found: list[Finding] = []
    for participant in model.get("Participants", []):
        participant_id = _participant_id(participant)
        name = str(participant.get("name") or participant_id).strip()
        if participant_id and participant_id not in active_participants:
            found.append(
                Finding(
                    rule_id,
                    f"메시지상에서 한 번도 호출/응답하지 않는 고립된 참가자 '{name}'",
                    name,
                )
            )

    return found


def sequence_duplicate_consecutive_messages(model: dict, state: dict) -> list[Finding]:
    """무의미한 연속 중복 메시지 탐지.

    loop나 alt 같은 복합 조각 밖에서 동일한 source, target, label, type을 가진 메시지가
    연달아 기입된 경우 지적한다.
    """
    rule_id = "sequence.duplicate-consecutive-messages"
    found: list[Finding] = []
    messages = model.get("Messages", [])

    for i in range(1, len(messages)):
        prev = messages[i - 1]
        curr = messages[i]

        prev_key = (
            str(prev.get("source", "")).strip(),
            str(prev.get("target", "")).strip(),
            str(prev.get("label", "")).strip(),
            str(prev.get("type", "sync")).strip().lower(),
            repr(_message_fragments(prev)),
        )
        curr_key = (
            str(curr.get("source", "")).strip(),
            str(curr.get("target", "")).strip(),
            str(curr.get("label", "")).strip(),
            str(curr.get("type", "sync")).strip().lower(),
            repr(_message_fragments(curr)),
        )

        if prev_key == curr_key and prev_key[2]:  # label이 비어있지 않은 경우
            source, target, label = curr_key[0], curr_key[1], curr_key[2]
            location = f"{source} -> {target} : {label}"
            found.append(
                Finding(
                    rule_id,
                    f"동일한 메시지 '{label}'가 연달아 중복 기입되어 있음 ({source} → {target})",
                    location,
                )
            )

    return found


def sequence_message_naming_convention(model: dict, state: dict) -> list[Finding]:
    """오퍼레이션 라벨 표기법 규약 검사.

    메시지 라벨이 클래스 이름 형태(PascalCase, 예: OrderControl)로 잘못 기입된 경우를
    지적한다. 오퍼레이션 라벨은 camelCase (예: registerOrder()) 또는 동사구이어야 한다.
    """
    rule_id = "sequence.message-naming-convention"
    found: list[Finding] = []

    for msg in model.get("Messages", []):
        if str(msg.get("type", "sync")).lower() not in {"sync", "async", "self"}:
            continue
        label = str(msg.get("label", "")).strip()
        if not label:
            continue

        # 괄호나 매개변수 이전의 첫 단어 추출
        raw_name = re.sub(r'^[+\-#~]\s*', '', label)
        match = re.match(r'([A-Za-z_]\w*)', raw_name)
        if match:
            first_word = match.group(1)
            # 첫 문자가 대문자이고(PascalCase), 단어가 오퍼레이션이 아닌 클래스명으로 오인될 수 있는 형태 검사
            # 단, ALL_CAPS 상수는 무시
            if first_word[0].isupper() and not first_word.isupper():
                source = str(msg.get("source", "")).strip()
                target = str(msg.get("target", "")).strip()
                location = f"{source} -> {target} : {label}"
                found.append(
                    Finding(
                        rule_id,
                        f"메시지 라벨 '{label}'이 클래스 명칭 형태(PascalCase)로 시작함 (camelCase 또는 verbNoun() 권장)",
                        location,
                    )
                )

    return found


def sequence_participant_kind_validity(model: dict, state: dict) -> list[Finding]:
    """참가자 종류(Kind) 표준성 검사.

    kind 필드가 5가지 표준 BCE/시퀀스 종류(actor, boundary, control, entity, database)
    내에 속하는지 검사한다.
    """
    rule_id = "sequence.participant-kind-validity"
    valid_kinds = {"actor", "boundary", "control", "entity", "database"}
    found: list[Finding] = []

    for participant in model.get("Participants", []):
        name = str(participant.get("name", "")).strip()
        kind = str(participant.get("kind", "")).strip().lower()

        if kind and kind not in valid_kinds:
            found.append(
                Finding(
                    rule_id,
                    f"참가자 '{name}'의 kind '{kind}'가 표준 종류(actor, boundary, control, entity, database)에 속하지 않음",
                    name,
                )
            )

    return found


def sequence_message_type_validity(model: dict, state: dict) -> list[Finding]:
    """메시지 호출 타입(Type) 표준성 검사.

    type 필드가 3가지 표준 호출 화살표 타입(sync, async, return) 내에 속하는지 검사한다.
    """
    rule_id = "sequence.message-type-validity"
    valid_types = {"sync", "async", "return", "self", "activate", "deactivate"}
    found: list[Finding] = []

    for msg in model.get("Messages", []):
        m_type = str(msg.get("type", "")).strip().lower()
        source = str(msg.get("source", "")).strip()
        target = str(msg.get("target", "")).strip()
        label = str(msg.get("label", "")).strip()
        location = f"{source} -> {target} : {label}"

        if m_type and m_type not in valid_types:
            found.append(
                Finding(
                    rule_id,
                    f"메시지 호출 타입 '{m_type}'이 표준 타입(sync, async, return)에 속하지 않음",
                    location,
                )
            )

    return found


def sequence_no_lifecycle_events(model: dict, state: dict) -> list[Finding]:
    """Keep every generated sequence within the fixed no-activation template."""
    rule_id = "sequence.no-lifecycle-events"
    return [
        Finding(
            rule_id,
            "공통 시퀀스 템플릿은 activation/deactivation 네모 박스를 사용하지 않음",
            f"{message.get('source', '')} -> {message.get('target', '')}",
        )
        for message in model.get("Messages", []) or []
        if isinstance(message, dict)
        and str(message.get("type", "")).strip().lower()
        in {"activate", "deactivate"}
    ]


def sequence_class_diagram_version(model: dict, state: dict) -> list[Finding]:
    """Reject a collection validated against a different class-method contract."""
    rule_id = "sequence.class-diagram-version"
    expected = str(model.get("class_diagram_hash") or "").strip()
    if not expected:
        return []  # legacy single-diagram models predate the version contract.
    actual = hashlib.sha256(
        str(state.get("class_diagram_puml") or "").encode("utf-8")
    ).hexdigest()
    if expected == actual:
        return []
    return [
        Finding(
            rule_id,
            "시퀀스가 현재 클래스 다이어그램과 다른 메서드 계약 버전에서 생성됨",
            "class_diagram_hash",
        )
    ]


SEQUENCE_DIAGRAM_DETECTORS: dict[str, Callable[[dict, dict], list[Finding]]] = {
    "sequence_participants": sequence_participants,
    "sequence_bce_flow": sequence_bce_flow,
    "sequence_boundary_operation_direction": sequence_boundary_operation_direction,
    "sequence_traceability": sequence_traceability,
    "sequence_class_diagram_version": sequence_class_diagram_version,
    "sequence_participant_classes": sequence_participant_classes,
    "sequence_message_methods": sequence_message_methods,
    "sequence_initial_entry": sequence_initial_entry,
    "sequence_call_return_links": sequence_call_return_links,
    "sequence_unmatched_returns": sequence_unmatched_returns,
    "sequence_async_returns": sequence_async_returns,
    "sequence_return_values_match_methods": sequence_return_values_match_methods,
    "sequence_nonvoid_calls_have_returns": sequence_nonvoid_calls_have_returns,
    "sequence_causal_call_chain": sequence_causal_call_chain,
    "sequence_argument_data_flow": sequence_argument_data_flow,
    "sequence_actor_step_involvement": sequence_actor_step_involvement,
    "sequence_usecase_coverage": sequence_usecase_coverage,
    "sequence_step_operation_distinctness": sequence_step_operation_distinctness,
    "sequence_flow_order": sequence_flow_order,
    "sequence_unresolved_steps": sequence_unresolved_steps,
    "sequence_fragment_condition_consistency": sequence_fragment_condition_consistency,
    "sequence_database_access_discipline": sequence_database_access_discipline,
    "sequence_self_call_method_validation": sequence_self_call_method_validation,
    "sequence_orphan_participant_detection": sequence_orphan_participant_detection,
    "sequence_duplicate_consecutive_messages": sequence_duplicate_consecutive_messages,
    "sequence_message_naming_convention": sequence_message_naming_convention,
    "sequence_participant_kind_validity": sequence_participant_kind_validity,
    "sequence_message_type_validity": sequence_message_type_validity,
    "sequence_no_lifecycle_events": sequence_no_lifecycle_events,
}
API_SPEC_DETECTORS: dict[str, Callable[[dict, dict], list[Finding]]] = {
    "api_operations_present": api_operations_present,
    "api_path_parameters": api_path_parameters,
    "api_schema_references": api_schema_references,
    "api_operation_ids": api_operation_ids,
    "api_traceability": api_traceability,
    "api_control_binding": api_control_binding,
    "api_control_arguments": api_control_arguments,
    "api_control_outcomes": api_control_outcomes,
    "api_control_sequence": api_control_sequence,
}
SPEC_DETECTORS = {**CLASS_DIAGRAM_DETECTORS, **SEQUENCE_DIAGRAM_DETECTORS, **API_SPEC_DETECTORS}


def _artifact_findings(model: dict, state: dict, stage: str) -> list[Finding]:
    found: list[Finding] = []
    for rule in rules.judged_by(stage, rules.JUDGED_DETECTOR):
        found.extend(SPEC_DETECTORS[rule.detector](model or {}, state or {}))
    return found


def sequence_diagram_findings(model: dict, state: dict) -> list[Finding]:
    diagrams = model.get("Diagrams") if isinstance(model, dict) else None
    if isinstance(diagrams, list):
        found: list[Finding] = sequence_class_diagram_version(model, state)
        known = _known_use_case_ids(state)
        identifiers = [
            str(diagram.get("use_case_id") or "").strip()
            for diagram in diagrams
            if isinstance(diagram, dict)
        ]
        for use_case_id in sorted(known - set(identifiers)):
            found.append(
                Finding(
                    "sequence.usecase-step-coverage",
                    f"유스케이스 '{use_case_id}'의 시퀀스 다이어그램이 없음",
                    use_case_id,
                )
            )
        for use_case_id in sorted(set(identifiers) - known if known else set()):
            found.append(
                Finding(
                    "sequence.references-exist",
                    f"입력에 없는 유스케이스 '{use_case_id}'의 시퀀스 다이어그램이 있음",
                    use_case_id,
                )
            )
        seen: set[str] = set()
        for diagram in diagrams:
            if not isinstance(diagram, dict):
                continue
            use_case_id = str(diagram.get("use_case_id") or "").strip()
            if use_case_id in seen:
                found.append(
                    Finding(
                        "sequence.usecase-step-coverage",
                        f"유스케이스 '{use_case_id}'의 시퀀스 다이어그램이 중복됨",
                        use_case_id,
                    )
                )
            seen.add(use_case_id)
            for message in diagram.get("Messages") or []:
                references = {
                    str(value).strip()
                    for value in message.get("use_case_ids") or []
                    if value
                }
                if references != {use_case_id}:
                    found.append(
                        Finding(
                            "sequence.references-exist",
                            f"'{use_case_id}' 다이어그램 메시지가 다른 유스케이스를 참조함",
                            str(message.get("label") or "<message>"),
                        )
                    )
            found.extend(_artifact_findings(diagram, state, rules.SEQUENCE_DIAGRAM))
        return found
    return _artifact_findings(model, state, rules.SEQUENCE_DIAGRAM)


def api_spec_findings(model: dict, state: dict) -> list[Finding]:
    return _artifact_findings(model, state, rules.API_SPEC)
