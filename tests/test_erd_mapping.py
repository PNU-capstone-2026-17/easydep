"""BCE → 논리 데이터 모델 사상 (네트워크 불필요).

사상 규칙마다 하나씩 확인한다. 전부 표준 UML→관계형 사상이고, 우리가 고른 둘(상속 전략,
대리키)은 **골랐다는 사실이 모델에 남는지**까지 본다.

여기에는 성격이 다른 테스트가 하나 더 있다. 맨 아래의 **불변식** 둘 —
"모든 테이블에 기본키가 있다"와 "외래키는 실재 테이블을 가리킨다" — 은 원래 규칙
(`knowledge/rules.py`)으로 적었다가 뺀 것들이다. 사상이 구성에 의해 보장하므로 어떤
모델로도 위반을 만들 수 없었고, **걸 수 없는 규칙의 "0건"은 눈금이 아니다.** 그 둘은
모델에 대한 규칙이 아니라 우리 코드의 불변식이라 자리가 여기다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.design.knowledge.detectors import erd_findings
from app.design.services.common import fields as mapping_fields
from app.design.services.erd.inheritance import order_for_mapping
from app.design.services.erd.mapping import build_logical_model

from tests.test_erd_convert import LIBRARY_BCE_LEGACY, LIBRARY_BCE_TODAY


def _entity(name, fields=(), identifier=()):
    return {
        "className": name,
        "stereotype": "Entity",
        "fields": list(fields),
        "identifier": list(identifier),
        "methods": [],
    }


def _table(logical, name):
    return next(t for t in logical["Tables"] if t["name"] == name)


def _names(logical):
    return [t["name"] for t in logical["Tables"]]


def _column(table, name):
    return next(c for c in table["columns"] if c["name"] == name)


# ---------------------------------------------------------------------------
# 다중도가 결정하는 것
# ---------------------------------------------------------------------------
def test_one_to_many_puts_the_foreign_key_on_the_many_side():
    logical = build_logical_model({
        "Classes": [_entity("Member", ["email : String"]), _entity("Order", ["at : Date"])],
        "Relationships": [{"source": "Member", "target": "Order", "type": "Association",
                           "sourceMultiplicity": "1", "targetMultiplicity": "*"}],
    })

    assert _column(_table(logical, "Order"), "member_id")["references"] == "Member"
    assert not [c for c in _table(logical, "Member")["columns"] if c["references"]]


def test_many_to_many_becomes_a_join_table():
    """**여기가 예전 사상에 아예 없던 길이다.**

    다중도를 담을 칸이 없어 모든 관계를 1:N으로 단정했고, 그래서 다대다가 연결 테이블이
    되는 경로가 코드에 존재하지 않았다. 관계형에서 다대다 → 연결 테이블은 선택이 아니다.
    """
    logical = build_logical_model({
        "Classes": [_entity("Book", ["title : String"]), _entity("Tag", ["label : String"])],
        "Relationships": [{"source": "Book", "target": "Tag", "type": "Association",
                           "sourceMultiplicity": "*", "targetMultiplicity": "*"}],
    })
    junction = _table(logical, "BookTag")

    assert junction["origin"]["kind"] == "junction"
    # 두 외래키가 **함께** 기본키다 — 한쪽만 키면 같은 쌍이 여러 번 들어간다.
    assert junction["primaryKey"] == ["book_id", "tag_id"]
    assert {c["references"] for c in junction["columns"]} == {"Book", "Tag"}
    # 양쪽에서 연결 테이블로 선이 간다. Book–Tag 직결선은 없다.
    assert {(r["source"], r["target"]) for r in logical["Relations"]} == {
        ("Book", "BookTag"), ("Tag", "BookTag")
    }


def test_one_to_one_is_a_unique_foreign_key_on_the_optional_side():
    """선택인 쪽이 든다 — 그래야 NULL이 될 수 있는 칸과 실제로 선택인 자리가 같아진다."""
    logical = build_logical_model({
        "Classes": [_entity("Member", ["email : String"]), _entity("Profile", ["bio : String"])],
        "Relationships": [{"source": "Member", "target": "Profile", "type": "Association",
                           "sourceMultiplicity": "1", "targetMultiplicity": "0..1"}],
    })
    foreign = _column(_table(logical, "Profile"), "member_id")

    assert foreign["references"] == "Member" and foreign["unique"] is True
    assert not [c for c in _table(logical, "Member")["columns"] if c["references"]]


@pytest.mark.parametrize(
    "source_multiplicity, target_multiplicity, symbol",
    [
        ("1", "*", "||..o{"),
        ("1", "1..*", "||..|{"),   # 하한 1이 기호에 남는다
        ("0..1", "*", "|o..o{"),
        ("1", "1", "||..||"),
    ],
)
def test_the_symbol_carries_the_multiplicity(source_multiplicity, target_multiplicity, symbol):
    """크로우풋 기호가 다중도를 그대로 옮기는가 — 하한까지."""
    logical = build_logical_model({
        "Classes": [_entity("A", ["x : Int"]), _entity("B", ["y : Int"])],
        "Relationships": [{"source": "A", "target": "B", "type": "Association",
                           "sourceMultiplicity": source_multiplicity,
                           "targetMultiplicity": target_multiplicity}],
    })

    assert logical["Relations"][0]["symbol"] == symbol


@pytest.mark.parametrize(
    "kind, source_multiplicity, target_multiplicity, holder, column, mandatory",
    [
        # 다쪽이 외래키를 든다. 널 허용은 **참조되는 쪽 끝**이 정한다.
        ("Association", "1", "*", "B", "a_id", True),      # B 하나마다 A가 정확히 하나
        ("Association", "1", "1..*", "B", "a_id", True),
        ("Association", "0..1", "*", "B", "a_id", False),  # A 없는 B가 있을 수 있다
        ("Association", "*", "1", "A", "b_id", True),      # 방향만 뒤집힌 같은 규칙
        ("Association", "*", "0..1", "A", "b_id", False),
        # 1:1 — 외래키를 드는 쪽은 선택인 쪽, 널 허용은 같은 규칙.
        ("Association", "1", "0..1", "B", "a_id", True),
        ("Association", "0..1", "1", "A", "b_id", True),
        ("Association", "0..1", "0..1", "B", "a_id", False),
        # 집약도 연관과 같다 — 종류가 아니라 다중도가 정한다.
        ("Aggregation", "1", "*", "B", "a_id", True),
        ("Aggregation", "0..1", "*", "B", "a_id", False),
    ],
    ids=lambda v: str(v),
)
def test_a_foreign_key_is_not_null_when_the_referenced_end_is_mandatory(
    kind, source_multiplicity, target_multiplicity, holder, column, mandatory
):
    """외래키의 널 허용은 **참조되는 쪽 끝의 다중도**가 정한다.

    한동안 관계 **종류**가 정했다 — 합성일 때만 NOT NULL이었다. 합성이 대개 필수인 것과
    우연히 겹쳐 흔한 경우에는 맞는 답이 나왔지만 근거가 달랐고, 연관·집약은 다중도가
    뭐라고 하든 전부 nullable이 됐다.

    `A "1" — "*" B`에서 A쪽 `"1"`은 *"B 하나마다 A가 정확히 하나"*라는 뜻이므로 `B.a_id`는
    비어 있을 수 없다. 모델이 이미 그렇게 적어 놨는데 사상이 안 읽고 있었다.

    그림에서 안 보이는 곳으로 새는 결함이라 더 중요하다 — 하류가 이 산출물로 DDL을
    만들므로 `NOT NULL` 하나가 실제 스키마 제약이다.
    """
    logical = build_logical_model({
        "Classes": [_entity("A", ["x : Int"]), _entity("B", ["y : Int"])],
        "Relationships": [{"source": "A", "target": "B", "type": kind,
                           "sourceMultiplicity": source_multiplicity,
                           "targetMultiplicity": target_multiplicity}],
    })

    assert _column(_table(logical, holder), column)["mandatory"] is mandatory


def test_a_relationship_without_multiplicity_is_left_unmapped_not_guessed():
    logical = build_logical_model({
        "Classes": [_entity("A", ["x : Int"]), _entity("B", ["y : Int"])],
        "Relationships": [{"source": "A", "target": "B", "type": "Association"}],
    })

    assert logical["Relations"] == []
    assert logical["Unmapped"][0]["reason"] == "multiplicity-missing"


def test_a_dependency_between_two_entities_is_surfaced_not_dropped():
    """행위 링크로 조용히 버리면, 종류를 잘못 적은 데이터 관계와 구별되지 않는다."""
    logical = build_logical_model({
        "Classes": [_entity("A", ["x : Int"]), _entity("B", ["y : Int"])],
        "Relationships": [{"source": "A", "target": "B", "type": "Dependency"}],
    })

    assert logical["Unmapped"][0]["reason"] == "dependency-between-entities"


# ---------------------------------------------------------------------------
# 우리가 고른 것 둘
# ---------------------------------------------------------------------------
def test_inheritance_makes_the_subclass_key_point_at_the_superclass():
    """상속은 **자식**의 기본키가 부모를 가리키는 외래키가 된다(클래스별 테이블).

    예전 사상은 상속 종류를 몰라 일반 연관처럼 다뤘고, 모델의 source가 자식인데
    `link(source, target)`을 불러 **부모에 외래키를 달았다** — 부모가 다, 자식이 1인
    거꾸로 된 그림이다. 이 테스트가 그 회귀를 막는다.
    """
    logical = build_logical_model({
        "Classes": [_entity("Member", ["email : String"]), _entity("PremiumMember", ["tier : String"])],
        # 모델의 source가 자식이다 (`class_diagram/plantuml.py`가 `target <|-- source`로 그린다).
        "Relationships": [{"source": "PremiumMember", "target": "Member", "type": "Inheritance"}],
    })
    child, parent = _table(logical, "PremiumMember"), _table(logical, "Member")

    assert child["keyOrigin"] == "inherited"
    assert _column(child, "member_id")["references"] == "Member"
    assert child["primaryKey"] == ["member_id"]
    # 부모는 자식을 모른다. 외래키가 부모에 붙으면 방향이 뒤집힌 것이다.
    assert not [c for c in parent["columns"] if c["references"]]


def test_a_declared_identifier_wins_over_a_surrogate_key():
    logical = build_logical_model({
        "Classes": [_entity("Book", ["isbn : String", "title : String"], identifier=["isbn"])],
        "Relationships": [],
    })
    book = _table(logical, "Book")

    assert book["primaryKey"] == ["isbn"] and book["keyOrigin"] == "natural"
    assert "book_id" not in [c["name"] for c in book["columns"]]


def test_a_surrogate_key_records_that_we_added_it():
    """대리키를 쓰는 것 자체는 흔한 선택이지만, **골랐다는 사실이 남아야** 한다."""
    logical = build_logical_model({
        "Classes": [_entity("Order", ["at : Date"])],
        "Relationships": [],
    })

    assert _table(logical, "Order")["keyOrigin"] == "surrogate"


def test_a_repeated_identifier_still_yields_the_natural_key():
    """`identifier`에 같은 이름이 두 번 있어도 자연키를 버리지 않는다.

    예전에는 `len(natural) == len(identifier)`로 비교해서, 중복이 있으면 개수가 안 맞아
    **자연키를 통째로 버리고 대리키로 조용히 떨어졌다.** 모델이 키를 선언했는데 우리가
    말없이 다른 키를 쓰는 것이라, 산출물과 모델이 어긋난 채로 나갔다.
    """
    logical = build_logical_model({
        "Classes": [_entity("Book", ["isbn : String"], identifier=["isbn", "isbn"])],
        "Relationships": [],
    })
    book = _table(logical, "Book")

    assert book["primaryKey"] == ["isbn"] and book["keyOrigin"] == "natural"


def test_a_multivalued_field_cannot_be_the_natural_key():
    """다중값 필드는 자연키가 **될 수 없다** — 이 표에 칸이 안 남기 때문이다.

    허용하면 `primaryKey`가 실재하지 않는 칸 이름을 담고, 그 유령 키를 자식 표의
    외래키가 가리킨다. 불변식(`assert_sound`)이 그것을 잡지만, 잡히기 전에 **애초에
    만들지 않는** 것이 맞다. 대신 검사기가 사유와 함께 지적한다.
    """
    model = {
        "Classes": [_entity("Order", ["tags : List<String>", "at : Date"], identifier=["tags"])],
        "Relationships": [],
    }
    logical = build_logical_model(model)
    order = _table(logical, "Order")

    assert order["keyOrigin"] == "surrogate"
    assert order["primaryKey"] == ["order_id"]
    assert_sound(logical)

    issues = [f.as_issue() for f in erd_findings(model, {})]
    assert any("erd.identifier-fields-exist" in i and "다중값" in i for i in issues), issues


def test_a_declared_field_is_not_silently_retyped_by_the_surrogate_key():
    """대리키와 이름이 겹치는 선언 필드가 **조용히** 밀려나지 않는가.

    밀려나는 것 자체는 막지 못한다(기본키는 있어야 하고, 이름이 하나뿐이므로). 막는 것은
    **조용함**이다 — `order_id : String`이라 적은 칸이 말없이 `BIGINT`가 되어 하류 DDL까지
    가는 것을 지적으로 드러낸다.

    자연키로 승격시키지는 않는다. 이름이 `{표}_id`라고 해서 식별자라고 읽는 것은 이름에서
    의도를 읽는 것이고, 이 작업이 지운 바로 그 추론이다.
    """
    model = {
        "Classes": [_entity("Order", ["order_id : String", "at : Date"])],
        "Relationships": [],
    }
    issues = [f.as_issue() for f in erd_findings(model, {})]

    assert any("erd.surrogate-key-collides" in i for i in issues), issues


def test_the_collision_is_reported_under_the_name_the_model_actually_wrote():
    """대소문자만 다른 충돌에서 **모델이 적은 이름**을 말하는가.

    `orderId`와 `order_id`는 `squash` 기준으로 같은 이름이라 칸이 하나로 모인다. 그런데
    한동안 지적이 우리가 지은 이름(`order_id`)을 말했다 — 모델 안에 없는 이름이라
    고치라는 말을 듣고도 **어디를 고쳐야 할지 알 수가 없었다.** 재생성도 마찬가지다.
    """
    model = {
        "Classes": [_entity("Order", ["orderId : String", "at : Date"])],
        "Relationships": [],
    }
    issues = [f.as_issue() for f in erd_findings(model, {})]
    collisions = [i for i in issues if "erd.surrogate-key-collides" in i]

    assert collisions, issues
    assert "'orderId'" in collisions[0], collisions


# ---------------------------------------------------------------------------
# 컬럼
# ---------------------------------------------------------------------------
def test_an_untyped_field_stays_untyped():
    """타입을 지어내지 않는다 — 예전에는 `VARCHAR(255)`가 붙어 DDL까지 갔다."""
    logical = build_logical_model({
        "Classes": [_entity("Order", ["note"])],
        "Relationships": [],
    })

    assert _column(_table(logical, "Order"), "note")["type"] is None


@pytest.mark.parametrize(
    "java_type, sql",
    [
        ("int", "INT"),
        ("Integer", "INT"),
        ("long", "BIGINT"),
        ("BigDecimal", "DECIMAL(19,4)"),
        # 과거/외부 BCE에 남은 별칭도 Java 소수 타입과 같은 SQL 타입으로 읽는다.
        ("decimal", "DECIMAL(19,4)"),
    ],
)
def test_java_scalar_types_have_explicit_sql_mappings(java_type, sql):
    logical = build_logical_model({
        "Classes": [_entity("Value", [f"value : {java_type}"])],
        "Relationships": [],
    })

    assert _column(_table(logical, "Value"), "value")["type"] == sql


def test_a_multivalued_field_becomes_a_child_table():
    logical = build_logical_model({
        "Classes": [_entity("Loan", ["tags : List<String>"])],
        "Relationships": [],
    })
    child = _table(logical, "LoanTags")

    assert child["origin"] == {"kind": "multivalued", "table": "Loan", "field": "tags"}
    assert _column(child, "loan_id")["references"] == "Loan"
    assert _column(child, "tags_value")["type"] == "VARCHAR(255)"


def test_a_collection_of_entities_is_left_to_the_relationship():
    """원소가 Entity면 자식 테이블을 만들지 않는다 — 그건 관계가 말할 일이다."""
    logical = build_logical_model({
        "Classes": [_entity("Order", ["lines : List<OrderLine>"]), _entity("OrderLine", ["qty : Int"])],
        "Relationships": [],
    })

    assert "OrderLines" not in _names(logical)


def test_a_set_is_a_collection_too():
    """`Set<T>`도 다중값이다 — 한동안 `List`·`Array`만 읽었다.

    안 읽히면 컬럼 하나로 눌러앉아 제1정규화가 안 되고, `SET<STRING>`이라는 SQL 아닌
    타입이 그림과 하류 DDL로 나간다. 같은 함수를 `erd_identifier_fields`도 쓰므로
    "다중값은 키가 될 수 없다"는 지적까지 함께 사라진다.
    """
    logical = build_logical_model({
        "Classes": [_entity("Order", ["tags : Set<String>"])],
        "Relationships": [],
    })

    assert "OrderTags" in _names(logical)
    assert _column(_table(logical, "OrderTags"), "tags_value")["type"] == "VARCHAR(255)"


@pytest.mark.parametrize(
    "raw_type",
    ["Playlist", "Dataset", "Asset", "Ruleset", "Wishlist", "Checklist", "Offset",
     "Subset", "Setting", "Listing"],
)
def test_a_type_that_merely_contains_a_collection_word_is_not_a_collection(raw_type):
    """**부분 문자열로 보면 안 된다** — `Playlist`에 `list`가, `Dataset`에 `set`이 들어 있다.

    한동안 `"list" in lowered` 식이었고, 그래서 이 흔한 이름들이 전부 다중값으로 읽혔다.
    피해가 조용했다: 원소 타입을 못 읽으니 컬럼도 안 만들고 **모델에 없는 1NF 자식 표**를
    하나 만들어 냈다. `referenced_entity`도 `None`을 돌려주어
    `erd.entity-typed-field-needs-relationship`이 침묵했고,
    `erd.identifier-fields-exist`는 "다중값이라 키가 될 수 없다"는 **거짓 지적**을 냈다.
    """
    assert mapping_fields.is_collection(raw_type) is False


@pytest.mark.parametrize(
    "raw_type",
    ["List<String>", "Set<String>", "String[]", "HashSet<Long>", "TreeSet<X>",
     "ArrayList<Y>", "Collection<Z>", "Iterable<Q>", "java.util.List<String>", "List"],
)
def test_the_real_collection_notations_still_read_as_collections(raw_type):
    """좁히면서 **원래 읽던 것을 잃지 않았는가.** 구체 타입(`HashSet`)과 패키지 한정
    이름(`java.util.List`)까지 본다 — 못 읽으면 제1정규화가 통째로 안 걸린다.
    """
    assert mapping_fields.is_collection(raw_type) is True


def test_an_entity_named_like_a_collection_does_not_invent_a_table():
    """`Playlist`를 가리키는 스칼라 필드가 **유령 자식 표**를 만들던 자리.

    관계가 그 사실을 들고 가므로 `Member`에는 `playlist_id` 하나만 남아야 하고,
    `MemberFav` 같은 표는 모델 어디에도 근거가 없다.
    """
    logical = build_logical_model({
        "Classes": [_entity("Member", ["fav : Playlist"]), _entity("Playlist", ["title : String"])],
        "Relationships": [{"source": "Playlist", "target": "Member", "type": "Association",
                           "sourceMultiplicity": "1", "targetMultiplicity": "*"}],
    })

    assert _names(logical) == ["Member", "Playlist"]
    assert [c["name"] for c in _table(logical, "Member")["columns"]] == ["member_id", "playlist_id"]


def test_a_first_normal_form_child_cannot_exist_without_its_parent():
    """1NF 자식의 부모 외래키는 NOT NULL이다.

    부모의 다중값 필드를 떼어낸 표라 부모를 안 가리키는 행은 무엇의 값인지 말할 수 없다.
    한동안 nullable이었는데, 그리는 기호는 `||..o{`(자식마다 부모가 정확히 하나)여서
    **그림과 컬럼이 서로 다른 말을 했다.** 하류가 이 산출물로 DDL을 만드는 것이 요점이다.
    """
    logical = build_logical_model({
        "Classes": [_entity("Loan", ["tags : List<String>"])],
        "Relationships": [],
    })

    assert _column(_table(logical, "LoanTags"), "loan_id")["mandatory"] is True


def test_an_entity_typed_field_is_left_to_the_relationship():
    """스칼라 Entity 타입 필드도 컬럼이 아니다 — 컬렉션과 같은 규칙이다.

    한동안 컬렉션만 건너뛰고 스칼라는 안 건너뛰어서, 관계에서 나온 `member_id` 옆에
    `member : MEMBER`가 하나 더 생겼다. 같은 사실이 두 칸에 있었고 `MEMBER`는 SQL
    타입도 아닌데 하류 DDL까지 갔다. 지적도 0건이었다 —
    `erd.field-looks-like-reference`는 관계가 있으면 일부러 침묵하기 때문이다.
    """
    model = {
        "Classes": [_entity("Order", ["member : Member"]), _entity("Member", ["email : String"])],
        "Relationships": [{"source": "Member", "target": "Order", "type": "Association",
                           "sourceMultiplicity": "1", "targetMultiplicity": "*"}],
    }
    order = _table(build_logical_model(model), "Order")

    assert [c["name"] for c in order["columns"]] == ["order_id", "member_id"]
    # 관계가 그 사실을 들고 가므로 고칠 것이 없다 — 지적하면 예산만 태운다.
    assert not [f for f in erd_findings(model, {})
                if "entity-typed-field" in f.as_issue()]


def test_an_entity_typed_field_without_a_relationship_is_surfaced():
    """관계가 없으면 **드러난다.** 사상이 칸을 안 만들므로 여기가 유일한 자리다.

    예전에는 `List<Entity>`가 관계 없이도 조용히 사라졌다 — 칸도 자식 표도 관계선도
    없고 `Unmapped`에도 안 들어갔다. 모델이 적은 링크가 산출물 어디에도 안 남는데
    아무도 그것을 못 봤다.
    """
    model = {
        "Classes": [_entity("Order", ["lines : List<OrderLine>"]), _entity("OrderLine", ["qty : Int"])],
        "Relationships": [],
    }
    issues = [f.as_issue() for f in erd_findings(model, {})]

    assert any("erd.entity-typed-field-needs-relationship" in i for i in issues), issues


def test_an_inherited_composite_natural_key_stays_composite():
    """복합 자연키는 **함께** 유일하다 — 칸마다 걸면 모델에 없던 제약이 생긴다.

    상속이 기본키 자리를 가져갈 때 자연키 칸은 남는다(유일성도 남는다). 그런데 칸마다
    `unique`를 걸면 `UNIQUE(a,b)`가 `UNIQUE(a) AND UNIQUE(b)`가 되어, 원래 허용되던
    행(`("x","1")`과 `("x","2")`)을 스키마가 거부한다. 우리가 조용히 좁힌 것이다.
    """
    logical = build_logical_model({
        "Classes": [_entity("Base", ["x : Int"]),
                    _entity("Sub", ["a : String", "b : String"], identifier=["a", "b"])],
        "Relationships": [{"source": "Sub", "target": "Base", "type": "Inheritance"}],
    })
    sub = _table(logical, "Sub")

    assert sub["uniqueTogether"] == [["a", "b"]]
    assert not any(c["unique"] for c in sub["columns"])


def test_an_inherited_composite_key_keeps_the_parents_column_order():
    """물려받은 복합키의 **순서가 부모와 같아야** 한다.

    한동안 자식이 `(b, a)`였다 — 외래키를 하나씩 `insert(0, …)` 로 앞에 밀어 넣어서
    순서가 뒤집혔다. 외래키 자체는 칸마다 `referencesColumn`을 들고 있어 어긋나지
    않지만, **그림이 부모와 자식을 다르게 보여주고 하류는 그 텍스트로 DDL을 만든다.**
    """
    logical = build_logical_model({
        "Classes": [_entity("Base", ["a : String", "b : String", "x : Int"], identifier=["a", "b"]),
                    _entity("Sub", ["y : Int"])],
        "Relationships": [{"source": "Sub", "target": "Base", "type": "Inheritance"}],
    })

    assert _table(logical, "Base")["primaryKey"] == ["a", "b"]
    assert _table(logical, "Sub")["primaryKey"] == ["a", "b"]


def test_a_single_inherited_natural_key_still_uses_the_column_flag():
    """한 칸짜리는 그대로 컬럼의 `unique`다 — 그림이 안 바뀐다."""
    logical = build_logical_model({
        "Classes": [_entity("Base", ["x : Int"]),
                    _entity("Sub", ["code : String"], identifier=["code"])],
        "Relationships": [{"source": "Sub", "target": "Base", "type": "Inheritance"}],
    })
    sub = _table(logical, "Sub")

    assert sub["uniqueTogether"] == []
    assert _column(sub, "code")["unique"] is True


def test_a_second_many_to_many_between_the_same_pair_is_surfaced_not_duplicated():
    """연결 표 이름이 같아지는 둘째 다대다는 만들지 않고 드러낸다.

    만들면 완전히 똑같은 표가 둘 생긴다. `erd.table-names-unique`가 지적하기는 했지만
    **연결 표 이름은 사상이 짓는 것이라 모델이 고칠 수가 없었다** — 고칠 수 없는 지적은
    재생성이 위반 수를 못 줄여 수정본을 통째로 버리게 만든다. 무엇을 하면 되는지
    말할 수 있는 사유로 바꾼다.
    """
    logical = build_logical_model({
        "Classes": [_entity("Member", ["n : String"]), _entity("Book", ["t : String"])],
        "Relationships": [{"source": "Member", "target": "Book", "type": "Association",
                           "sourceMultiplicity": "*", "targetMultiplicity": "*"},
                          {"source": "Member", "target": "Book", "type": "Association",
                           "sourceMultiplicity": "*", "targetMultiplicity": "*"}],
    })

    assert _names(logical).count("MemberBook") == 1
    assert logical["Unmapped"][0]["reason"] == "duplicate-junction"


def test_duplicate_relationship_does_not_create_a_second_named_foreign_key():
    relationship = {
        "source": "Member", "target": "Order", "type": "Association",
        "sourceMultiplicity": "1", "targetMultiplicity": "*",
    }
    logical = build_logical_model({
        "Classes": [_entity("Member", ["name : String"]), _entity("Order", ["number : String"])],
        "Relationships": [relationship, dict(relationship)],
    })

    order = _table(logical, "Order")
    assert [column["name"] for column in order["columns"]] == [
        "order_id", "number", "member_id",
    ]
    assert logical["Unmapped"] == [{
        "source": "Member", "target": "Order", "reason": "duplicate-relationship",
    }]


def test_required_reference_that_closes_a_cycle_is_left_for_bce_repair():
    logical = build_logical_model({
        "Classes": [_entity("Member", ["name : String"]), _entity("Order", ["number : String"])],
        "Relationships": [
            {"source": "Member", "target": "Order", "type": "Association",
             "sourceMultiplicity": "1", "targetMultiplicity": "*"},
            {"source": "Order", "target": "Member", "type": "Association",
             "sourceMultiplicity": "1", "targetMultiplicity": "*"},
        ],
    })

    assert logical["Unmapped"] == [{
        "source": "Order", "target": "Member", "reason": "mandatory-reference-cycle",
    }]
    assert not any(
        column.get("references") == "Order"
        for column in _table(logical, "Member")["columns"]
    )


def test_inheritance_ordering_terminates_when_a_cycle_is_reachable():
    """순환 **밖에서 안으로** 들어가는 상속에서 순서잡기가 안 끝나던 자리.

    `A↔B`는 순환이라 거절되지만 `C→A`는 자기 자신을 다시 만나지 않아 사상 대상으로
    남는다. 그 조상 사슬은 `A → B → A → …`로 끝이 없어서, 깊이를 재는 while이 방문
    표시 없이 돌면 안 돌아온다. 순수 함수라 타임아웃이 없어 `check_erd` 노드가 통째로
    멈췄고, `_TANGLED`의 `inheritance-cycle`은 A·B 둘뿐이라 이 코드를 아예 안 밟았다.

    `C→A`를 **거절하지 않는 것도 함께 고정한다** — 순환은 `A↔B` 쪽에서 이미 지적되고,
    거기 딸린 상속까지 싸잡으면 실수 하나가 지적 여럿이 되어 재생성이 막힌다.
    """
    mappable, rejected = order_for_mapping([
        {"source": "A", "target": "B", "type": "Inheritance"},
        {"source": "B", "target": "A", "type": "Inheritance"},
        {"source": "C", "target": "A", "type": "Inheritance"},
    ])

    assert [(r["source"], r["target"]) for r in mappable] == [("C", "A")]
    assert {reason for _, reason in rejected} == {"inheritance-cycle"}


def test_a_name_collision_is_kept_visible_not_renamed_away():
    """겹친 이름을 조용히 고치면 없던 테이블이 생기고, 버리면 있던 것이 사라진다.

    사상은 둘 다 그대로 두고, 검사기가 지적한다(`erd.table-names-unique`).
    """
    logical = build_logical_model({
        "Classes": [_entity("Order", ["a : Int"]), _entity("Order", ["b : Int"])],
        "Relationships": [],
    })

    assert _names(logical) == ["Order", "Order"]


# ---------------------------------------------------------------------------
# 불변식 — 규칙이 아니라 우리 코드가 지켜야 하는 것
# ---------------------------------------------------------------------------
# **이 절은 두 번 약했다.** 그래서 이번에는 검사를 흩어 두지 않고 한 함수로 모은다.
#
#   1차: "외래키가 실재 *테이블*을 가리키나"만 봤다 → 상속이 키를 바꾸면서 생긴
#        컬럼 수준 결함 둘을 놓쳤다(테이블은 멀쩡했으니까).
#   2차: "기본키가 **비어 있지 않나**"만 봤다 → `primaryKey`가 실재하지 않는 컬럼
#        이름을 담은 것을 놓쳤다(비어 있지는 않았으니까).
#
# 매번 한 겹씩 얕았고, 매번 "통과했다"가 근거 없는 안심이었다. 검사가 흩어져 있으면
# 어느 겹이 비었는지 아무도 못 본다.
def assert_sound(logical: dict) -> None:
    """논리 데이터 모델이 **언제나** 만족해야 하는 것 전부.

    규칙(`knowledge/rules.py`)이 아니라 불변식이다. 규칙은 모델이 지킬 일이고, 이것은
    우리 사상이 지킬 일이다. 모델이 아무리 이상해도 여기가 깨지면 그건 우리 결함이다.
    """
    tables = logical["Tables"]
    by_name = {t["name"]: t for t in tables}

    for table in tables:
        columns = [c["name"] for c in table["columns"]]

        # ① 컬럼 이름이 표 안에서 유일하다.
        assert len(columns) == len(set(columns)), f"{table['name']}: 같은 칸이 둘 {columns}"

        # ② 기본키가 있고, 그 이름들이 **실재 컬럼**이다.
        assert table["primaryKey"], f"{table['name']}: 기본키가 없다"
        for key in table["primaryKey"]:
            assert key in columns, (
                f"{table['name']}: 기본키 '{key}'가 실재 칸이 아니다 (칸: {columns})"
            )

        # ③ 외래키가 실재 표의 **기본키**를 가리킨다.
        #    컬럼 이름이 아니라 `referencesColumn`으로 되짚는다 — 외래키 컬럼의 이름은
        #    참조 대상의 이름과 다를 수 있다(`Copy.book_isbn` → `Book.isbn`).
        for column in table["columns"]:
            referenced = column["references"]
            if not referenced:
                continue
            assert referenced in by_name, (
                f"{table['name']}.{column['name']} → 없는 표 '{referenced}'"
            )
            target = by_name[referenced]
            assert column["referencesColumn"] in target["primaryKey"], (
                f"{table['name']}.{column['name']} → "
                f"{referenced}.{column['referencesColumn']} 는 기본키가 아니다 "
                f"(기본키: {target['primaryKey']})"
            )

    # ④ 그려질 선의 양 끝이 실재 표다. 표는 사라졌는데 선만 남으면 PlantUML이 그 이름으로
    #    **빈 표를 하나 만들어 준다** — 클래스 다이어그램에서 실측한 그 동작과 같은 함정이다.
    for relation in logical["Relations"]:
        for end in ("source", "target"):
            assert relation[end] in by_name, f"선 {relation}: '{relation[end]}' 표가 없다"

    # ⑤ **선에는 뒷받침이 있다.** 양 끝이 실재하는 것만으로는 부족하다 — 그 선이 말하는
    #    관계를 실제로 만드는 칸이 있어야 한다. 상속선인데 자식이 부모를 가리키는 외래키를
    #    안 갖고 있으면, 그림은 상속을 말하는데 스키마에는 아무 연결도 없다.
    #
    #    다중 상속에서 실제로 그랬다: 두 번째 상속이 첫 번째가 넣은 외래키를 덮어 버리는데
    #    **선은 둘 다 남아서**, 뒷받침 없는 선이 하나 생겼다. 양 끝 표는 멀쩡하니 ④는
    #    통과했다.
    for relation in logical["Relations"]:
        if relation.get("kind") != "inheritance":
            continue
        parent, child = relation["source"], by_name[relation["target"]]
        assert child["keyOrigin"] == "inherited", (
            f"상속선 {parent} → {child['name']}: 자식의 keyOrigin이 "
            f"'{child['keyOrigin']}'이라 상속이 실제로는 안 걸렸다"
        )
        assert any(c["references"] == parent for c in child["columns"]), (
            f"상속선 {parent} → {child['name']}: 자식에 {parent}를 가리키는 외래키가 없다 "
            f"— 뒷받침 없는 선이다"
        )


def assert_no_declared_field_is_lost(model: dict, logical: dict) -> None:
    """모델이 적은 필드가 **어딘가의 칸으로 살아남는가.**

    논리 모델만 봐서는 못 묻는 질문이라 원본 BCE를 함께 받는다. 그래서 `assert_sound`와
    따로 있다.

    상속이 자식의 선언된 자연키 컬럼을 **삭제하던** 것을 잡으려고 세웠다. 대리키를
    걷어내는 코드가 `role == "pk"`로 지웠는데, 자연키는 우리가 붙인 것이 아니라 모델이
    적은 진짜 속성이다. 기본키 자리를 내주는 것과 칸이 사라지는 것은 전혀 다른 일이고,
    사라지면 하류 스키마에서도 사라진다.

    다중값 필드는 뺀다 — 제1정규화로 자식 표에 가는 것이 정상이다(자식 표에 값 칸으로
    남아 있는지는 `test_a_multivalued_field_becomes_a_child_table`이 본다).

    **Entity 타입 필드도 뺀다** — 이건 계약을 넓힌 것이라 이유를 남긴다. `member : Member`는
    칸이 아니라 관계다(사상표를 볼 것). 예전에는 이것도 칸으로 만들었는데, 관계에서 나온
    `member_id` 옆에 `member : MEMBER`가 하나 더 생겨 **같은 사실이 두 칸에 있었고**
    `MEMBER`는 SQL 타입도 아닌 채로 하류 DDL까지 갔다.

    빼도 "조용히 사라지는" 자리가 안 생긴다는 것이 중요하다: 관계가 있으면 그 관계가
    사실을 들고 가고, 없으면 `erd.entity-typed-field-needs-relationship`이 지적한다.
    이 단언이 막으려던 것은 **아무 데도 안 남고 아무도 모르는 것**이지 칸 자체가 아니다.
    """
    from app.design.services.common import fields

    entity_names = [
        c.get("className") for c in model.get("Classes") or [] if fields.is_entity(c)
    ]
    surviving = {c["name"] for t in logical["Tables"] for c in t["columns"]}
    for class_item in model.get("Classes") or []:
        if not fields.is_entity(class_item):
            continue
        for raw in class_item.get("fields") or []:
            name, raw_type = fields.split_field(raw)
            if not name or fields.is_collection(raw_type):
                continue
            if fields.referenced_entity(raw_type, entity_names):
                continue
            assert fields.squash(name) in {fields.squash(s) for s in surviving}, (
                f"{class_item.get('className')}.{name} 이 사라졌다 — 남은 칸: {sorted(surviving)}"
            )


#: 사상을 꼬이게 만드는 배치들. 상속이 **키를 바꾸므로** 상속과 얽힌 것이 특히 위험하고,
#: `identifier`는 **기본키를 통째로 갈아 끼우므로** 그다음으로 위험하다.
_TANGLED = {
    "inheritance-chain": {
        "Classes": [_entity("A", ["a : Int"]), _entity("B", ["b : Int"]), _entity("C", ["c : Int"])],
        # **입력 순서가 자식부터다.** 부모의 키가 아직 안 정해졌을 때 자식이 그것을 가져간다.
        "Relationships": [{"source": "C", "target": "B", "type": "Inheritance"},
                          {"source": "B", "target": "A", "type": "Inheritance"}],
    },
    "inheritance-plus-multivalued": {
        "Classes": [_entity("Base", ["x : Int"]), _entity("Sub", ["tags : List<String>"])],
        "Relationships": [{"source": "Sub", "target": "Base", "type": "Inheritance"}],
    },
    "inheritance-plus-association": {
        "Classes": [_entity("Base", ["x : Int"]), _entity("Sub", ["y : Int"]),
                    _entity("Note", ["z : Int"])],
        "Relationships": [{"source": "Sub", "target": "Base", "type": "Inheritance"},
                          {"source": "Sub", "target": "Note", "type": "Association",
                           "sourceMultiplicity": "1", "targetMultiplicity": "*"}],
    },
    "self-many-to-many": {
        "Classes": [_entity("Person", ["name : String"])],
        "Relationships": [{"source": "Person", "target": "Person", "type": "Association",
                           "sourceMultiplicity": "*", "targetMultiplicity": "*"}],
    },
    # --- `identifier`가 기본키를 갈아 끼우는 자리들 --------------------------
    "identifier-names-a-collection": {
        # 다중값 필드는 1NF로 자식 표에 가므로 **부모 표에 칸이 안 남는다.** 그것을
        # 자연키로 쓰면 기본키가 유령 이름이 된다.
        "Classes": [_entity("Order", ["tags : List<String>", "at : Date"], identifier=["tags"])],
        "Relationships": [],
    },
    "identifier-repeated": {
        "Classes": [_entity("Book", ["isbn : String"], identifier=["isbn", "isbn"])],
        "Relationships": [],
    },
    "declared-field-collides-with-surrogate": {
        "Classes": [_entity("Order", ["order_id : String", "at : Date"])],
        "Relationships": [],
    },
    "declared-field-collides-with-surrogate-by-case-only": {
        # 이름이 **똑같지는 않고** `squash` 기준으로만 같다. 칸은 하나로 모이는데,
        # 위의 것과 달리 살아남는 이름(`order_id`)이 모델이 적은 이름(`orderId`)과
        # 다르다 — 그래서 "선언한 것이 사라지지 않았나"를 글자 그대로 세면 걸린다.
        "Classes": [_entity("Order", ["orderId : String", "at : Date"])],
        "Relationships": [],
    },
    "type-named-like-a-collection": {
        # `Playlist`에 `list`가 들어 있다. 부분 문자열로 보면 다중값이 되어 모델에 없는
        # 1NF 자식 표가 생기고, 그 표의 값 칸은 타입이 `None`이다.
        "Classes": [_entity("Member", ["fav : Playlist", "tags : Set<String>"]),
                    _entity("Playlist", ["title : String"])],
        "Relationships": [{"source": "Playlist", "target": "Member", "type": "Association",
                           "sourceMultiplicity": "1", "targetMultiplicity": "*"}],
    },
    "two-fields-that-squash-to-the-same-name": {
        # `member_id`와 `memberId`가 `squash` 기준으로 같다. 후보를 집합에 담으면
        # 어느 쪽이 자연키가 되는지가 **프로세스 해시 시드에 달린다.**
        "Classes": [_entity("Book", ["member_id : String", "memberId : Int"],
                            identifier=["memberid"])],
        "Relationships": [],
    },
    "identifier-referenced-by-someone-else": {
        # 자연키를 가진 표를 다른 표가 가리킨다 — 외래키가 그 자연키의 모양을 따라야 한다.
        "Classes": [_entity("Book", ["isbn : String"], identifier=["isbn"]),
                    _entity("Copy", ["status : String"])],
        "Relationships": [{"source": "Book", "target": "Copy", "type": "Composition",
                           "sourceMultiplicity": "1", "targetMultiplicity": "1..*"}],
    },
    # --- 상속이 이미 만든 표를 뜯어고치는 자리들 ----------------------------
    # **이 사상에서 상속만 그렇게 한다.** 나머지는 칸을 더하기만 하는데, 상속은 자식의
    # 기본키를 갈아 끼운다. 세 번 연속 결함이 여기서 나왔다.
    "inheritance-over-a-natural-key": {
        # 자식이 자기 자연키를 선언했는데 상속도 한다. 기본키 자리는 부모가 가져가지만
        # **선언된 칸까지 사라지면 안 된다.**
        "Classes": [_entity("Base", ["x : Int"]),
                    _entity("Sub", ["code : String"], identifier=["code"])],
        "Relationships": [{"source": "Sub", "target": "Base", "type": "Inheritance"}],
    },
    "multiple-inheritance": {
        "Classes": [_entity("P1", ["a : Int"]), _entity("P2", ["b : Int"]),
                    _entity("C", ["c : Int"])],
        "Relationships": [{"source": "C", "target": "P1", "type": "Inheritance"},
                          {"source": "C", "target": "P2", "type": "Inheritance"}],
    },
    "inheritance-cycle": {
        "Classes": [_entity("A", ["a : Int"]), _entity("B", ["b : Int"])],
        "Relationships": [{"source": "A", "target": "B", "type": "Inheritance"},
                          {"source": "B", "target": "A", "type": "Inheritance"}],
    },
    # --- 순환 **밖에서 안으로** 들어가는 상속 --------------------------------
    # 위의 `inheritance-cycle`은 A·B 둘뿐이라 둘 다 거절되고 `keep`이 빈다. 그래서
    # **깊이를 재는 코드가 아예 안 돌았고**, 거기 있던 무한 루프가 여덟 달을 살아남았다.
    # 아래 둘은 순환에 걸리지 않는 상속을 하나 남겨 그 코드를 실제로 돌게 만든다.
    #
    # `C→A`는 자기 자신을 다시 만나지 않으므로 순환 판정을 통과해 사상된다. 그 조상
    # 사슬(`A → B → A → …`)은 끝이 없어서, 방문 표시가 없으면 여기서 안 돌아온다.
    # 순수 함수라 타임아웃도 없어 `check_erd` 노드가 통째로 멈췄다.
    "cycle-with-outside-child": {
        "Classes": [_entity("A", ["a : Int"]), _entity("B", ["b : Int"]),
                    _entity("C", ["c : Int"])],
        "Relationships": [{"source": "A", "target": "B", "type": "Inheritance"},
                          {"source": "B", "target": "A", "type": "Inheritance"},
                          {"source": "C", "target": "A", "type": "Inheritance"}],
    },
    "self-inheritance-with-child": {
        # 길이 1짜리 순환으로도 같은 일이 난다.
        "Classes": [_entity("A", ["a : Int"]), _entity("C", ["c : Int"])],
        "Relationships": [{"source": "A", "target": "A", "type": "Inheritance"},
                          {"source": "C", "target": "A", "type": "Inheritance"}],
    },
    # --- 이번에 드러난 나머지 자리들 ----------------------------------------
    "composite-natural-key-inherited": {
        # 복합 자연키가 상속으로 기본키 자리를 내준다. 칸마다 `unique`를 걸면
        # `UNIQUE(a,b)`가 `UNIQUE(a) AND UNIQUE(b)`가 되어 모델에 없던 제약이 생긴다.
        "Classes": [_entity("Base", ["x : Int"]),
                    _entity("Sub", ["a : String", "b : String"], identifier=["a", "b"])],
        "Relationships": [{"source": "Sub", "target": "Base", "type": "Inheritance"}],
    },
    "entity-typed-fields": {
        # 스칼라·컬렉션 둘 다 Entity 타입이다. 어느 쪽도 칸이 되면 안 되고,
        # `identifier`가 그런 필드를 지목해도 유령 기본키가 나오면 안 된다.
        "Classes": [_entity("Order", ["member : Member", "lines : Set<OrderLine>"],
                            identifier=["member"]),
                    _entity("Member", ["email : String"]),
                    _entity("OrderLine", ["qty : Int"])],
        "Relationships": [{"source": "Member", "target": "Order", "type": "Association",
                           "sourceMultiplicity": "1", "targetMultiplicity": "*"}],
    },
    "duplicate-junction": {
        # 같은 두 표를 잇는 다대다가 둘. 이름이 같은 연결 표가 둘 생기면 안 된다.
        "Classes": [_entity("Member", ["n : String"]), _entity("Book", ["t : String"])],
        "Relationships": [{"source": "Member", "target": "Book", "type": "Association",
                           "sourceMultiplicity": "*", "targetMultiplicity": "*"},
                          {"source": "Member", "target": "Book", "type": "Association",
                           "sourceMultiplicity": "*", "targetMultiplicity": "*"}],
    },
    "inheriting-child-is-referenced": {
        # 상속으로 키가 바뀐 자식을 제3의 표가 가리킨다 — 바뀐 뒤의 키를 봐야 한다.
        "Classes": [_entity("Base", ["x : Int"]), _entity("Sub", ["y : Int"]),
                    _entity("Note", ["z : Int"])],
        "Relationships": [{"source": "Sub", "target": "Base", "type": "Inheritance"},
                          {"source": "Sub", "target": "Note", "type": "Association",
                           "sourceMultiplicity": "1", "targetMultiplicity": "*"}],
    },
}

_ALL_MODELS = {
    "today": LIBRARY_BCE_TODAY,
    "legacy": LIBRARY_BCE_LEGACY,
    "empty": {},
    "none": {"Classes": [], "Relationships": []},
    **_TANGLED,
}


#: 사상이 **안 끝나는** 것과 틀린 답을 내는 것은 다른 실패다. 틀린 답은 단언이 잡지만
#: 안 끝나는 것은 아무 단언에도 안 걸리고 CI를 통째로 매단다 — 실제로 그랬다.
#: 여유 있게 걸어 둔다. 이 사상은 밀리초 단위라 30초는 "느리다"가 아니라 "안 끝난다"다.
_MUST_TERMINATE = pytest.mark.timeout(30)


@_MUST_TERMINATE
@pytest.mark.parametrize("model", _ALL_MODELS.values(), ids=list(_ALL_MODELS))
def test_the_mapping_is_sound(model):
    """어떤 입력에도 사상이 스스로 모순되지 않는가.

    모델이 이상한 것과 우리 사상이 깨진 것은 다른 일이다. 모델이 이상하면 검사기가
    지적하면 되지만, 여기가 깨지면 **아무도 못 믿을 산출물**이 나간다.
    """
    assert_sound(build_logical_model(model))


@_MUST_TERMINATE
@pytest.mark.parametrize("model", _ALL_MODELS.values(), ids=list(_ALL_MODELS))
def test_no_declared_field_is_lost(model):
    """모델이 적은 것이 산출물에서 사라지지 않는가.

    사상이 못 옮기는 것은 있을 수 있고(다중도 없는 관계), 그건 `Unmapped`로 드러난다.
    그러나 **칸이 조용히 없어지는 것**은 다르다 — 드러날 자리가 아무 데도 없다.
    """
    assert_no_declared_field_is_lost(model, build_logical_model(model))


@_MUST_TERMINATE
@pytest.mark.parametrize("model", _ALL_MODELS.values(), ids=list(_ALL_MODELS))
def test_the_mapping_does_not_depend_on_the_process_hash_seed(model):
    """**순수 함수라는 말이 사실인가.** 같은 모델은 언제 돌려도 같은 산출물을 내야 한다.

    한동안 아니었다. 기본키 후보를 **집합**에 담고 `next()`로 훑었는데, 문자열 집합의
    순회 순서는 프로세스 해시 시드에 달려 있다. `member_id`와 `memberId`를 둘 다 선언한
    모델은 실행마다 기본키가 달라졌다.

    조용히 새는 결함이었다. `artifact_repository`가 **저장된 모델에서 매 로드마다 다시
    그리므로**, 아무도 모델을 안 고쳤는데 그림이 바뀔 수 있었다. 단언 하나에도 안 걸리고
    (한 번 돌리면 언제나 자기 자신과 같으니까) 눈금도 없었다.

    하위 프로세스로 도는 이유가 그것이다 — 해시 시드는 프로세스가 시작할 때 정해져서
    같은 인터프리터 안에서는 이 결함을 **원리상 볼 수 없다.**
    """
    script = (
        "import json,sys;"
        "from app.design.services.erd.mapping import build_logical_model;"
        "print(json.dumps(build_logical_model(json.loads(sys.argv[1])),"
        " ensure_ascii=False, sort_keys=True))"
    )
    payload = json.dumps(model, ensure_ascii=False)

    seen = set()
    for seed in ("0", "1", "4", "7"):
        result = subprocess.run(
            [sys.executable, "-c", script, payload],
            capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, "PYTHONHASHSEED": seed},
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert result.returncode == 0, result.stderr
        seen.add(result.stdout)

    assert len(seen) == 1, f"해시 시드마다 다른 산출물이 {len(seen)}가지 나왔다"
