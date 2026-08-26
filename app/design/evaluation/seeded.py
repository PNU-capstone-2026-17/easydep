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
            "methods": ["submitOrder(orderRequest : OrderRequest)"],
            "use_case_ids": ["UC1"],
        },
        {
            "className": "OrderController",
            "stereotype": "Control",
            "description": "Coordinates availability check and order recording.",
            "fields": [],
            "methods": [
                "placeOrder(orderRequest : OrderRequest): void",
                "checkAvailability(productId : String): boolean",
            ],
            "use_case_ids": ["UC1"],
        },
        {
            "className": "Order",
            "stereotype": "Entity",
            "description": "The recorded order.",
            "fields": ["orderedAt : DateTime", "totalAmount : Int"],
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


def _isolated_class() -> dict[str, Any]:
    model = _clean_model()
    model["Relationships"] = [
        relationship
        for relationship in model["Relationships"]
        if relationship["target"] != "Order"
    ]
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
            "fields": ["note : String"],
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


def _unknown_relationship_type() -> dict[str, Any]:
    # 렌더러가 모르는 종류로 바꾼다. `Realization`은 구조적 종류가 아니므로
    # `class.entity-association-multiplicity`를 함께 어기지 않는다.
    model = _clean_model()
    model["Relationships"][1]["type"] = "Realization"
    return model


def _entity_link_without_multiplicity() -> dict[str, Any]:
    # Entity 둘을 구조적으로 잇되 다중도를 안 준다. 새 Entity는 나머지 규칙을 전부
    # 지키게 둔다 — PascalCase, 이름 유일, UC1 참조, 끝점 선언됨. 그리고 Entity→Entity는
    # BCE 통신 규칙이 금지하는 조합이 아니다(금지는 B↔E · B↔B · E→C 뿐).
    model = _clean_model()
    model["Classes"].append(
        {
            "className": "OrderLine",
            "stereotype": "Entity",
            "description": "One line of the recorded order.",
            "fields": ["quantity : Int"],
            "methods": [],
            "use_case_ids": ["UC1"],
        }
    )
    model["Relationships"].append(
        {"source": "Order", "target": "OrderLine", "type": "Composition"}
    )
    return model


def _untyped_method_parameter() -> dict[str, Any]:
    model = _clean_model()
    model["Classes"][1]["methods"][1] = "checkAvailability(productId): boolean"
    return model


def _untyped_field() -> dict[str, Any]:
    model = _clean_model()
    model["Classes"][2]["fields"][0] = "orderedAt"
    return model


def _control_outcome_without_return_contract() -> dict[str, Any]:
    model = _clean_model()
    model["Classes"][1]["methods"][1] = "checkAvailability(productId : String)"
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
        "class.no-isolated-class",
        "Order가 어느 관계의 끝에도 나타나지 않는다",
        _isolated_class(),
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
    Seeded(
        "class.relationship-type-known",
        "관계 종류가 렌더러가 모르는 Realization이다",
        _unknown_relationship_type(),
        _clean_state(),
    ),
    Seeded(
        "class.entity-association-multiplicity",
        "Order와 OrderLine을 구조적으로 이었는데 다중도가 없다",
        _entity_link_without_multiplicity(),
        _clean_state(),
    ),
    Seeded(
        "class.method-parameters-typed",
        "checkAvailability의 productId 매개변수에 타입이 없다",
        _untyped_method_parameter(),
        _clean_state(),
    ),
    Seeded(
        "class.fields-typed",
        "Order의 orderedAt 필드에 타입이 없다",
        _untyped_field(),
        _clean_state(),
    ),
    Seeded(
        "class.control-outcome-return-contract",
        "결과를 확인하는 checkAvailability에 반환 계약이 없다",
        _control_outcome_without_return_contract(),
        _clean_state(),
    ),
)


# ---------------------------------------------------------------------------
# ERD
# ---------------------------------------------------------------------------
# 대조군이 따로 있는 이유: ERD 검사는 클래스 쪽과 **다른 것을 본다.** 클래스 대조군은
# Entity가 하나뿐이고 엔티티 사이 관계가 없어서, ERD 규칙 대부분이 아예 안 걸린다 —
# 그런 모델로는 "안 걸렸다"가 "지켰다"인지 "볼 것이 없었다"인지 구별되지 않는다.
#
# 그래서 이 대조군은 ERD가 판정하는 것을 **전부 한 번씩 갖고 있다**: 자연키를 쓰는
# 테이블과 대리키를 쓰는 테이블, 다중도가 붙은 구조적 관계, 그리고 사상에서 제외돼야
# 하는 행위 링크. 규칙마다 손으로 대조했다:
#   - Entity가 둘 있다 (`erd.has-entity`)
#   - 구조적 관계 하나에 양끝 다중도가 있고, 행위 링크는 Control이 끼어 있어 사상 대상이
#     아니다 (`erd.relationship-mapped`)
#   - Member의 `identifier`가 자기 필드 `email`을 가리킨다 (`erd.identifier-fields-exist`)
#   - 테이블 이름 Member·Order가 서로 다르고, 사상이 만드는 이름도 없다
#     (`erd.table-names-unique`)
#   - `<X>Id` 꼴 필드가 하나도 없다 (`erd.field-looks-like-reference`)
#   - Entity를 자료형으로 쓰는 필드가 없다 — 전부 String·Int·DateTime이다
#     (`erd.entity-typed-field-needs-relationship`)
ERD_CLEAN: dict[str, Any] = {
    "Classes": [
        {
            "className": "OrderController",
            "stereotype": "Control",
            "description": "Coordinates order recording.",
            "fields": [],
            "methods": ["placeOrder()"],
            "use_case_ids": ["UC1"],
        },
        {
            "className": "Member",
            "stereotype": "Entity",
            "description": "The account that places orders.",
            "fields": ["email : String", "displayName : String"],
            "identifier": ["email"],
            "methods": [],
            "use_case_ids": ["UC1"],
        },
        {
            "className": "Order",
            "stereotype": "Entity",
            "description": "The recorded order.",
            "fields": ["orderedAt : DateTime", "totalAmount : Int"],
            "identifier": [],
            "methods": [],
            "use_case_ids": ["UC1"],
        },
    ],
    "Relationships": [
        # 행위 링크 — 다중도가 없는 것이 정상이다. 끝 하나가 Control이라 사상이 지나간다.
        {"source": "OrderController", "target": "Order", "type": "Dependency"},
        # 구조적 연관 — 회원 하나가 주문 여럿을 갖는다. Order가 외래키를 든다.
        {
            "source": "Member",
            "target": "Order",
            "type": "Association",
            "sourceMultiplicity": "1",
            "targetMultiplicity": "*",
        },
    ],
}


def _clean_erd() -> dict[str, Any]:
    return copy.deepcopy(ERD_CLEAN)


def _erd_dangling_endpoint() -> dict[str, Any]:
    # 행위 링크 쪽에 붙인다. 구조적 연관에 붙이면 다중도까지 줘야 하고, 안 주면
    # `erd.relationship-mapped`를 함께 어긴다.
    model = _clean_erd()
    model["Relationships"].append(
        {"source": "OrderController", "target": "GhostEntity", "type": "Dependency"}
    )
    return model


def _erd_non_bce_stereotype() -> dict[str, Any]:
    # **Control 의 딱지를 바꾼다.** Entity 쪽을 바꾸면 그 표가 사라지면서 Member–Order
    # 관계도 못 옮기게 되어 `erd.relationship-mapped`가 함께 걸린다. Control 은 어차피
    # 표가 아니므로 사상 결과가 안 바뀌고, 그래서 이 규칙만 남는다.
    model = _clean_erd()
    model["Classes"][0]["stereotype"] = "Persistence"
    return model


def _erd_unusable_entity_name() -> dict[str, Any]:
    # 이름 없는 Entity 를 하나 **더한다.** 기존 것을 비우면 그것을 가리키던 관계가
    # 매달린 끝이 되어 규칙 둘을 어긴다. 필드를 주는 이유는 빈 표에 대한 지침
    # (`erd.entity-has-field`)이 지적은 안 하지만 대조군을 깨끗하게 두기 위해서다.
    model = _clean_erd()
    model["Classes"].append(
        {
            "className": "",
            "stereotype": "Entity",
            "description": "A class the model forgot to name.",
            "fields": ["note : String"],
            "identifier": [],
            "methods": [],
            "use_case_ids": ["UC1"],
        }
    )
    return model


def _no_entity_at_all() -> dict[str, Any]:
    # 두 Entity를 Control로 바꾼다. 테이블이 0개가 되면 나머지 검사는 **볼 것이 없어**
    # 조용하다 — 관계는 끝점이 테이블이 아니라 사상에서 지나가고, identifier 검사는
    # Entity가 아니라 건너뛴다.
    model = _clean_erd()
    for class_item in model["Classes"]:
        class_item["stereotype"] = "Control"
    return model


def _association_without_multiplicity() -> dict[str, Any]:
    # 다중도만 뗀다. Order는 외래키를 잃지만 대리키가 남으므로 다른 규칙은 성립한다.
    model = _clean_erd()
    model["Relationships"][1].pop("sourceMultiplicity")
    model["Relationships"][1].pop("targetMultiplicity")
    return model


def _identifier_names_a_missing_field() -> dict[str, Any]:
    # Member가 자기에게 없는 필드를 식별자로 지목한다. 사상은 조용히 대리키로 떨어지고,
    # Order의 외래키는 그 대리키를 가리키므로 참조는 여전히 성립한다.
    model = _clean_erd()
    model["Classes"][1]["identifier"] = ["memberNo"]
    return model


def _two_entities_share_a_name() -> dict[str, Any]:
    # 같은 이름의 Entity를 하나 더 둔다. 필드는 `<X>Id` 꼴이 아닌 것으로 두어
    # `erd.field-looks-like-reference`를 함께 어기지 않게 한다.
    model = _clean_erd()
    model["Classes"].append(
        {
            "className": "Order",
            "stereotype": "Entity",
            "description": "A second class that reuses the name.",
            "fields": ["note : String"],
            "identifier": [],
            "methods": [],
            "use_case_ids": ["UC1"],
        }
    )
    return model


def _mandatory_self_reference() -> dict[str, Any]:
    # Order 가 자기를 필수로 가리키게 한다("모든 주문에는 원주문이 있다"). 참조되는 끝이
    # `1` 이라 외래키가 NOT NULL 이 되고, 그러면 **첫 행을 넣을 수 없다.**
    # 다중도는 아는 표기이고 끝점·딱지·키는 그대로라 다른 규칙은 조용하다.
    model = _clean_erd()
    model["Relationships"].append(
        {
            "source": "Order",
            "target": "Order",
            "type": "Association",
            "sourceMultiplicity": "1",
            "targetMultiplicity": "*",
        }
    )
    return model


def _composition_with_an_optional_owner() -> dict[str, Any]:
    # Member–Order 를 합성으로 바꾸고 전체(Member) 쪽을 선택으로 만든다. 다중도는 아는
    # 표기이므로 사상은 되고(`erd.relationship-mapped` 조용함), 끝점·딱지도 그대로다.
    # 남는 것은 **종류와 다중도가 서로 다른 말을 한다**는 사실 하나다.
    model = _clean_erd()
    model["Relationships"][1]["type"] = "Composition"
    model["Relationships"][1]["sourceMultiplicity"] = "0..1"
    return model


def _surrogate_key_name_taken() -> dict[str, Any]:
    # Order 는 `identifier` 가 비어 있어 대리키 `order_id` 를 받는데, 같은 이름의 필드를
    # 이미 갖고 있다. `<X>Id` 꼴이지만 `Order` 는 **자기 자신**이라
    # `erd.field-looks-like-reference` 는 조용하다(자기 참조는 세지 않는다).
    model = _clean_erd()
    model["Classes"][2]["fields"].append("order_id : String")
    return model


def _field_points_at_an_entity_by_name() -> dict[str, Any]:
    # 관계를 지우고 그 자리를 이름으로 메운다 — 지워진 이름 추론이 하던 바로 그 일이다.
    # 관계가 없으므로 `erd.relationship-mapped`는 볼 것이 없다.
    model = _clean_erd()
    model["Relationships"] = [model["Relationships"][0]]
    model["Classes"][2]["fields"].append("memberId : Long")
    return model


def _field_points_at_an_entity_by_type() -> dict[str, Any]:
    # 관계를 지우고 그 자리를 **자료형**으로 메운다. 앞의 것(`memberId`)과 짝이다 —
    # 저쪽은 이름이 신호이고 이쪽은 타입이 신호다. 사상은 이 필드로 컬럼을 만들지
    # 않으므로 관계가 없으면 링크가 산출물 어디에도 안 남는다.
    #
    # 필드 이름을 `owner`로 둔 이유가 있다. `member : Member`로 두면 이름 신호에도
    # 걸려 `erd.field-looks-like-reference`까지 함께 울릴 뻔한 자리인데, 그쪽이
    # 타입 신호가 있는 필드를 비켜 주므로 실제로는 조용하다. 그래도 **한 시드가 한
    # 규칙만 어긴다는 것이 이 말뭉치의 규율**이라, 두 신호를 겹치지 않게 갈라 둔다.
    model = _clean_erd()
    model["Relationships"] = [model["Relationships"][0]]
    model["Classes"][2]["fields"].append("owner : Member")
    return model


#: ERD 규칙마다 하나. 클래스 쪽과 같은 규율이다 — **자기 규칙만** 어겨야 한다.
ERD_SEEDED: tuple[Seeded, ...] = (
    Seeded(
        "erd.relationship-endpoints-exist",
        "관계가 선언되지 않은 GhostEntity를 가리킨다",
        _erd_dangling_endpoint(),
        _clean_state(),
    ),
    Seeded(
        "erd.stereotype-is-bce",
        "OrderController의 스테레오타입이 BCE 밖(Persistence)이다",
        _erd_non_bce_stereotype(),
        _clean_state(),
    ),
    Seeded(
        "erd.entity-name-usable",
        "이름 없는 Entity가 있다",
        _erd_unusable_entity_name(),
        _clean_state(),
    ),
    Seeded(
        "erd.has-entity",
        "Entity가 하나도 없어 테이블이 안 나온다",
        _no_entity_at_all(),
        _clean_state(),
    ),
    Seeded(
        "erd.relationship-mapped",
        "Member–Order 연관에 다중도가 없어 사상되지 않는다",
        _association_without_multiplicity(),
        _clean_state(),
    ),
    Seeded(
        "erd.no-mandatory-reference-cycle",
        "Order가 자기 자신을 필수로 가리킨다 — 첫 행을 넣을 수 없다",
        _mandatory_self_reference(),
        _clean_state(),
    ),
    Seeded(
        "erd.composition-owner-is-mandatory",
        "합성인데 전체(Member) 쪽 다중도가 0..1이다",
        _composition_with_an_optional_owner(),
        _clean_state(),
    ),
    Seeded(
        "erd.identifier-fields-exist",
        "Member의 identifier가 없는 필드 memberNo를 가리킨다",
        _identifier_names_a_missing_field(),
        _clean_state(),
    ),
    Seeded(
        "erd.surrogate-key-collides",
        "Order가 우리가 붙일 대리키 order_id와 같은 이름의 필드를 갖고 있다",
        _surrogate_key_name_taken(),
        _clean_state(),
    ),
    Seeded(
        "erd.table-names-unique",
        "Order라는 이름의 Entity가 둘이다",
        _two_entities_share_a_name(),
        _clean_state(),
    ),
    Seeded(
        "erd.field-looks-like-reference",
        "Order.memberId가 관계 없이 Member를 이름으로 가리킨다",
        _field_points_at_an_entity_by_name(),
        _clean_state(),
    ),
    Seeded(
        "erd.entity-typed-field-needs-relationship",
        "Order.owner의 타입이 Member인데 둘 사이에 관계가 없다",
        _field_points_at_an_entity_by_type(),
        _clean_state(),
    ),
)
