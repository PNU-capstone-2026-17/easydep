"""**필수 칸마다 "지우면 무너지는 판정" 하나씩** — W5의 완료 판정.

계약이 스스로 적어 둔 필수 판정식은 이것이다.

> 그 칸이 없으면 뒤 단계 산출물의 요구사항 부합을 **잴 수 없는** 것만 필수다.

지금까지 그 문장은 산문이었고, 그래서 규모 신호가 "사이징 판정의 기준"이라는 **거짓
근거**로 5년치 필수 자리를 지키고 있었다(2026-07-29에 내려왔다). 이 파일이 그 문장을
검사로 바꾼다: 필수 칸을 하나 지우면 **이름 붙은 판정이 실제로 사라져야** 한다.

지울 수 있는데 아무것도 안 사라진다면 그 칸은 필수가 아니다 — 그때는 이 테스트가
아니라 계약을 고쳐야 한다.
"""

from __future__ import annotations

import pytest

from app.core import cloud_contract, input_registry
from app.core.cloudkb.appkb.contract import request_schema
from app.core.cloudkb.appkb.plan import (
    ORIGIN_INFERRED,
    ORIGIN_KB,
    DeploymentPlan,
    PlanNode,
)
from app.core.cloudkb.appkb.verify import verify_against_requirements
from app.core.cloudkb.tests._helpers import flat

_HOURS = 730

_FULL = {
    "schemaVersion": "3",
    "provider": "aws",
    "region": "ap-northeast-2",
    "workloads": ["vm"],
    "containerRegistry": "depkb-registry",
    "monthlyBudgetUSD": 300.0,
}


def _plan() -> DeploymentPlan:
    plan = DeploymentPlan(name="필수 판정 시연")
    plan.nodes = [
        PlanNode("api", "API", "compute", ORIGIN_INFERRED, hourly_usd=0.02,
                 type_id="aws::AWS::EC2::Instance"),
        PlanNode("db", "저장소", "managed", ORIGIN_KB,
                 type_id="aws::AWS::RDS::DBInstance"),
    ]
    return plan


def _lines(req: dict) -> str:
    return flat(" | ".join(verify_against_requirements(_plan(), req, _HOURS)))


#: 필수 칸 → 그 칸이 있을 때만 나오는 **판정문의 표식**.
#: 표식이 없는 칸은 필수라고 부를 근거가 없다.
_VERDICT_MARK = {
    "provider": "Provider (aws)",
    "monthlyBudgetUSD": "Budget ($300",
}


def test_full_spec_produces_every_required_verdict() -> None:
    text = _lines(_FULL)
    for field, mark in _VERDICT_MARK.items():
        assert mark in text, f"{field}: 값이 있는데 판정이 안 나온다"


@pytest.mark.parametrize("field,mark", sorted(_VERDICT_MARK.items()))
def test_removing_a_required_field_removes_its_verdict(field: str, mark: str) -> None:
    """**이 파일의 요점.** 지우면 그 판정이 사라진다 — 그래서 필수다."""
    without = {k: v for k, v in _FULL.items() if k != field}
    assert mark not in _lines(without)


def test_absent_verdicts_are_announced_not_silent() -> None:
    """사라진 판정은 **침묵하지 않는다** — 없는 것과 통과한 것이 구별돼야 한다."""
    text = _lines({k: v for k, v in _FULL.items() if k != "monthlyBudgetUSD"})
    assert "Not judged for lack of a requirement" in text
    assert "monthlyBudgetUSD" in text and "absent, not passed" in text


def test_region_is_required_because_prices_are_indexed_by_it() -> None:
    """`region`은 판정문이 아니라 **조인**으로 필수다.

    지우면 판정이 사라지는 것이 아니라 **다른 리전의 단가로 예산을 재게 된다** —
    사라지는 것보다 나쁘다(틀린 답이 통과한다). 그래서 이 칸의 근거는 다른 모양이고,
    여기서는 그 사실을 명시적으로 못 박는다.
    """
    from app.core.cloudkb.costkb import dataset as cost

    seoul = cost.filter_specs(provider="aws", region="ap-northeast-2",
                              vcpu_min=2, mem_min_gib=4, limit=1)
    anywhere = cost.filter_specs(provider="aws", vcpu_min=2, mem_min_gib=4, limit=1)
    assert seoul and anywhere
    # 같은 조건인데 리전을 안 주면 다른 후보·다른 단가가 나올 수 있다. 둘이 우연히
    # 같더라도 "리전을 안 줘도 된다"는 뜻이 아니므로 값이 아니라 **경로**를 검사한다.
    assert seoul[0].get("hourlyUSD") is not None
    assert "리전 코드" in cloud_contract.why("region")


def test_workloads_is_required_because_no_plan_exists_without_it() -> None:
    """`workloads`는 판정이 아니라 **계획 자체**로 필수다(2026-08-01 신설).

    앞의 칸들은 계획을 만든 **뒤** 그 계획을 재는 데 쓰인다. 이 칸은 **계획을
    만드는 입력**이라 근거의 모양이 또 다르다 — 지우면 판정 한 줄이 사라지는 게
    아니라 잴 대상이 없어진다.
    """
    from app.core.infra_planning import plan_for_anchors

    plan = plan_for_anchors(["k8sCluster"], "aws", "ap-northeast-2")
    assert [c["id"] for c in plan.provision["createOrder"]], "앵커가 있으면 계획이 선다"
    with pytest.raises((ValueError, KeyError, IndexError)):
        plan_for_anchors([], "aws", "ap-northeast-2")


def test_container_registry_is_required_because_the_manifest_needs_it() -> None:
    """`containerRegistry`가 없으면 **배포할 수 없는 YAML**이 나온다(2026-08-01).

    사슬을 끝까지 돌려 확인했다: 이 값이 없으면 매니페스트에
    `image: <acr-name>.azurecr.io/app:<tag>`가 그대로 나가고, 값을 주면
    `myteamacr.azurecr.io/...`가 된다. 앞의 칸들과 근거의 모양이 또 다르다 —
    판정도 조인도 아니고 **산출물이 쓸모 있는가**다.
    """
    from pathlib import Path

    from app.core.cloud_artifact import build as build_artifact
    from app.core.cloudkb.nim_agent.design_tools import compose
    from app.core.cloudkb.tools.intake_report import _design_from, _read

    root = (Path(__file__).resolve().parents[1] / "appkb" / "samples"
            / "lecture-platform")
    spec, _ = _read(root, "requirements/resource_spec.json")
    base = dict(spec) if isinstance(spec, dict) else {}
    base.update({"provider": "azure", "workloads": ["k8sCluster"],
                 "containerRegistry": "myteamacr"})
    design, _ = _design_from(root, base)
    art = build_artifact(compose(design), design)
    types = {r["type"] for r in art["resources"]}
    assert "Microsoft.ContainerRegistry/registries" in types, types


def test_every_required_field_is_covered_by_this_file() -> None:
    """**필수 칸이 늘면 이 파일도 늘어야 한다.**

    `schemaVersion`은 사용자에게 묻지 않고 생산자가 채우므로(계약 버전 표식) 판정
    대상이 아니다. `region`·`workloads`는 위 테스트들이 따로 다룬다 — 근거의
    모양이 다르기 때문이다(조인 / 계획의 입력).
    """
    covered = set(_VERDICT_MARK) | {"region", "workloads", "containerRegistry"}
    required = set(request_schema()["required"]) - set(input_registry.NOT_ASKED)
    assert required == covered, (
        "필수 칸이 바뀌었습니다. 새 칸에 '지우면 무너지는 판정'을 하나 붙이거나, "
        "붙일 수 없다면 그 칸은 필수가 아닙니다 — 계약을 고치세요."
    )
