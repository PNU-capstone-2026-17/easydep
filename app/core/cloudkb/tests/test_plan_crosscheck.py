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
        assert pc._bridge()["aws"][vendor] == resource


def test_the_other_csps_bridge_through_the_graph_not_a_hand_table() -> None:
    """3사 다리는 **aws 결속을 지렛대로** 자동으로 나온다.

    계획은 gcp에 `gcp::ComputeNetwork`를 쓰는데 `vocabulary.GCP_TYPES`는
    디스커버리 스키마 이름 `Network`를 쓴다 — 이름 체계가 달라 직접 못 잇는다.
    그 사이는 graphkb 동치 그래프가 알고, 앵커는 aws다(스키마 원문에 결속된
    유일한 축).

    이 우회가 없던 첫 판에서는 azure 노드가 **하나도 안 읽혀** 대조가 통째로
    거짓이었다(2026-08-01 스윕에서 잡혔다).
    """
    bridge = pc._bridge()
    for csp in ("gcp", "azure"):
        assert bridge.get(csp), f"{csp} 다리가 없다"
        assert "vm" in bridge[csp].values(), csp
        assert "network" in bridge[csp].values(), csp
    assert bridge["azure"]["Microsoft.Network/virtualNetworks"] == "network"
    assert bridge["gcp"]["ComputeNetwork"] == "network"


def test_the_bridge_never_invents_a_link() -> None:
    """그래프에 없는 것은 다리도 없다 — 못 읽으면 `out-of-vocabulary`로 남는다."""
    bridge = pc._bridge()
    # 관리형 서비스는 depkb가 실측한 적이 없으므로 어느 CSP에도 없어야 한다.
    for csp, table in bridge.items():
        assert "AWS::RDS::DBInstance" not in table, csp
        assert not any(v in ("relationalDatabase", "messageQueue")
                       for v in table.values()), csp


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


def test_a_declared_boundary_is_not_counted_as_a_gap() -> None:
    """**"아직 안 한 것"과 "안 하기로 한 것"을 가른다**(2026-08-01 결정).

    관리형 상품 서비스는 범위 밖으로 확정했다(`vocabulary.OUT_OF_SCOPE`) — 상품이
    CSP마다 수십 종이고 계속 늘어나 실측으로 따라갈 수 없어서다. 그전에는 이것들이
    매번 `out-of-vocabulary`로 세어져 **무엇을 더 재야 하는지가 안 보였다.**

    무엇이 관리형인지는 계획 자신이 안다(`PlanNode.role`) — 타입 이름으로 우리가
    다시 판정하지 않는다.
    """
    result = pc.crosscheck(_plan(
        _vm(),
        PlanNode("db", "db", "managed", "inferred",
                 type_id="aws::AWS::RDS::DBInstance"),
        PlanNode("pg", "pg", "external", "design"),
    ), "aws")
    kinds = {f.subject: f.kind for f in result.findings}
    assert kinds["db"] == pc.OUT_OF_SCOPE, kinds
    assert kinds["pg"] == pc.OUT_OF_SCOPE, kinds
    assert not [f for f in result.findings if f.kind == pc.OUT_OF_VOCABULARY]
    # 경계에는 **사유가 실린다** — 침묵이 아니라 선언이다.
    assert any("따라갈 수 없다" in f.measured
               for f in result.findings if f.subject == "db")


def test_a_real_gap_still_counts_as_one() -> None:
    """경계를 선언했다고 진짜 공백까지 조용해지면 안 된다.

    공유 자원인데 벤더 타입이 없는 노드가 그 예다 — gcp의 `sshkey`가 실제로
    그렇다. **gcp가 SSH 키를 지원하지 않는다는 뜻이 아니다**(지원한다). 키가
    독립 자원이 아니라 메타데이터(`ssh-keys`)·OS Login으로 다뤄져 어휘에 대응
    타입이 없을 뿐이고, 그래서 **계획이 그것을 노드로 그리는 것이 어긋난 자리**다.

    이건 어휘 결속 판단이지 실측이 아니다 — claims에 gcp sshKey 주장은 0건이다.
    """
    result = pc.crosscheck(_plan(
        _vm(), PlanNode("sshkey", "key", "shared", "kb")), "aws")
    assert any(f.kind == pc.OUT_OF_VOCABULARY and f.subject == "sshkey"
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
    assert all(f.kind in (pc.OUT_OF_VOCABULARY, pc.OUT_OF_SCOPE)
               for f in result.findings)


def test_the_wiring_closed_the_gaps_it_was_built_to_close() -> None:
    """**배선의 완료 판정.** 실물 표본이 17 → 4로 줄었다(2026-08-01).

    첫 측정에서 나온 것: 필수 누락 1 · 중복 1 · 미적용 검사 2 · 순서 2 · 경고 5 ·
    대기 2 · 약한 읽기 1 · 대조 불가 3. 앞의 여섯은 계획에 자리가 없어서 빠진
    것이었고 `plan_enrich`가 그 자리를 만들어 채웠다.

    남은 둘은 **배선으로 닫을 수 없는 종류**라 여기서 그렇게 못 박는다:

      `weak-reading`        계획이 컴퓨트 노드에 벤더 타입을 안 단다(표시
                            문자열뿐) — `design_tools`를 고쳐야 닫힌다.
      `out-of-vocabulary`   관리형 서비스(RDS·S3·SecretsManager)에 대한 의존
                            실측이 0건 — 새 실측 라운드가 있어야 닫힌다.

    이 둘이 0이 되면 그건 진짜 진척이므로 그때 이 테스트를 고친다.
    """
    from app.core.cloudkb.nim_agent.design_tools import compose
    from app.core.cloudkb.tools.intake_report import _design_from, _read

    spec, _ = _read(_SAMPLE, "requirements/resource_spec.json")
    design, problems = _design_from(_SAMPLE, spec if isinstance(spec, dict) else None)
    assert design is not None, problems
    result = pc.crosscheck(compose(design), "aws", "ap-northeast-2")
    counts = result.counts()

    assert result.anchors == ("loadBalancer", "vm"), result.anchors
    for kind in (pc.MISSING_REQUIRED, pc.DOUBLE_CREATE, pc.REDUNDANT_NODE,
                 pc.UNCHECKED_RULE, pc.ABSENT_ORDER, pc.ABSENT_WARNING,
                 pc.ABSENT_WAIT):
        assert not counts.get(kind), (
            f"{kind}가 되살아났다 — 배선이 끊겼는지 보라: {counts}")
    assert counts.get(pc.WEAK_READING) == 1, counts
    # 관리형 셋(RDS·S3·SecretsManager)은 **선언된 경계**이지 공백이 아니다.
    assert counts.get(pc.OUT_OF_SCOPE) == 3, counts
    assert not counts.get(pc.OUT_OF_VOCABULARY), counts
