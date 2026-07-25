"""cb-tumblebug 템플릿 → 리소스 군.

**같은 타입을 여러 줄로 담으면서, 정작 개수는 틀렸다.** 축을 연결하려고 멤버를
훑다가 나온 결함 셋을 여기서 고정한다.

    1  노드 그룹마다 한 줄     → `core::vm`이 28줄. 게다가 `nodeGroupSize`를 안 봐서
                                 그룹 17개 × 2대 = **34대를 17로** 셌다
    2  `core::cluster`         → 그런 노드는 없다. core 층 이름은 `core::k8sCluster`
    3  `vNetPolicy`만 봄       → `vNetReq.subnetInfoList`로 적은 템플릿은 서브넷 0개
"""

from __future__ import annotations

import pytest

from bundlekb.model import ALWAYS, Bundle, Member
from bundlekb.parsers.tumblebug import _member_of_template

# 실제 `init/templates/*.json`의 모양 그대로.
INFRA_MULTI = {
    "resourceType": "infra",
    "infraDynamicReq": {
        "nodeGroups": [
            {"name": "g1", "nodeGroupSize": 2, "specId": "aws+ap-northeast-2+t3.small"},
            {"name": "g2", "nodeGroupSize": 2, "specId": "aws+us-east-1+t3.small"},
            {"name": "g3", "nodeGroupSize": 1, "specId": "gcp+asia-east1+e2-small"},
        ]
    },
}

K8S_ACROSS = {
    "resourceType": "k8sCluster",
    "k8sMultiClusterDynamicReq": {
        "clusters": [
            {"specId": "aws+ap-northeast-2+t3a.xlarge", "desiredNodeSize": 1},
            {"specId": "gcp+asia-east1+e2-standard-4", "desiredNodeSize": 2},
        ]
    },
}

VNET_LISTED = {
    "resourceType": "vNet",
    "vNetReq": {
        "cidrBlock": "10.0.0.0/16",
        "subnetInfoList": [
            {"name": "public", "ipv4_CIDR": "10.0.1.0/24"},
            {"name": "private", "ipv4_CIDR": "10.0.2.0/24"},
            {"name": "database", "ipv4_CIDR": "10.0.3.0/24"},
        ],
    },
}

VNET_POLICY = {
    "resourceType": "vNet",
    "vNetPolicy": {"cidrBlock": "auto", "subnetCount": 2, "multiZone": True},
}


def _by_type(members: list[Member]) -> dict[str, Member]:
    return {m.type_id: m for m in members}


# --- 1. 한 타입은 한 줄, 개수는 count -----------------------------------------

def test_node_groups_collapse_to_one_member() -> None:
    _, members, anchor = _member_of_template(INFRA_MULTI)
    assert [m.type_id for m in members] == ["core::vm"]
    assert anchor == "core::vm"


def test_node_group_size_is_not_thrown_away() -> None:
    """**그룹 수가 아니라 VM 대수다.** 이걸 놓쳐서 34대를 17로 세고 있었다."""
    _, members, _ = _member_of_template(INFRA_MULTI)
    assert members[0].count == 5  # 2 + 2 + 1 이지 3이 아니다
    assert "노드 그룹 3개" in (members[0].note or "")


def test_missing_node_group_size_counts_as_one_not_zero() -> None:
    """개수를 안 적은 그룹을 0으로 읽으면 **'안 만든다'로 뜻이 뒤집힌다.**"""
    doc = {"resourceType": "infra", "infraDynamicReq": {"nodeGroups": [{"name": "g"}]}}
    _, members, _ = _member_of_template(doc)
    assert members[0].count == 1


def test_duplicate_members_stop_the_build() -> None:
    """조용히 합치지 않는다 — note를 어떻게 할지는 **파서가 원본을 보고** 정할 일이다."""
    with pytest.raises(ValueError, match="appears twice"):
        Bundle(
            id="x",
            name="x",
            provider="core",
            evidence="tumblebug-template",
            members=(Member("core::vm", ALWAYS), Member("core::vm", ALWAYS)),
        )


def test_count_below_one_is_rejected() -> None:
    with pytest.raises(ValueError):
        Member("core::vm", ALWAYS, count=0)


# --- 2. 타입 id가 다른 KB와 맞아야 한다 ---------------------------------------

def test_k8s_cluster_uses_the_core_layer_name() -> None:
    """`core::cluster`는 **graphkb에 없는 노드**다. 안 맞으면 조인이 끊긴다."""
    _, members, anchor = _member_of_template(K8S_ACROSS)
    types = _by_type(members)
    assert "core::cluster" not in types
    assert anchor == "core::k8sCluster"
    assert types["core::k8sCluster"].count == 2


def test_k8s_node_groups_come_with_the_clusters() -> None:
    _, members, _ = _member_of_template(K8S_ACROSS)
    node_group = _by_type(members)["core::k8sNodeGroup"]
    assert node_group.count == 2
    assert "3대" in (node_group.note or "")  # desiredNodeSize 1 + 2


# --- 3. 서브넷을 적는 방식이 템플릿마다 다르다 --------------------------------

def test_subnets_declared_as_a_list_are_counted() -> None:
    """`vNetPolicy`만 보던 코드가 3개짜리 AWS 템플릿을 **0개**로 만들었다."""
    _, members, _ = _member_of_template(VNET_LISTED)
    subnet = _by_type(members)["core::subnet"]
    assert subnet.count == 3
    assert "CIDR" in (subnet.note or "")


def test_subnets_declared_as_a_policy_are_counted() -> None:
    _, members, _ = _member_of_template(VNET_POLICY)
    subnet = _by_type(members)["core::subnet"]
    assert subnet.count == 2
    assert "정책" in (subnet.note or "")


def test_vnet_without_subnet_info_says_nothing_rather_than_zero() -> None:
    _, members, _ = _member_of_template({"resourceType": "vNet"})
    assert "core::subnet" not in _by_type(members)


# --- 산출물 계약 ---------------------------------------------------------------

def test_count_one_is_not_written_to_the_artifact() -> None:
    """거의 다 1이라, 다 적으면 **1이 아닌 곳이 안 보인다.**"""
    bundle = Bundle(
        id="x",
        name="x",
        provider="core",
        evidence="tumblebug-template",
        members=(Member("core::vNet", ALWAYS), Member("core::subnet", ALWAYS, count=3)),
    )
    rows = {m["typeId"]: m for m in bundle.to_dict()["members"]}
    assert "count" not in rows["core::vNet"]
    assert rows["core::subnet"]["count"] == 3
    # `to_dict`가 구성원을 정렬하므로 순서가 아니라 **내용**이 살아남는지 본다.
    assert set(Bundle.from_dict(bundle.to_dict()).members) == set(bundle.members)
