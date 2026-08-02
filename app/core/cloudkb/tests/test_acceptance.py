"""구현 이후 테스팅 — 요구 도출 수용 스위트의 규율.

여기서 지키는 것: **요구가 잣대이고, 매핑 못 하면 지어내지 않고 unmapped로
남긴다**(앱의 자기 테스트가 자기를 채점하지 않게 하는 것이 이 축의 존재 이유).
"""
from __future__ import annotations

from app.core.cloudkb.appkb import acceptance

_OPENAPI = {
    "paths": {
        "/auth/login": {"post": {"summary": "Authenticate field engineer"}},
        "/engineers/{id}/sites": {"get": {"summary": "View assigned sites"}},
        "/reports/drafts": {"post": {"summary": "Create inspection report draft"}},
    }
}

_REQS = [
    {"id": "FR1", "type": "FR",
     "text": "The system shall authenticate a field engineer using credentials."},
    {"id": "FR2", "type": "FR",
     "text": "The system shall present the list of assigned sites to an engineer."},
    {"id": "FR9", "type": "FR",
     "text": "The system shall compute the orbital trajectory of a satellite."},
    {"id": "NFR1", "type": "NFR",
     "text": "Browsing the report list must respond within 3 seconds."},
    {"id": "NFR2", "type": "NFR",
     "text": "Inspection records must be kept for five years and survive redeployment."},
    {"id": "NFR3", "type": "NFR",
     "text": "A failure in photo processing must not prevent submitting new reports."},
    {"id": "NFR9", "type": "NFR",
     "text": "The interface shall use a calming shade of blue."},
]


def test_functional_requirement_maps_to_its_endpoint():
    checks = {c.requirement_id: c for c in acceptance.derive(_REQS, _OPENAPI)}
    assert checks["FR1"].endpoint == ("POST", "/auth/login")
    assert "Authenticate" in checks["FR1"].operation
    assert not checks["FR1"].unmapped
    assert checks["FR2"].endpoint == ("GET", "/engineers/{id}/sites")


def test_unmapped_requirement_is_said_not_faked():
    """엔드포인트가 없는 요구(위성 궤도)는 **unmapped**로 남는다 — 통과로 안 친다."""
    checks = {c.requirement_id: c for c in acceptance.derive(_REQS, _OPENAPI)}
    assert checks["FR9"].endpoint is None
    assert checks["FR9"].unmapped


def test_nfr_matches_verification_pattern():
    checks = {c.requirement_id: c for c in acceptance.derive(_REQS, _OPENAPI)}
    assert "latency" in checks["NFR1"].how
    assert "survival" in checks["NFR2"].how and "재배포" in checks["NFR2"].passes
    assert "isolation" in checks["NFR3"].how


def test_nfr_without_a_pattern_is_unmapped():
    """검증 패턴이 없는 NFR(색상)은 지어내지 않는다 — 사람 몫으로 남긴다."""
    checks = {c.requirement_id: c for c in acceptance.derive(_REQS, _OPENAPI)}
    assert checks["NFR9"].unmapped


def test_coverage_counts_the_silence():
    checks = acceptance.derive(_REQS, _OPENAPI)
    cov = acceptance.coverage(checks)
    assert cov["total"] == 7
    assert cov["unmapped"] >= 2   # FR9(위성) + NFR9(색상)
    assert cov["functional"] == 3 and cov["nfr"] == 4


def test_it_runs_on_the_real_field_report_artifacts():
    """실물 회귀 — 씨앗의 13요구가 대부분 엔드포인트에 매핑돼야 한다."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "appkb" / "samples" / "field-report"
    reqs = json.loads((root / "requirements" / "classified.json").read_text("utf-8"))
    api = json.loads((root / "design" / "api_spec.json").read_text("utf-8"))
    checks = acceptance.derive(reqs, api)
    cov = acceptance.coverage(checks)
    # 대부분의 기능 요구가 매핑되어야 도출이 실물에서 작동한다는 증거다.
    mapped_fr = [c for c in checks if c.kind == "functional" and not c.unmapped]
    assert len(mapped_fr) >= 6, cov
