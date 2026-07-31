"""뷰 충분성 — **뷰만 보고 실제 실험을 재현할 수 있는가**.

## 왜 있나

뷰의 필드 구조는 "소비자가 요구한 것"이 아니라 우리가 정한 것이다(사용자
지적, 2026-08-01). 그 지적을 검증한 방법이 worked example이었다:
`provision_view(["k8sCluster"], "aws")`만 보고 `aws-eks3` 라운드 19스텝을
도출할 수 있는지 하나씩 대조했다.

**검증이 실제로 공백을 찾았다** — 그때 나온 것이 연산 성질(`waitFor`)이고,
같이 지목했던 "역할에 정책 부착"은 실측이 기각했다(`aws-qual2`: 정책 없는
역할로도 클러스터가 ACTIVE까지 갔다 → 뷰가 맞았고 우리 실험이 관행을
따랐던 것).

여기서는 그 대조를 **테스트로 고정**한다. 사후 확인을 규율로 올려서, 뷰가
얇아지면 실패하게 한다.

## 무엇을 지키나

실험 스크립트가 실제로 한 일 중 **의존 지식이 나르는 것**만 본다. 자원 이름·
CIDR·리전 같은 값은 사용자 결정이지 우리 축이 아니므로 대상이 아니다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.cloudkb.depkb import build_claims
from app.core.infra_planning import plan_for_anchors

_EXPERIMENTS = Path(build_claims.__file__).resolve().parent / "experiments"


def _steps(experiment: str) -> dict:
    path = _EXPERIMENTS / experiment / "results.json"
    return json.loads(path.read_text(encoding="utf-8"))["steps"]


@pytest.fixture(scope="module")
def eks3_view() -> dict:
    return plan_for_anchors(["k8sCluster"], "aws", "ap-northeast-2").provision


def test_create_order_covers_the_experiment_prerequisites(eks3_view) -> None:
    """`aws-eks3`가 클러스터 전에 만든 것이 전부 createOrder에 있다.

    실험: 역할 → VPC → 서브넷 2개 → 클러스터. 뷰가 그 순서를 도출해야 한다.
    """
    order = [c["id"] for c in eks3_view["createOrder"]]
    assert order == ["iamRole", "network", "subnet", "k8sCluster"], order
    steps = _steps("aws-eks3-2026-07-31")
    # 실험이 실제로 그 순서로 만들었다는 것(스텝 존재로 확인)
    for step in ("R.create-role", "R.create-vpc", "R.create-subnet1",
                 "R.create-subnet2", "K1.create-cluster"):
        assert step in steps, step


def test_view_carries_the_placement_condition(eks3_view) -> None:
    """서브넷이 **둘**이어야 하는 이유가 뷰에 있다 — 없으면 하나만 만든다.

    실험은 `R.create-subnet1/2`로 둘을 만들었고, 그 근거는 카디널리티가 아니라
    **분산**이다(같은 AZ 둘도 거부됐다 — aws-eks2/C2).
    """
    rules = [c["rule"] for c in eks3_view["checks"]
             if c["object"] == "subnet"]
    assert rules, "배치 조건이 뷰에 없다"
    assert any("다른 AZ" in r for r in rules), rules


def test_view_says_what_not_to_create(eks3_view) -> None:
    """실험이 SG를 만들지 않았는데 클러스터에 SG가 생겼다(서버 합성).

    뷰가 이걸 말하지 않으면 IaC가 SG를 또 만든다.
    """
    assert "firewall" in {d["id"] for d in eks3_view["doNotCreate"]}
    # 실험은 SG를 만든 적이 없는데 클러스터 모양에 sg-…가 실재한다.
    # (기록의 키는 축약된 `sg`다 — 원문 그대로 본다.)
    shape = _steps("aws-eks3-2026-07-31")["K3.cluster-shape"]["excerpt"]
    assert "sg-" in shape, shape
    assert not any(s.startswith("R.create-sg")
                   for s in _steps("aws-eks3-2026-07-31")), "실험이 SG를 만들었다면 전제가 다르다"


def test_view_carries_the_async_wait(eks3_view) -> None:
    """**worked example이 찾은 공백** — 실험은 ACTIVE까지 폴링했다.

    createOrder만 보고 실행하면 클러스터가 CREATING인 채 다음을 시도한다.
    """
    waits = {(w["id"], w["op"]) for w in eks3_view["waitFor"]}
    assert ("k8sCluster", "create") in waits, eks3_view["waitFor"]
    assert ("k8sCluster", "delete") in waits, eks3_view["waitFor"]
    assert "K2.cluster-active" in _steps("aws-eks3-2026-07-31")


def test_delete_order_matches_the_measured_refusals(eks3_view) -> None:
    """실험이 만난 삭제 거부(L1·L2)를 뷰가 미리 막는다.

    뷰의 `deleteBefore`를 지키면 그 거부를 만나지 않는다.
    """
    pairs = {tuple(p) for p in eks3_view["deleteBefore"]}
    assert ("k8sCluster", "subnet") in pairs
    assert ("k8sCluster", "network") in pairs
    steps = _steps("aws-eks3-2026-07-31")
    for step in ("L1.delete-subnet-in-use", "L2.delete-vpc-in-use"):
        assert "DependencyViolation" in steps[step]["errorCodes"], step


def test_role_needs_no_policy_and_the_view_is_right_to_omit_it() -> None:
    """**기각된 공백** — 뷰에 '정책 부착'이 없는 것이 맞다.

    worked example이 이를 공백으로 지목했으나 `aws-qual2`가 기각했다: 정책이
    하나도 안 붙은 역할로도 클러스터가 ACTIVE까지 갔다. 우리 실험들이
    정책을 붙여 온 것은 관행이었지 필요가 아니었다.

    이 테스트는 **없는 것을 지킨다** — 근거 없이 되살리면 실패한다.
    """
    qual = _steps("aws-qual2-2026-08-01")
    assert qual["R4.role-has-no-policy"]["excerpt"].strip() in ("[]", "[]\n")
    assert "ACTIVE" in qual["A2.final-state-without-policy"]["excerpt"]
    view = plan_for_anchors(["k8sCluster"], "aws", "-").provision
    blob = json.dumps(view, ensure_ascii=False)
    assert "AmazonEKSClusterPolicy" not in blob, (
        "정책 부착은 실측이 기각한 요건이다 — 뷰에 되살리려면 새 실측이 먼저다"
    )
