"""심어 둔 결함으로 **검사기 자체를 검사한다.**

## 왜 이게 필요한가

검출기를 만들었다고 눈금이 맞는 것은 아니다. `class.no-boundary-entity-link` 위반이
"0건"이라고 나올 때, 그것이 정말 없다는 뜻인지 **검출기가 못 잡는다**는 뜻인지 구별할
근거가 없으면 그 0은 아무 정보가 아니다. 그러면 재생성 루프도 게이트 표시도 전부 근거
없는 수 위에 올라간다.

그래서 **결함을 알고 심은 모델**을 두고, 검출기가 그것을 잡는지 본다. 규칙마다 하나씩,
**정확히 그 규칙만** 어기게 심는다. 잡으면 그 규칙의 눈금이 살아 있다는 뜻이고, 못
잡으면 그 규칙에 대한 모든 0은 근거가 없다는 뜻이다.

`CLEAN`은 대조군이다. 아무 결함이 없으므로 검출기가 아무것도 내지 않아야 한다 — 여기서
무언가 나오면 그건 **오탐**이고, 오탐이 있는 검출기는 재생성 예산을 고칠 수 없는 지적에
태운다(위반 수가 안 줄어 `no_improvement`로 멈춘다).

## 왜 CI에 넣을 수 있는가

검출기가 전부 결정론이라 LLM이 안 들어간다. 판정이 흔들리지 않으므로 **전수가 통과해야
한다**는 기계적 게이트를 세울 수 있다. LLM 의미 검증자를 나중에 넣으면 그쪽은 이렇게
할 수 없고, 반복 표본으로 검출률을 재야 한다(`app/requirements/evaluation/semantic.py`가
그 예다).

## 심는 규칙: 하나만 어긴다

각 케이스는 **자기 규칙만** 어겨야 한다. 둘 이상 어기면 어느 검출기가 잡은 것인지 알 수
없고, 그러면 "전부 통과"가 검출기 하나의 고장을 덮는다.

이 제약 때문에 넣지 못한 케이스가 하나 있다. `class.names-unique`의 흥미로운 쪽 —
`Order Item`과 `Order_Item`처럼 **렌더 후에야 충돌하는** 이름 — 은 `Order Item`이
PascalCase가 아니라서 `class.name-pascal-case`도 함께 어긴다. 단독으로 심을 수 없으므로
여기 대신 회귀 테스트에 따로 둔다(`tests/test_design_detectors.py`).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

#: 대조군의 입력 유스케이스. 검출기 중 셋(`usecase_ids`·`usecase_coverage`)이 상류를
#: 보므로, 모델만으로는 대조군이 성립하지 않는다.
CLEAN_STATE: dict[str, Any] = {
    "usecase_spec": {
        "use_cases": [
            {"id": "UC1", "name": "Place an order"},
        ]
    }
}

#: 결함이 없는 BCE 모델. **대조군이므로 검출기가 여기서 아무것도 내면 안 된다.**
#:
#: ⚠ 요구사항 쪽에서 대조군이 실은 깨끗하지 않아 오탐률 100%가 나온 적이 있다
#: (`app/requirements/evaluation/seeded.py` docstring). 대조군이 오염되면 오탐을 못 재고,
#: 못 재면 "다 잡는 눈금"과 "제대로 잡는 눈금"이 구별되지 않는다. 그래서 여기 있는 것은
#: 규칙 하나하나에 대고 손으로 확인한 것이다:
#:   - 관계 두 개의 양 끝이 전부 선언돼 있다
#:   - Boundary→Control, Control→Entity — 금지 조합이 아니다
#:   - 세 이름 모두 PascalCase이고 렌더 후에도 서로 다르다
#:   - UC1을 세 클래스가 전부 가리키고, 그 밖의 id는 없다
CLEAN: dict[str, Any] = {
    "Classes": [
        {
            "className": "OrderForm",
            "stereotype": "Boundary",
            "description": "Collects the order request from the member.",
            "fields": [],
            "methods": ["submitOrder()"],
            "use_case_ids": ["UC1"],
        },
        {
            "className": "OrderController",
            "stereotype": "Control",
            "description": "Coordinates availability check and order recording.",
            "fields": [],
            "methods": ["placeOrder()", "checkAvailability()"],
            "use_case_ids": ["UC1"],
        },
        {
            "className": "Order",
            "stereotype": "Entity",
            "description": "The recorded order.",
            "fields": ["orderedAt", "totalAmount"],
            "methods": [],
            "use_case_ids": ["UC1"],
        },
    ],
    "Relationships": [
        {"source": "OrderForm", "target": "OrderController", "type": "Dependency"},
        {"source": "OrderController", "target": "Order", "type": "Dependency"},
    ],
}


@dataclass(frozen=True)
class Seeded:
    """결함 하나를 심은 케이스. `rule_id`만 잡혀야 한다."""

    rule_id: str
    #: 무엇을 어떻게 망가뜨렸는지. 케이스를 읽는 사람을 위한 것이지 판정에 안 쓴다.
    what: str
    model: dict[str, Any]
    state: dict[str, Any]


def _clean_model() -> dict[str, Any]:
    """대조군의 깊은 사본. 케이스가 서로를 오염시키지 않게 한다."""
    return copy.deepcopy(CLEAN)


def _clean_state() -> dict[str, Any]:
    return copy.deepcopy(CLEAN_STATE)


def _dangling_endpoint() -> dict[str, Any]:
    model = _clean_model()
    model["Relationships"].append(
        {"source": "OrderController", "target": "GhostEntity", "type": "Dependency"}
    )
    return model


def _invented_usecase_id() -> dict[str, Any]:
    # Order 만 없는 id를 가리킨다. UC1은 나머지 둘이 여전히 가리키므로 커버리지는 성립하고,
    # 그래야 이 케이스가 `class.covers-use-cases`를 함께 어기지 않는다.
    model = _clean_model()
    model["Classes"][2]["use_case_ids"] = ["UC9"]
    return model


def _non_bce_stereotype() -> dict[str, Any]:
    # Order 의 스테레오타입만 BCE 밖으로 바꾼다. 그러면 Control→Order 관계는 통신 규칙
    # 검사에서 **건너뛰어지므로**(끝 하나의 스테레오타입을 못 읽는다) 그 규칙은 안 걸린다.
    model = _clean_model()
    model["Classes"][2]["stereotype"] = "Persistence"
    return model


def _boundary_entity_link() -> dict[str, Any]:
    model = _clean_model()
    model["Relationships"].append(
        {"source": "OrderForm", "target": "Order", "type": "Association"}
    )
    return model


def _boundary_boundary_link() -> dict[str, Any]:
    model = _clean_model()
    model["Classes"].append(
        {
            "className": "OrderReceiptView",
            "stereotype": "Boundary",
            "description": "Shows the accepted order back to the member.",
            "fields": [],
            "methods": ["showReceipt()"],
            "use_case_ids": ["UC1"],
        }
    )
    model["Relationships"].append(
        {"source": "OrderForm", "target": "OrderReceiptView", "type": "Association"}
    )
    return model


def _entity_initiates() -> dict[str, Any]:
    model = _clean_model()
    model["Relationships"].append(
        {"source": "Order", "target": "OrderController", "type": "Association"}
    )
    return model


def _duplicate_name() -> dict[str, Any]:
    # 정확히 같은 이름을 둘 둔다. 렌더 후에야 충돌하는 쪽(`Order Item` vs `Order_Item`)은
    # PascalCase 규칙도 함께 어기므로 여기 심을 수 없다 — 회귀 테스트에 따로 있다.
    model = _clean_model()
    model["Classes"].append(
        {
            "className": "Order",
            "stereotype": "Entity",
            "description": "A second class that reuses the name.",
            "fields": ["note"],
            "methods": [],
            "use_case_ids": ["UC1"],
        }
    )
    return model


def _not_pascal_case() -> dict[str, Any]:
    # 이름을 바꾸면서 **관계의 끝도 함께** 바꾼다. 안 그러면 매달린 끝이 되어
    # `class.relationship-endpoints-exist`를 함께 어긴다.
    model = _clean_model()
    model["Classes"][0]["className"] = "order_form"
    model["Relationships"][0]["source"] = "order_form"
    return model


def _uncovered_use_case() -> dict[str, Any]:
    # 모델은 그대로 두고 **상류에 유스케이스를 하나 더** 둔다. 모델이 UC2를 아예 모르므로
    # 커버리지만 깨지고, 지어낸 id는 없으므로 `class.usecase-ids-exist`는 성립한다.
    state = _clean_state()
    state["usecase_spec"]["use_cases"].append({"id": "UC2", "name": "Cancel an order"})
    return state


#: 규칙마다 하나. **`knowledge/rules.py`의 DEFECT 규칙 전수를 덮어야 한다** —
#: 빠진 규칙이 있으면 테스트가 실패한다(눈금 없는 규칙이 조용히 생기는 것을 막는다).
SEEDED: tuple[Seeded, ...] = (
    Seeded(
        "class.relationship-endpoints-exist",
        "관계가 선언되지 않은 GhostEntity를 가리킨다",
        _dangling_endpoint(),
        _clean_state(),
    ),
    Seeded(
        "class.usecase-ids-exist",
        "Order가 입력에 없는 UC9를 가리킨다",
        _invented_usecase_id(),
        _clean_state(),
    ),
    Seeded(
        "class.stereotype-is-bce",
        "Order의 스테레오타입이 BCE 밖(Persistence)이다",
        _non_bce_stereotype(),
        _clean_state(),
    ),
    Seeded(
        "class.no-boundary-entity-link",
        "OrderForm(B)과 Order(E)를 직접 이었다",
        _boundary_entity_link(),
        _clean_state(),
    ),
    Seeded(
        "class.no-boundary-boundary-link",
        "OrderForm(B)과 OrderReceiptView(B)를 직접 이었다",
        _boundary_boundary_link(),
        _clean_state(),
    ),
    Seeded(
        "class.entity-does-not-initiate",
        "Order(E)가 OrderController(C)를 향해 관계를 시작한다",
        _entity_initiates(),
        _clean_state(),
    ),
    Seeded(
        "class.names-unique",
        "Order라는 이름의 클래스가 둘이다",
        _duplicate_name(),
        _clean_state(),
    ),
    Seeded(
        "class.name-pascal-case",
        "order_form이 PascalCase가 아니다",
        _not_pascal_case(),
        _clean_state(),
    ),
    Seeded(
        "class.covers-use-cases",
        "상류의 UC2를 가리키는 클래스가 없다",
        _clean_model(),
        _uncovered_use_case(),
    ),
)
