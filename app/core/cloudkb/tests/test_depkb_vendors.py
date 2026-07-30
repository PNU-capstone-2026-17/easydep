"""aws·gcp 후보 산출물의 구조적 불변식 — azure(test_depkb)와 같은 규율.

원문 캐시(.cache/cloudkb)는 gitignored라, 캐시가 필요한 검사는 없는 환경에서
건너뛴다. 산출물 자체의 불변식(핀 기록·대표 사실)은 항상 돈다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.cloudkb.depkb import extract_vendors, fetch_vendors
from app.core.cloudkb.depkb.vocabulary import GCP_TYPES

_HERE = Path(extract_vendors.__file__).resolve().parent
_HAVE_CACHE = all(
    (fetch_vendors.CACHE / s["file"]).exists()
    for s in fetch_vendors.SOURCES.values()
)


@pytest.fixture(scope="module")
def aws() -> dict:
    return json.loads((_HERE / "aws_candidates.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def gcp() -> dict:
    return json.loads((_HERE / "gcp_candidates.json").read_text(encoding="utf-8"))


def test_artifacts_record_their_pins(aws, gcp) -> None:
    """산출물의 핀은 수집기의 핀과 같아야 한다 — 어긋나면 다른 판에서 뽑은 것."""
    assert aws["_pin"]["sha256"] == fetch_vendors.SOURCES["aws-cfn"]["sha256"]
    assert gcp["_pin"]["sha256"] == fetch_vendors.SOURCES["gcp-compute"]["sha256"]


@pytest.mark.skipif(not _HAVE_CACHE, reason="벤더 원문 캐시가 없는 환경")
def test_candidates_are_recomputable(aws, gcp) -> None:
    assert aws == extract_vendors.extract_aws()
    assert gcp == extract_vendors.extract_gcp()


def test_required_flags_carry_position_nuance(aws) -> None:
    """CFN Required는 속성 위치의 플래그다 — 중첩 trail의 required를 간선
    필수로 읽으면 안 되고, 그 경고가 산출물에 있어야 한다.

    실례: vm→disk의 required는 `Volumes.VolumeId` — 블록을 쓸 때 필수이지
    VM이 디스크를 요구하는 것이 아니다.
    """
    assert "그 속성 위치의 플래그" in aws["_note"]
    vm_disk = [c for c in aws["candidates"]
               if (c["subject"], c["object"]) == ("vm", "disk")]
    assert vm_disk and all("." in c["trail"] for c in vm_disk if c["requiredInSchema"])


def test_the_three_csp_key_story_holds(aws, gcp) -> None:
    """sshKey는 3사 3색이다 — 이 가족(family)이 CSP 색인 주장의 표본이다.

    aws: 자원이 있고 VM이 이름으로 참조하되 **선택**(KeyName, Required:False —
    cb-tumblebug의 '필수'와 대조). azure: 등록 자원은 있으나 VM이 참조하지
    않는다(depkb azure 산출물에 vm→sshKey 없음). gcp: 자원 자체가 없다
    (결속 None, 후보 0).
    """
    aws_key = [c for c in aws["candidates"]
               if (c["subject"], c["object"]) == ("vm", "sshKey")]
    assert aws_key and not aws_key[0]["requiredInSchema"]
    assert GCP_TYPES["sshKey"] is None
    assert not [c for c in gcp["candidates"]
                if "sshKey" in (c["subject"], c["object"])]


def test_flat_vm_to_subnet_is_aws_native_not_universal(aws, gcp) -> None:
    """vm→subnet 직결은 aws에만 있다 — CB의 평탄 모델이 aws에서는 네이티브고
    azure에서는 드라이버 합성이었던 이유가 스키마 층에서 그대로 보인다.

    aws Instance는 SubnetId를 직접 받고(최상위 trail), gcp는 내장 NIC를
    경유해야 한다(vm→nic schema-ref는 있고 vm→subnet 직결은 없다).
    """
    aws_direct = [c for c in aws["candidates"]
                  if (c["subject"], c["object"]) == ("vm", "subnet")
                  and "." not in c["trail"]]
    assert aws_direct, "aws Instance.SubnetId 직결이 사라졌다"
    assert not [c for c in gcp["candidates"]
                if (c["subject"], c["object"]) == ("vm", "subnet")]
    assert [c for c in gcp["candidates"]
            if (c["subject"], c["object"]) == ("vm", "nic")]


def test_nic_to_subnet_is_the_cross_csp_common_core(aws, gcp) -> None:
    """nic→subnet은 3사 공통핵의 첫 후보다 — aws는 CFN Required:true까지 준다.

    azure는 preflight가 `SubnetIsRequired`로 이미 확인했다(P5a). 세 증거의
    형태가 다 다르다는 것(플래그·거부 코드·$ref)이 오라클 층화의 요점이다.
    """
    aws_edge = [c for c in aws["candidates"]
                if (c["subject"], c["object"]) == ("nic", "subnet")
                and "." not in c["trail"]]
    assert aws_edge and aws_edge[0]["requiredInSchema"], (
        "aws NetworkInterface.SubnetId의 Required가 사라졌다"
    )
    assert [c for c in gcp["candidates"]
            if (c["subject"], c["object"]) == ("nic", "subnet")]
