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

import re
from collections.abc import Iterable
from typing import Any

from app.design.services.common import multiplicity
from app.design.services.erd.inheritance import order_for_mapping

# `Unmapped` 사유는 이 모듈 출력의 어휘라 소비자가 한 곳에서 다 가져가야 한다. 상속 쪽
# 둘만 다른 모듈에 살므로 다시 내보낸다.
from app.design.services.erd.inheritance import (  # noqa: F401
    UNMAPPED_INHERITANCE_CYCLE,
    UNMAPPED_MULTIPLE_INHERITANCE,
)

#: 기본키·외래키에 쓰는 정수 타입.
_KEY_TYPE = "BIGINT"

#: 언어 자료형 → RDBMS 자료형. **없는 것은 지어내지 않는다** — 표에 없으면 원문 대문자,
#: 타입이 아예 없으면 `None`.
_SQL_TYPES: dict[str, str] = {
    "string": "VARCHAR(255)",
    "int": "INT",
    "integer": "INT",
    "long": "BIGINT",
    "boolean": "BOOLEAN",
    "bool": "BOOLEAN",
    "date": "DATE",
    "datetime": "DATETIME",
    "float": "FLOAT",
    "double": "DOUBLE",
}


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


def sanitize_entity_name(name: str) -> str:
    """테이블 식별자에 안전한 단어 문자만 남긴다."""
    if not name:
        return "UnknownEntity"
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def _sanitize_text(text: str) -> str:
    """컬럼명 등 자유 텍스트의 특수 공백·줄바꿈을 한 줄로 정제한다."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip().replace("‑", "-")


def squash(name: str) -> str:
    """식별자 비교용 정규화: 대소문자·밑줄·공백을 없앤다. `memberId == member_id`."""
    return re.sub(r"[_\s]", "", name).lower()


def is_entity(class_item: dict) -> bool:
    """이 클래스가 표가 되는가. **정확히 일치로 본다** — `NotAnEntity`가 표가 되면 안 된다.

    읽는 관대함(`<<Entity>>`·`entity`)은 `detectors._stereotype_of`와 같게 유지한다.
    """
    raw = str(class_item.get("stereotype", ""))
    return raw.replace("<", "").replace(">", "").strip().lower() == "entity"


def split_field(raw: str) -> tuple[str, str | None]:
    """`"name : Type"` → `("name", "Type")`. 타입이 없으면 `None` — **채우지 않는다.**
    채우면 아무도 고르지 않은 타입이 하류 DDL까지 간다.
    """
    clean = _sanitize_text(raw)
    if ":" in clean:
        name, raw_type = clean.split(":", 1)
        return name.strip(), (raw_type.strip() or None)
    return clean, None


def _sql_type(raw_type: str | None) -> str | None:
    """언어 자료형 → RDBMS 자료형. 모르면 원문 대문자, 없으면 `None`."""
    if not raw_type:
        return None
    return _SQL_TYPES.get(raw_type.strip().lower(), raw_type.strip().upper())


def _inner_type(raw_type: str) -> str | None:
    """`List<String>`·`String[]`에서 원소 타입을 꺼낸다. 못 읽으면 `None`."""
    match = re.search(r"<(.*?)>", raw_type)
    if match:
        return match.group(1).strip() or None
    if "[]" in raw_type:
        return raw_type.replace("[]", "").strip() or None
    return None


#: 다중값으로 읽는 표기. **`Map`·`Dict`는 일부러 없다** — 원소가 쌍이라 자식 표의
#: `{field}_value` 한 칸에 안 들어가고, 지금 형태로 제1정규화를 걸면 값의 절반이 사라진다.
#: 그건 이 표에 한 줄 더하는 것으로 될 일이 아니라 별도 결정이다.
_COLLECTION_WORDS = ("list", "array", "set", "collection")


def is_collection(raw_type: str | None) -> bool:
    """`List<T>`·`T[]`·`Set<T>`·`Collection<T>`. **`Set`이 한동안 빠져 있었다** —
    흔한 선언인데 컬럼 하나로 눌러앉아 제1정규화가 안 됐고, `SET<STRING>`이라는 SQL
    아닌 타입이 그림과 하류 DDL로 나갔다.

    `erd_identifier_fields`도 이 함수를 쓴다. 여기가 못 읽으면 "다중값은 키가 될 수
    없다"는 지적도 함께 사라진다.
    """
    lowered = (raw_type or "").lower()
    return any(word in lowered for word in _COLLECTION_WORDS) or "[" in lowered


def names_an_entity(raw_type: str | None, entity_names: Iterable[str]) -> bool:
    """이 자료형이 Entity 이름인가. 컬렉션이면 원소 타입을 먼저 꺼내 볼 것.

    **타입을 읽는 것이지 이름을 읽는 것이 아니다.** `erd.fk-from-field-name`이 금지하는
    것은 `memberId`라는 *필드 이름*에서 외래키를 짐작하는 일이고, 여기서 보는 것은
    모델이 `member : Member`라고 **직접 적은 자료형**이다. 짐작할 것이 없다.

    `detectors.py`도 이 함수를 쓴다 — 사상이 컬럼을 안 만드는 기준과 검사기가 관계를
    요구하는 기준이 갈라지면, 칸은 사라졌는데 아무도 지적하지 않는 자리가 생긴다.
    """
    if not raw_type:
        return False
    return any(squash(raw_type) == squash(str(name)) for name in entity_names)


def referenced_entity(raw_type: str | None, entity_names: Iterable[str]) -> str | None:
    """이 자료형이 가리키는 Entity 이름. 컬렉션이면 **원소 타입**을 보고, 아니면 `None`.

    `names_an_entity`가 "그런가?"라면 이것은 "누구인가?"다. 사상은 앞엣것만 있으면 되지만
    검사기는 지적 문구에 이름을 적어야 해서 뒤엣것이 필요하다. 컬렉션을 벗기는 자리가
    둘로 갈라지지 않게 여기 한 번만 둔다.
    """
    if not raw_type:
        return None
    wanted = _inner_type(raw_type) if is_collection(raw_type) else raw_type
    if not wanted:
        return None
    return next((str(n) for n in entity_names if squash(wanted) == squash(str(n))), None)


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
        if not is_entity(class_item):
            continue
        name = sanitize_entity_name(class_item.get("className", ""))
        table = _table(name, {"kind": "class", "className": class_item.get("className", "")})
        table["_class"] = class_item
        made.append(table)
        tables.setdefault(name, table)

    entity_names = {t["_class"].get("className", "") for t in made}

    for table in made:
        class_item = table["_class"]
        declared = [split_field(f) for f in class_item.get("fields", []) if _sanitize_text(f)]

        # --- 기본키 -------------------------------------------------------
        # **칸이 안 남는 필드는 후보가 아니다.** 다중값은 자식 표로 가고, Entity 타입은
        # 관계가 들고 간다. 후보로 두면 `primaryKey`가 실재하지 않는 칸 이름을 담는다
        # (`assert_sound` ②가 그걸 잡는다). 못 쓰는 이유는 `erd.identifier-fields-exist`가 말한다.
        keyable = {
            n
            for n, raw in declared
            if n and not is_collection(raw) and not names_an_entity(raw, entity_names)
        }
        identifier = [str(i).strip() for i in (class_item.get("identifier") or []) if str(i).strip()]
        # **집합으로 짝짓는다** — 개수로 비교하면 중복(`["isbn","isbn"]`)에서 키를 버린다.
        matched = {
            i: next((n for n in keyable if squash(n) == squash(i)), None) for i in identifier
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
            collided = next((k for k in keyable if squash(k) == squash(surrogate)), None)
            table["surrogateCollidesWith"] = surrogate if collided else None
            _add_column(table, _column(surrogate, _KEY_TYPE, role="pk"))
            table["keyOrigin"] = KEY_SURROGATE
            table["primaryKey"] = [surrogate]

        # --- 일반 컬럼 ----------------------------------------------------
        for field_name, raw_type in declared:
            if not field_name:
                continue
            # 대리키와 의미상 충돌하는(대소문자만 다른) 필드는 대리키로 덮어써진 것이므로 생략한다.
            if table.get("surrogateCollidesWith") and squash(field_name) == squash(table["surrogateCollidesWith"]):
                continue
            if is_collection(raw_type):
                inner = _inner_type(raw_type or "")
                # 원소가 Entity면 컬럼이 아니라 관계다 — 만들면 같은 사실이 두 곳에서 나온다.
                if names_an_entity(inner, entity_names):
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
            if names_an_entity(raw_type, entity_names):
                continue
            role = "pk" if field_name in table["primaryKey"] else "attribute"
            _add_column(table, _column(field_name, _sql_type(raw_type), role=role))

    return made, tables, children


def _multivalued_child(parent: dict, field_name: str, inner_type: str | None) -> dict:
    """다중값 필드 하나를 제1정규화로 떼어낸 자식 표.
    **부모의 기본키가 확정된 뒤에 부를 것** — 상속이 그 키를 바꾼다.
    """
    pascal = field_name[0].upper() + field_name[1:] if field_name else "Items"
    child = _table(
        sanitize_entity_name(f"{parent['name']}{pascal}"),
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
    _add_column(child, _column(f"{field_name}_value", _sql_type(inner_type)))
    return child


# ---------------------------------------------------------------------------
# 2) 외래키 컬럼
# ---------------------------------------------------------------------------
def _foreign_key_columns(
    referenced: dict, holder: dict, unique: bool = False, mandatory: bool = False, inherited: bool = False
) -> list[dict]:
    """`referenced`의 기본키를 `holder`가 들고 있을 컬럼들. 복합 기본키면 여러 개가 된다
    — 외래키는 참조되는 키의 **모양을 그대로** 따라야 하고 뭉뚱그릴 방법이 없다.
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
        original_name = name
        counter = 1
        while name in _column_names(holder):
            name = f"related_{original_name}" if counter == 1 else f"related{counter}_{original_name}"
            counter += 1
            
        columns.append(
            _column(
                name,
                pk_column["type"],
                role="fk",
                references=referenced["name"],
                references_column=pk_name,
                # 복합키일 경우 컬럼 개별이 아닌 조합이 unique해야 하므로 여기선 단일키일때만 설정
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
        tables.get(sanitize_entity_name(relationship.get("source", ""))),
        tables.get(sanitize_entity_name(relationship.get("target", ""))),
    )


def _junction(left: dict, right: dict) -> dict:
    """다 : 다 → 연결 테이블. 두 외래키가 함께 복합 기본키가 된다."""
    table = _table(
        sanitize_entity_name(f"{left['name']}{right['name']}"),
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
        for fk in _foreign_key_columns(target, source, mandatory=True, inherited=True):
            fk["role"] = "pk"
            source["columns"].insert(0, fk)
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
