"""주장 산출물(claims.json)의 구조적 불변식.

빌드 자체가 인용 코드를 실험 실측과 대조해 죽지만(판정-측정 정합), 여기서는
산출물이 그 빌드의 사영으로 남아 있는가와 판정 규율(통과·침묵은 증거가 아니다)을
지킨다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.cloudkb.depkb import build_claims

_ARTIFACT = Path(build_claims.__file__).resolve().parent / "claims.json"


@pytest.fixture(scope="module")
def artifact() -> dict:
    return json.loads(_ARTIFACT.read_text(encoding="utf-8"))


def test_claims_are_recomputable(artifact) -> None:
    """산출물은 빌드의 사영이다 — 손대려면 판정표(우리 구성)를 고쳐라."""
    assert artifact == build_claims.build()


def test_verdicts_require_dynamic_evidence(artifact) -> None:
    """required/optional/holds는 동적 층(preflight 거부·apply) 증거 없이 못 선다.

    스키마 후보만으로 판정이 서면 그건 '참조가 있다'를 '필요하다'로 승격한 것 —
    이 저장소가 세 번 저지른 실패의 재발이다.
    """
    for c in artifact["claims"]:
        if c["verdict"] == "unknown":
            continue
        dynamic = [e for e in c["evidence"] if e["layer"] in ("preflight", "apply")]
        assert dynamic, f"{c['csp']} {c['subject']}→{c['object']}: 동적 증거 없는 판정"


def test_unknown_is_recorded_not_hidden(artifact) -> None:
    """aws·gcp는 계정이 없어 전부 unknown이어야 한다 — 하나라도 판정이 서 있으면
    측정 없이 승격된 것이다(T9). unknown에는 사유가 붙는다."""
    for c in artifact["claims"]:
        if c["csp"] in ("aws", "gcp"):
            assert c["verdict"] == "unknown", (
                f"{c['csp']} {c['subject']}→{c['object']}: 동적 실험 없이 판정"
            )
        if c["verdict"] == "unknown":
            assert c["note"], "unknown에 사유가 없다"


def test_the_verified_azure_core_holds(artifact) -> None:
    """azure 검증핵 — 바뀌면 실험 기록이 바뀐 것이니 사람이 봐야 한다."""
    got = {(c["subject"], c["object"], c["question"]): c["verdict"]
           for c in artifact["claims"] if c["csp"] == "azure"
           and c["verdict"] != "unknown"}
    assert got == {
        ("nic", "subnet", "existence"): "required",
        ("nic", "subnet", "lifecycle"): "holds",
        ("vm", "nic", "existence"): "required",
        ("subnet", "network", "existence"): "required",
        ("subnet", "network", "lifecycle"): "holds",
        ("nic", "firewall", "existence"): "optional",
        ("nic", "firewall", "lifecycle"): "holds",
        ("loadBalancer", "subnet|publicIp|publicIPPrefix", "existence"): "required",
    }


def test_optional_and_lifecycle_are_independent(artifact) -> None:
    """nic→firewall: 생성엔 선택인데 붙어 있으면 삭제가 막힌다 — 질문 둘을 한
    필드에 눌렀다면 이 사실은 표현 자체가 불가능했다. 유형 분리의 존재 증명."""
    nf = {c["question"]: c["verdict"] for c in artifact["claims"]
          if c["csp"] == "azure" and (c["subject"], c["object"]) == ("nic", "firewall")}
    assert nf == {"existence": "optional", "lifecycle": "holds"}
