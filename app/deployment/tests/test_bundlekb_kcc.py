"""GCP 번들 — Config Connector 샘플.

**두 번 헛짚고 찾은 소스다.** Terraform 모듈(cloud-foundation-fabric)로는 안 됐다 —
주 리소스에도 `count = var.x_create ? 1 : 0`이 걸려 있어 무조건 리소스가 0개인 모듈이
86개 중 63개였고, 변수 기본값까지 추적해도 리소스 선언 719개 중 46개(6.4%)만 풀렸다.
"""

from __future__ import annotations

import pytest

from app.deployment.bundlekb.parsers.kcc import anchor_of, kinds_in

MULTI_DOC = """apiVersion: alloydb.cnrm.cloud.google.com/v1beta1
kind: AlloyDBCluster
metadata:
  name: alloydbcluster-sample
---
apiVersion: compute.cnrm.cloud.google.com/v1beta1
kind: ComputeNetwork
metadata:
  name: computenetwork-sample
---
apiVersion: v1
kind: Namespace
metadata:
  name: config-connector
"""

K8S_ONLY = """apiVersion: k8s.cnrm.cloud.google.com/v1alpha1
kind: ConfigConnector
metadata:
  name: configconnector.core.cnrm.cloud.google.com
"""


def test_documents_are_split_before_reading() -> None:
    """**파일 전체에서 정규식을 돌리면 apiVersion과 kind의 짝이 어긋난다.**"""
    assert kinds_in(MULTI_DOC) == ["AlloyDBCluster", "ComputeNetwork"]


def test_kubernetes_housekeeping_is_not_a_member() -> None:
    """`Namespace`는 쿠버네티스 살림이지 클라우드 리소스가 아니다."""
    assert "Namespace" not in kinds_in(MULTI_DOC)


def test_k8s_api_group_is_skipped() -> None:
    assert kinds_in(K8S_ONLY) == []


def test_empty_text_is_empty_not_error() -> None:
    assert kinds_in("") == []


@pytest.mark.parametrize(
    ("scenario", "kinds", "expected"),
    [
        ("alloydbcluster/regular-cluster", {"AlloyDBCluster", "ComputeNetwork"},
         "AlloyDBCluster"),
        ("computeinstance", {"ComputeInstance", "ComputeNetwork"}, "ComputeInstance"),
        # 디렉터리 이름과 같은 kind가 없으면 앵커를 지어내지 않는다
        ("some-scenario", {"ComputeNetwork"}, None),
    ],
)
def test_anchor_comes_from_directory_name(scenario, kinds, expected) -> None:
    assert anchor_of(scenario, kinds) == expected


def test_anchor_ignores_separators() -> None:
    """`dns-record-set` 같은 이름도 `DNSRecordSet`에 맞는다."""
    assert anchor_of("dnsrecordset/policy", {"DNSRecordSet"}) == "DNSRecordSet"
