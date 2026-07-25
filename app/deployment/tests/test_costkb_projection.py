"""spec_infos 행 → costkb 레코드 투영 테스트.

값은 실물 덤프(73,083행)에서 확인한 것을 그대로 쓴다:
- `gcp+asia-east1+n2-highmem-8` → v_cpu=8, memory_gi_b=62.5, cost=0.7494...
- `aws+ap-southeast-1+m5.large` → v_cpu=2, memory_gi_b=8, cost=0.12
- cost_per_hour: 양수 68,705 / **0이 6건** / 음수 4,372
"""

from __future__ import annotations

import jsonschema
import pytest

from app.deployment.costkb.dataset import _schema
from app.deployment.costkb.parsers.tumblebug import (
    SYSTEM_NAMESPACE,
    build_dataset,
    correct_memory,
    format_audit,
    project_row,
    project_rows,
)


def row(**overrides) -> dict:
    base = {
        "id": "aws+ap-southeast-1+m5.large",
        "namespace": SYSTEM_NAMESPACE,
        "csp_spec_name": "m5.large",
        "name": "aws+ap-southeast-1+m5.large",
        "provider_name": "aws",
        "region_name": "ap-southeast-1",
        "v_cpu": 2,
        "memory_gi_b": 8,
        "cost_per_hour": 0.12,
        "architecture": "x86_64",
        "infra_type": "vm",
        "disk_size_gb": 0,
        "accelerator_type": None,
        "accelerator_model": None,
        "accelerator_count": 0,
        "accelerator_memory_gb": 0,
    }
    base.update(overrides)
    return base


def gcp_row(**overrides) -> dict:
    return row(
        **{
            "id": "gcp+asia-east1+n2-highmem-8",
            "csp_spec_name": "n2-highmem-8",
            "provider_name": "gcp",
            "region_name": "asia-east1",
            "v_cpu": 8,
            "memory_gi_b": 62.5,
            "cost_per_hour": 0.7494000196456909,
            **overrides,
        }
    )


# --- 기본 매핑 ---


def test_maps_columns_to_record() -> None:
    record = project_row(row())
    assert record["id"] == "aws+ap-southeast-1+m5.large"
    assert record["provider"] == "aws"
    assert record["region"] == "ap-southeast-1"
    assert record["specName"] == "m5.large"  # csp_spec_name (name이 아님)
    assert record["vCPU"] == 2
    assert record["memGiB"] == 8
    assert record["hourlyUSD"] == 0.12
    assert record["architecture"] == "x86_64"


# --- ⭐ 메모리: **빌드가 고쳐서 담는다** ---
#
# 예전에는 원본을 그대로 두고 `memGiBActual`에 보정값을 병기했다 — 라이브 MCP와
# 필터 결과를 맞추려던 배포기의 이유다. 값 하나를 두 칸에 나누니 표시·필터·정렬이
# 서로 다른 칸을 보게 됐고, "16 GiB 이상"에서 실제로는 만족하는 3,765건이 빠졌다.
# 지금은 `memGiB`가 곧 실제 값이고, 보정 규칙은 데이터셋 메타데이터에 남는다.


def test_gcp_memory_is_corrected_in_place() -> None:
    """실제 64 GiB인 스펙은 `memGiB`에 64로 담긴다 — 칸을 나누지 않는다."""
    record = project_row(gcp_row())
    assert record["memGiB"] == 64.0
    assert "memGiBActual" not in record


def test_azure_memory_also_corrected() -> None:
    record = project_row(gcp_row(provider_name="azure", memory_gi_b=15.625))
    assert record["memGiB"] == 16.0


def test_aws_memory_is_not_corrected() -> None:
    """AWS는 원시 MiB를 그대로 쓰므로 정확하다 — 보정하면 오히려 틀린다."""
    record = project_row(row(memory_gi_b=8))
    assert record["memGiB"] == 8


@pytest.mark.parametrize("provider", ["tencent", "alibaba", "ibm", "ncp", "kt", "nhn", "openstack"])
def test_untraced_providers_are_not_corrected(provider: str) -> None:
    """덤프 실측상 이들은 ×1.024 지문이 0.0%였다 → 보정 대상이 아니다."""
    record = project_row(row(provider_name=provider, memory_gi_b=16))
    assert record["memGiB"] == 16


def test_correct_memory_directly() -> None:
    assert correct_memory("gcp", 62.5) == 64.0
    assert correct_memory("gcp", 15.625) == 16.0
    assert correct_memory("azure", 0.9765625) == 1.0
    assert correct_memory("aws", 8.0) == 8.0
    assert correct_memory("tencent", 4.0) == 4.0


# --- ⭐ 가격 센티널 ---


@pytest.mark.parametrize("cost", [-1, -1.0, 0, 0.0])
def test_nonpositive_cost_is_unknown_not_free(cost) -> None:
    """0도 '가격 미상'이지 무료가 아니다 (덤프에 0이 6건 실재).

    Tumblebug 정렬 SQL이 `CASE WHEN cost_per_hour > 0 ... ELSE 999999`이다.
    """
    assert project_row(row(cost_per_hour=cost))["hourlyUSD"] is None


def test_positive_cost_kept() -> None:
    assert project_row(row(cost_per_hour=0.0584))["hourlyUSD"] == 0.0584


def test_hourly_key_always_present_even_when_null() -> None:
    """스키마가 hourlyUSD를 required로 두므로 None이어도 키는 있어야 한다."""
    assert "hourlyUSD" in project_row(row(cost_per_hour=-1))


# --- 무효 행 ---


@pytest.mark.parametrize(
    "bad",
    [
        {"provider_name": None},
        {"region_name": ""},
        {"csp_spec_name": None},
        {"v_cpu": 0},
        {"v_cpu": None},
        {"memory_gi_b": 0},
        {"memory_gi_b": None},
    ],
)
def test_unusable_rows_are_skipped(bad: dict) -> None:
    assert project_row(row(**bad)) is None


def test_empty_optional_fields_are_dropped() -> None:
    record = project_row(row(accelerator_type=None, accelerator_model=""))
    assert "acceleratorType" not in record
    assert "acceleratorModel" not in record


def test_gpu_fields_kept_when_present() -> None:
    record = project_row(
        row(accelerator_type="gpu", accelerator_model="nvidia-tesla-a100", accelerator_count=8)
    )
    assert record["acceleratorType"] == "gpu"
    assert record["acceleratorModel"] == "nvidia-tesla-a100"
    assert record["acceleratorCount"] == 8


# --- namespace ---


def test_other_namespaces_are_skipped() -> None:
    specs, stats = project_rows([row(), row(namespace="user-ns")])
    assert len(specs) == 1
    assert stats["skipped_namespace"] == 1


def test_namespace_filter_can_be_disabled() -> None:
    specs, _ = project_rows([row(), row(namespace="user-ns")], namespace=None)
    assert len(specs) == 2


# --- 감사 통계 ---


def test_audit_flags_memory_bug_fingerprint() -> None:
    specs, stats = project_rows([row(), gcp_row()])
    assert len(specs) == 2
    assert stats["memory_audit"]["aws"]["integral"] == 1
    assert stats["memory_audit"]["aws"]["bug_fingerprint"] == 0
    assert stats["memory_audit"]["gcp"]["bug_fingerprint"] == 1  # 62.5 × 1.024 = 64


def test_audit_reports_unknown_provider() -> None:
    _, stats = project_rows([row(provider_name="oracle")])
    assert dict(stats["unknown_providers"]) == {"oracle": 1}
    assert "oracle" in format_audit(stats)
    assert "schema.json" in format_audit(stats)


def test_format_audit_is_readable() -> None:
    _, stats = project_rows([row(), gcp_row()])
    text = format_audit(stats)
    assert "보정 적용" in text  # gcp에 표시
    assert "aws" in text and "gcp" in text


# --- 데이터셋 조립 + 스키마 검증 ---


def test_build_dataset_validates_against_schema() -> None:
    dataset, _ = build_dataset([row(), gcp_row()])
    jsonschema.validate(dataset, _schema())
    assert len(dataset["specs"]) == 2
    note = " ".join(dataset["_note"].split()).lower()
    assert "mirror" in note
    assert "2.4% lower" in note  # 상위 버그 고지


def test_unknown_provider_fails_schema_validation() -> None:
    """조용한 드리프트 대신 크게 실패한다."""
    dataset, _ = build_dataset([row(provider_name="oracle")])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(dataset, _schema())
