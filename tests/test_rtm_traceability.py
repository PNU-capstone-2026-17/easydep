"""Unit tests for EasyDep Implementation RTM Traceability Matrix."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.implementation.application.feedback import assess_feedback_eligibility
from app.implementation.workflows.traceability import build_rtm_traceability_map


def _spec_and_run(
    tmp_path: Path,
    *,
    controls: tuple[str, ...] = ("OrderController",),
) -> tuple[SimpleNamespace, Path]:
    model = {
        "Classes": [
            *[
                {
                    "className": control,
                    "stereotype": "Control",
                    "operations": [
                        {
                            "name": f"start{control.removesuffix('Controller')}",
                            "parameters": [],
                            "returnType": "void",
                        }
                    ],
                }
                for control in controls
            ],
            {"className": "OrderEntity", "stereotype": "Entity", "operations": []},
        ],
    }
    bce_model = tmp_path / "bce-model.json"
    bce_model.write_text(json.dumps(model), encoding="utf-8")
    erd_model = tmp_path / "erd-model.json"
    erd_model.write_text(json.dumps(model), encoding="utf-8")
    api_model = tmp_path / "api-model.json"
    api_model.write_text('{"Endpoints": []}', encoding="utf-8")
    cloud = tmp_path / "resource-spec.json"
    cloud.write_text('{"provider": "aws"}', encoding="utf-8")
    return (
        SimpleNamespace(
            name="orders",
            base_package="com.example.demo",
            inputs={
                "bceModel": bce_model,
                "apiModel": api_model,
                "erdBceModel": erd_model,
                "cloud": cloud,
            },
        ),
        tmp_path / "run",
    )


def _write_manifest(run: Path, tasks: list[dict[str, object]]) -> None:
    reports = run / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "run-manifest.json").write_text(
        json.dumps({"implementation_tasks": tasks}), encoding="utf-8"
    )


def test_rtm_traceability_map_building(tmp_path: Path) -> None:
    spec, run = _spec_and_run(tmp_path)

    rtm_map = build_rtm_traceability_map(spec, run)

    assert rtm_map["schemaVersion"] == "implementation-rtm-traceability/v1alpha2"
    assert rtm_map["basePackage"] == "com.example.demo"
    mappings = {m["element_name"]: m for m in rtm_map["mappings"]}
    assert "OrderController" in mappings
    assert mappings["OrderController"]["contract_level"] == "IMMUTABLE_CONTRACT"
    assert mappings["OrderController"]["origin_artifact"] == "bceModel"
    assert mappings["OrderController"]["verificationStatus"] == "MISSING"
    assert rtm_map["summary"]["missing"] == rtm_map["summary"]["expected"]
    # 리소스 입력만 있고 배포 설계가 미완료라면 Terraform은 선택 산출물이다.
    assert "TerraformMain" not in mappings
    assert "Dockerfile" not in mappings


def test_task_mapping_preserves_direct_operation_and_planning_provenance(
    tmp_path: Path,
) -> None:
    spec, run = _spec_and_run(tmp_path)
    target = (
        "application/src/main/java/com/example/demo/application/impl/"
        "OrderControllerService.java"
    )
    required_test = "application/src/test/java/com/example/demo/OrderFlowTest.java"
    _write_manifest(
        run,
        [
            {
                "task_id": "implement-orders",
                "allowed_write_paths": [target],
                "required_output_paths": [target],
                "requirement_ids": ["REQ-ORDER"],
                "use_case_ids": ["UC-ORDER"],
                "required_test_paths": [required_test],
                "source_artifacts": {
                    "bceModel": str(spec.inputs["bceModel"]),
                    "apiModel": str(spec.inputs["apiModel"]),
                },
                "source_refs": [
                    "api:createOrder",
                    "operation:OrderControl::createOrder(request:OrderRequest)",
                    "workload:orders-app",
                ],
            }
        ],
    )

    rtm_map = build_rtm_traceability_map(spec, run)

    mapping = next(item for item in rtm_map["mappings"] if item["target_file"] == target)
    assert mapping["taskId"] == "implement-orders"
    assert mapping["sourceRefs"] == [
        "api:createOrder",
        "operation:OrderControl::createOrder(request:OrderRequest)",
        "workload:orders-app",
    ]
    assert mapping["requirementIds"] == ["REQ-ORDER"]
    assert mapping["useCaseIds"] == ["UC-ORDER"]
    assert mapping["requiredTestPaths"] == [required_test]
    assert set(mapping["sourceArtifacts"]) == {"apiModel", "bceModel"}

    # Manifest에 소유 파일로 없는 기존 설계 mapping의 provenance는 그대로다.
    contract = next(
        item for item in rtm_map["mappings"] if item["element_name"] == "OrderController"
    )
    assert contract["origin_artifact"] == "bceModel"
    assert contract["origin_element"] == "component OrderController <<control>>"
    assert "taskId" not in contract
    assert "sourceRefs" not in contract


def test_required_outputs_keep_file_ownership_separate_between_tasks(
    tmp_path: Path,
) -> None:
    spec, run = _spec_and_run(
        tmp_path, controls=("OrderController", "BillingController")
    )
    order_file = (
        "application/src/main/java/com/example/demo/application/impl/"
        "OrderControllerService.java"
    )
    billing_file = (
        "application/src/main/java/com/example/demo/application/impl/"
        "BillingControllerService.java"
    )
    shared_edit_scope = [order_file, billing_file]
    _write_manifest(
        run,
        [
            {
                "task_id": "implement-orders",
                "allowed_write_paths": shared_edit_scope,
                "required_output_paths": [order_file],
                "use_case_ids": ["UC-ORDER"],
                "source_refs": ["api:createOrder"],
            },
            {
                "task_id": "implement-billing",
                "allowed_write_paths": shared_edit_scope,
                "required_output_paths": [billing_file],
                "use_case_ids": ["UC-BILLING"],
                "source_refs": ["api:chargeOrder"],
            },
        ],
    )

    rtm_map = build_rtm_traceability_map(spec, run)

    by_target = {item["target_file"]: item for item in rtm_map["mappings"]}
    assert by_target[order_file]["taskId"] == "implement-orders"
    assert by_target[order_file]["sourceRefs"] == ["api:createOrder"]
    assert by_target[billing_file]["taskId"] == "implement-billing"
    assert by_target[billing_file]["sourceRefs"] == ["api:chargeOrder"]


def test_evaluate_feedback_with_rtm_rejects_design_contract_changes() -> None:
    result = assess_feedback_eligibility("OpenAPI 엔드포인트와 응답 DTO 스키마를 변경해줘")
    assert result["status"] == "UNSUITABLE"
    assert result["rtmValidated"] is True


def test_evaluate_feedback_with_rtm_accepts_pure_implementation_edits() -> None:
    result = assess_feedback_eligibility("배송 시작 로직의 예외 처리 문구를 다듬어줘")
    assert result["status"] == "ELIGIBLE"
    assert result["rtmValidated"] is True
