"""구조화된 설계 모델에서 Java 21 BCE 계약을 직접 만든다.

이 모듈은 사람이 보기 위한 PlantUML을 다시 해석하지 않는다. 클래스 설계 단계가 저장한
``BCEModel`` JSON을 검증한 뒤, 같은 입력에는 항상 같은 경로와 같은 UTF-8 내용을 만든다.
생성 결과는 이후 LLM 구현 작업이 따라야 하는 공개 계약이며 업무 동작을 추측하지 않는다.
"""
from __future__ import annotations

import re
import textwrap
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.design.schemas.class_model import (
    AcceptedBCEClass,
    BCEModel,
    ClassOperation,
    DataType,
)

JAVA_SCAFFOLDER_VERSION = "1.3.0"

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
    "string": "String",
    "integer": "Integer",
    "int": "Integer",
    "boolean": "Boolean",
    "bool": "Boolean",
    "bigdecimal": "BigDecimal",
    "decimal": "BigDecimal",
    "uuid": "UUID",
    "localdate": "LocalDate",
    "localdatetime": "LocalDateTime",
    "localtime": "LocalTime",
}
_BINARY_TYPE_NAMES = frozenset({"bytes", "byte[]", "bytes[]"})
_GENERIC_TYPES = {"list": "List", "array": "List", "optional": "Optional"}
_IMPORTS = {
    "BigDecimal": "java.math.BigDecimal",
    "LocalDate": "java.time.LocalDate",
    "LocalDateTime": "java.time.LocalDateTime",
    "LocalTime": "java.time.LocalTime",
    "List": "java.util.List",
    "Optional": "java.util.Optional",
    "UUID": "java.util.UUID",
}
_JAVA_INTERFACE = re.compile(
    r"\bpublic\s+interface\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\b[^\{]*\{"
)
_JAVA_IMPORT = re.compile(r"(?m)^import\s+[^;]+;$")
_OPENAPI_METHOD = re.compile(
    r"(?ms)(?P<mapping>@RequestMapping\s*\(.*?\))\s*"
    r"(?P<signature>(?:(?:public|abstract|default)\s+)*[A-Za-z_$][^;{}]*?)\s*;"
)


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
        """파일을 만들기 전에 Java 이름과 field 선언 형식만 확인한다."""
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
                    for parameter in operation.parameters:
                        _require_identifier(
                            parameter.name, f"parameter in {operation.name}"
                        )
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

        # API schemas and BCE components intentionally use different Java
        # packages (``.api.model`` and ``.bce``).  A domain entity such as
        # ``Course`` commonly appears in both models, and that is not a Java
        # type collision.  The scaffold renders only BCE declarations; the
        # OpenAPI generator owns API model declarations in its own package.
        # Rejecting equal simple names here therefore blocked valid designs
        # before either generator could create a file.
        return self


def render_java_scaffold(scaffold: JavaScaffoldInput) -> dict[str, str]:
    """BCE 타입별 Java 파일을 경로 기준으로 정렬해 반환한다.

    ``sequenceModel``과 ``apiModel``은 같은 설계 snapshot임을 보장하기 위해 입력 계약에
    포함된다. 이 함수가 만드는 BCE 선언에는 class model만 사용하고, HTTP adapter와
    실행 순서는 각 전용 생성·구현 단계가 담당한다.
    """
    package_name = f"{scaffold.base_package}.bce"
    package_path = package_name.replace(".", "/")
    declared_types = {
        *(item.class_name for item in scaffold.bce_model.Classes),
        *(item.name for item in scaffold.bce_model.DataTypes),
    }
    files: dict[str, str] = {}
    for data_type in sorted(scaffold.bce_model.DataTypes, key=lambda item: item.name):
        _require_identifier(data_type.name, "DataType name")
        files[f"{package_path}/{data_type.name}.java"] = _render_data_type(
            package_name, data_type, declared_types
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
            package_name, component, declared_types
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


def render_openapi_controller_scaffold(
    interface_source: str, base_package: str
) -> tuple[str, str]:
    """생성된 OpenAPI interface에서 Controller 선언만 그대로 옮긴다.

    OpenAPI operationId와 DTO 타입을 다시 해석하거나 이름을 추측하지 않는다. 이미
    생성된 interface의 import, method annotation, parameter, 반환 타입을 재사용하고,
    구현 작업은 각 method body의 실패 유도 표식만 교체한다.
    """
    interface = _JAVA_INTERFACE.search(interface_source)
    if interface is None:
        raise ValueError("Generated OpenAPI source does not contain a public interface")
    interface_name = interface.group("name")
    methods = [
        _render_controller_method(
            match.group("mapping"), match.group("signature")
        )
        for match in _OPENAPI_METHOD.finditer(interface_source)
    ]
    if not methods:
        raise ValueError(
            f"Generated OpenAPI interface {interface_name} has no overridable methods"
        )
    controller_name = f"{interface_name}Controller"
    imports = {
        item.strip()
        for item in _JAVA_IMPORT.findall(interface_source)
    }
    imports.add(f"import {base_package}.api.{interface_name};")
    imports.add("import org.springframework.web.bind.annotation.RestController;")
    return (
        controller_name,
        f"package {base_package}.adapter.in.web;\n\n"
        + "\n".join(sorted(imports))
        + "\n\n"
        + "/** OpenAPI interface 선언을 보존하고 업무 본문만 남긴 Controller 골격이다. */\n"
        + "@RestController\n"
        + f"public class {controller_name} implements {interface_name} {{\n\n"
        + "\n\n".join(methods)
        + "\n}\n",
    )


def _render_controller_method(mapping: str, signature: str) -> str:
    """생성 interface의 mapping·선언을 보존한 method body 골격을 만든다."""
    signature = re.sub(
        r"^(?:(?:public|abstract|default)\s+)+", "", signature.strip()
    )
    if "(" not in signature or ")" not in signature:
        raise ValueError("Generated OpenAPI method declaration is incomplete")
    rendered = textwrap.indent(mapping.strip(), "    ") + "\n"
    rendered += "    @Override\n"
    rendered += "    public " + textwrap.indent(signature, "    ").lstrip()
    rendered += " {\n"
    # 앞선 persistence 작업도 전체 Java source를 compile한다. 따라서 골격 자체는
    # compile되어야 하며, use-case 작업의 빠른 완료 검사가 아래 표식을 따로 찾는다.
    rendered += (
        '        throw new UnsupportedOperationException('
        '"EASYDEP_CONTROLLER_BODY_REQUIRED");\n'
    )
    rendered += "    }"
    return rendered


def _render_data_type(
    package_name: str, data_type: DataType, declared_types: set[str]
) -> str:
    """설계 DataType을 enum 또는 record로 렌더링한다."""
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

    fields = [
        _field(item, owner=data_type.name, declared_types=declared_types)
        for item in data_type.fields
    ]
    imports = _render_imports(field_type for _name, field_type, _todo_type in fields)
    if any(todo_type for _name, _field_type, todo_type in fields):
        declarations = ",\n".join(
            f"{_todo(todo_type, indent='    ')}    {field_type} {name}"
            for name, field_type, todo_type in fields
        )
        record_body = f"(\n{declarations}\n)"
    else:
        declarations = ", ".join(
            f"{field_type} {name}" for name, field_type, _todo_type in fields
        )
        record_body = f"({declarations})"
    return (
        f"package {package_name};\n\n{imports}"
        "/** 값과 생성자를 함께 고정하는 Java 21 record 계약이다. */\n"
        f"public record {data_type.name}{record_body} {{}}\n"
    )


def _render_component(
    package_name: str, component: AcceptedBCEClass, declared_types: set[str]
) -> str:
    """BCE class를 설계에 있는 필드와 operation만 가진 최소 Java 선언으로 만든다."""
    signatures: set[str] = set()
    for operation in component.operations:
        parameter_types = ",".join(
            java_type(parameter.type, declared_types=declared_types)
            for parameter in operation.parameters
        )
        signature = f"{operation.name}({parameter_types})"
        if signature in signatures:
            raise ValueError(
                f"{component.class_name} emits duplicate Java signature: {signature}"
            )
        signatures.add(signature)
    methods = [_method_declaration(operation, declared_types) for operation in component.operations]
    fields = [
        _field(item, owner=component.class_name, declared_types=declared_types)
        for item in component.fields
    ]
    method_types = [
        java_type(operation.return_type, declared_types=declared_types)
        for operation in component.operations
    ] + [
        java_type(parameter.type, declared_types=declared_types)
        for operation in component.operations
        for parameter in operation.parameters
    ]
    imports = _render_imports([
        *method_types, *(field_type for _name, field_type, _todo_type in fields)
    ])
    header = f"package {package_name};\n\n{imports}"
    if component.stereotype in {"Boundary", "Control"}:
        interface_methods = "\n".join(
            f"{_todo(todo_types, indent='    ')}    {declaration};"
            for declaration, _return_type, todo_types in methods
        )
        if interface_methods:
            interface_methods += "\n"
        return (
            header
            + f"/** {component.stereotype} 역할의 변경 금지 Java 계약이다. */\n"
            + f"public interface {component.class_name} {{\n{interface_methods}}}\n"
        )

    lines = [
        header + "/** Entity의 상태와 설계에 선언된 연산을 보존하는 초기 코드다. */",
        f"public class {component.class_name} {{",
    ]
    for name, field_type, todo_type in fields:
        lines.append(f"{_todo(todo_type, indent='    ')}    private {field_type} {name};")
    for declaration, return_type, todo_types in methods:
        lines.append("")
        lines.append(f"{_todo(todo_types, indent='    ')}    public {declaration} {{")
        if return_type != "void":
            lines.append("        return null;")
        lines.append("    }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def java_type(design_type: str, *, declared_types: set[str] | None = None) -> str:
    """설계 타입 하나를 컴파일 가능한 Java 타입으로 바꾼다.

    ``declared_types``에는 같은 BCE 설계에 실제로 선언된 class·DataType 이름을 넣는다.
    목록에 없는 타입은 추측하지 않고 ``Object``가 된다. 렌더러는 이때 함께 얻는 TODO
    정보를 사용하지만, 이 공개 helper는 호출자가 바로 쓸 Java 타입 문자열만 반환한다.
    """
    return _java_type(design_type, declared_types=declared_types or set())[0]


def _java_type(
    design_type: str, *, declared_types: set[str]
) -> tuple[str, str | None]:
    """작은 변환표만 적용하고, 모르는 원문은 TODO를 위해 함께 돌려준다."""
    source = re.sub(r"\s+", "", str(design_type))
    if not source:
        return "Object", "(비어 있는 설계 타입)"
    if source.casefold() in _BINARY_TYPE_NAMES:
        return "byte[]", None
    if source == "void":
        return "void", None
    if source.casefold() == "object":
        return "Object", None

    alias = _TYPE_ALIASES.get(source.casefold())
    if alias is not None:
        return alias, None

    generic = re.fullmatch(
        r"(?P<outer>List|Array|Optional)<(?P<argument>.+)>", source, re.IGNORECASE
    )
    if generic is not None:
        argument, todo_type = _java_type(
            generic.group("argument"), declared_types=declared_types
        )
        return f"{_GENERIC_TYPES[generic.group('outer').casefold()]}<{argument}>", (
            source if todo_type else None
        )

    if source in declared_types:
        return source, None
    return "Object", source


def _method_declaration(
    operation: ClassOperation, declared_types: set[str]
) -> tuple[str, str, str | None]:
    """operation의 Java 선언과 TODO가 필요한 원래 타입을 만든다."""
    _require_identifier(operation.name, "operation name")
    parameters: list[str] = []
    todo_types: list[str] = []
    for parameter in operation.parameters:
        _require_identifier(parameter.name, f"parameter in {operation.name}")
        parameter_type, todo_type = _java_type(
            parameter.type, declared_types=declared_types
        )
        parameters.append(f"{parameter_type} {parameter.name}")
        if todo_type:
            todo_types.append(todo_type)
    return_type, todo_type = _java_type(
        operation.return_type, declared_types=declared_types
    )
    if todo_type:
        todo_types.append(todo_type)
    return (
        f"{return_type} {operation.name}({', '.join(parameters)})",
        return_type,
        ", ".join(dict.fromkeys(todo_types)) or None,
    )


def _parse_field(value: str, *, owner: str) -> tuple[str, str]:
    match = _FIELD.fullmatch(value)
    if match is None:
        raise ValueError(f"{owner} has an invalid field declaration: {value}")
    name = match.group("name")
    _require_identifier(name, f"field in {owner}")
    return name, match.group("type")


def _field(
    value: str, *, owner: str, declared_types: set[str]
) -> tuple[str, str, str | None]:
    name, design_type = _parse_field(value, owner=owner)
    field_type, todo_type = _java_type(design_type, declared_types=declared_types)
    return name, field_type, todo_type


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


def _todo(design_type: str | None, *, indent: str) -> str:
    if not design_type:
        return ""
    return f"{indent}// TODO(EasyDep): 설계 타입 `{design_type}`에 맞는 Java 타입으로 교체한다.\n"


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
