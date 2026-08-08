"""설계 산출물과 구현 에이전트 사이의 **텍스트 계약** (네트워크 불필요).

구현 쪽은 설계 산출물을 dict가 아니라 **렌더된 문자열로** 받아 정규식으로 다시 읽는다
(`app/implementation/engine/design_context.py`). 그래서 렌더 형태를 바꾸면 하류가 조용히
덜 읽는다 — 예외가 아니라 빈 결과로 실패하므로 아무도 눈치채지 못한다.

이 파일이 생긴 계기가 그 위험이었다. 클래스 관계에 다중도를 넣고(`A "1" --> "0..*" B`)
ERD에 연결 테이블·1NF 자식 테이블을 추가하면서, 네 파서가 여전히 같은 것을 뽑는지
확인해야 했다. 확인은 통과했고, 그 확인을 여기 남긴다.

**하류를 고치려는 파일이 아니다.** 여기 있는 정규식들은 우리 관할 밖이고
(`docs/cloud-native-extension.md` §9의 "다른 영역의 살아있는 명세"), 이 테스트는 우리가
그쪽 입력을 깨지 않았다는 것만 말한다.
"""
from __future__ import annotations

from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.erd.plantuml import generate_erd_from_bce_json
from app.implementation.engine.planning.design_context import (
    RELATION_PATTERN,
    parse_design_classes,
    parse_relations,
    slice_erd,
)

#: 새 스키마의 모든 새 요소를 한 번씩 지나는 모델: 다중도, 자연키, 다대다(연결 테이블),
#: 상속, 다중값 필드(1NF 자식).
MODEL = {
    "Classes": [
        {"className": "OrderForm", "stereotype": "Boundary", "fields": [], "methods": ["submit()"]},
        {"className": "Member", "stereotype": "Entity",
         "fields": ["email : String"], "identifier": ["email"], "methods": []},
        {"className": "PremiumMember", "stereotype": "Entity",
         "fields": ["tier : String"], "methods": []},
        {"className": "Order", "stereotype": "Entity",
         "fields": ["orderedAt : DateTime", "notes : List<String>"], "methods": []},
        {"className": "Tag", "stereotype": "Entity", "fields": ["label : String"], "methods": []},
        {"className": "OrderLine", "stereotype": "Entity",
         "fields": ["quantity : Int"], "methods": []},
    ],
    "Relationships": [
        {"source": "Member", "target": "Order", "type": "Association",
         "sourceMultiplicity": "1", "targetMultiplicity": "*"},
        {"source": "Order", "target": "Tag", "type": "Association",
         "sourceMultiplicity": "*", "targetMultiplicity": "*"},
        # 합성 → ERD에서 **실선**이 된다. 하류의 관계 줄 정규식은 점선(`..`)과 실선(`--`)을
        # 둘 다 받아야 한다.
        {"source": "Order", "target": "OrderLine", "type": "Composition",
         "sourceMultiplicity": "1", "targetMultiplicity": "1..*"},
        {"source": "PremiumMember", "target": "Member", "type": "Inheritance"},
        {"source": "OrderForm", "target": "Order", "type": "Dependency"},
    ],
}

CLASS_PUML = generate_plantuml_from_bce_json(MODEL)
ERD_PUML = generate_erd_from_bce_json(MODEL)


# ---------------------------------------------------------------------------
# 클래스 다이어그램 — 다중도가 끼어들어도 같은 것을 뽑는가
# ---------------------------------------------------------------------------
def test_class_declarations_still_parse():
    parsed = {(c.name, c.stereotype) for c in parse_design_classes(CLASS_PUML)}

    assert parsed == {
        ("OrderForm", "Boundary"), ("Member", "Entity"), ("PremiumMember", "Entity"),
        ("Order", "Entity"), ("Tag", "Entity"), ("OrderLine", "Entity"),
    }


def test_relation_endpoints_survive_the_multiplicity_labels():
    """`Member "1" --> "*" Order`에서 여전히 (Member, Order)가 나오는가.

    하류는 `[A-Za-z_]\\w*`로 식별자를 긁고 첫/끝을 끝점으로 삼는다. 다중도는 `"1"`·`"*"`
    처럼 따옴표에 싸인 숫자·기호라 그 정규식에 안 걸린다 — 그래서 가운데 끼어도 안전하다.
    """
    pairs = {(source, target) for source, target, _ in parse_relations(CLASS_PUML)}

    assert ("Member", "Order") in pairs
    assert ("Order", "Tag") in pairs
    assert ("OrderForm", "Order") in pairs


def test_the_downstream_parser_does_not_see_inheritance_at_all():
    """**우리가 만든 구멍이 아니라 원래 있던 구멍이다** — 그래서 적어 둔다.

    `parse_relations`는 `-->` · `..>` · `*--` · `o--`만 찾는다(`design_context.py`).
    상속은 `<|--`로 그려지므로 네 표기 어디에도 안 걸리고, 구현 에이전트는 클래스
    다이어그램의 일반화 관계를 **한 번도 본 적이 없다.**

    이 저장소의 변경 전후로 같다: 상속 줄의 모양(`target <|-- source`)은 그대로다.
    여기 적는 이유는 ERD 쪽에서 상속을 제대로 사상하기 시작했기 때문이다 — ERD는 이제
    상속을 테이블로 옮기는데 클래스 다이어그램 경로는 여전히 못 옮긴다. 둘이 어긋나
    있다는 사실이 어디에도 안 적혀 있으면, 다음 사람이 ERD만 보고 "상속은 처리된다"고
    읽는다.
    """
    inheritance = [line for line in CLASS_PUML.splitlines() if "<|--" in line]
    assert inheritance == ["Member <|-- PremiumMember"]

    pairs = {(source, target) for source, target, _ in parse_relations(CLASS_PUML)}
    assert ("Member", "PremiumMember") not in pairs


def test_the_other_relation_regex_survives_too():
    """`RELATION_PATTERN`은 `parse_relations`와 다른 정규식이다 — 둘 다 봐야 한다."""
    line = 'Member "1" --> "*" Order'
    match = RELATION_PATTERN.search(line)

    assert match and (match.group("left"), match.group("right")) == ("Member", "Order")


# ---------------------------------------------------------------------------
# ERD — 사상이 만든 테이블도 잘라 갈 수 있는가
# ---------------------------------------------------------------------------
def test_slice_erd_still_finds_a_plain_table():
    assert 'entity "Member" as Member {' in slice_erd(ERD_PUML, {"Member"})


def test_slice_erd_finds_the_tables_the_mapping_invented():
    """연결 테이블과 1NF 자식도 **같은 선언 형태**를 지켜야 한다.

    형태가 다르면 그 테이블만 하류에서 조용히 사라진다 — 그리고 사라진 것은 대개
    다대다 관계를 담은 테이블이다.
    """
    assert 'entity "OrderTag" as OrderTag {' in slice_erd(ERD_PUML, {"OrderTag"})
    assert 'entity "OrderNotes" as OrderNotes {' in slice_erd(ERD_PUML, {"OrderNotes"})


def test_slice_erd_carries_the_relationship_lines():
    """테이블 블록만 가져가면 하류가 관계를 모른다."""
    sliced = slice_erd(ERD_PUML, {"Member", "Order"})

    assert "Member ||..o{ Order" in sliced


def test_slice_erd_carries_solid_lines_too():
    """식별 관계는 실선(`--`)으로 그려진다 — 하류 정규식이 점선만 찾으면 안 된다.

    `slice_erd`는 `(?:\\.\\.|--)`를 찾으므로 둘 다 받는다. 합성을 실선으로 바꾸면서
    확인한 것이고, 확인을 여기 남긴다.
    """
    sliced = slice_erd(ERD_PUML, {"Order", "OrderLine"})

    assert "Order ||--|{ OrderLine" in sliced


def test_slice_erd_carries_a_composite_unique_constraint():
    """복합 유일 제약은 **표 블록 안**에 있어야 하류까지 따라간다.

    `slice_erd`는 `entity "N" as N {` 부터 `}` 까지를 잘라 가므로, 블록 밖에 적으면 그
    제약만 조용히 빠진다. 컬럼의 `<<unique>>`와 같은 자리에 있어야 같은 운명을 겪는다.

    별도 모델을 쓰는 이유: 위의 `MODEL`은 자연키가 한 칸(`Member.email`)이라 복합 제약이
    아예 안 나온다. 상속이 **복합** 자연키를 강등하는 자리를 지나야 이 줄이 생긴다.
    """
    puml = generate_erd_from_bce_json({
        "Classes": [
            {"className": "Base", "stereotype": "Entity", "fields": ["x : Int"], "methods": []},
            {"className": "Sub", "stereotype": "Entity",
             "fields": ["a : String", "b : String"], "identifier": ["a", "b"], "methods": []},
        ],
        "Relationships": [{"source": "Sub", "target": "Base", "type": "Inheritance"}],
    })

    assert "unique (a, b)" in slice_erd(puml, {"Sub"})


def test_an_unknown_entity_name_is_reported_not_crashed():
    assert slice_erd(ERD_PUML, {"NoSuchTable"}) == "' No directly related ERD entity"
