"""typed 설계 모델에서 Java 초기 코드를 만드는 공개 계약을 검사한다.

이 테스트는 생성기의 내부 함수나 문자열 조립 순서에 기대지 않는다. 사용자가 실제로
호출하는 ``JavaScaffoldInput``과 ``render_java_scaffold``만 사용하고, 생성 결과에 필요한
Java 선언이 있는지와 Java 컴파일러가 결과를 읽을 수 있는지를 확인한다.
"""

from __future__ import annotations

import copy
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


def _scaffold_payload() -> dict[str, object]:
    """record, enum, BCE 세 종류와 주요 Java 타입을 한 입력에 담는다."""

    return {
        "bceModel": {
            "Classes": [
                {
                    "className": "OrderBoundary",
                    "stereotype": "Boundary",
                    "use_case_ids": ["UC-ORDER"],
                    "operations": [
                        {
                            "operationId": "입력에서 받은 값은 BCEModel이 다시 계산한다",
                            "name": "submit",
                            "parameters": [
                                {"name": "request", "type": "OrderRequest"}
                            ],
                            "returnType": "OrderReceipt",
                            "stepRefs": ["UC-ORDER:main:1"],
                        }
                    ],
                },
                {
                    "className": "OrderControl",
                    "stereotype": "Control",
                    "use_case_ids": ["UC-ORDER"],
                    "operations": [
                        {
                            "operationId": "입력에서 받은 값은 BCEModel이 다시 계산한다",
                            "name": "place",
                            "parameters": [
                                {"name": "request", "type": "OrderRequest"},
                                {"name": "attempt", "type": "integer"},
                            ],
                            "returnType": "optional<OrderReceipt>",
                            "stepRefs": ["UC-ORDER:main:2"],
                        }
                    ],
                },
                {
                    "className": "Order",
                    "stereotype": "Entity",
                    "use_case_ids": ["UC-ORDER"],
                    "identifier": ["id"],
                    "fields": [
                        "id : uuid",
                        "quantity : integer",
                        "total : decimal",
                        "createdAt : datetime",
                        "tags : list<String>",
                        "note : optional<String>",
                        "creditHours : optional<integer>",
                        "status : OrderStatus",
                    ],
                    "operations": [
                        {
                            "operationId": "입력에서 받은 값은 BCEModel이 다시 계산한다",
                            "name": "rename",
                            "parameters": [{"name": "title", "type": "string"}],
                            "returnType": "void",
                            "stepRefs": ["UC-ORDER:main:3"],
                        }
                    ],
                },
            ],
            "DataTypes": [
                {
                    "name": "OrderRequest",
                    "kind": "valueObject",
                    "fields": ["itemIds : list<uuid>", "requestedAt : datetime"],
                },
                {
                    "name": "OrderReceipt",
                    "kind": "valueObject",
                    "fields": ["orderId : uuid", "accepted : boolean"],
                },
                {
                    "name": "OrderStatus",
                    "kind": "enumeration",
                    "values": ["PENDING", "ACCEPTED", "REJECTED"],
                },
            ],
            "Relationships": [],
            "Collaborations": [],
        },
        # 스캐폴더는 두 모델을 임의로 다시 해석하지 않는다. 다만 job 입력에서 누락되지
        # 않았는지 검증할 수 있도록 현재 단계의 typed JSON을 그대로 받는다.
        "sequenceModel": {"Diagrams": []},
        "apiModel": {"endpoints": []},
        "basePackage": "com.example.orders",
        "javaVersion": 21,
        "applicationName": "Orders",
    }


def _render() -> dict[str, str]:
    request = JavaScaffoldInput.model_validate(_scaffold_payload())
    return render_java_scaffold(request)


def _java_source(files: dict[str, str], type_name: str) -> str:
    """출력 루트의 표현과 무관하게 Java 타입 파일 하나를 찾는다."""

    matches = [
        source
        for path, source in files.items()
        if path.replace("\\", "/").endswith(f"/{type_name}.java")
        or path == f"{type_name}.java"
    ]
    assert len(matches) == 1, f"{type_name}.java가 하나여야 한다: {sorted(files)}"
    return matches[0]


def test_renders_records_enum_and_all_three_bce_kinds() -> None:
    files = _render()

    request = _java_source(files, "OrderRequest")
    status = _java_source(files, "OrderStatus")
    boundary = _java_source(files, "OrderBoundary")
    control = _java_source(files, "OrderControl")
    entity = _java_source(files, "Order")

    assert "package com.example.orders.bce;" in request
    assert re.search(r"\bpublic\s+record\s+OrderRequest\s*\(", request)
    assert re.search(r"\bpublic\s+enum\s+OrderStatus\b", status)
    assert all(value in status for value in ("PENDING", "ACCEPTED", "REJECTED"))

    # Boundary와 Control은 후속 구현 작업이 구현체를 붙일 수 있는 계약이어야 한다.
    assert re.search(r"\bpublic\s+interface\s+OrderBoundary\b", boundary)
    assert re.search(r"\bOrderReceipt\s+submit\s*\(OrderRequest\s+request\)\s*;", boundary)
    assert re.search(r"\bpublic\s+interface\s+OrderControl\b", control)
    assert re.search(
        r"\bOptional<OrderReceipt>\s+place\s*\(OrderRequest\s+request,\s*int\s+attempt\)\s*;",
        control,
    )

    # Entity는 외부 도구가 정한 메서드를 덧붙이지 않고, typed 모델에 선언된 내용만
    # 컴파일 가능한 클래스 형태로 옮긴다.
    assert re.search(r"\bpublic\s+class\s+Order\b", entity)
    assert re.search(r"\bUUID\s+id\b", entity)
    assert re.search(r"\bvoid\s+rename\s*\(String\s+title\)", entity)
    assert "stop(" not in entity


def test_maps_scalar_collection_optional_and_time_types() -> None:
    files = _render()
    request = _java_source(files, "OrderRequest")
    entity = _java_source(files, "Order")

    # 별칭을 그대로 Java 코드에 쓰면 컴파일되지 않는다. 아래 검사는 타입마다 import를
    # 어느 줄에 두는지 대신, 최종 선언에서 올바른 Java 이름을 썼는지만 확인한다.
    expected_entity_declarations = (
        r"\bUUID\s+id\b",
        r"\bint\s+quantity\b",
        r"\bBigDecimal\s+total\b",
        r"\bOffsetDateTime\s+createdAt\b",
        r"\bList<String>\s+tags\b",
        r"\bOptional<String>\s+note\b",
        r"\bOptional<Integer>\s+creditHours\b",
    )
    assert all(re.search(pattern, entity) for pattern in expected_entity_declarations)
    assert re.search(r"\bList<UUID>\s+itemIds\b", request)
    assert re.search(r"\bOffsetDateTime\s+requestedAt\b", request)


@pytest.mark.parametrize(
    ("design_type", "expected"),
    [
        ("optional<integer>", "Optional<Integer>"),
        ("list<int>", "List<Integer>"),
        ("set<boolean>", "Set<Boolean>"),
        ("map<string, double>", "Map<String,Double>"),
        ("byte[]", "byte[]"),
    ],
)
def test_boxes_primitive_types_inside_java_generics(
    design_type: str, expected: str
) -> None:
    assert java_type(design_type) == expected


def test_same_typed_input_produces_byte_identical_files() -> None:
    request = JavaScaffoldInput.model_validate(_scaffold_payload())

    first = render_java_scaffold(request)
    second = render_java_scaffold(request)

    assert list(first) == list(second)
    assert {path: text.encode("utf-8") for path, text in first.items()} == {
        path: text.encode("utf-8") for path, text in second.items()
    }


def test_allows_api_schema_with_the_same_domain_name_as_bce_entity() -> None:
    """OpenAPI models and BCE entities are emitted into distinct packages."""
    payload = _scaffold_payload()
    payload["apiModel"] = {"Schemas": [{"name": "Order"}]}

    request = JavaScaffoldInput.model_validate(payload)
    files = render_java_scaffold(request)

    assert "public class Order" in _java_source(files, "Order")


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("basePackage", "com.example.bad-package"),
        ("basePackage", "com.class.orders"),
    ],
)
def test_rejects_invalid_java_package(field: str, bad_value: str) -> None:
    payload = _scaffold_payload()
    payload[field] = bad_value

    with pytest.raises(ValidationError):
        JavaScaffoldInput.model_validate(payload)


@pytest.mark.parametrize("bad_name", ["9Order", "Bad-Type", "class"])
def test_rejects_invalid_java_type_identifier(bad_name: str) -> None:
    payload = copy.deepcopy(_scaffold_payload())
    payload["bceModel"]["Classes"][2]["className"] = bad_name  # type: ignore[index]

    with pytest.raises(ValidationError):
        JavaScaffoldInput.model_validate(payload)


@pytest.mark.skipif(shutil.which("javac") is None, reason="JDK가 설치된 환경에서만 컴파일한다")
def test_rendered_java_sources_compile_with_javac(tmp_path: Path) -> None:
    files = _render()
    source_root = tmp_path / "source"
    class_root = tmp_path / "classes"
    java_files: list[Path] = []

    for relative, source in files.items():
        if not relative.endswith(".java"):
            continue
        # 생성기가 반환한 상대 경로가 작업 디렉터리 밖으로 나갈 수 없다는 것도 함께
        # 확인한다. 실제 구현 경로에서는 이 dict를 그대로 파일로 저장하기 때문이다.
        normalized = Path(relative.replace("/", "\\"))
        assert not normalized.is_absolute()
        assert ".." not in normalized.parts
        target = source_root / normalized
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
        java_files.append(target)

    assert java_files
    class_root.mkdir()
    result = subprocess.run(
        ["javac", "-encoding", "UTF-8", "-d", str(class_root), *map(str, java_files)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stderr
