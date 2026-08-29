"""구조화된 설계 모델에서 Java 21 BCE 계약을 직접 만든다.

이 모듈은 사람이 보기 위한 PlantUML을 다시 해석하지 않는다. 클래스 설계 단계가 저장한
``BCEModel`` JSON을 검증한 뒤, 같은 입력에는 항상 같은 경로와 같은 UTF-8 내용을 만든다.
생성 결과는 이후 LLM 구현 작업이 따라야 하는 공개 계약이며 업무 동작을 추측하지 않는다.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.design.schemas.class_model import (
    AcceptedBCEClass,
    BCEModel,
    ClassOperation,
    DataType,
)

JAVA_SCAFFOLDER_VERSION = "1.0.0"

_JAVA_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_FIELD = re.compile(
    r"^\s*[+#~\-]?\s*(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*:\s*(?P<type>.+?)\s*$"
)
_JAVA_KEYWORDS = frozenset({
    "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char",
    "class", "const", "continue", "default", "do", "double", "else", "enum",
    "extends", "final", "finally", "float", "for", "goto", "if", "implements",
    "import", "instanceof", "int", "interface", "long", "native", "new", "package",
    "private", "protected", "public", "return", "short", "static", "strictfp",
    "super", "switch", "synchronized", "this", "throw", "throws", "transient",
    "try", "void", "volatile", "while", "record", "sealed", "permits", "yield",
})
_TYPE_ALIASES = {
    "any": "Object",
    "object": "Object",
    "string": "String",
    "str": "String",
    "integer": "int",
    "int": "int",
    "long": "long",
    "short": "short",
    "byte": "byte",
    "float": "double",
    "double": "double",
    "bool": "boolean",
    "boolean": "boolean",
    "character": "char",
    "char": "char",
    "biginteger": "BigInteger",
    "bigdecimal": "BigDecimal",
    "decimal": "BigDecimal",
    "number": "BigDecimal",
    "uuid": "UUID",
    "guid": "UUID",
    "localdate": "LocalDate",
    "date": "LocalDate",
    "localtime": "LocalTime",
    "time": "LocalTime",
    "localdatetime": "LocalDateTime",
    "offsetdatetime": "OffsetDateTime",
    "datetime": "OffsetDateTime",
    "instant": "Instant",
    "timestamp": "Instant",
    "list": "List",
    "array": "List",
    "collection": "List",
    "page": "List",
    "set": "Set",
    "map": "Map",
    "optional": "Optional",
    "iterable": "Iterable",
    "void": "void",
}
_IMPORTS = {
    "BigDecimal": "java.math.BigDecimal",
    "BigInteger": "java.math.BigInteger",
    "Instant": "java.time.Instant",
    "LocalDate": "java.time.LocalDate",
    "LocalDateTime": "java.time.LocalDateTime",
    "LocalTime": "java.time.LocalTime",
    "OffsetDateTime": "java.time.OffsetDateTime",
    "Iterable": "java.lang.Iterable",
    "List": "java.util.List",
    "Map": "java.util.Map",
    "Optional": "java.util.Optional",
    "Set": "java.util.Set",
    "UUID": "java.util.UUID",
}


class JavaScaffoldInput(BaseModel):
    """Java 생성에 필요한 설계 snapshot을 한 번에 검증하는 입력 계약."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    bce_model: BCEModel = Field(alias="bceModel")
    sequence_model: dict[str, Any] = Field(alias="sequenceModel")
    api_model: dict[str, Any] = Field(alias="apiModel")
    erd_bce_model: BCEModel | None = Field(default=None, alias="erdBceModel")
    base_package: str = Field(alias="basePackage", min_length=1)
    java_version: Literal[21] = Field(default=21, alias="javaVersion")
    application_name: str = Field(alias="applicationName", min_length=1)

    @field_validator("base_package")
    @classmethod
    def validate_base_package(cls, value: str) -> str:
        """점으로 나눈 모든 package 조각이 Java 식별자인지 확인한다."""
        parts = value.split(".")
        if any(not _valid_identifier(part) for part in parts):
            raise ValueError("basePackage must contain valid Java identifiers")
        return value

    @field_validator("sequence_model", "api_model")
    @classmethod
    def require_structured_model(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("structured design model must not be empty")
        return value

    @model_validator(mode="after")
    def validate_java_names(self) -> JavaScaffoldInput:
        """파일을 만들기 전에 모든 Java 이름과 field 선언을 확인한다."""
        models = [self.bce_model]
        if self.erd_bce_model is not None:
            models.append(self.erd_bce_model)
        for model in models:
            for data_type in model.DataTypes:
                _require_identifier(data_type.name, "DataType name")
                for value in data_type.values:
                    _require_identifier(value, f"enum value in {data_type.name}")
                for field in data_type.fields:
                    _parse_field(field, owner=data_type.name)
            for component in model.Classes:
                _require_identifier(component.class_name, "className")
                for field in component.fields:
                    _parse_field(field, owner=component.class_name)
                for operation in component.operations:
                    _require_identifier(operation.name, "operation name")
                    java_type(operation.return_type)
                    for parameter in operation.parameters:
                        _require_identifier(
                            parameter.name, f"parameter in {operation.name}"
                        )
                        java_type(parameter.type)
        if self.erd_bce_model is not None:
            class_entities = {
                item.class_name
                for item in self.bce_model.Classes
                if item.stereotype == "Entity"
            }
            erd_entities = {
                item.class_name
                for item in self.erd_bce_model.Classes
                if item.stereotype == "Entity"
            }
            if class_entities != erd_entities:
                raise ValueError(
                    "bceModel and erdBceModel must contain the same Entity names"
                )
            class_relations = _entity_relation_pairs(self.bce_model, class_entities)
            erd_relations = _entity_relation_pairs(self.erd_bce_model, erd_entities)
            if class_relations != erd_relations:
                raise ValueError(
                    "bceModel and erdBceModel contain different Entity relationships"
                )

        api_schema_names = {
            str(item.get("name"))
            for item in self.api_model.get("Schemas", [])
            if isinstance(item, dict) and item.get("name")
        }
        component_names = {item.class_name for item in self.bce_model.Classes}
        collisions = sorted(api_schema_names & component_names)
        if collisions:
            raise ValueError(
                "API schema names collide with BCE component names: "
                + ", ".join(collisions)
            )
        return self


def render_java_scaffold(scaffold: JavaScaffoldInput) -> dict[str, str]:
    """BCE 타입별 Java 파일을 경로 기준으로 정렬해 반환한다.

    ``sequenceModel``과 ``apiModel``은 같은 설계 snapshot임을 보장하기 위해 입력 계약에
    포함된다. 이 함수가 만드는 BCE 선언에는 class model만 사용하고, HTTP adapter와
    실행 순서는 각 전용 생성·구현 단계가 담당한다.
    """
    package_name = f"{scaffold.base_package}.bce"
    package_path = package_name.replace(".", "/")
    files: dict[str, str] = {}
    for data_type in sorted(scaffold.bce_model.DataTypes, key=lambda item: item.name):
        _require_identifier(data_type.name, "DataType name")
        files[f"{package_path}/{data_type.name}.java"] = _render_data_type(
            package_name, data_type
        )
    erd_entities = {
        item.class_name: item
        for item in (scaffold.erd_bce_model.Classes if scaffold.erd_bce_model else [])
        if item.stereotype == "Entity"
    }
    for component in sorted(scaffold.bce_model.Classes, key=lambda item: item.class_name):
        _require_identifier(component.class_name, "className")
        if component.stereotype == "Entity" and component.class_name in erd_entities:
            erd_component = erd_entities[component.class_name]
            component = component.model_copy(update={
                "fields": list(erd_component.fields),
                "identifier": list(erd_component.identifier),
            })
        files[f"{package_path}/{component.class_name}.java"] = _render_component(
            package_name, component
        )
    return dict(sorted(files.items()))


def build_java_scaffold_trace(
    scaffold: JavaScaffoldInput, files: dict[str, str]
) -> dict[str, Any]:
    """생성 파일을 BCE class·operation·use case와 연결하는 추적 정보를 만든다."""
    path_by_name = {path.rsplit("/", 1)[-1].removesuffix(".java"): path for path in files}
    mappings: list[dict[str, Any]] = []
    for component in sorted(scaffold.bce_model.Classes, key=lambda item: item.class_name):
        mappings.append({
            "file": path_by_name[component.class_name],
            "type": component.class_name,
            "kind": component.stereotype,
            "useCaseIds": sorted(set(component.use_case_ids)),
            "operations": [
                {
                    "operationId": operation.operation_id,
                    "stepRefs": list(operation.step_refs),
                }
                for operation in component.operations
            ],
        })
    for data_type in sorted(scaffold.bce_model.DataTypes, key=lambda item: item.name):
        mappings.append({
            "file": path_by_name[data_type.name],
            "type": data_type.name,
            "kind": data_type.kind,
            "useCaseIds": [],
            "operations": [],
        })
    return {
        "schemaVersion": "easydep-java-scaffold-trace/v1",
        "generatorVersion": JAVA_SCAFFOLDER_VERSION,
        "applicationName": scaffold.application_name,
        "mappings": mappings,
    }


def _render_data_type(package_name: str, data_type: DataType) -> str:
    if data_type.kind == "enumeration":
        values = []
        for value in data_type.values:
            _require_identifier(value, f"enum value in {data_type.name}")
            values.append(value)
        body = ",\n".join(f"    {value}" for value in values)
        return (
            f"package {package_name};\n\n"
            "/** 클래스 설계에서 생성한 열거형 계약이다. */\n"
            f"public enum {data_type.name} {{\n{body}\n}}\n"
        )

    fields = [_parse_field(item, owner=data_type.name) for item in data_type.fields]
    declarations = ", ".join(f"{field_type} {name}" for name, field_type in fields)
    imports = _render_imports(field_type for _name, field_type in fields)
    return (
        f"package {package_name};\n\n{imports}"
        "/** 값과 생성자를 함께 고정하는 Java 21 record 계약이다. */\n"
        f"public record {data_type.name}({declarations}) {{}}\n"
    )


def _render_component(package_name: str, component: AcceptedBCEClass) -> str:
    method_types = [
        java_type(operation.return_type)
        for operation in component.operations
    ] + [
        java_type(parameter.type)
        for operation in component.operations
        for parameter in operation.parameters
    ]
    fields = [_parse_field(item, owner=component.class_name) for item in component.fields]
    imports = _render_imports([*method_types, *(kind for _name, kind in fields)])
    header = f"package {package_name};\n\n{imports}"
    if component.stereotype in {"Boundary", "Control"}:
        methods = "\n".join(
            f"    {_method_declaration(operation)};"
            for operation in component.operations
        )
        if methods:
            methods += "\n"
        return (
            header
            + f"/** {component.stereotype} 역할의 변경 금지 Java 계약이다. */\n"
            + f"public interface {component.class_name} {{\n{methods}}}\n"
        )

    lines = [
        header + "/** Entity의 상태와 설계에 선언된 연산을 보존하는 초기 코드다. */",
        f"public class {component.class_name} {{",
    ]
    for name, field_type in fields:
        lines.append(f"    private {field_type} {name};")
    if fields:
        lines.append("")
    lines.extend([
        f"    public {component.class_name}() {{}}",
    ])
    if fields:
        parameters = ", ".join(f"{field_type} {name}" for name, field_type in fields)
        lines.extend(["", f"    public {component.class_name}({parameters}) {{"])
        lines.extend(f"        this.{name} = {name};" for name, _field_type in fields)
        lines.append("    }")
        for name, field_type in fields:
            suffix = name[:1].upper() + name[1:]
            lines.extend([
                "",
                f"    public {field_type} get{suffix}() {{",
                f"        return this.{name};",
                "    }",
                "",
                f"    public void set{suffix}({field_type} {name}) {{",
                f"        this.{name} = {name};",
                "    }",
            ])
    for operation in component.operations:
        lines.extend(["", f"    public {_method_declaration(operation)} {{"])
        statement = _default_return(operation.return_type, component.class_name)
        if statement:
            lines.append(f"        {statement}")
        lines.append("    }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def java_type(design_type: str) -> str:
    """설계 타입 어휘를 닫힌 Java 타입 어휘로 바꾼다."""
    source = re.sub(r"\s+", "", str(design_type))
    if not source:
        raise ValueError("Java type must not be empty")

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        return _TYPE_ALIASES.get(token.casefold(), token)

    converted = re.sub(r"[A-Za-z_$][A-Za-z0-9_$]*", replace, source)
    if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*(?:<[A-Za-z0-9_$<>,?]+>)?", converted):
        raise ValueError(f"unsupported Java design type: {design_type}")
    return converted


def _method_declaration(operation: ClassOperation) -> str:
    _require_identifier(operation.name, "operation name")
    parameters = []
    for parameter in operation.parameters:
        _require_identifier(parameter.name, f"parameter in {operation.name}")
        parameters.append(f"{java_type(parameter.type)} {parameter.name}")
    return f"{java_type(operation.return_type)} {operation.name}({', '.join(parameters)})"


def _parse_field(value: str, *, owner: str) -> tuple[str, str]:
    match = _FIELD.fullmatch(value)
    if match is None:
        raise ValueError(f"{owner} has an invalid field declaration: {value}")
    name = match.group("name")
    _require_identifier(name, f"field in {owner}")
    return name, java_type(match.group("type"))


def _render_imports(types: Any) -> str:
    imports = sorted({
        import_path
        for value in types
        for token, import_path in _IMPORTS.items()
        if re.search(rf"\b{re.escape(token)}\b", value)
        and not import_path.startswith("java.lang.")
    })
    if not imports:
        return ""
    return "".join(f"import {path};\n" for path in imports) + "\n"


def _default_return(return_type: str, owner: str) -> str:
    java = java_type(return_type)
    if java == "void":
        return ""
    if java == owner:
        return "return this;"
    if java == "boolean":
        return "return false;"
    if java == "char":
        return "return '\\0';"
    if java in {"byte", "short", "int", "long", "float", "double"}:
        return "return 0;"
    if java.startswith("Optional<"):
        return "return Optional.empty();"
    if java.startswith("List<"):
        return "return List.of();"
    if java.startswith("Set<"):
        return "return Set.of();"
    if java.startswith("Map<"):
        return "return Map.of();"
    return "return null;"


def _valid_identifier(value: str) -> bool:
    return bool(_JAVA_IDENTIFIER.fullmatch(value)) and value not in _JAVA_KEYWORDS


def _require_identifier(value: str, label: str) -> None:
    if not _valid_identifier(value):
        raise ValueError(f"{label} is not a valid Java identifier: {value}")


def _entity_relation_pairs(
    model: BCEModel, entity_names: set[str]
) -> set[tuple[str, str]]:
    return {
        (
            min(relation.source, relation.target),
            max(relation.source, relation.target),
        )
        for relation in model.Relationships
        if relation.source in entity_names and relation.target in entity_names
    }
