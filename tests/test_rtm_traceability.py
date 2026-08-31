"""Unit tests for EasyDep Implementation RTM Traceability Matrix."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from app.implementation.application.feedback import assess_feedback_eligibility
from app.implementation.workflows.traceability import build_rtm_traceability_map


def test_rtm_traceability_map_building() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        bce_model = root / "bce-model.json"
        model = {
            "Classes": [
                {"className": "OrderController", "stereotype": "Control",
                 "operations": [{"name": "startOrder", "parameters": [], "returnType": "void"}]},
                {"className": "OrderEntity", "stereotype": "Entity", "operations": []},
            ],
        }
        bce_model.write_text(json.dumps(model), encoding="utf-8")
        erd_model = root / "erd-model.json"
        erd_model.write_text(json.dumps(model), encoding="utf-8")
        api_model = root / "api-model.json"
        api_model.write_text(json.dumps({"Endpoints": []}), encoding="utf-8")
        cloud = root / "resource-spec.json"
        cloud.write_text('{"provider": "aws"}', encoding="utf-8")

        spec = SimpleNamespace(
            name="orders",
            base_package="com.example.demo",
            inputs={
                "bceModel": bce_model,
                "apiModel": api_model,
                "erdBceModel": erd_model,
                "cloud": cloud,
            },
        )

        rtm_map = build_rtm_traceability_map(spec, root / "run")

        assert rtm_map["schemaVersion"] == "implementation-rtm-traceability/v1alpha1"
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


def test_evaluate_feedback_with_rtm_rejects_design_contract_changes() -> None:
    result = assess_feedback_eligibility("OpenAPI 엔드포인트와 응답 DTO 스키마를 변경해줘")
    assert result["status"] == "UNSUITABLE"
    assert result["rtmValidated"] is True


def test_evaluate_feedback_with_rtm_accepts_pure_implementation_edits() -> None:
    result = assess_feedback_eligibility("배송 시작 로직의 예외 처리 문구를 다듬어줘")
    assert result["status"] == "ELIGIBLE"
    assert result["rtmValidated"] is True
