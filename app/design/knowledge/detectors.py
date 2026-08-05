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

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.design import rtm
from app.design.knowledge import rules
from app.design.services.class_diagram.plantuml import sanitize_class_name

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
    rule_id = "class.relationship-endpoints-exist"
    declared = {c.get("className") for c in _classes(model) if c.get("className")}
    found: list[Finding] = []
    for relationship in _relationships(model):
        label = _relation_label(relationship)
        for end in ("source", "target"):
            name = relationship.get(end)
            if name and name not in declared:
                found.append(
                    Finding(rule_id, f"{end} '{name}'가 Classes에 없음", label)
                )
    return found


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


def stereotype_is_bce(model: dict, state: dict) -> list[Finding]:
    """스테레오타입이 Boundary/Control/Entity 중 하나인가.

    **통신 규칙보다 먼저 돈다.** 이게 깨지면 아래 세 규칙이 무판정이 되고, 무판정은
    겉보기에 통과와 같다.
    """
    rule_id = "class.stereotype-is-bce"
    found: list[Finding] = []
    for class_item in _classes(model):
        name = class_item.get("className") or "?"
        stereotype = _stereotype_of(class_item)
        if not stereotype:
            found.append(Finding(rule_id, "스테레오타입 없음", name))
        elif stereotype not in BCE_STEREOTYPES:
            found.append(
                Finding(rule_id, f"BCE 밖의 스테레오타입 '{stereotype}'", name)
            )
    return found


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
