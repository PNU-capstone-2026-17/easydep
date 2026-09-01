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

from app.design.contracts.api_spec import ApiSpecModel
from app.design.schemas.class_model import BCEModel
from app.implementation.generation.java_scaffold import (
    JavaScaffoldInput,
    java_type,
    render_java_scaffold,
    render_openapi_controller_scaffold,
)
from app.implementation.generation.persistence_scaffold import (
    render_persistence_scaffold,
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
                    "operations": [
                        {
                            "operationId": "입력값은 BCEModel이 다시 계산한다",
                            "name": "submit",
                            "parameters": [{"name": "request", "type": "OrderRequest"}],
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
                            "operationId": "입력값은 BCEModel이 다시 계산한다",
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
                    "fields": [
                        "accepted : boolean",
                        "id : uuid",
                        "createdAt : LocalDateTime",
                        "tags : array<string>",
                    ],
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
        source
        for path, source in files.items()
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
    receipt = _source(files, "OrderReceipt")
    entity = _source(files, "Order")

    assert "byte[] payload" in request
    assert "Object source" in request
    assert "설계 타입 `ExternalPayload`" in request
    assert "Object id" in entity
    assert "설계 타입 `ExternalOrderId`" in entity
    assert "TODO(EasyDep)" not in receipt

    expected = {
        "string": "String",
        "integer": "Integer",
        "int": "Integer",
        "boolean": "Boolean",
        "bool": "Boolean",
        "decimal": "BigDecimal",
        "uuid": "UUID",
        "LocalDate": "LocalDate",
        "LocalDateTime": "LocalDateTime",
        "LocalTime": "LocalTime",
        "bytes[]": "byte[]",
        "list<string>": "List<String>",
        "array<string>": "List<String>",
        "optional<integer>": "Optional<Integer>",
        "UnclearType": "Object",
    }
    assert {source: java_type(source) for source in expected} == expected
    assert java_type("OrderReceipt", declared_types={"OrderReceipt"}) == "OrderReceipt"


def test_same_input_produces_identical_files() -> None:
    request = JavaScaffoldInput.model_validate(_payload())
    assert render_java_scaffold(request) == render_java_scaffold(request)


def test_erd_entities_generate_persistence_without_an_llm_mapper() -> None:
    """ERD의 확정 필드만으로 JPA·Repository·migration을 바로 만든다."""
    model = BCEModel.model_validate(_payload()["bceModel"])

    files = render_persistence_scaffold(model, "com.example.orders")

    entity = files["src/main/java/com/example/orders/persistence/entity/OrderEntity.java"]
    repository = files[
        "src/main/java/com/example/orders/persistence/repository/OrderRepository.java"
    ]
    migration = files["src/main/resources/db/migration/V1__initial_schema.sql"]
    assert "@Entity" in entity
    assert "@Id" in entity
    assert "private String id;" in entity
    assert "DB 기본 키로 쓸 수 있는 문자열 표현" in entity
    assert "private OrderStatus status;" in entity
    assert "extends JpaRepository<OrderEntity, String>" in repository
    assert "CREATE TABLE easydep_order (" in migration
    assert "id VARCHAR(255) PRIMARY KEY" in migration
    assert all("BcePersistenceMapper" not in path for path in files)
    assert files == render_persistence_scaffold(model, "com.example.orders")


def test_controller_scaffold_preserves_generated_openapi_declarations() -> None:
    """OpenAPI Generator interface 선언을 보존하고 구현 본문만 골격으로 채운다."""
    interface = """package com.example.orders.api;

import com.example.orders.api.model.CreateOrderRequest;
import com.example.orders.api.model.CreateOrderResponse;
import io.swagger.v3.oas.annotations.Parameter;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;

public interface OrdersApi {
    @RequestMapping(
        method = RequestMethod.POST,
        value = "/orders/{orderId}",
        produces = { "application/json" }
    )
    ResponseEntity<CreateOrderResponse> submitOrderWithLongOperationIdentifier(
        @Parameter(name = "orderId", required = true)
        @PathVariable("orderId") String orderId,
        @Valid @RequestBody CreateOrderRequest request);
}
"""

    controller_name, source = render_openapi_controller_scaffold(interface, "com.example.orders")

    assert controller_name == "OrdersApiController"
    assert "public class OrdersApiController implements OrdersApi" in source
    assert "@RequestMapping(" in source
    assert "method = RequestMethod.POST" in source
    assert 'value = "/orders/{orderId}"' in source
    assert (
        "public ResponseEntity<CreateOrderResponse> "
        "submitOrderWithLongOperationIdentifier(" in source
    )
    assert '@PathVariable("orderId") String orderId' in source
    assert "@Valid @RequestBody CreateOrderRequest request" in source
    assert source.count("EASYDEP_CONTROLLER_BODY_REQUIRED") == 1
    assert "EASYDEP_CONTROLLER_BODY_REQUIRED:submitOrderWithLongOperationIdentifier" in source


def test_controller_scaffold_connects_typed_control_without_llm_rewrite() -> None:
    """타입이 완결된 API binding은 생성 시점에 Control과 응답까지 연결한다."""
    interface = """package com.example.orders.api;

import com.example.orders.api.model.OrderReceipt;
import com.example.orders.api.model.OrderRequest;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;

public interface OrdersApi {
    @RequestMapping(method = RequestMethod.POST, value = "/orders")
    ResponseEntity<OrderReceipt> submitOrder(
        @Valid @RequestBody OrderRequest request);
}
"""
    payload = _payload()["bceModel"]
    payload["Classes"][1]["operations"][0]["parameters"] = [
        {"name": "request", "type": "OrderRequest"}
    ]
    payload["Classes"][1]["operations"][0]["returnType"] = "OrderReceipt"
    payload["DataTypes"][0]["fields"] = ["name : string"]
    payload["DataTypes"][1]["fields"] = ["accepted : boolean"]
    bce_model = BCEModel.model_validate(payload)
    api_model = ApiSpecModel.model_validate(
        {
            "Endpoints": [
                {
                    "interaction_id": (
                        "OrderBoundary::submit(request:OrderRequest) -> "
                        "OrderControl::place(request:OrderRequest,attempt:integer)"
                    ),
                    "method": "POST",
                    "path": "/orders",
                    "request_schema": "OrderRequest",
                    "responses": [
                        {
                            "status": 201,
                            "schema_name": "OrderReceipt",
                            "is_array": False,
                        }
                    ],
                    "control_binding": {
                        "control": "OrderControl",
                        "method": "place",
                        "arguments": [{"name": "request", "source": "$body"}],
                        "outcomes": [{"status": 201, "outcome": "created"}],
                    },
                }
            ],
            "Schemas": [
                {
                    "name": "OrderRequest",
                    "fields": [{"name": "name", "type": "string", "required": True}],
                },
                {
                    "name": "OrderReceipt",
                    "fields": [{"name": "accepted", "type": "boolean", "required": True}],
                },
            ],
        }
    )

    _name, source = render_openapi_controller_scaffold(
        interface,
        "com.example.orders",
        api_model=api_model,
        bce_model=bce_model,
    )

    assert "private final OrderControl orderControl;" in source
    assert "private final ObjectMapper objectMapper;" in source
    assert (
        "public OrdersApiController(OrderControl orderControl, ObjectMapper objectMapper)" in source
    )
    assert "var result = orderControl.place(" in source
    assert "com.example.orders.bce.OrderRequest.class" in source
    assert "return ResponseEntity.status(201).body(response);" in source
    assert "EASYDEP_CONTROLLER_BODY_REQUIRED" not in source


def test_controller_keeps_llm_body_when_typed_fields_do_not_match() -> None:
    """API가 요구하는 값을 BCE 결과가 제공하지 못하면 자동 변환을 성공 처리하지 않는다."""
    payload = _payload()["bceModel"]
    payload["Classes"][1]["operations"][0]["parameters"] = [
        {"name": "request", "type": "OrderRequest"}
    ]
    payload["Classes"][1]["operations"][0]["returnType"] = "OrderReceipt"
    payload["DataTypes"][0]["fields"] = ["name : string"]
    payload["DataTypes"][1]["fields"] = ["accepted : boolean"]
    bce_model = BCEModel.model_validate(payload)
    api_model = ApiSpecModel.model_validate(
        {
            "Endpoints": [
                {
                    "interaction_id": "typed interaction",
                    "method": "POST",
                    "path": "/orders",
                    "request_schema": "OrderRequest",
                    "responses": [{"status": 201, "schema_name": "OrderReceipt"}],
                    "control_binding": {
                        "control": "OrderControl",
                        "method": "place",
                        "arguments": [{"name": "request", "source": "$body"}],
                    },
                }
            ],
            "Schemas": [
                {
                    "name": "OrderRequest",
                    "fields": [{"name": "name", "type": "string", "required": True}],
                },
                {
                    "name": "OrderReceipt",
                    "fields": [
                        {"name": "accepted", "type": "boolean", "required": True},
                        {"name": "missingValue", "type": "string", "required": True},
                    ],
                },
            ],
        }
    )
    interface = """package com.example.orders.api;
import com.example.orders.api.model.OrderReceipt;
import com.example.orders.api.model.OrderRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
public interface OrdersApi {
    @RequestMapping(method = RequestMethod.POST, value = "/orders")
    ResponseEntity<OrderReceipt> submitOrder(@RequestBody OrderRequest request);
}
"""

    _name, source = render_openapi_controller_scaffold(
        interface,
        "com.example.orders",
        api_model=api_model,
        bce_model=bce_model,
    )

    assert "private final OrderControl orderControl;" in source
    assert "EASYDEP_CONTROLLER_BODY_REQUIRED:POST:/orders" in source
    assert "ObjectMapper objectMapper" not in source
    assert "missingValue" not in source


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
