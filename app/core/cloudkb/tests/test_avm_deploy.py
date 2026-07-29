"""AVM 배포 순서 엣지 (graphkb/parsers/avm.py).

여기서 지켜야 하는 것:
- **텔레메트리를 걸러낸다.** AVM은 모든 모듈에 `Microsoft.Resources/deployments`를
  넣는다. 담으면 모든 타입이 여기 의존하는 **가짜 허브**가 생긴다(실측 111쌍).
- **심볼을 못 풀면 담지 않는다.** `dependsOn`은 심볼 이름이고 `resourceId(...)` 식은
  못 푼다 — 문자열을 억지로 파싱하면 없는 타입을 만들어낸다.
- 이 엣지는 "모듈이 이 순서로 배포한다"이지 "API가 강제한다"가 아니다.
"""

from __future__ import annotations

from app.core.cloudkb.graphkb.parsers.avm import type_pairs

TEMPLATE = {
    "resources": {
        "storageAccount": {"type": "Microsoft.Storage/storageAccounts",
                           "dependsOn": ["cMKKeyVault"]},
        "cMKKeyVault": {"type": "Microsoft.KeyVault/vaults"},
        "diag": {"type": "Microsoft.Insights/diagnosticSettings",
                 "dependsOn": ["storageAccount"]},
        "avmTelemetry": {"type": "Microsoft.Resources/deployments"},
        "withTelemetry": {"type": "Microsoft.Authorization/locks",
                          "dependsOn": ["avmTelemetry", "storageAccount"]},
        "unresolvable": {"type": "Microsoft.Network/virtualNetworks",
                         "dependsOn": ["[resourceId('Microsoft.Foo/bar', 'x')]"]},
        "selfdep": {"type": "Microsoft.Storage/storageAccounts",
                    "dependsOn": ["storageAccount"]},
    }
}


def test_symbol_is_resolved_to_type() -> None:
    pairs = type_pairs(TEMPLATE)
    assert ("Microsoft.Storage/storageAccounts", "Microsoft.KeyVault/vaults") in pairs
    assert (
        "Microsoft.Insights/diagnosticSettings",
        "Microsoft.Storage/storageAccounts",
    ) in pairs


def test_unresolvable_dependson_is_dropped() -> None:
    """`resourceId(...)` 식은 심볼이 아니라 못 푼다 — 지어내지 않는다."""
    pairs = type_pairs(TEMPLATE)
    assert not [p for p in pairs if p[0] == "Microsoft.Network/virtualNetworks"]


def test_self_dependency_is_dropped() -> None:
    """같은 타입끼리는 담지 않는다 — 순서 정보가 아니다."""
    pairs = type_pairs(TEMPLATE)
    assert (
        "Microsoft.Storage/storageAccounts",
        "Microsoft.Storage/storageAccounts",
    ) not in pairs


def test_telemetry_pair_is_present_here_and_filtered_later() -> None:
    """순수 함수는 텔레메트리도 돌려준다 — 거르는 일은 parse_tarball 몫이다.

    두 자리에서 다 거르면 어느 쪽이 실제로 막고 있는지 알 수 없다.
    """
    pairs = type_pairs(TEMPLATE)
    assert (
        "Microsoft.Authorization/locks",
        "Microsoft.Resources/deployments",
    ) in pairs


def test_array_schema_is_not_parsed() -> None:
    """옛 ARM 스키마는 resources가 배열이라 심볼이 없다. 빈 결과가 맞다."""
    assert type_pairs({"resources": [{"type": "X", "dependsOn": ["Y"]}]}) == set()
