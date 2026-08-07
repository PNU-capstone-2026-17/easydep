"""BCE 클래스의 **필드와 자료형을 읽는 어휘** 한 벌 — 이름 정제·타입 분해·컬렉션 판정.

## 왜 한 곳인가

`common/multiplicity.py`와 같은 이유다. 이 어휘를 아는 곳이 둘이다: ERD 사상
(`erd/mapping.py` — 무엇이 컬럼이 되고 무엇이 자식 표로 가는지 정한다)과 검출기
(`knowledge/detectors.py` — 모델이 제대로 적었는지 판정한다). 둘이 각자 판단하면
**칸은 사라졌는데 아무도 지적하지 않는 자리**가 생긴다.

실제로 그런 모양이었다. 사상은 `member : Member`를 컬럼으로 안 만들고(관계가 그 사실을
들고 가므로), 검출기는 관계가 없으면 그것을 지적한다. 두 쪽이 "이 타입이 Entity인가"를
다르게 읽는 순간 그 짝이 깨진다 — 사상은 칸을 안 만들었는데 검출기는 지적할 것이 없다고
판단하고, 모델이 적은 링크가 산출물 어디에도 안 남는 채로 조용히 통과한다.

컬렉션 판정도 같다. `is_collection`이 못 읽으면 다중값 필드가 컬럼 하나로 눌러앉아
제1정규화가 안 되고, "다중값은 키가 될 수 없다"는 지적(`erd.identifier-fields-exist`)까지
함께 사라진다.

## 여기 있는 것과 없는 것

여기 있는 것은 **모델이 적은 글자를 읽는 일**뿐이다. 무엇이 표가 되고 외래키가 어디
붙는지 같은 **사상 결정은 `erd/mapping.py`에 있다** — 결정이 흩어지면 아무도 그것이
결정인 줄 모른다.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

#: 언어 자료형 → RDBMS 자료형. **없는 것은 지어내지 않는다** — 표에 없으면 원문 대문자,
#: 타입이 아예 없으면 `None`.
SQL_TYPES: dict[str, str] = {
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


def sanitize_entity_name(name: str) -> str:
    """테이블 식별자에 안전한 단어 문자만 남긴다."""
    if not name:
        return "UnknownEntity"
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def sanitize_text(text: str) -> str:
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
    clean = sanitize_text(raw)
    if ":" in clean:
        name, raw_type = clean.split(":", 1)
        return name.strip(), (raw_type.strip() or None)
    return clean, None


def sql_type(raw_type: str | None) -> str | None:
    """언어 자료형 → RDBMS 자료형. 모르면 원문 대문자, 없으면 `None`."""
    if not raw_type:
        return None
    return SQL_TYPES.get(raw_type.strip().lower(), raw_type.strip().upper())


def inner_type(raw_type: str) -> str | None:
    """`List<String>`·`String[]`에서 원소 타입을 꺼낸다. 못 읽으면 `None`."""
    match = re.search(r"<(.*?)>", raw_type)
    if match:
        return match.group(1).strip() or None
    if "[]" in raw_type:
        return raw_type.replace("[]", "").strip() or None
    return None


#: 다중값으로 읽는 **타입 생성자의 이름.** 구체 타입까지 적어 두는 이유는 아래 함수의
#: 판정이 **이름 전체 일치**이기 때문이다 — `HashSet<T>`을 안 적으면 그냥 컬럼이 된다.
#:
#: **`Map`·`Dict`는 일부러 없다** — 원소가 쌍이라 자식 표의 `{field}_value` 한 칸에 안
#: 들어가고, 지금 형태로 제1정규화를 걸면 값의 절반이 사라진다. 그건 이 목록에 한 줄
#: 더하는 것으로 될 일이 아니라 별도 결정이다.
_COLLECTION_TYPES = frozenset({
    "list", "arraylist", "linkedlist",
    "set", "hashset", "treeset", "linkedhashset",
    "collection", "iterable", "sequence",
    "array",
})


def is_collection(raw_type: str | None) -> bool:
    """`List<T>`·`T[]`·`Set<T>`·`Collection<T>`. **`Set`이 한동안 빠져 있었다** —
    흔한 선언인데 컬럼 하나로 눌러앉아 제1정규화가 안 됐고, `SET<STRING>`이라는 SQL
    아닌 타입이 그림과 하류 DDL로 나갔다.

    **타입 생성자의 이름을 통째로 맞춘다. 부분 문자열이 아니다** — 한동안
    `"list" in lowered` 식으로 봐서 `Playlist`·`Dataset`·`Asset`·`Offset`·`Setting`이
    전부 컬렉션이었다. 셋 다 흔한 Entity 이름이고, 그래서 `fav : Playlist` 하나가
    **모델에 없는 1NF 자식 표**(`MemberFav`, 값 칸의 타입은 `None`)를 만들어 냈다.
    원소 타입을 못 읽으니 `referenced_entity`도 `None`을 돌려주어
    `erd.entity-typed-field-needs-relationship`과 `erd.field-looks-like-reference`가
    함께 침묵했고, `erd.identifier-fields-exist`는 그 필드를 두고 "다중값이라 키가 될 수
    없다"는 **사실이 아닌 지적**을 냈다.

    `erd_identifier_fields`도 이 함수를 쓴다. 여기가 못 읽으면 "다중값은 키가 될 수
    없다"는 지적도 함께 사라진다.
    """
    text = (raw_type or "").strip()
    if not text:
        return False
    if "[" in text:  # `String[]` · `int[5]`
        return True
    head = text.split("<", 1)[0].strip()  # `List<String>` → `List`
    return head.rsplit(".", 1)[-1].lower() in _COLLECTION_TYPES  # `java.util.List` → `List`


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
    wanted = inner_type(raw_type) if is_collection(raw_type) else raw_type
    if not wanted:
        return None
    return next((str(n) for n in entity_names if squash(wanted) == squash(str(n))), None)
