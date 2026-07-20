"""레코드 간 불변식 계층 테스트.

배경: 스키마는 레코드 **하나의 형태**만 봐서, `default=0` + `min=10` 같은 모순이
그대로 산출물이 됐다(kb-data-audit-2026-07-20 §1-(1)). 모순은 두 레코드를 나란히
놓아야 보인다.
"""

from __future__ import annotations

import json

import pytest

from capacitykb.invariants import INVARIANTS as CAPACITY_INVARIANTS
from kbcommon.artifact import ArtifactInvalid, write_dataset
from kbcommon.invariants import (
    Invariant,
    Violation,
    accelerator_fields_agree,
    one_confidence_per_evidence,
    run,
)

ANY_SCHEMA = {"type": "object"}


def constraint(type_id: str, prop: str, kind: str, value, **extra) -> dict:
    return {"type_id": type_id, "property": prop, "kind": kind, "value": value,
            "evidence": "cfn-schema", "confidence": 1.0, **extra}


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
    dataset = {"constraints": [
        constraint("aws::T", "P", "min", 1, evidence="cfn-description", confidence=0.6),
        constraint("aws::T", "Q", "min", 2, evidence="cfn-description", confidence=0.8),
    ]}
    result = write_dataset(path, dataset, ANY_SCHEMA, CAPACITY_INVARIANTS)

    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == dataset
    assert [i.name for i, _ in result.reports] == ["one-confidence-per-evidence"]
    assert "0.6" in result.summary() and "0.8" in result.summary()


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


def test_one_confidence_per_evidence_reports_the_split() -> None:
    check = one_confidence_per_evidence("edges")
    violations = list(check({"edges": [
        {"evidence": "heuristic", "confidence": 0.5},
        {"evidence": "heuristic", "confidence": 0.6},
        {"evidence": "arm-hierarchy", "confidence": 1.0},
    ]}))
    assert [v.where for v in violations] == ["heuristic"]
    assert "[0.5, 0.6]" in violations[0].detail


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
    from costkb.invariants import INVARIANTS as COST
    from perfkb.invariants import INVARIANTS as PERF

    bad = {"specs": [{"provider": "kt", "specName": "g", "acceleratorType": "gpu",
                      "acceleratorCount": 0}]}
    assert run(bad, COST).ok          # 산출물은 만들어진다
    assert run(bad, COST).reports     # 다만 조용하지 않다
    assert not run(bad, PERF).ok      # 이쪽은 막는다
