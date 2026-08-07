"""BCE 모델 → **논리 데이터 모델.** 개념 모델을 관계형으로 옮긴다.

렌더러는 이 dict를 그리기만 하고 검사기는 판정만 한다. **사상 결정은 전부 여기 있다** —
흩어져 있으면 아무도 그것이 결정인 줄 모른다.

## 사상 규칙 — 표준이고 우리가 발명한 것이 아니다

| 입력 | 결과 |
|---|---|
| Entity 클래스 | 표 |
| 1 : 다 | **다**쪽에 외래키 |
| 다 : 다 | **연결 표** — 두 외래키가 복합 기본키 |
| 1 : 1 | 한쪽에 외래키 + 유일 제약 |
| 합성 | 식별 관계 — 그림에서 실선 |
| 다중값 필드(`List<T>`·`Set<T>`) | 자식 표 (제1정규화) |
| Entity 타입 필드(`member : Member`) | **컬럼이 아니다** — 관계가 들고 간다 |
| 상속 | 자식 표의 기본키가 곧 부모를 가리키는 외래키 |

외래키의 **널 허용은 가리켜지는 쪽 끝의 다중도**가 정한다(하한 1 이상이면 NOT NULL).
관계의 종류가 아니다 — 종류는 선의 모양만 정한다.

## 지켜야 할 것 넷

  - **우리가 고른 것은 고른 것이라고 적는다.** 상속 전략(클래스별 테이블)과 대리키가
    그렇다. 대리키는 `keyOrigin`에 남긴다 — 안 남기면 우리가 정한 키가 요구사항에서 온
    것처럼 읽힌다.
  - **옮길 수 없으면 옮기지 않는다.** `Unmapped`에 사유와 함께 넣고 검사기가 말한다.
    이름으로 추측하지도 않는다(`rules.py`의 `erd.fk-from-field-name`).
  - **상속은 이미 만든 표를 뜯어고치는 유일한 연산이다.** 자식의 기본키를 갈아 끼우므로
    자식을 가리키는 외래키와 제1정규화 자식 표는 전부 그 뒤에 만든다. 고치기 전에
    `tests/test_erd_mapping.py`의 `_TANGLED`를 볼 것.
  - **알려진 한계**: 같은 두 표를 잇는 관계가 둘이면 역할 이름이 없다(구매자·수령인이
    `member_id`·`related_member_id`가 된다). `description`을 컬럼 이름으로 옮기는 규칙을
    세우기 전에는 지어내는 것이 된다. 그 둘이 **다 : 다**이면 연결 표 이름까지 같아지는데,
    그때는 이름을 지어내는 대신 둘째를 `Unmapped`에 넣는다(`UNMAPPED_DUPLICATE_JUNCTION`).
"""
from __future__ import annotations

from typing import Any

from app.design.services.common import fields, multiplicity
from app.design.services.erd.inheritance import order_for_mapping

# `Unmapped` 사유는 이 모듈 출력의 어휘라 소비자가 한 곳에서 다 가져가야 한다. 상속 쪽
# 둘만 다른 모듈에 살므로 다시 내보낸다.
from app.design.services.erd.inheritance import (  # noqa: F401
    UNMAPPED_INHERITANCE_CYCLE,
    UNMAPPED_MULTIPLE_INHERITANCE,
)

#: 기본키·외래키에 쓰는 정수 타입.
_KEY_TYPE = "BIGINT"

#: 구조적 연관으로 취급하는 종류. `Dependency`는 행위 링크라 여기 없다.
STRUCTURAL_TYPES = ("Association", "Aggregation", "Composition")

#: 기본키가 **어디서 왔는가.** 셋을 구별하는 것이 요점이다.
KEY_NATURAL = "natural"      # 모델이 `identifier`로 지목했다
KEY_SURROGATE = "surrogate"  # 우리가 붙였다
KEY_INHERITED = "inherited"  # 부모에게서 물려받았다

#: `Unmapped`의 사유. 검사기가 이걸로 갈라 보므로 문자열을 그때그때 적지 않는다.
UNMAPPED_MULTIPLICITY = "multiplicity-missing"
UNMAPPED_DEPENDENCY = "dependency-between-entities"
#: 같은 두 표를 잇는 다 : 다가 둘 이상이다. 연결 표 이름이 같아져 표가 통째로 겹친다.
UNMAPPED_DUPLICATE_JUNCTION = "duplicate-junction"


def _column(
    name: str,
    type_: str | None,
    role: str = "attribute",
    references: str | None = None,
    references_column: str | None = None,
    unique: bool = False,
    mandatory: bool = False,
) -> dict[str, Any]:
    """컬럼 하나. 참조는 **표와 칸을 둘 다** 적는다 — 외래키 이름이 참조 대상과 다를 수
    있어서(`Copy.book_isbn` → `Book.isbn`) 이름으로 되짚는 것이 성립하지 않는다.
    """
    return {
        "name": name,
        "type": type_,
        "role": role,
        "references": references,
        "referencesColumn": references_column,
        "unique": unique,
        "mandatory": mandatory,
    }


def _table(name: str, origin: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "primaryKey": [],
        "keyOrigin": KEY_SURROGATE,
        "columns": [],
        # 여러 칸이 **함께** 유일한 것. 컬럼의 `unique`는 불리언 하나라 한 칸의 유일성밖에
        # 못 담는데, 상속이 복합 자연키의 기본키 자리를 뺏을 때 담을 곳이 필요하다.
        "uniqueTogether": [],
        "origin": origin,
    }


def _column_names(table: dict) -> set[str]:
    return {c["name"] for c in table["columns"]}


def _add_column(table: dict, column: dict) -> None:
    """**이름이 겹치면 안 붙인다.** 같은 칸이 두 줄 나오면 한 칸인지 두 칸인지 알 수 없다.
    앞의 것이 이긴다 — 대개 기본키이거나 모델이 직접 적은 필드다.
    """
    if column["name"] not in _column_names(table):
        table["columns"].append(column)


# ---------------------------------------------------------------------------
# 1) 클래스 → 테이블
# ---------------------------------------------------------------------------
def _build_tables(classes: list[dict]) -> tuple[list[dict], dict[str, dict], list[dict]]:
    """Entity마다 표 하나 → (만든 표 전부, 이름으로 찾는 표, 제1정규화 자식들).

    **둘을 함께 돌려주는 이유가 이름 충돌이다.** 겹치면 조회용 표에는 하나만 남지만
    목록에는 둘 다 남는다 — 버리거나 고쳐 주면 표가 사라진 것을 아무도 못 보므로 그대로
    두고 `erd.table-names-unique`가 말한다. 끝점은 조회용 표에서 찾아 먼저 선언된 쪽이 이긴다.
    """
    made: list[dict] = []
    tables: dict[str, dict] = {}
    children: list[dict] = []

    for class_item in classes:
        if not fields.is_entity(class_item):
            continue
        name = fields.sanitize_entity_name(class_item.get("className", ""))
        table = _table(name, {"kind": "class", "className": class_item.get("className", "")})
        table["_class"] = class_item
        made.append(table)
        tables.setdefault(name, table)

    entity_names = {t["_class"].get("className", "") for t in made}

    for table in made:
        class_item = table["_class"]
        declared = [
            fields.split_field(f)
            for f in class_item.get("fields", [])
            if fields.sanitize_text(f)
        ]

        # --- 기본키 -------------------------------------------------------
        # **칸이 안 남는 필드는 후보가 아니다.** 다중값은 자식 표로 가고, Entity 타입은
        # 관계가 들고 간다. 후보로 두면 `primaryKey`가 실재하지 않는 칸 이름을 담는다
        # (`assert_sound` ②가 그걸 잡는다). 못 쓰는 이유는 `erd.identifier-fields-exist`가 말한다.
        # **선언 순서를 지키는 목록이다. 집합이면 안 된다** — 아래에서 `next()`로 훑어
        # 먼저 걸린 것을 쓰는데, 문자열 집합의 순회 순서는 프로세스 해시 시드에 달려
        # 있다. 한동안 집합이었고, 그래서 `member_id`와 `memberId`를 둘 다 선언한 모델의
        # 기본키가 **실행마다 달라졌다.** 순수 함수라던 것이 실은 아니었고, 저장된 모델에서
        # 매 로드마다 다시 그리므로 모델을 안 고쳐도 그림이 바뀔 수 있었다.
        # 목록이면 "먼저 선언된 것이 이긴다"로 확정된다.
        keyable = [
            n
            for n, raw in declared
            if n and not fields.is_collection(raw) and not fields.names_an_entity(raw, entity_names)
        ]
        identifier = [
            str(i).strip() for i in (class_item.get("identifier") or []) if str(i).strip()
        ]
        # **짝을 지어 본다** — 개수로 비교하면 중복(`["isbn","isbn"]`)에서 키를 버린다.
        matched = {
            i: next((n for n in keyable if fields.squash(n) == fields.squash(i)), None)
            for i in identifier
        }
        natural = list(dict.fromkeys(n for n in matched.values() if n))
        if identifier and all(matched.values()):
            table["keyOrigin"] = KEY_NATURAL
            table["primaryKey"] = natural
        else:
            # `identifier`가 비었거나 쓸 수 없다 — 뒤엣것은 `erd.identifier-fields-exist`가
            # 지적하고, 여기서는 어느 쪽이든 대리키를 붙인다(`keyOrigin`에 남는다).
            surrogate = f"{table['name'].lower()}_id"
            # 그 이름을 선언 필드가 이미 쓰면 **표시만 하고 덮어쓴다.** 자연키로 승격시키는
            # 것은 이름에서 의도를 읽는 것이라 안 한다.
            #
            # **담는 것은 모델이 적은 이름이지 우리가 지은 이름이 아니다.** 한동안
            # `surrogate`를 담았는데, 대소문자만 다른 충돌(`orderId` ↔ `order_id`)에서
            # 지적이 "선언한 `order_id`가 밀려난다"고 말했다 — 모델은 그렇게 안 적었고,
            # 고치라는 이름이 모델 안에 없으니 어디를 고쳐야 할지 알 수가 없었다.
            table["surrogateCollidesWith"] = next(
                (k for k in keyable if fields.squash(k) == fields.squash(surrogate)), None
            )
            _add_column(table, _column(surrogate, _KEY_TYPE, role="pk"))
            table["keyOrigin"] = KEY_SURROGATE
            table["primaryKey"] = [surrogate]

        # --- 일반 컬럼 ----------------------------------------------------
        for field_name, raw_type in declared:
            if not field_name:
                continue
            # 대리키와 의미상 충돌하는(대소문자만 다른) 필드는 대리키로 덮어써진
            # 것이므로 생략한다.
            collision = table.get("surrogateCollidesWith")
            if collision and fields.squash(field_name) == fields.squash(collision):
                continue
            if fields.is_collection(raw_type):
                inner = fields.inner_type(raw_type or "")
                # 원소가 Entity면 컬럼이 아니라 관계다 — 만들면 같은 사실이 두 곳에서 나온다.
                if fields.names_an_entity(inner, entity_names):
                    continue
                # **여기서 만들지 않는다** — 자식이 들고 갈 부모 기본키를 상속이 바꾼다.
                # 무엇을 만들지만 적어 두고 생성은 관계가 끝난 뒤로 미룬다.
                children.append({"table": table, "field": field_name, "inner": inner})
                continue
            # 스칼라인데 타입이 Entity다 — **컬렉션과 똑같은 이유로** 컬럼이 아니다.
            # 한동안 여기만 빠져 있어서 `member : Member`가 관계에서 나온 `member_id`
            # 옆에 `member : MEMBER`를 하나 더 만들었다. 같은 사실이 두 칸에 있었고,
            # `MEMBER`는 SQL 타입도 아닌데 하류가 그것으로 DDL을 만들었다.
            # 관계가 없으면 `erd.entity-typed-field-needs-relationship`이 지적한다.
            if fields.names_an_entity(raw_type, entity_names):
                continue
            role = "pk" if field_name in table["primaryKey"] else "attribute"
            _add_column(table, _column(field_name, fields.sql_type(raw_type), role=role))

    return made, tables, children


def _multivalued_child(parent: dict, field_name: str, inner_type: str | None) -> dict:
    """다중값 필드 하나를 제1정규화로 떼어낸 자식 표.
    **부모의 기본키가 확정된 뒤에 부를 것** — 상속이 그 키를 바꾼다.
    """
    pascal = field_name[0].upper() + field_name[1:] if field_name else "Items"
    child = _table(
        fields.sanitize_entity_name(f"{parent['name']}{pascal}"),
        {"kind": "multivalued", "table": parent["name"], "field": field_name},
    )
    own_key = f"{child['name'].lower()}_id"
    _add_column(child, _column(own_key, _KEY_TYPE, role="pk"))
    child["primaryKey"] = [own_key]
    # **부모 없는 자식 행은 뜻이 없다.** 이 표는 부모의 다중값 필드를 떼어낸 것이므로
    # 부모를 안 가리키는 행은 무엇의 값인지 말할 수 없다. 한동안 기본값(nullable)으로
    # 불렀는데, 그리는 기호는 `||..o{`(자식마다 부모가 정확히 하나)라 **그림과 컬럼이
    # 서로 다른 말을 했다.** 연결 표는 처음부터 필수였고 여기만 빠져 있었다.
    for fk in _foreign_key_columns(parent, child, mandatory=True):
        _add_column(child, fk)
    _add_column(child, _column(f"{field_name}_value", fields.sql_type(inner_type)))
    return child


# ---------------------------------------------------------------------------
# 2) 외래키 컬럼
# ---------------------------------------------------------------------------
def _foreign_key_columns(
    referenced: dict,
    holder: dict,
    unique: bool = False,
    mandatory: bool = False,
    inherited: bool = False,
) -> list[dict]:
    """`referenced`의 기본키를 `holder`가 들고 있을 컬럼들. 복합 기본키면 여러 개가 된다
    — 외래키는 참조되는 키의 **모양을 그대로** 따라야 하고 뭉뚱그릴 방법이 없다.

    `inherited`면 부모의 키 이름을 그대로 쓴다 — 상속에서는 그 칸이 자식의 기본키
    **이기도** 해서, 표 이름을 앞에 붙이면 같은 키가 층마다 다른 이름을 갖게 된다.
    """
    prefix = referenced["name"].lower()
    columns: list[dict] = []
    for pk_name in referenced["primaryKey"]:
        pk_column = next(
            (c for c in referenced["columns"] if c["name"] == pk_name),
            _column(pk_name, _KEY_TYPE),
        )
        if inherited:
            name = pk_name
        else:
            name = pk_name if pk_name.lower().startswith(prefix) else f"{prefix}_{pk_name}"

        # 자기 참조·같은 표를 가리키는 관계들. **칸이 계속 필요한** 자리라 못 버린다.
        wanted, counter = name, 1
        while name in _column_names(holder):
            name = f"related_{wanted}" if counter == 1 else f"related{counter}_{wanted}"
            counter += 1

        columns.append(
            _column(
                name,
                pk_column["type"],
                role="fk",
                references=referenced["name"],
                references_column=pk_name,
                # **복합키는 함께 유일하지 각자 유일하지 않다.** 칸마다 걸면
                # `UNIQUE(a,b)`가 `UNIQUE(a) AND UNIQUE(b)`가 되어 모델에 없는 제약이
                # 생긴다. 한 칸일 때만 컬럼에 적고, 여럿이면 아래에서 표 수준으로 올린다.
                unique=unique and len(referenced["primaryKey"]) == 1,
                mandatory=mandatory,
            )
        )

    if unique and len(columns) > 1:
        holder["uniqueTogether"].append([c["name"] for c in columns])

    return columns


# ---------------------------------------------------------------------------
# 3) 관계 → 외래키 · 연결 테이블
# ---------------------------------------------------------------------------
def _endpoints(relationship: dict, tables: dict[str, dict]) -> tuple[dict | None, dict | None]:
    """관계의 양 끝을 표로 바꾼다. 표가 아닌 끝은 `None`."""
    return (
        tables.get(fields.sanitize_entity_name(relationship.get("source", ""))),
        tables.get(fields.sanitize_entity_name(relationship.get("target", ""))),
    )


def _junction(left: dict, right: dict) -> dict:
    """다 : 다 → 연결 테이블. 두 외래키가 함께 복합 기본키가 된다."""
    table = _table(
        fields.sanitize_entity_name(f"{left['name']}{right['name']}"),
        {"kind": "junction", "tables": [left["name"], right["name"]]},
    )
    for referenced in (left, right):
        for fk in _foreign_key_columns(referenced, table, mandatory=True):
            fk["role"] = "pk"
            _add_column(table, fk)
    table["primaryKey"] = [c["name"] for c in table["columns"]]
    return table


def _map_relationship(
    relationship: dict, tables: dict[str, dict], junctions: set[str]
) -> tuple[list[dict], list[dict], list[dict]]:
    """관계 하나 → (새 표들, 그려질 선들, 사상 못 한 것들).

    양 끝이 모두 표일 때만 본다. Boundary·Control이 낀 링크는 행위 흐름이라 **말없이
    지나간다** — 결함이 아니라 정상이다.

    `junctions`는 **이미 만든 연결 표 이름**이고 여기서 채워 나간다. 이름이 두 끝점에서
    나오므로 같은 두 표를 잇는 다 : 다가 둘이면 같은 이름이 두 번 나온다.
    """
    source, target = _endpoints(relationship, tables)
    if not source or not target:
        return [], [], []

    kind = str(relationship.get("type") or "Association")

    # --- 상속: 클래스별 테이블. **모델의 source가 자식이다** -----------------
    # (`class_diagram/plantuml.py`가 `target <|-- source`로 그린다.)
    if kind == "Inheritance":
        if source["name"] == target["name"]:
            return [], [], []
        # **걷어내는 것과 자리를 내주는 것은 다르다.** 대리키는 우리가 붙인 것이니 지우고,
        # 자연키는 모델이 적은 속성이니 칸으로 남긴다(기본키 자리만 내주고 유일성은 유지).
        if source["keyOrigin"] == KEY_NATURAL:
            demoted = [c for c in source["columns"] if c["role"] == "pk"]
            for column in demoted:
                column["role"] = "attribute"
            # **복합 자연키는 함께 유일한 것이지 각자 유일한 것이 아니다.** 칸마다
            # `unique`를 걸면 `UNIQUE(a,b)`가 `UNIQUE(a) AND UNIQUE(b)`가 되어, 모델이
            # 적지 않은 제약을 우리가 얹고 정당한 행을 거부한다. 한 칸일 때만 컬럼에
            # 적고(그림이 안 바뀐다), 여럿이면 표 수준으로 올린다.
            if len(demoted) == 1:
                demoted[0]["unique"] = True
            elif demoted:
                source["uniqueTogether"].append([c["name"] for c in demoted])
        else:
            source["columns"] = [c for c in source["columns"] if c["role"] != "pk"]
        source["primaryKey"] = []
        # **한 번에 앞에 붙인다.** 하나씩 `insert(0, …)` 하면 순서가 뒤집혀서, 부모가
        # `(a, b)`인데 자식은 `(b, a)`가 됐다. 외래키 자체는 칸마다 `referencesColumn`을
        # 들고 있어 어긋나지 않지만, 그림이 부모와 자식을 다르게 보여주고 하류는 그
        # 텍스트로 DDL을 만든다.
        inherited_keys = _foreign_key_columns(target, source, mandatory=True, inherited=True)
        for fk in inherited_keys:
            fk["role"] = "pk"
        source["columns"][:0] = inherited_keys
        source["primaryKey"] = [c["name"] for c in source["columns"] if c["role"] == "pk"]
        source["keyOrigin"] = KEY_INHERITED
        # 상속은 언제나 식별 관계다 — 자식 행은 부모 행 없이 존재할 수 없다.
        return [], [{"source": target["name"], "target": source["name"],
                     "symbol": "||--||", "kind": "inheritance", "identifying": True}], []

    if kind not in STRUCTURAL_TYPES:
        # Entity 둘 사이의 `Dependency`. 종류를 잘못 적은 경우와 구별할 수 없어 드러낸다.
        return [], [], [{"source": source["name"], "target": target["name"],
                         "reason": UNMAPPED_DEPENDENCY, "type": kind}]

    # `0..*`≡`*`, `1..1`≡`1`만 접는다 — **표준이 같다고 정한 것뿐**이고 `n`·`many`는
    # 여전히 모르는 표기다(`common/multiplicity.py`).
    source_multiplicity = multiplicity.normalize(relationship.get("sourceMultiplicity"))
    target_multiplicity = multiplicity.normalize(relationship.get("targetMultiplicity"))
    if not source_multiplicity or not target_multiplicity:
        # **모르는 것을 옮기지 않는다.** 1:N으로 단정하면 그림이 모델에 없는 말을 한다.
        return [], [], [{"source": source["name"], "target": target["name"],
                         "reason": UNMAPPED_MULTIPLICITY,
                         "sourceMultiplicity": str(relationship.get("sourceMultiplicity") or ""),
                         "targetMultiplicity": str(relationship.get("targetMultiplicity") or "")}]

    # 합성은 식별 관계(실선), 연관·집약은 비식별(점선). **정하는 것은 선의 모양뿐이고**
    # 널 허용은 아래에서 다중도가 정한다 — 두 뜻을 한 값에 얹지 않는다.
    identifying = kind == "Composition"
    symbol = (
        multiplicity.crow_left(source_multiplicity)
        + ("--" if identifying else "..")
        + multiplicity.crow_right(target_multiplicity)
    )
    source_many = multiplicity.is_many(source_multiplicity)
    target_many = multiplicity.is_many(target_multiplicity)

    # --- 다 : 다 → 연결 테이블 --------------------------------------------
    if source_many and target_many:
        junction = _junction(source, target)
        # 같은 두 표를 잇는 다 : 다가 이미 있다. **둘째 표를 만들지 않는다** — 이름이
        # 같아 완전히 똑같은 표가 둘 생기고, `erd.table-names-unique`가 그것을 지적해도
        # 연결 표 이름은 사상이 짓는 것이라 **모델이 고칠 수가 없다.** 지적은 나오는데
        # 고칠 방법이 없으면 재생성이 위반 수를 못 줄여 수정본이 통째로 버려진다.
        # 무엇을 하면 되는지 말할 수 있는 사유로 드러낸다.
        if junction["name"] in junctions:
            return [], [], [{"source": source["name"], "target": target["name"],
                             "reason": UNMAPPED_DUPLICATE_JUNCTION,
                             "junction": junction["name"]}]
        junctions.add(junction["name"])
        # 연결 표의 두 외래키는 **언제나** 식별 관계다 — 둘이 합쳐 그 행의 기본키다.
        return (
            [junction],
            [
                {"source": end["name"], "target": junction["name"],
                 "symbol": "||--|{", "kind": "many-to-many", "identifying": True}
                for end in (source, target)
            ],
            [],
        )

    source_needed = multiplicity.is_mandatory(source_multiplicity)
    target_needed = multiplicity.is_mandatory(target_multiplicity)

    if source_many != target_many:
        # 1 : 다 — **다쪽이** 외래키를 든다.
        holder, referenced = (source, target) if source_many else (target, source)
        relation_kind, unique = "one-to-many", False
    else:
        # 1 : 1 — **선택인 쪽이** 든다. 그래야 외래키가 NULL이 될 수 있는 자리와 실제로
        # 선택인 자리가 같아진다. 둘 다 필수(또는 둘 다 선택)면 target이 든다(우리 규약).
        holder, referenced = (
            (source, target) if target_needed and not source_needed else (target, source)
        )
        relation_kind, unique = "one-to-one", True

    # NOT NULL은 **가리켜지는 쪽 끝**의 다중도가 정한다 — `A "1" — "*" B`면 B 하나마다
    # A가 정확히 하나이므로 `B.a_id`는 비울 수 없다.
    mandatory = source_needed if referenced is source else target_needed
    for fk in _foreign_key_columns(referenced, holder, unique=unique, mandatory=mandatory):
        _add_column(holder, fk)
    return [], [{"source": source["name"], "target": target["name"], "symbol": symbol,
                 "kind": relation_kind, "identifying": identifying}], []


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------
def build_logical_model(bce: dict[str, Any]) -> dict[str, Any]:
    """BCE 모델 → 논리 데이터 모델. **순수 함수이고 LLM을 안 부른다.**

      `Tables`    표 — `primaryKey` · `keyOrigin` · `columns` · `origin`(클래스/연결/1NF)
      `Relations` 그려질 선 — `symbol`은 크로우풋 표기
      `Unmapped`  **옮기지 못한 관계와 사유.** 비어 있지 않으면 그림에 없는 관계가 모델에 있다
    """
    if not bce:
        return {"Tables": [], "Relations": [], "Unmapped": []}

    classes = [c for c in (bce.get("Classes") or []) if isinstance(c, dict)]
    relationships = [r for r in (bce.get("Relationships") or []) if isinstance(r, dict)]

    made, tables, children = _build_tables(classes)

    relations: list[dict] = []
    unmapped: list[dict] = []
    extra: list[dict] = []

    junctions: set[str] = set()
    mappable, rejected = order_for_mapping(relationships)
    for relationship in mappable:
        new_tables, new_relations, new_unmapped = _map_relationship(
            relationship, tables, junctions
        )
        extra.extend(new_tables)
        relations.extend(new_relations)
        unmapped.extend(new_unmapped)

    # 미리 가려낸 상속들. **양 끝이 표일 때만** 적는다 — 아니면 다른 결함이고
    # (`erd.relationship-endpoints-exist`) 여기서 또 세면 한 실수가 지적 둘이 된다.
    for relationship, reason in rejected:
        source, target = _endpoints(relationship, tables)
        if source and target:
            unmapped.append({"source": source["name"], "target": target["name"],
                             "reason": reason})

    for table in made:
        table.pop("_class", None)

    # 제1정규화 자식은 **맨 마지막에** — 자식이 들고 갈 부모 기본키를 상속이 바꾼다.
    child_tables: list[dict] = []
    for pending in children:
        child = _multivalued_child(pending["table"], pending["field"], pending["inner"])
        child_tables.append(child)
        relations.append({"source": pending["table"]["name"], "target": child["name"],
                          "symbol": "||..o{", "kind": "multivalued", "identifying": False})

    return {
        "Tables": made + extra + child_tables,
        "Relations": _dedupe(relations),
        "Unmapped": unmapped,
    }


def _dedupe(relations: list[dict]) -> list[dict]:
    """같은 선을 두 번 그리지 않는다. 자기 자신과의 다대다에서 연결 표로 가는 선이
    양쪽에서 나와 겹치는데, 그림에서는 티가 안 나도 하류가 셀 때 둘로 센다.
    """
    by_key = {
        (r["source"], r["symbol"], r["target"], r["kind"]): r for r in reversed(relations)
    }
    return list(reversed(by_key.values()))
