"""제약 검사기(depkb.check)의 규율 — P4.

멈추는 조건이 "위반을 잡는 음성 테스트가 있을 것"이었다. 그래서 이 파일의
중심은 **실측된 거부를 계획 층에서 재현하는 것**이다 — 우리가 클라우드에서
받은 거부 코드가 곧 이 검사의 근거이므로, 같은 상황을 계획으로 만들면 잡혀야 한다.
"""

from __future__ import annotations

from app.core.cloudkb.depkb.check import check
from app.core.cloudkb.depkb.infra_intent import build


def _plan(**resources) -> dict:
    return {"resources": [{"id": rid, "instances": insts}
                          for rid, insts in resources.items()]}


def test_aws_eks_same_az_is_caught() -> None:
    """실측 재현: 같은 AZ 서브넷 둘로 EKS를 만들면 컨트롤 플레인이 거부했다
    (`aws-eks2`, 'must be in at least two different AZs'). 계획 층에서 잡는다."""
    intent = build(["k8sCluster"], "aws", "r")
    report = check(intent, _plan(
        network=[{"name": "vpc"}],
        subnet=[{"name": "s1", "zone": "a"}, {"name": "s2", "zone": "a"}],
        k8sCluster=[{"name": "c"}]))
    assert not report.ok
    assert any("같은 영역" in v.detail for v in report.violations)


def test_aws_eks_one_subnet_is_caught() -> None:
    """서브넷 하나도 거부됐다 — 개수 조건."""
    intent = build(["k8sCluster"], "aws", "r")
    report = check(intent, _plan(
        network=[{"name": "vpc"}],
        subnet=[{"name": "s1", "zone": "a"}],
        k8sCluster=[{"name": "c"}]))
    assert not report.ok
    assert any("2개 이상" in v.detail for v in report.violations)


def test_aws_eks_two_azs_passes() -> None:
    """양성 대조 — 다른 AZ 둘이면 실제로 클러스터가 섰다(`aws-eks3`)."""
    intent = build(["k8sCluster"], "aws", "r")
    report = check(intent, _plan(
        network=[{"name": "vpc"}],
        subnet=[{"name": "s1", "zone": "a"}, {"name": "s2", "zone": "b"}],
        k8sCluster=[{"name": "c"}]))
    assert report.ok, report.violations


def test_gcp_zone_mismatch_is_caught() -> None:
    """실측 재현: 다른 존의 디스크를 붙인 인스턴스는 거부됐다
    (`gcp-paircompat`, invalid)."""
    intent = build(["vm"], "gcp", "r")
    report = check(intent, _plan(
        vm=[{"name": "v", "zone": "a"}],
        disk=[{"name": "d", "zone": "b"}],
        nic=[{"name": "n"}]))
    assert not report.ok
    assert any(v.kind == "쌍 호환" for v in report.violations)


def test_gcp_same_zone_passes() -> None:
    intent = build(["vm"], "gcp", "r")
    report = check(intent, _plan(
        vm=[{"name": "v", "zone": "a"}],
        disk=[{"name": "d", "zone": "a"}],
        nic=[{"name": "n"}]))
    assert report.ok, report.violations


def test_azure_gateway_subnet_name_is_caught() -> None:
    """실측 재현: 다른 이름의 서브넷만 있으면 게이트웨이가 안 섰다
    (`azure-k8s-vpn`, InvalidResourceReference)."""
    intent = build(["vpn"], "azure", "r")
    report = check(intent, _plan(
        network=[{"name": "vnet"}],
        subnet=[{"name": "nodes"}],
        publicIp=[{"name": "pip"}],
        vpn=[{"name": "vng"}]))
    assert not report.ok
    assert any("GatewaySubnet" in v.detail for v in report.violations)


def test_azure_gateway_subnet_right_name_passes() -> None:
    """양성 대조 — 그 이름이면 실제로 섰다(`azure-vpn2`, Succeeded)."""
    intent = build(["vpn"], "azure", "r")
    report = check(intent, _plan(
        network=[{"name": "vnet"}],
        subnet=[{"name": "GatewaySubnet"}],
        publicIp=[{"name": "pip"}],
        vpn=[{"name": "vng"}]))
    assert report.ok, report.violations


def test_missing_required_resource_is_reported() -> None:
    """필수인데 계획에 없으면 잡는다 — 단 서버가 채우는 것은 부재가 정상이다."""
    intent = build(["k8sCluster"], "aws", "r")
    report = check(intent, _plan(k8sCluster=[{"name": "c"}]))
    assert set(report.missing_required) == {"network", "subnet"}
    assert "firewall" not in report.missing_required, (
        "서버가 만드는 것을 누락으로 세면 계획이 불필요한 자원을 만든다"
    )


def test_unchecked_is_reported_not_silently_passed() -> None:
    """검사 함수가 없는 부류는 통과가 아니라 미검사로 보고한다 — 조용히
    넘어가면 계획이 '검사 통과'로 읽힌다."""
    intent = build(["k8sCluster"], "azure", "r")
    report = check(intent, _plan(k8sCluster=[{"name": "c"}],
                                 k8sNodeGroup=[{"name": "np"}]))
    assert any("수명 조건" in u for u in report.unchecked)
