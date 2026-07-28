"""행 → 성능 레코드 투영 테스트.

핵심 관심사: **세 프로바이더의 상시 CPU 판정이 다른 개념에서 온다**는 것이 레코드에
정직하게 드러나는가(근거·신뢰도). 하나로 뭉개면 거짓말이 된다.
"""

from __future__ import annotations

import json

import jsonschema

from app.deployment.perfkb.dataset import _schema
from app.deployment.perfkb.parsers.build import build_dataset, format_audit, project_rows
from app.deployment.perfkb.parsers.project import project_row
from app.deployment.tests.test_perfkb_details import (
    EBS_INFO,
    NETWORK_INFO,
    PROCESSOR_INFO,
    VCPU_INFO,
)


def _details(pairs) -> str:
    return json.dumps([{"key": k, "value": v} for k, v in pairs])


def aws_row(spec="t3.medium", burstable="true", current_gen="true", **over) -> dict:
    row = {
        "id": f"aws+us-east-1+{spec}",
        "namespace": "system",
        "provider_name": "aws",
        "csp_spec_name": spec,
        "details": _details([
            ("BurstablePerformanceSupported", burstable),
            ("CurrentGeneration", current_gen),
            ("BareMetal", "false"),
            ("EbsInfo", EBS_INFO),
            ("NetworkInfo", NETWORK_INFO),
            ("ProcessorInfo", PROCESSOR_INFO),
            ("VCpuInfo", VCPU_INFO),
        ]),
    }
    row.update(over)
    return row


def gcp_row(spec="e2-micro", shared="true", **over) -> dict:
    row = {
        "id": f"gcp+us-central1+{spec}",
        "namespace": "system",
        "provider_name": "gcp",
        "csp_spec_name": spec,
        "details": _details([
            ("IsSharedCpu", shared),
            ("Description", "Efficient Instance, 8 vCPUs, 64 GB RAM"),
            ("MaximumPersistentDisks", "128"),
            ("MaximumPersistentDisksSizeGb", "263168"),
        ]),
    }
    row.update(over)
    return row


def azure_row(spec="Standard_B2s", family="standardBSFamily", acu=None, **over) -> dict:
    pairs = [("Family", family), ("vCPUsPerCore", "2"), ("UncachedDiskIOPS", "3200"),
             ("AcceleratedNetworkingEnabled", "True"), ("PremiumIO", "True")]
    if acu is not None:
        pairs.append(("ACUs", acu))
    row = {
        "id": f"azure+eastus+{spec}",
        "namespace": "system",
        "provider_name": "azure",
        "csp_spec_name": spec,
        "details": _details(pairs),
    }
    row.update(over)
    return row


# --- sustainedCpu: 세 프로바이더, 세 메커니즘 ---


def test_aws_burstable_is_not_sustained_and_is_stated() -> None:
    rec = project_row(aws_row(burstable="true"))
    assert rec["sustainedCpu"]["value"] is False
    assert rec["sustainedCpu"]["evidence"] == "aws-burstable-field"
    assert rec["sustainedCpu"]["basis"] == "stated"
    assert "credits" in rec["sustainedCpu"]["note"]


def test_aws_non_burstable_is_an_inference_not_a_statement() -> None:
    """**P1 회귀**: "버스트 아님"에서 "상시 보장"을 끌어내는 건 추론이다.

    AWS 필드는 한 방향만 직접 말한다 — `BurstablePerformanceSupported: true`면
    "버스트다"이지만, false는 "버스트로 분류하지 않는다"까지다. 그 추론은 실제로
    깨진다: `t1.micro`는 false를 받지만 T2 크레딧 모델보다 앞선 세대라서일 뿐,
    상시 성능이 보장되지 않는다. 예전엔 이걸 신뢰도 1.0으로 단언했다(8건).
    """
    rec = project_row(aws_row(spec="m5.large", burstable="false"))
    assert rec["sustainedCpu"]["value"] is True
    assert rec["sustainedCpu"]["evidence"] == "aws-non-burstable-inferred"
    assert rec["sustainedCpu"]["basis"] == "inferred"
    assert "inferred" in rec["sustainedCpu"]["note"]


def test_aws_burstable_is_stated_by_the_field() -> None:
    """반대 방향은 필드가 직접 말한다 — 경고는 여기 걸려 있으므로 사실이어야 한다."""
    rec = project_row(aws_row(spec="t3.micro", burstable="true"))
    assert rec["sustainedCpu"]["value"] is False
    assert rec["sustainedCpu"]["evidence"] == "aws-burstable-field"
    assert rec["sustainedCpu"]["basis"] == "stated"


def test_gcp_shared_cpu_is_a_different_mechanism_than_aws_burst() -> None:
    """공유 코어는 크레딧 모델이 아니다 — note가 그 차이를 담아야 한다."""
    rec = project_row(gcp_row(shared="true"))
    assert rec["sustainedCpu"]["value"] is False
    assert rec["sustainedCpu"]["evidence"] == "gcp-shared-cpu-field"
    assert rec["sustainedCpu"]["basis"] == "stated"
    assert "shared" in rec["sustainedCpu"]["note"].lower()
    assert "credit" not in rec["sustainedCpu"]["note"].lower()


def test_azure_family_inference_is_marked_as_a_guess() -> None:
    """이름 규칙 추론이라 1.0이 아니다 — B가 아닌 버스트 패밀리가 생기면 놓친다."""
    rec = project_row(azure_row(family="standardBsv2Family"))
    assert rec["sustainedCpu"]["value"] is False
    assert rec["sustainedCpu"]["evidence"] == "azure-family-name"
    assert rec["sustainedCpu"]["basis"] == "inferred"


def test_azure_family_match_is_case_insensitive() -> None:
    """실측상 B계열은 전부 소문자였지만 Family 표기는 섞여 있다(StandardFXmsv2Family).
    대소문자를 구분하면 표기가 바뀌는 순간 경고가 조용히 사라진다."""
    assert project_row(azure_row(family="StandardBsv2Family"))["sustainedCpu"]["value"] is False
    assert project_row(azure_row(family="standardBsv2Family"))["sustainedCpu"]["value"] is False


def test_azure_non_burst_family_is_sustained() -> None:
    rec = project_row(azure_row(spec="Standard_D2s_v3", family="standardDSv3Family"))
    assert rec["sustainedCpu"]["value"] is True


def test_unknown_provider_gets_no_sustained_verdict() -> None:
    """신호를 추적 못 한 프로바이더는 '보장된다'가 아니라 '모른다'다."""
    row = aws_row(provider_name="tencent", id="tencent+ap-seoul+S5.MEDIUM4")
    rec = project_row(row)
    assert rec is None or "sustainedCpu" not in rec


def test_missing_burstable_field_yields_no_verdict() -> None:
    row = aws_row()
    row["details"] = _details([("CurrentGeneration", "true"), ("EbsInfo", EBS_INFO)])
    assert "sustainedCpu" not in project_row(row)


# --- 성능 필드 ---


def test_aws_performance_fields() -> None:
    rec = project_row(aws_row(current_gen="false"))
    assert rec["currentGeneration"] is False
    assert rec["clockGHz"] == 2.5
    assert rec["threadsPerCore"] == 2
    assert rec["ebsBaselineMbps"] == 347
    assert rec["ebsMaxMbps"] == 2085
    assert rec["networkPerformance"] == "Up to 5 Gigabit"
    assert rec["networkIsBurst"] is True


def test_azure_acu_absent_means_unknown_not_zero() -> None:
    """ACU는 37.7%만 있고 결측이 세대로 설명되지 않는다 — 없으면 키 자체가 없어야 한다."""
    assert "acu" not in project_row(azure_row(acu=None))
    assert project_row(azure_row(acu="160"))["acu"] == 160


def test_gcp_fields() -> None:
    rec = project_row(gcp_row())
    assert rec["maxPersistentDisks"] == 128
    assert rec["vendorDescription"].startswith("Efficient Instance")
    # GCP는 세대를 명시하지 않는다 — 이름으로 추측하지 않는다.
    assert "currentGeneration" not in rec


def test_cross_provider_fields_stay_absent() -> None:
    """ACU는 Azure에만, 클럭은 AWS에만 — 없는 걸 지어내면 프로바이더 간 비교가 가능한 척하게 된다."""
    assert "acu" not in project_row(aws_row())
    assert "clockGHz" not in project_row(azure_row(acu="160"))
    assert "acu" not in project_row(gcp_row())


# --- 필터링 ---


def test_non_system_namespace_is_skipped() -> None:
    assert project_row(aws_row(namespace="user-ns")) is None


def test_row_without_any_signal_is_dropped() -> None:
    row = aws_row()
    row["details"] = "[]"
    assert project_row(row) is None


def test_row_missing_identity_is_dropped() -> None:
    assert project_row(aws_row(id="")) is None
    assert project_row(aws_row(csp_spec_name="")) is None


# --- 데이터셋 · 감사 ---


def test_build_dataset_matches_schema() -> None:
    rows = [aws_row(), aws_row(spec="m5.large", burstable="false", current_gen="false"),
            gcp_row(), azure_row(acu="160")]
    dataset, _ = build_dataset(rows)
    jsonschema.validate(dataset, _schema())
    assert len(dataset["specs"]) == 4
    assert "Performance cannot be compared across providers" in dataset["_note"]


def test_audit_counts_the_findings_that_produce_warnings() -> None:
    rows = [aws_row(burstable="true"), aws_row(spec="m5.large", burstable="false", current_gen="false")]
    _, stats = project_rows(rows)
    assert stats["findings"]["aws"]["not_sustained"] == 1
    assert stats["findings"]["aws"]["old_generation"] == 1
    text = format_audit(stats)
    assert "not_sustained" in text and "old_generation" in text


def test_audit_reports_untracked_providers_as_intentional() -> None:
    rows = [aws_row(), {"id": "kt+kr1+x", "namespace": "system", "provider_name": "kt",
                        "csp_spec_name": "x", "details": "[]"}]
    _, stats = project_rows(rows)
    assert stats["untracked_providers"]["kt"] == 1
    assert "의도된 공백" in format_audit(stats)
