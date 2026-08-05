"""어휘 판정의 **전수성과 사유** — 잊은 것과 뺀 것을 가른다."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.cloudkb.graphkb import scope

_ARTIFACT = (
    Path(__file__).resolve().parents[1] / "graphkb" / "parsers" / "tumblebug_resources.json"
)


@pytest.fixture(scope="module")
def surveyed() -> set[str]:
    data = json.loads(_ARTIFACT.read_text(encoding="utf-8"))
    return {r["id"] for r in data["resources"]}


def test_every_surveyed_resource_has_a_decision(surveyed) -> None:
    """조사가 찾은 자원에 판정이 없으면 **뺀 것이 아니라 잊은 것**이다.

    전수 조사를 해 놓고 판정을 일부만 하면, 빠진 자원은 조용히 사라진다. 그 침묵이
    "검토한 결과 뺐다"로 읽히는 것이 이 저장소가 계속 막아 온 종류의 오독이다.
    """
    missing = surveyed - set(scope.SCOPE)
    assert not missing, f"판정이 없는 자원: {sorted(missing)}"


def test_no_decision_without_a_resource(surveyed) -> None:
    """반대 방향 — 조사에 없는 것을 판정하고 있으면 어휘가 갈린 것이다."""
    extra = set(scope.SCOPE) - surveyed
    assert not extra, f"조사에 없는데 판정만 있는 것: {sorted(extra)}"


def test_every_decision_carries_a_reason() -> None:
    """사유 없는 판정은 판정이 아니라 취향이다."""
    for name, d in scope.SCOPE.items():
        assert d.why.strip(), f"{name}: 사유가 없다"
        assert d.role is None or d.role in scope.ROLES, f"{name}: 모르는 역할 {d.role}"


def test_exclusions_say_whether_they_can_be_revisited() -> None:
    """**구조적 제외와 조건부 제외는 다르다.**

    `objectStorage`는 고를 스펙이 아예 없어 재검토 대상이 아니고, `sqlDb`는 카탈로그
    데이터가 없어서 빠진 것이라 조건이 바뀌면 돌아올 수 있다. 둘을 같은 얼굴로 적으면
    나중에 "왜 뺐나"가 하나로 뭉개진다.
    """
    excluded = {n: d for n, d in scope.SCOPE.items() if d.role is None}
    assert excluded, "빼는 것이 하나도 없으면 판정을 안 한 것이다"
    structural = {n for n, d in excluded.items() if not d.reversible}
    assert "objectStorage" in structural, "고를 스펙이 없다는 것은 구조적 사유다"
    assert scope.SCOPE["sqlDb"].reversible, "데이터 부재는 뒤집힐 수 있는 사유다"


def test_no_role_classifies_nothing() -> None:
    """**빈 역할을 두지 않는다.**

    이전 판에 `BOUND`("리전·존·쿼터")를 선언해 놓고 해당하는 자원이 하나도 없었다.
    쓰이지 않는 칸은 분류가 아니라 우리가 만든 자리이고, 그런 것이 남아 있으면 나중에
    사실처럼 인용된다 — 실제로 그 일이 여러 번 있었다. 필요해지면 **그때 해당 자원과
    함께** 추가한다.
    """
    empty = [r for r in scope.ROLES if not scope.by_role(r)]
    assert not empty, f"분류하는 자원이 없는 역할: {empty}"


def test_the_selectable_vocabulary_is_exactly_spec_and_image() -> None:
    """**선택 역할은 둘뿐이다.**

    늘어나면 "추천 가능한가"를 포함 자격으로 쓰던 오류가 재발한 것일 수 있다 —
    그 기준을 포함 자격으로 쓰면 vNet·subnet까지 탈락한다. 늘릴 때는 그 자원이
    정말 **다른 자원의 속성으로** 산출물에 들어가는지부터 봐야 한다.
    """
    assert set(scope.by_role(scope.SELECT)) == {"spec", "image"}


def test_the_managed_pair_is_out_and_kubernetes_is_in() -> None:
    """2026-07-29 판정 — 근거는 `docs/cloud-native-extension.md` §5.1~5.2."""
    assert scope.SCOPE["sqlDb"].role is None
    assert scope.SCOPE["objectStorage"].role is None
    assert scope.SCOPE["k8sCluster"].role == scope.COMPOSE
    assert scope.SCOPE["k8sCluster"].conditional, "컨테이너 배포일 때만 등장한다"


def test_unknown_resource_raises_with_guidance() -> None:
    with pytest.raises(KeyError, match="빼기로 한 것과 잊은 것은 다르므로"):
        scope.decision_of("relationalDatabase")


def test_dependency_edges_stay_inside_the_vocabulary(surveyed) -> None:
    """경계 밖 자원이 **간선의 출발점**으로 남아 있으면 계획이 그것을 지목하게 된다.

    도착점은 다르다 — 경계 밖으로 나가는 간선은 *"이건 우리가 안 만든다"*를 말해 주므로
    남겨 두는 것이 정직하다. 출발점만 막는다.
    """
    data = json.loads(_ARTIFACT.read_text(encoding="utf-8"))
    outside = {n for n, d in scope.SCOPE.items() if d.role is None}
    offenders = sorted(
        {(e["from"], e["to"]) for e in data["edges"] if e["from"] in outside}
    )
    # 지금은 조사 산출물이 경계 판정 **이전**의 전수 기록이라 걸리는 것이 있다.
    # 그 사실을 숨기지 않고 목록으로 고정한다 — 줄어들면 좋고, 늘면 사람이 봐야 한다.
    known = {
        ("customImage", "node"), ("publicIp", "node"), ("publicIp", "vNic"),
        ("sqlDb", "subnet"), ("sqlDb", "vNet"), ("vNic", "node"),
        ("vNic", "securityGroup"), ("vNic", "subnet"), ("vNic", "vNet"),
        ("fileSystem", "vNet"), ("globalDns", "infra"),
    }
    assert set(offenders) == known, (
        "경계 밖 자원에서 나가는 간선이 바뀌었다. 조사 산출물은 전수 기록이라 "
        f"경계 밖도 담고 있다 — 어휘를 좁히면 여기가 줄어야 한다: {offenders}"
    )
