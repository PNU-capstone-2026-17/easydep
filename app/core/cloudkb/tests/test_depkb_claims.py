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
    """unknown에는 사유가 붙는다 — 빈칸이 아니라 '미실행'의 기록이다."""
    for c in artifact["claims"]:
        if c["verdict"] == "unknown":
            assert c["note"], "unknown에 사유가 없다"


def test_aws_core_and_the_key_story_closure(artifact) -> None:
    """aws 검증핵 — 그리고 sshKey 이야기의 완결.

    같은 DryRun 성공 하나가 서브넷·키 둘 다의 생략 가능을 증명한다(기본 VPC
    대체 실측). CB의 'sshKey 필수'는 이제 3사 전부에서 도구의 요구로 확정됐다 —
    aws 선택(동적) · azure 무참조(스키마) · gcp 자원 부재(스키마).
    aws nic→subnet의 required는 클라이언트 층 거부라는 한계가 note에 있다.
    """
    got = {(c["subject"], c["object"], c["question"]): c["verdict"]
           for c in artifact["claims"] if c["csp"] == "aws"
           and c["verdict"] != "unknown"}
    assert got == {
        ("nic", "subnet", "existence"): "required",
        ("nic", "subnet", "lifecycle"): "holds",
        ("nic", "firewall", "existence"): "optional",
        ("nic", "firewall", "lifecycle"): "holds",
        ("subnet", "network", "existence"): "required",
        ("subnet", "network", "lifecycle"): "holds",
        ("firewall", "network", "existence"): "optional",
        ("vm", "subnet", "existence"): "optional",
        ("vm", "sshKey", "existence"): "optional",
        ("vm", "nic", "existence"): "optional",
        ("vm", "firewall", "existence"): "optional",
        ("vm", "disk", "existence"): "optional",
        ("loadBalancer", "subnet", "existence"): "required",
        ("loadBalancer", "firewall", "existence"): "optional",
        ("k8sCluster", "subnet", "existence"): "required",
    }
    key_claim = next(c for c in artifact["claims"] if c["csp"] == "aws"
                     and (c["subject"], c["object"]) == ("vm", "sshKey"))
    assert key_claim["verdict"] == "optional"


def test_no_unknown_remains_in_the_vocabulary(artifact) -> None:
    """3사 × 어휘 전체가 판정됐다(2026-07-31) — unknown이 다시 생기면
    스키마 층에 새 후보가 들어온 것이니 실험을 따라 붙여야 한다."""
    unknown = [(c["csp"], c["subject"], c["object"])
               for c in artifact["claims"] if c["verdict"] == "unknown"]
    assert not unknown, f"판정 없는 주장이 생겼다: {unknown}"


def test_vm_nic_modality_flips_across_csps(artifact) -> None:
    """vm→nic: azure 필수 · gcp 필수 · aws 선택(서버 ENI 암묵) — 두 번째 양상
    반전. vm→disk(gcp만 필수)와 함께, '벤더 중립 필수 플래그 하나'로는 이
    지식을 표현할 수 없다는 논거의 기둥이다."""
    by_csp = {c["csp"]: c["verdict"] for c in artifact["claims"]
              if (c["subject"], c["object"], c["question"])
              == ("vm", "nic", "existence")}
    assert by_csp == {"azure": "required", "gcp": "required", "aws": "optional"}


def test_gcp_core_and_the_modality_flip(artifact) -> None:
    """gcp 검증핵 — 그리고 첫 CSP 양상 반전: vm→disk.

    azure는 OS 디스크를 서버가 합성해 **선택**, gcp는 부트 디스크 명세가
    **필수**다. 같은 간선의 필연이 CSP에 따라 뒤집힌다는 것이 CSP 색인 주장
    형식이 필요한 이유고, 이 반전이 그 첫 실측이다.
    """
    got = {(c["subject"], c["object"], c["question"]): c["verdict"]
           for c in artifact["claims"] if c["csp"] == "gcp"
           and c["verdict"] != "unknown"}
    assert got == {
        ("subnet", "network", "existence"): "required",
        ("subnet", "network", "lifecycle"): "holds",
        ("firewall", "network", "existence"): "optional",
        ("vm", "nic", "existence"): "required",
        ("vm", "disk", "existence"): "required",
        ("vm", "disk", "lifecycle"): "holds",
        ("nic", "network", "existence"): "optional",
        ("nic", "subnet", "existence"): "optional",
        ("nic", "subnet", "lifecycle"): "holds",
        ("loadBalancer", "network", "existence"): "optional",
        ("loadBalancer", "subnet", "existence"): "optional",
    }
    azure = {(c["subject"], c["object"], c["question"]): c["verdict"]
             for c in artifact["claims"] if c["csp"] == "azure"}
    assert azure[("vm", "disk", "existence")] == "optional"
    assert got[("vm", "disk", "existence")] == "required"


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
        ("nic", "publicIp", "existence"): "optional",
        ("nic", "publicIp", "lifecycle"): "holds",
        ("vm", "disk", "existence"): "optional",
        ("vm", "disk", "lifecycle"): "holds",
        ("network", "subnet", "existence"): "optional",
        ("subnet", "firewall", "existence"): "optional",
        ("loadBalancer", "subnet", "existence"): "optional",
        ("loadBalancer", "publicIp", "existence"): "optional",
        ("loadBalancer", "subnet|publicIp|publicIPPrefix", "existence"): "required",
        ("k8sCluster", "k8sNodeGroup", "existence"): "required",
        ("vpn", "subnet", "existence"): "required",
    }


def test_optional_and_lifecycle_are_independent(artifact) -> None:
    """nic→firewall: 생성엔 선택인데 붙어 있으면 삭제가 막힌다 — 질문 둘을 한
    필드에 눌렀다면 이 사실은 표현 자체가 불가능했다. 유형 분리의 존재 증명."""
    nf = {c["question"]: c["verdict"] for c in artifact["claims"]
          if c["csp"] == "azure" and (c["subject"], c["object"]) == ("nic", "firewall")}
    assert nf == {"existence": "optional", "lifecycle": "holds"}
