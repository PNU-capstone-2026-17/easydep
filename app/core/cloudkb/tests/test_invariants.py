"""레코드 간 불변식 계층 테스트.

배경: 스키마는 레코드 **하나의 형태**만 봐서, `default=0` + `min=10` 같은 모순이
그대로 산출물이 됐다(kb-data-audit-2026-07-20 §1-(1)). 모순은 두 레코드를 나란히
놓아야 보인다.
"""

from __future__ import annotations

import json

import pytest

from app.core.cloudkb.capacitykb.invariants import INVARIANTS as CAPACITY_INVARIANTS
from app.core.cloudkb.kbcommon.artifact import ArtifactInvalid, write_dataset
from app.core.cloudkb.kbcommon.invariants import (
    Invariant,
    Violation,
    accelerator_fields_agree,
    one_basis_per_evidence,
    run,
)

ANY_SCHEMA = {"type": "object"}


def constraint(type_id: str, prop: str, kind: str, value, **extra) -> dict:
    return {"type_id": type_id, "property": prop, "kind": kind, "value": value,
            "evidence": "cfn-schema", "basis": "stated", **extra}


# --- 얼개 ---------------------------------------------------------------------


def test_severity_must_be_known() -> None:
    with pytest.raises(ValueError, match="severity"):
        Invariant(name="x", question="?", severity="warning", check=lambda d: ())


def test_run_separates_errors_from_reports() -> None:
    """심각도를 나누는 이유: 전부 빌드 실패로 만들면 미러가 못 만들어진다."""
    def one(_):
        return [Violation(where="a", detail="d")]

    result = run({}, [
        Invariant(name="hard", question="?", severity="error", check=one),
        Invariant(name="soft", question="?", severity="report", check=one),
    ])
    assert not result.ok
    assert [i.name for i, _ in result.errors] == ["hard"]
    assert [i.name for i, _ in result.reports] == ["soft"]


def test_clean_dataset_is_ok() -> None:
    result = run({}, CAPACITY_INVARIANTS)
    assert result.ok and not result.reports


# --- 쓰기 관문 ----------------------------------------------------------------


def test_error_severity_refuses_to_write(tmp_path) -> None:
    """**핵심 계약**: 모순이 있으면 파일을 만들지 않는다.

    나쁜 산출물이 존재하지 않는 것이 존재하는 것보다 낫다(artifact.py의 원칙).
    """
    path = tmp_path / "out.json"
    dataset = {"constraints": [
        constraint("aws::T", "P", "default", 0),
        constraint("aws::T", "P", "min", 10),
    ]}
    with pytest.raises(ArtifactInvalid, match="default=0인데 min=10"):
        write_dataset(path, dataset, ANY_SCHEMA, CAPACITY_INVARIANTS)
    assert not path.exists()


def test_error_leaves_previous_artifact_intact(tmp_path) -> None:
    path = tmp_path / "out.json"
    write_dataset(path, {"constraints": []}, ANY_SCHEMA, CAPACITY_INVARIANTS)
    before = path.read_text(encoding="utf-8")

    bad = {"constraints": [
        constraint("aws::T", "P", "default", 0),
        constraint("aws::T", "P", "min", 10),
    ]}
    with pytest.raises(ArtifactInvalid):
        write_dataset(path, bad, ANY_SCHEMA, CAPACITY_INVARIANTS)
    assert path.read_text(encoding="utf-8") == before


def test_report_severity_writes_and_returns_the_violation(tmp_path) -> None:
    """report는 산출물을 막지 않는다 — 대신 **호출자가 알려야 한다.**"""
    path = tmp_path / "out.json"
    dataset = {"specs": [
        {"provider": "kt", "specName": "g", "acceleratorType": "gpu",
         "acceleratorCount": 0},
    ]}
    from app.core.cloudkb.costkb.invariants import INVARIANTS as COST

    result = write_dataset(path, dataset, ANY_SCHEMA, COST)

    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == dataset
    assert [i.name for i, _ in result.reports] == ["accelerator-fields-agree"]


def test_no_invariants_means_schema_only(tmp_path) -> None:
    """기존 호출자를 깨지 않는다 — 불변식을 안 주면 예전과 같이 동작한다."""
    path = tmp_path / "out.json"
    dataset = {"constraints": [
        constraint("aws::T", "P", "default", 0),
        constraint("aws::T", "P", "min", 10),
    ]}
    assert write_dataset(path, dataset, ANY_SCHEMA).ok
    assert path.exists()


# --- capacitykb의 검사 --------------------------------------------------------


def test_default_below_min_is_caught() -> None:
    """실측: AWS 스키마가 `Period: default=0, minimum=10`으로 적어 놓았다."""
    dataset = {"constraints": [
        constraint("aws::AWS::MediaLive::CloudWatchAlarmTemplate", "Period", "default", 0),
        constraint("aws::AWS::MediaLive::CloudWatchAlarmTemplate", "Period", "min", 10),
    ]}
    result = run(dataset, CAPACITY_INVARIANTS)
    assert not result.ok


def test_default_above_max_is_caught() -> None:
    dataset = {"constraints": [
        constraint("aws::T", "P", "default", 100),
        constraint("aws::T", "P", "max", 10),
    ]}
    assert not run(dataset, CAPACITY_INVARIANTS).ok


def test_default_within_bounds_is_fine() -> None:
    dataset = {"constraints": [
        constraint("aws::T", "P", "default", 30),
        constraint("aws::T", "P", "min", 10),
        constraint("aws::T", "P", "max", 60),
    ]}
    assert run(dataset, CAPACITY_INVARIANTS).ok


def test_bounds_on_different_properties_do_not_interact() -> None:
    """min은 **같은 속성**의 것만 본다 — 아니면 온 타입이 서로를 위반한다."""
    dataset = {"constraints": [
        constraint("aws::T", "P", "default", 0),
        constraint("aws::T", "Q", "min", 10),
    ]}
    assert run(dataset, CAPACITY_INVARIANTS).ok


def test_read_only_required_is_caught() -> None:
    """실측: EmailContact 하위 6개가 readOnly면서 definitions.required에도 있었다."""
    dataset = {"constraints": [
        constraint("aws::AWS::NotificationsContacts::EmailContact",
                   "EmailContact/Arn", "mutability", "read_only"),
        constraint("aws::AWS::NotificationsContacts::EmailContact",
                   "EmailContact/Arn", "required", True),
    ]}
    result = run(dataset, CAPACITY_INVARIANTS)
    assert not result.ok
    assert "읽기 전용인데 필수" in result.summary()


def test_create_only_may_be_required() -> None:
    """만들 때만 정할 수 있는 속성은 필수일 수 있다 — 읽기 전용과 다르다."""
    dataset = {"constraints": [
        constraint("aws::T", "P", "mutability", "create_only"),
        constraint("aws::T", "P", "required", True),
    ]}
    assert run(dataset, CAPACITY_INVARIANTS).ok


# --- 공용 검사 ----------------------------------------------------------------


def test_one_basis_per_evidence_reports_the_split() -> None:
    """한 라벨이 두 성격을 뭉뚱그리면 라벨을 쪼개라는 신호다.

    실제로 `kcc-ref`가 그랬다 — ServiceMapping(원본 명시)과 설명문 정규식(짐작)이
    같은 라벨을 쓰는 바람에, 라벨 단위 검수가 **짐작 322건까지 싸잡아 승인**했다.
    """
    check = one_basis_per_evidence("edges")
    violations = list(check({"edges": [
        {"evidence": "kcc-ref", "basis": "stated"},
        {"evidence": "kcc-ref", "basis": "inferred"},
        {"evidence": "arm-hierarchy", "basis": "stated"},
    ]}))
    assert [v.where for v in violations] == ["kcc-ref"]
    assert "inferred" in violations[0].detail and "stated" in violations[0].detail


def test_accelerator_type_without_count_is_caught() -> None:
    check = accelerator_fields_agree("specs")
    violations = list(check({"specs": [
        {"provider": "kt", "specName": "8x64.gpu", "acceleratorType": "gpu",
         "acceleratorCount": 0},
        {"provider": "aws", "specName": "p4d.24xlarge", "acceleratorType": "gpu",
         "acceleratorCount": 8},
        {"provider": "aws", "specName": "t3.micro", "acceleratorType": None,
         "acceleratorCount": 0},
    ]}))
    assert [v.where for v in violations] == ["kt 8x64.gpu"]


def test_mirror_and_derived_kb_disagree_on_severity() -> None:
    """같은 검사라도 미러(costkb)는 report, 골라 담는 쪽(perfkb)은 error다.

    미러가 상류 모순을 고쳐 쓰면 그 순간 미러가 아니게 된다.
    """
    from app.core.cloudkb.costkb.invariants import INVARIANTS as COST
    from app.core.cloudkb.perfkb.invariants import INVARIANTS as PERF

    bad = {"specs": [{"provider": "kt", "specName": "g", "acceleratorType": "gpu",
                      "acceleratorCount": 0}]}
    assert run(bad, COST).ok          # 산출물은 만들어진다
    assert run(bad, COST).reports     # 다만 조용하지 않다
    assert not run(bad, PERF).ok      # 이쪽은 막는다


# --- 센티널 정규화 (2026-07-21, 감사 §5-4) ---


def test_negative_measurement_blocks_the_write(tmp_path) -> None:
    """음수는 측정값이 아니라 "모른다"는 표시다 — null로 옮기는 건 파서의 일이다.

    실측: 상류가 `disk_size_gb = -1`로 37,466건을 보낸다. 그대로 실으면 소비자가
    계산에 넣는다.
    """
    from app.core.cloudkb.costkb.invariants import INVARIANTS as COST

    bad = {"specs": [{"provider": "aws", "specName": "d2.8xlarge", "diskSizeGB": -1.0}]}
    result = run(bad, COST)
    assert not result.ok
    assert "음수" in result.summary()


def test_zero_disk_is_reported_not_rewritten() -> None:
    """0은 사실인지 미기입인지 **가릴 수 없다** — 미러는 다시 쓰지 않고 건수만 밝힌다.

    Azure에서 이름상 로컬 디스크가 확실한 v6 계열이 0으로 오는가 하면(미기입),
    0이 맞는 스펙도 있다. 애매한 값을 고쳐 쓰면 그 순간 미러가 아니게 된다.
    """
    from app.core.cloudkb.costkb.invariants import INVARIANTS as COST

    data = {"specs": [{"provider": "azure", "specName": "Standard_E48ads_v6",
                       "diskSizeGB": 0}]}
    result = run(data, COST)
    assert result.ok                                  # 산출물은 만들어진다
    assert any(i.name == "disk-size-zero-is-ambiguous" for i, _ in result.reports)


def test_disk_size_parser_maps_minus_one_to_none() -> None:
    from app.core.cloudkb.costkb.parsers.tumblebug import _disk_size

    assert _disk_size(-1) is None
    assert _disk_size(0) == 0        # 0은 건드리지 않는다
    assert _disk_size(100) == 100
    assert _disk_size(None) is None
