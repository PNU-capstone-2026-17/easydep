"""대조기의 불변식 — **끊긴 자리를 세되, 못 본 것과 어긋난 것을 섞지 않는다.**

대조기(`app/core/plan_crosscheck.py`)는 두 계획 경로가 서로를 모른다는 관측에서
나왔다. 세는 것이 목적이므로 **세는 규칙 자체가 흔들리면 숫자가 의미를 잃는다** —
여기서 그 규칙을 고정한다.

지키는 것:

  - 앵커는 **워크로드 역할**의 노드에서만 나온다. 계획이 그린 것을 전부 앵커로
    삼으면 폐포가 그것들을 "사용자가 고른 것"으로 받아들여 *서버가 대신 만든다*는
    주장이 통째로 사라진다(실제로 첫 판이 그랬고, 이중 생성 검사가 0건이었다).
  - `server-implicit`(대신 만든다)와 `server-default`(안 정하면 기본값)를 **가른다.**
    뭉치면 정상 계획을 결함으로 부른다.
  - 어휘 결속이 없는 노드는 **대조하지 않고 그렇다고 적는다.**
  - 표시 문자열로 읽은 것은 **약한 읽기**로 남는다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core import plan_crosscheck as pc
from app.core.cloudkb.appkb.plan import DeploymentPlan, PlanNode

_SAMPLE = (Path(pc.__file__).resolve().parent
           / "cloudkb" / "appkb" / "samples" / "lecture-platform")


def _plan(*nodes: PlanNode) -> DeploymentPlan:
    plan = DeploymentPlan(name="t")
    plan.nodes = list(nodes)
    return plan


def _vm(node_id: str = "app") -> PlanNode:
    return PlanNode(node_id, "app", "compute", "inferred", host="VM")


def test_vendor_types_are_read_through_the_measured_binding() -> None:
    """어휘 매핑을 손으로 적지 않는다 — `vocabulary.AWS_TYPES`의 역인덱스다."""
    from app.core.cloudkb.depkb import vocabulary

    for resource, vendor in vocabulary.AWS_TYPES.items():
        assert pc._BY_CSP["aws"][vendor] == resource


def test_only_workload_roles_become_anchors() -> None:
    """공유 자원을 앵커로 삼으면 *서버가 대신 만든다*가 사라진다.

    첫 판이 그랬다: 계획이 그린 SecurityGroup이 앵커가 되는 바람에 폐포가 그것을
    "사용자가 고른 것"으로 읽었고, 이중 생성 검사가 영영 0건이었다.
    """
    result = pc.crosscheck(_plan(
        _vm(),
        PlanNode("sg", "sg", "shared", "kb", type_id="aws::AWS::EC2::SecurityGroup"),
        PlanNode("net", "net", "shared", "kb", type_id="aws::AWS::EC2::VPC"),
    ), "aws")
    assert result.anchors == ("vm",), result.anchors
    assert "firewall" not in result.anchors


def test_server_default_and_server_implicit_are_not_the_same_finding() -> None:
    """**두 사실을 가른다.** 뭉치면 정상 계획을 결함으로 부른다.

    `server-implicit`(nic·disk — 서버가 대신 만든다)를 또 그리면 이중 생성이지만,
    `server-default`(firewall — 안 정하면 기본값)를 정하는 것은 정상일 수 있다.

    이 구별은 대조기가 필요로 해서 드러났다 — `AutoFilled`가 술어 부류를 계산해
    놓고 버리고 있었다(2026-08-01).
    """
    result = pc.crosscheck(_plan(
        _vm(),
        PlanNode("sg", "sg", "shared", "kb", type_id="aws::AWS::EC2::SecurityGroup"),
        PlanNode("eni", "eni", "shared", "kb",
                 type_id="aws::AWS::EC2::NetworkInterface"),
    ), "aws")
    kinds = {f.subject: f.kind for f in result.findings}
    assert kinds.get("firewall") == pc.REDUNDANT_NODE, kinds
    assert kinds.get("nic") == pc.DOUBLE_CREATE, kinds


def test_the_intent_carries_the_predicate_class() -> None:
    """대조기가 문장을 파싱하지 않도록 뷰가 부류를 싣는다."""
    from app.core.infra_planning import plan_for_anchors

    by_id = {d["id"]: d for d in
             plan_for_anchors(["vm"], "aws", "-").provision["doNotCreate"]}
    assert by_id["nic"]["kind"] == "server-implicit"
    assert by_id["firewall"]["kind"] == "server-default"


def test_unmapped_nodes_are_recorded_not_ignored() -> None:
    """어휘 밖은 **대조 불가**로 남는다 — 침묵은 "문제없다"로 읽힌다."""
    result = pc.crosscheck(_plan(
        _vm(),
        PlanNode("db", "db", "managed", "inferred",
                 type_id="aws::AWS::RDS::DBInstance"),
    ), "aws")
    assert "db" in result.unmapped
    assert any(f.kind == pc.OUT_OF_VOCABULARY and f.subject == "db"
               for f in result.findings)


def test_a_compute_node_read_by_its_display_string_is_flagged_weak() -> None:
    """계획이 컴퓨트 노드에 벤더 타입을 안 단다 — 그 사실을 매번 남긴다."""
    result = pc.crosscheck(_plan(_vm()), "aws")
    assert result.weak == {"app": "VM"}
    assert any(f.kind == pc.WEAK_READING for f in result.findings)
    assert result.anchors == ("vm",)


def test_a_plan_with_nothing_measurable_says_so_instead_of_passing() -> None:
    """앵커가 없으면 **통과가 아니라 대조 불가**다."""
    result = pc.crosscheck(_plan(
        PlanNode("db", "db", "managed", "inferred",
                 type_id="aws::AWS::RDS::DBInstance")), "aws")
    assert result.anchors == ()
    assert all(f.kind == pc.OUT_OF_VOCABULARY for f in result.findings)


def test_the_real_sample_still_produces_the_measured_gaps() -> None:
    """**실물 표본의 결과를 고정한다** — 배선이 진행되면 이 숫자가 줄어야 한다.

    2026-08-01 첫 측정: 합계 17(필수 누락 1 · 중복 1 · 미적용 검사 2 · 순서 2 ·
    경고 5 · 대기 2 · 약한 읽기 1 · 대조 불가 3). 종류별 존재만 고정하고 합계는
    고정하지 않는다 — 실측이 늘면 경고도 는다.
    """
    path = _SAMPLE / "crosscheck.json"
    if not path.exists():
        pytest.skip("표본 대조 기록이 없다 — `python -m app.core.plan_crosscheck` 먼저")
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["anchors"] == ["loadBalancer", "vm"], doc["anchors"]
    counts = doc["counts"]
    for kind in (pc.MISSING_REQUIRED, pc.UNCHECKED_RULE, pc.ABSENT_ORDER,
                 pc.ABSENT_WARNING, pc.ABSENT_WAIT, pc.OUT_OF_VOCABULARY):
        assert counts.get(kind), f"{kind}가 사라졌다 — 배선했다면 이 기대를 고쳐라"
