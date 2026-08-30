"""typed 설계를 최소 Java 계약으로 바꾸는 공개 동작만 검사한다.

내부 helper나 문자열을 조립하는 순서는 검사하지 않는다. 설계에 적힌 선언만 생성되는지,
불명확한 타입을 추측하지 않는지, 그리고 결과를 Java 컴파일러가 읽을 수 있는지만 확인한다.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.implementation.generation.java_scaffold import (
    JavaScaffoldInput,
    java_type,
    render_java_scaffold,
)


def _payload() -> dict[str, object]:
    """record·enum·BCE 세 역할과 타입 처리 사례를 하나의 입력에 담는다."""
    return {
        "bceModel": {
            "Classes": [
                {
                    "className": "OrderBoundary",
                    "stereotype": "Boundary",
                    "use_case_ids": ["UC-ORDER"],
                    "operations": [{
                        "operationId": "입력값은 BCEModel이 다시 계산한다",
                        "name": "submit",
                        "parameters": [{"name": "request", "type": "OrderRequest"}],
                        "returnType": "OrderReceipt",
                        "stepRefs": ["UC-ORDER:main:1"],
                    }],
                },
                {
                    "className": "OrderControl",
                    "stereotype": "Control",
                    "use_case_ids": ["UC-ORDER"],
                    "operations": [{
                        "operationId": "입력값은 BCEModel이 다시 계산한다",
                        "name": "place",
                        "parameters": [
                            {"name": "request", "type": "OrderRequest"},
                            {"name": "attempt", "type": "integer"},
                        ],
                        "returnType": "optional<OrderReceipt>",
                        "stepRefs": ["UC-ORDER:main:2"],
                    }],
                },
                {
                    "className": "Order",
                    "stereotype": "Entity",
                    "use_case_ids": ["UC-ORDER"],
                    "identifier": ["id"],
                    "fields": [
                        "id : ExternalOrderId",
                        "quantity : integer",
                        "payload : bytes[]",
                        "status : OrderStatus",
                    ],
                    "operations": [
                        {
                            "operationId": "입력값은 BCEModel이 다시 계산한다",
                            "name": "getQuantity",
                            "parameters": [],
                            "returnType": "integer",
                            "stepRefs": ["UC-ORDER:main:3"],
                        },
                        {
                            "operationId": "입력값은 BCEModel이 다시 계산한다",
                            "name": "rename",
                            "parameters": [{"name": "title", "type": "string"}],
                            "returnType": "void",
                            "stepRefs": ["UC-ORDER:main:4"],
                        },
                    ],
                },
            ],
            "DataTypes": [
                {
                    "name": "OrderRequest",
                    "kind": "valueObject",
                    "fields": ["payload : bytes", "source : ExternalPayload"],
                },
                {
                    "name": "OrderReceipt",
                    "kind": "valueObject",
                    "fields": ["accepted : boolean"],
                },
                {
                    "name": "OrderStatus",
                    "kind": "enumeration",
                    "values": ["PENDING", "ACCEPTED"],
                },
            ],
            "Relationships": [],
            "Collaborations": [],
        },
        "sequenceModel": {"Diagrams": []},
        "apiModel": {"endpoints": []},
        "basePackage": "com.example.orders",
        "javaVersion": 21,
        "applicationName": "Orders",
    }


def _render() -> dict[str, str]:
    return render_java_scaffold(JavaScaffoldInput.model_validate(_payload()))


def _source(files: dict[str, str], type_name: str) -> str:
    matches = [
        source for path, source in files.items()
        if path.replace("\\", "/").endswith(f"/{type_name}.java")
    ]
    assert len(matches) == 1
    return matches[0]


def test_renders_only_explicit_bce_contracts() -> None:
    files = _render()
    boundary = _source(files, "OrderBoundary")
    control = _source(files, "OrderControl")
    entity = _source(files, "Order")

    assert "OrderReceipt submit(OrderRequest request);" in boundary
    assert "Optional<OrderReceipt> place(OrderRequest request, Integer attempt);" in control
    assert "Integer getQuantity()" in entity
    assert "void rename(String title)" in entity

    # field에서 getter/setter를 자동으로 만들지 않는다. 설계에 명시한 getQuantity도
    # 정확히 한 번만 출력되므로 과거의 중복 Java signature가 다시 생기지 않는다.
    assert entity.count("getQuantity()") == 1
    assert "getPayload()" not in entity
    assert "setQuantity(" not in entity


def test_uses_small_type_map_and_marks_unknown_types() -> None:
    files = _render()
    request = _source(files, "OrderRequest")
    entity = _source(files, "Order")

    assert "byte[] payload" in request
    assert "Object source" in request
    assert "설계 타입 `ExternalPayload`" in request
    assert "Object id" in entity
    assert "설계 타입 `ExternalOrderId`" in entity

    expected = {
        "string": "String",
        "integer": "Integer",
        "boolean": "Boolean",
        "decimal": "BigDecimal",
        "bytes[]": "byte[]",
        "list<string>": "List<String>",
        "optional<integer>": "Optional<Integer>",
        "UnclearType": "Object",
    }
    assert {source: java_type(source) for source in expected} == expected
    assert java_type("OrderReceipt", declared_types={"OrderReceipt"}) == "OrderReceipt"


def test_same_input_produces_identical_files() -> None:
    request = JavaScaffoldInput.model_validate(_payload())
    assert render_java_scaffold(request) == render_java_scaffold(request)


@pytest.mark.parametrize("bad_name", ["9Order", "Bad-Type", "class"])
def test_rejects_invalid_java_names(bad_name: str) -> None:
    payload = _payload()
    payload["basePackage"] = f"com.example.{bad_name}"
    with pytest.raises(ValidationError):
        JavaScaffoldInput.model_validate(payload)


@pytest.mark.skipif(shutil.which("javac") is None, reason="JDK가 설치된 환경에서만 컴파일한다")
def test_rendered_sources_compile(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    class_root = tmp_path / "classes"
    java_files: list[Path] = []
    rendered = _render()
    for relative, source in rendered.items():
        target = source_root / Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
        java_files.append(target)

    class_root.mkdir()
    result = subprocess.run(
        ["javac", "-encoding", "UTF-8", "-d", str(class_root), *map(str, java_files)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert all(
        re.search(r"\bpublic\s+(?:class|interface|record|enum)\b", source)
        for source in rendered.values()
    )
