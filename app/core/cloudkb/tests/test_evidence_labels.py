"""근거 라벨이 **틀린 출처를 대지 않는가.**

`tpcsp-schema` 라벨이 "Terraform 프로바이더 스키마(Alibaba·Tencent)"였다. 그 라벨은
처음 두 CSP만 있을 때 맞았고, 이후 ibm·ncp·openstack·oracle·nhn이 같은 라벨을 쓰게
되면서 **NHN 타입의 근거를 "Alibaba·Tencent"라고 말하고 있었다.**

이 프로젝트가 가장 경계하는 실패가 출처를 틀리게 대는 것인데, 그게 모델이 아니라
**우리 데이터 쪽에서** 나고 있었다.
"""

from __future__ import annotations

import pytest

from app.core.cloudkb.capacitykb.parsers.tpcsp import PROVIDERS
from app.core.cloudkb.kbcommon.basis import BASIS_OF_EVIDENCE
from app.core.cloudkb.kbcommon.display import evidence_name

# 여러 프로바이더가 공유하는 라벨 — 특정 벤더 이름이 박히면 안 된다.
_SHARED = ("tpcsp-schema",)

_VENDOR_WORDS = (
    "Alibaba", "alicloud", "Tencent", "tencentcloud", "IBM", "NCP", "Naver",
    "OpenStack", "Oracle", "NHN", "nhncloud",
)


@pytest.mark.parametrize("evidence", _SHARED)
def test_shared_label_names_no_single_vendor(evidence) -> None:
    """**핵심 회귀.** 어느 CSP인지는 type_id 네임스페이스가 이미 말한다."""
    label = evidence_name(evidence)
    found = [w for w in _VENDOR_WORDS if w.lower() in label.lower()]
    assert not found, f"{evidence} 라벨에 특정 벤더가 박혀 있습니다: {found} — {label!r}"


def test_shared_label_is_actually_shared_by_many() -> None:
    """공유 라벨이라는 전제 자체를 고정한다 — 하나뿐이면 이름을 박아도 된다."""
    assert len(PROVIDERS) > 2


@pytest.mark.parametrize("evidence", sorted(BASIS_OF_EVIDENCE))
def test_every_evidence_has_a_human_label(evidence) -> None:
    """라벨이 없으면 근거 이름이 날것으로 나간다 — 사람이 읽을 말이어야 한다."""
    label = evidence_name(evidence)
    assert label and label != evidence, f"{evidence}의 표시 이름이 없습니다"
