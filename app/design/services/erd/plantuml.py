"""BCE 추출 결과(JSON)에서 <<Entity>>만 골라 ERD PlantUML로 변환한다.

클래스 다이어그램(class_diagram.plantuml)과 같은 결정론적 변환이다: LLM은 구조화된
BCE만 편집하고, 다이어그램 텍스트는 이 함수가 "구성에 의해" 항상 문법적으로 유효한
PlantUML로 재렌더한다. 그래서 BCE와 ERD가 어긋나지 않고 문법 수리 루프가 필요 없다.

엔티티 → 테이블, 관계 → 외래키(FK)로 매핑하고, 리스트/배열 필드는 제1정규화(1NF)에
따라 별도의 1:N 자식 테이블로 분리한다. jar 실행·렌더는 common.plantuml이 맡는다.
"""
from __future__ import annotations

import re
from typing import Any

# 관계에서 추론한 PK/FK의 기본 자료형.
DEFAULT_PK_TYPE = "BIGINT"

# 프로그래밍 언어 자료형 → RDBMS 자료형.
_SQL_TYPE_MAPPING = {
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
    """엔티티/테이블 식별자에 안전한 단어 문자만 남긴다."""
    if not name:
        return "UnknownEntity"
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def sanitize_text(text: str) -> str:
    """컬럼명 등 자유 텍스트의 특수 공백·줄바꿈을 한 줄로 정제한다."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("‑", "-")


def _extract_inner_type(raw_type: str) -> str:
    """List<String>·String[] 형태에서 내부 자료형(String)을 뽑아낸다."""
    match = re.search(r"<(.*?)>", raw_type)
    if match:
        return match.group(1).strip()
    if "[]" in raw_type:
        return raw_type.replace("[]", "").strip()
    return "String"


def _map_to_sql_type(raw_type: str) -> str:
    """단일 자료형을 RDBMS 자료형으로 변환한다."""
    return _SQL_TYPE_MAPPING.get(raw_type.strip().lower(), raw_type.strip().upper())


def _split_field(clean_field: str) -> tuple[str, str]:
    """'name : Type' 필드를 (이름, 원시타입)으로 나눈다. 타입이 없으면 String."""
    if ":" in clean_field:
        name, raw_type = clean_field.split(":", 1)
        return name.strip(), raw_type.strip()
    return clean_field, "String"


def _squash(name: str) -> str:
    """식별자 비교용 정규화: 대소문자·밑줄·공백을 없앤다. memberId == member_id."""
    return re.sub(r"[_\s]", "", name).lower()


def _referenced_entity(field_name: str, entities: dict[str, Any]) -> str | None:
    """`memberId` 같은 필드가 가리키는 엔티티를 찾는다. 없으면 None.

    **왜 이름으로 추론하나.** FK는 원래 Relationships에서 나와야 하는데, BCE의 관계는
    행위 흐름(Boundary→Control→Entity)이라 **엔티티끼리의 데이터 관계가 거의 안 나온다.**
    실제로 LLM은 `Loan`에 `memberId`·`copyId`를 필드로 넣어두고 관계는 안 만든다.
    그러면 ERD에 선이 하나도 안 그려진다.

    필드 이름이 이미 그 정보를 담고 있으므로 여기서 읽어낸다 — PK 자동 생성이나 1NF
    분리와 같은 성격의 결정론적 변환이다. `memberId` / `member_id` / `MemberID` 를 모두
    받는다.
    """
    stem = re.sub(r"[_\s]*id$", "", field_name.strip(), flags=re.IGNORECASE)
    if not stem:
        return None
    for entity in entities:
        if _squash(entity) == _squash(stem):
            return entity
    return None


def generate_erd_from_bce_json(json_data: dict[str, Any]) -> str:
    """BCE JSON 중 <<Entity>> 클래스만 ERD PlantUML로 변환한다.

    엔티티가 하나도 없으면 빈 문자열을 반환한다(변환할 대상 없음).
    """
    if not json_data:
        return ""

    classes = json_data.get("Classes", [])
    relationships = json_data.get("Relationships", [])

    entities: dict[str, dict[str, Any]] = {}
    for cls in classes:
        if "entity" in cls.get("stereotype", "").lower():
            name = sanitize_entity_name(cls.get("className", "UnknownEntity"))
            entities[name] = cls

    if not entities:
        return ""

    puml_lines = [
        "@startuml",
        "hide circle",
        "!theme plain",
        "",
        "skinparam linetype ortho",
        "",
    ]

    # FK는 두 곳에서 나온다.
    #   (1) 명시된 관계 — target 테이블이 source의 PK를 FK로 갖는다.
    #   (2) 필드 이름 — `Loan.memberId` 처럼 엔티티를 가리키는 필드.
    # (2)가 없으면 ERD에 선이 거의 안 그려진다. BCE의 관계는 행위 흐름이라
    # 엔티티끼리의 데이터 관계를 담지 않기 때문이다(_referenced_entity 참조).
    erd_relations: set[str] = set()
    fk_mapping: dict[str, dict[str, str]] = {name: {} for name in entities}

    def link(source: str, target: str, symbol: str = "||..o{") -> None:
        """source 1 : N target. target 이 FK 를 갖는다."""
        erd_relations.add(f"{source} {symbol} {target}")
        fk_mapping[target].setdefault(f"{source.lower()}_id", DEFAULT_PK_TYPE)

    for rel in relationships:
        source = sanitize_entity_name(rel.get("source", ""))
        target = sanitize_entity_name(rel.get("target", ""))
        if source not in entities or target not in entities:
            continue
        link(source, target, "||..|{" if rel.get("type") == "Composition" else "||..o{")

    # 필드 이름에서 읽어낸 FK. 자기 자신을 가리키는 것(Member.memberId)은 PK 이므로 뺀다.
    fk_fields: dict[str, set[str]] = {name: set() for name in entities}
    for name, cls in entities.items():
        for field in cls.get("fields", []):
            field_name, _ = _split_field(sanitize_text(field))
            referenced = _referenced_entity(field_name, entities)
            if referenced is None or referenced == name:
                continue
            fk_fields[name].add(field_name)      # 일반 컬럼으로 또 찍지 않게 표시
            link(referenced, name)

    # 제1정규화(1NF)로 리스트/배열 필드에서 분리될 자식 테이블·관계.
    dynamic_entities: list[str] = []
    dynamic_relations: list[str] = []

    for name, cls in entities.items():
        puml_lines.append(f'entity "{name}" as {name} {{')

        pk_name = f"{name.lower()}_id"
        puml_lines.append(f"  * {pk_name} : {DEFAULT_PK_TYPE}")
        puml_lines.append("  --")

        for field in cls.get("fields", []):
            clean_field = sanitize_text(field)
            if not clean_field:
                continue

            f_name, f_raw_type = _split_field(clean_field)
            # 이 엔티티 자신의 식별자 필드는 위에서 만든 합성 PK와 같은 것이다.
            # `memberId` / `member_id` / `MEMBER ID` 를 모두 같게 본다 — 안 그러면
            # 한 테이블에 `* member_id : BIGINT` 와 `memberId : VARCHAR(255)` 가 함께 남는다.
            if _squash(f_name) == _squash(pk_name):
                continue
            # FK로 승격된 필드는 아래 FK 절에서 찍는다 — 두 번 나오면 안 된다.
            if f_name in fk_fields[name]:
                continue

            # 리스트/배열은 별도의 1:N 자식 테이블로 분리(1NF).
            t_lower = f_raw_type.lower()
            if "list" in t_lower or "array" in t_lower or "[" in t_lower:
                inner_type = _extract_inner_type(f_raw_type)
                # 내부 타입이 이미 엔티티면 관계로 이어지므로 컬럼을 만들지 않는다.
                if any(e.lower() == inner_type.lower() for e in entities):
                    continue

                pascal = f_name[0].upper() + f_name[1:] if f_name else "Items"
                child = f"{name}{pascal}"
                dynamic_entities.append(
                    "\n".join(
                        [
                            f'entity "{child}" as {child} {{',
                            f"  * {child.lower()}_id : {DEFAULT_PK_TYPE}",
                            "  --",
                            f"  {pk_name} : {DEFAULT_PK_TYPE} <<FK>>",
                            f"  {f_name}_value : {_map_to_sql_type(inner_type)}",
                            "}",
                        ]
                    )
                )
                dynamic_relations.append(f"{name} ||..o{{ {child}")
                continue

            puml_lines.append(f"  {f_name} : {_map_to_sql_type(f_raw_type)}")

        for fk_name, fk_type in fk_mapping[name].items():
            puml_lines.append(f"  {fk_name} : {fk_type} <<FK>>")

        puml_lines.append("}")
        puml_lines.append("")

    if dynamic_entities:
        puml_lines.append("' === 제1정규화(1NF) 분리 테이블 ===")
        for child_block in dynamic_entities:
            puml_lines.append(child_block)
            puml_lines.append("")

    puml_lines.extend(sorted(erd_relations))
    puml_lines.extend(dynamic_relations)

    puml_lines.append("")
    puml_lines.append("@enduml")

    final_puml = "\n".join(puml_lines)
    return final_puml.replace("\xa0", " ").replace("​", "")
