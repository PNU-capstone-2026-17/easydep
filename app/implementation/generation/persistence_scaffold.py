"""ERD의 typed Entity 모델에서 Spring Data persistence 골격을 만든다.

이 모듈은 테이블 이름이나 Java 필드를 LLM에 다시 묻지 않는다. ERD 단계가 확정한
Entity 이름, 필드, 식별자와 타입만 사용해 JPA Entity, Repository와 첫 migration을
항상 같은 내용으로 만든다. ERD에 없는 null 허용 여부나 관계의 외래 키 소유자는
추측하지 않는다.
"""
from __future__ import annotations

import re

from app.design.schemas.class_model import AcceptedBCEClass, BCEModel

from .java_scaffold import java_type

PERSISTENCE_SCAFFOLDER_VERSION = "1.0.0"

_FIELD = re.compile(
    r"^\s*[+#~\-]?\s*(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*:\s*(?P<type>.+?)\s*$"
)
_JAVA_IMPORTS = {
    "BigDecimal": "java.math.BigDecimal",
    "LocalDate": "java.time.LocalDate",
    "LocalDateTime": "java.time.LocalDateTime",
    "LocalTime": "java.time.LocalTime",
    "List": "java.util.List",
    "Optional": "java.util.Optional",
    "UUID": "java.util.UUID",
}


def render_persistence_scaffold(
    model: BCEModel,
    base_package: str,
) -> dict[str, str]:
    """애플리케이션 root 기준의 persistence 파일을 경로별로 반환한다."""

    entities = sorted(
        (item for item in model.Classes if item.stereotype == "Entity"),
        key=lambda item: item.class_name,
    )
    if not entities:
        return {}

    declared_types = {
        *(item.class_name for item in model.Classes),
        *(item.name for item in model.DataTypes),
    }
    enum_types = {
        item.name for item in model.DataTypes if item.kind == "enumeration"
    }
    value_types = {
        item.name for item in model.DataTypes if item.kind == "valueObject"
    }
    package_path = base_package.replace(".", "/")
    files: dict[str, str] = {}
    for entity in entities:
        fields = _entity_fields(entity, declared_types)
        identifiers = _identifier_fields(entity, fields)
        entity_package = f"{base_package}.persistence.entity"
        entity_path = (
            f"src/main/java/{package_path}/persistence/entity/"
            f"{entity.class_name}Entity.java"
        )
        key_type = identifiers[0][1]
        if len(identifiers) > 1:
            key_type = f"{entity.class_name}Key"
            files[
                f"src/main/java/{package_path}/persistence/entity/{key_type}.java"
            ] = _render_composite_key(entity_package, key_type, identifiers)
        files[entity_path] = _render_entity(
            entity,
            fields,
            identifiers,
            base_package,
            enum_types,
            value_types,
        )
        files[
            f"src/main/java/{package_path}/persistence/repository/"
            f"{entity.class_name}Repository.java"
        ] = _render_repository(entity.class_name, key_type, base_package)

    files["src/main/resources/db/migration/V1__initial_schema.sql"] = (
        _render_migration(entities, declared_types, enum_types, value_types)
    )
    return dict(sorted(files.items()))


def _entity_fields(
    entity: AcceptedBCEClass,
    declared_types: set[str],
) -> list[tuple[str, str, str]]:
    """설계 필드를 이름, Java 타입, 원래 타입 순서로 읽는다."""

    fields: list[tuple[str, str, str]] = []
    for declaration in entity.fields:
        match = _FIELD.fullmatch(declaration)
        if match is None:
            raise ValueError(
                f"{entity.class_name} has an invalid field declaration: {declaration}"
            )
        name = match.group("name")
        design_type = match.group("type")
        fields.append(
            (name, java_type(design_type, declared_types=declared_types), design_type)
        )
    return fields


def _identifier_fields(
    entity: AcceptedBCEClass,
    fields: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    """ERD가 명시한 식별자를 실제 필드와 연결한다."""

    by_name = {name: (name, field_type, source) for name, field_type, source in fields}
    identifiers = [by_name[name] for name in entity.identifier if name in by_name]
    missing = [name for name in entity.identifier if name not in by_name]
    if missing:
        raise ValueError(
            f"{entity.class_name} identifier fields are missing: {', '.join(missing)}"
        )
    if not identifiers:
        raise ValueError(f"{entity.class_name} requires at least one ERD identifier")
    return identifiers


def _render_entity(
    entity: AcceptedBCEClass,
    fields: list[tuple[str, str, str]],
    identifiers: list[tuple[str, str, str]],
    base_package: str,
    enum_types: set[str],
    value_types: set[str],
) -> str:
    """한 ERD Entity를 독립적으로 사용할 수 있는 JPA class로 만든다."""

    package = f"{base_package}.persistence.entity"
    identifier_names = {name for name, _field_type, _source in identifiers}
    imports = {
        "jakarta.persistence.Column",
        "jakarta.persistence.Entity",
        "jakarta.persistence.Id",
        "jakarta.persistence.Table",
    }
    if len(identifiers) > 1:
        imports.add("jakarta.persistence.IdClass")
    for _name, field_type, _source in fields:
        imports.update(_imports_for_type(field_type, base_package, enum_types | value_types))
        if _is_enum(field_type, enum_types):
            imports.update({"jakarta.persistence.EnumType", "jakarta.persistence.Enumerated"})
        elif _is_json_type(field_type, value_types):
            imports.update(
                {"org.hibernate.annotations.JdbcTypeCode", "org.hibernate.type.SqlTypes"}
            )
        elif field_type == "byte[]":
            imports.add("jakarta.persistence.Lob")

    lines = [
        f"package {package};",
        "",
        *(f"import {name};" for name in sorted(imports)),
        "",
        "/** ERD의 필드와 식별자를 그대로 옮긴 JPA 저장 객체다. */",
        "@Entity",
        f'@Table(name = "{_table_name(entity.class_name)}")',
    ]
    if len(identifiers) > 1:
        lines.append(f"@IdClass({entity.class_name}Key.class)")
    lines.append(f"public class {entity.class_name}Entity {{")

    for name, field_type, source in fields:
        lines.append("")
        if name in identifier_names:
            lines.append("    @Id")
        if _is_enum(field_type, enum_types):
            lines.append("    @Enumerated(EnumType.STRING)")
        elif _is_json_type(field_type, value_types):
            if field_type == "Object":
                lines.append(
                    f"    // 설계 타입 `{source}`은 Java 기본 타입이 아니므로 JSON으로 보존한다."
                )
            lines.append("    @JdbcTypeCode(SqlTypes.JSON)")
        elif field_type == "byte[]":
            lines.append("    @Lob")
        column_parts = [f'name = "{_snake_case(name)}"']
        if name in identifier_names:
            column_parts.extend(["nullable = false", "updatable = false"])
        if _is_json_type(field_type, value_types):
            column_parts.append('columnDefinition = "json"')
        lines.append(f"    @Column({', '.join(column_parts)})")
        lines.append(f"    private {field_type} {name};")

    lines.extend(["", f"    public {entity.class_name}Entity() {{}}"])
    parameters = ", ".join(f"{field_type} {name}" for name, field_type, _ in fields)
    lines.extend(["", f"    public {entity.class_name}Entity({parameters}) {{"])
    lines.extend(f"        this.{name} = {name};" for name, _field_type, _source in fields)
    lines.append("    }")
    for name, field_type, _source in fields:
        method_name = name[:1].upper() + name[1:]
        lines.extend(
            [
                "",
                f"    public {field_type} get{method_name}() {{",
                f"        return {name};",
                "    }",
                "",
                f"    public void set{method_name}({field_type} {name}) {{",
                f"        this.{name} = {name};",
                "    }",
            ]
        )
    lines.append("}")
    return "\n".join(lines) + "\n"


def _render_repository(entity_name: str, key_type: str, base_package: str) -> str:
    imports = [
        f"{base_package}.persistence.entity.{entity_name}Entity",
        "org.springframework.data.jpa.repository.JpaRepository",
    ]
    if "." not in key_type and key_type not in {
        "Boolean", "Integer", "Long", "Object", "String", "byte[]",
    }:
        if key_type == f"{entity_name}Key":
            imports.append(f"{base_package}.persistence.entity.{key_type}")
        elif key_type in _JAVA_IMPORTS:
            imports.append(_JAVA_IMPORTS[key_type])
        elif key_type not in {"BigDecimal", "LocalDate", "LocalDateTime", "LocalTime"}:
            imports.append(f"{base_package}.bce.{key_type}")
    if key_type in _JAVA_IMPORTS and _JAVA_IMPORTS[key_type] not in imports:
        imports.append(_JAVA_IMPORTS[key_type])
    return (
        f"package {base_package}.persistence.repository;\n\n"
        + "".join(f"import {name};\n" for name in sorted(set(imports)))
        + "\n/** 생성된 JPA Entity의 기본 저장·조회 계약이다. */\n"
        + f"public interface {entity_name}Repository "
        + f"extends JpaRepository<{entity_name}Entity, {key_type}> {{}}\n"
    )


def _render_composite_key(
    package: str,
    key_name: str,
    fields: list[tuple[str, str, str]],
) -> str:
    imports = {"java.io.Serializable", "java.util.Objects"}
    imports.update(
        import_name
        for _name, field_type, _source in fields
        for import_name in _imports_for_type(field_type, package.rsplit(".persistence", 1)[0], set())
    )
    lines = [
        f"package {package};",
        "",
        *(f"import {name};" for name in sorted(imports)),
        "",
        "/** 둘 이상의 ERD 식별자 필드를 묶는 JPA 복합 키다. */",
        f"public class {key_name} implements Serializable {{",
    ]
    lines.extend(f"    private {field_type} {name};" for name, field_type, _ in fields)
    lines.extend(["", f"    public {key_name}() {{}}", ""])
    parameters = ", ".join(f"{field_type} {name}" for name, field_type, _ in fields)
    lines.append(f"    public {key_name}({parameters}) {{")
    lines.extend(f"        this.{name} = {name};" for name, _field_type, _ in fields)
    lines.append("    }")
    for name, field_type, _source in fields:
        method = name[:1].upper() + name[1:]
        lines.extend(
            [
                "",
                f"    public {field_type} get{method}() {{ return {name}; }}",
                f"    public void set{method}({field_type} {name}) {{ this.{name} = {name}; }}",
            ]
        )
    comparisons = " && ".join(f"Objects.equals({name}, other.{name})" for name, _, _ in fields)
    values = ", ".join(name for name, _field_type, _source in fields)
    lines.extend(
        [
            "",
            "    @Override",
            "    public boolean equals(Object value) {",
            "        if (this == value) return true;",
            f"        if (!(value instanceof {key_name} other)) return false;",
            f"        return {comparisons};",
            "    }",
            "",
            "    @Override",
            "    public int hashCode() {",
            f"        return Objects.hash({values});",
            "    }",
            "}",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_migration(
    entities: list[AcceptedBCEClass],
    declared_types: set[str],
    enum_types: set[str],
    value_types: set[str],
) -> str:
    statements = ["-- ERD typed 모델에서 생성한 최초 schema다."]
    for entity in entities:
        fields = _entity_fields(entity, declared_types)
        identifiers = _identifier_fields(entity, fields)
        identifier_names = {name for name, _field_type, _source in identifiers}
        columns: list[str] = []
        for name, field_type, _source in fields:
            column = f"    {_snake_case(name)} {_sql_type(field_type, enum_types, value_types)}"
            if len(identifiers) == 1 and name in identifier_names:
                column += " PRIMARY KEY"
            columns.append(column)
        if len(identifiers) > 1:
            names = ", ".join(_snake_case(name) for name in identifier_names)
            columns.append(f"    PRIMARY KEY ({names})")
        statements.extend(
            [
                "",
                f"CREATE TABLE {_table_name(entity.class_name)} (",
                ",\n".join(columns),
                ");",
            ]
        )
    return "\n".join(statements) + "\n"


def _imports_for_type(
    field_type: str,
    base_package: str,
    bce_types: set[str],
) -> set[str]:
    imports = {
        path for token, path in _JAVA_IMPORTS.items() if re.search(rf"\b{token}\b", field_type)
    }
    imports.update(
        f"{base_package}.bce.{name}"
        for name in bce_types
        if re.search(rf"\b{re.escape(name)}\b", field_type)
    )
    return imports


def _is_enum(field_type: str, enum_types: set[str]) -> bool:
    return field_type in enum_types


def _is_json_type(field_type: str, value_types: set[str]) -> bool:
    return (
        field_type == "Object"
        or field_type in value_types
        or field_type.startswith("List<")
        or field_type.startswith("Optional<")
    )


def _sql_type(field_type: str, enum_types: set[str], value_types: set[str]) -> str:
    if field_type == "String" or field_type in enum_types:
        return "VARCHAR(255)"
    if field_type == "UUID":
        return "BINARY(16)"
    if field_type == "Integer":
        return "INTEGER"
    if field_type == "Boolean":
        return "BOOLEAN"
    if field_type == "BigDecimal":
        return "DECIMAL(38, 18)"
    if field_type == "LocalDate":
        return "DATE"
    if field_type == "LocalDateTime":
        return "TIMESTAMP"
    if field_type == "LocalTime":
        return "TIME"
    if field_type == "byte[]":
        return "LONGBLOB"
    if _is_json_type(field_type, value_types):
        return "JSON"
    return "JSON"


def _snake_case(value: str) -> str:
    first = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", first).lower()


def _table_name(entity_name: str) -> str:
    """DB 예약어와 겹치지 않도록 생성 테이블에 고정 접두사를 붙인다."""

    return f"easydep_{_snake_case(entity_name)}"
