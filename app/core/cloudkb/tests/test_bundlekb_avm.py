"""AVM → 리소스 군 세 층.

**판별자를 두 번 틀렸고, 둘 다 조용히 틀린 KB를 만들었을 것이다.** 여기 테스트는
그 두 실패와, 더 위험했던 세 번째(중첩 재귀 누락)를 고정한다.

    1차  condition만 봄        → 한 Cosmos 계정에 Cassandra·Gremlin·Mongo·SQL 동시
    2차  defaultValue 부재=필수 → diagnosticSettings가 89개 모듈의 필수 동반자
    3차  중첩 한 단만 봄        → **VM은 아무것도 필요 없다** (사실과 정반대)
"""

from __future__ import annotations

import pytest

from app.core.cloudkb.bundlekb.model import ALWAYS, OPTIONAL, REQUIRED
from app.core.cloudkb.bundlekb.parsers.avm import classify, resolve_types

# 실제 AVM 컴파일 결과의 모양 그대로. languageVersion 2.0은 resources가 dict다.
VM_LIKE = {
    "languageVersion": "2.0",
    "resources": {
        "telemetry": {
            "type": "Microsoft.Resources/deployments",
            "condition": "[parameters('enableTelemetry')]",
            "properties": {"template": {"resources": []}},
        },
        "vm": {"type": "Microsoft.Compute/virtualMachines"},
        "lock": {
            "type": "Microsoft.Authorization/locks",
            "condition": "[not(empty(parameters('lock')))]",
        },
        "roles": {
            "type": "Microsoft.Authorization/roleAssignments",
            "copy": {"count": "[length(coalesce(parameters('roleAssignments'), createArray()))]"},
        },
        # **두 단 중첩** — 실제 VM 모듈이 이 모양이다.
        "vm_nic": {
            "type": "Microsoft.Resources/deployments",
            "copy": {"count": "[length(parameters('nicConfigurations'))]"},
            "properties": {
                "template": {
                    "resources": [
                        {
                            "type": "Microsoft.Resources/deployments",
                            "properties": {
                                "template": {
                                    "resources": [
                                        {"type": "Microsoft.Network/networkInterfaces"}
                                    ]
                                }
                            },
                        }
                    ]
                }
            },
        },
    },
}

COSMOS_LIKE = {
    "resources": [
        {"type": "Microsoft.DocumentDB/databaseAccounts"},
        {
            "type": "Microsoft.Resources/deployments",
            "copy": {"count": "[length(coalesce(parameters('gremlinDatabases'), createArray()))]"},
            "properties": {
                "template": {
                    "resources": [
                        {"type": "Microsoft.DocumentDB/databaseAccounts/gremlinDatabases"}
                    ]
                }
            },
        },
        {
            "type": "Microsoft.Resources/deployments",
            "copy": {"count": "[length(coalesce(parameters('mongodbDatabases'), createArray()))]"},
            "properties": {
                "template": {
                    "resources": [
                        {"type": "Microsoft.DocumentDB/databaseAccounts/mongodbDatabases"}
                    ]
                }
            },
        },
    ]
}


# --- 3차 실패 회귀: 가장 위험했던 것 -----------------------------------------

def test_two_level_nesting_is_resolved() -> None:
    """**핵심 회귀.** 한 단만 보면 VM의 유일한 필수 동반자가 사라진다."""
    tiers = classify(VM_LIKE)
    assert tiers.get("Microsoft.Network/networkInterfaces") == REQUIRED


def test_vm_is_not_alone() -> None:
    """"VM은 아무것도 필요 없다"는 사실과 정반대다 — Azure VM은 NIC이 있어야 한다."""
    tiers = classify(VM_LIKE)
    assert [t for t, v in tiers.items() if v == REQUIRED]


def test_resolve_returns_nothing_for_bare_deployment() -> None:
    """안이 비면 아무 타입도 안 나온다 — 없는 걸 지어내지 않는다."""
    assert resolve_types({"type": "Microsoft.Resources/deployments"}) == []


# --- 1차 실패 회귀 -----------------------------------------------------------

def test_looped_children_are_not_always() -> None:
    """한 Cosmos 계정이 Gremlin과 Mongo를 **동시에** 무조건 갖게 하면 안 된다."""
    tiers = classify(COSMOS_LIKE)
    assert tiers["Microsoft.DocumentDB/databaseAccounts"] == ALWAYS
    assert tiers["Microsoft.DocumentDB/databaseAccounts/gremlinDatabases"] == OPTIONAL
    assert tiers["Microsoft.DocumentDB/databaseAccounts/mongodbDatabases"] == OPTIONAL


# --- 2차 실패 회귀 -----------------------------------------------------------

def test_coalesce_fallback_means_optional() -> None:
    """AVM은 nullable을 `coalesce(x, createArray())`로 처리한다 — 기본값이 본문에 있다."""
    tiers = classify(VM_LIKE)
    assert tiers["Microsoft.Authorization/roleAssignments"] == OPTIONAL


def test_loop_without_fallback_means_required() -> None:
    """폴백이 없으면 값을 반드시 줘야 한다 — 그게 '필수'의 뜻이다."""
    tiers = classify(VM_LIKE)
    assert tiers["Microsoft.Network/networkInterfaces"] == REQUIRED


# --- 그 밖 -------------------------------------------------------------------

def test_condition_wins_over_no_loop() -> None:
    tiers = classify(VM_LIKE)
    assert tiers["Microsoft.Authorization/locks"] == OPTIONAL


def test_telemetry_is_not_a_member() -> None:
    """텔레메트리 배포는 모듈 기능이지 리소스 군이 아니다(그래프 파서와 같은 함정)."""
    assert "Microsoft.Resources/deployments" not in classify(VM_LIKE)


def test_resources_may_be_a_list_or_a_dict() -> None:
    """languageVersion 2.0은 dict, 그 전은 list. 하나만 가정하면 조용히 0건이 된다."""
    assert classify(VM_LIKE)          # dict 모양
    assert classify(COSMOS_LIKE)      # list 모양


def test_strongest_tier_wins_on_duplicate() -> None:
    """같은 타입이 여러 자리에 나오면 강한 등급을 남긴다."""
    doc = {
        "resources": [
            {"type": "Microsoft.Storage/storageAccounts"},
            {
                "type": "Microsoft.Storage/storageAccounts",
                "condition": "[parameters('extra')]",
            },
        ]
    }
    assert classify(doc)["Microsoft.Storage/storageAccounts"] == ALWAYS


@pytest.mark.parametrize("depth", [3, 5])
def test_deep_nesting_terminates(depth) -> None:
    """깊이 제한이 없으면 순환 참조에서 안 끝난다."""
    doc: dict = {"type": "Microsoft.Network/networkInterfaces"}
    for _ in range(depth):
        doc = {
            "type": "Microsoft.Resources/deployments",
            "properties": {"template": {"resources": [doc]}},
        }
    assert resolve_types(doc) == [("Microsoft.Network/networkInterfaces", False)]
