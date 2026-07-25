"""**"바꿀 수 있다"도 원본이 한 말이다.**

`x-ms-mutability`에 `update`가 있으면 예전엔 그냥 버렸다. 그러면 원본이 명시한 사실이
"우리가 모른다"와 **같은 모양**이 된다 — 이 KB가 줄곧 좁히려 한 바로 그 '없음'이다.

## 왜 이걸 지금 했나 — ARM 규약 불변성의 결말

"ARM은 이름과 리전을 못 바꾼다"를 규약으로 선언해 4,358건을 채우자는 안이 보류돼
있었다. 이유는 "실제로 못 바꾸는데 **원본이 그렇게 말한 문장이 없다**"였다.

원본을 직접 세어 보니 문장이 없는 게 아니라 **반대로 말하는 문장이 있었다.**

    name      [create,read] 8 · [read] 3        반례 0건
    location  [create,read] 160 · [read] 2      반례 **2건**
              [create,read,update] 2  ← DocumentDB/cassandraClusters
                                        Capacity/reservationOrders

`kcc-immutable-prefix`는 반례 0건이라 명시로 취급했다. 여기는 반례가 있으니 **같은
논리로 일반화할 수 없다** — 규약으로 채웠다면 최소 2종에서 거짓을 단언했을 것이고,
어느 2종인지 알 방법도 없었을 것이다.
"""

from __future__ import annotations

import pytest

from app.deployment.capacitykb.model import CapacitySet, Constraint
from app.deployment.capacitykb.query import immutable_properties

CASSANDRA = "azure::Microsoft.DocumentDB/cassandraClusters"
APIM = "azure::Microsoft.ApiManagement/service"


@pytest.fixture
def capacity() -> CapacitySet:
    """반례 한 종과 통상 한 종."""
    cap = CapacitySet()
    for type_id, value in ((CASSANDRA, "updatable"), (APIM, "create_only")):
        cap.add_constraint(Constraint(
            type_id=type_id, property="location", kind="mutability",
            value=value, evidence="swagger-mutability",
        ))
    cap.add_constraint(Constraint(
        type_id=CASSANDRA, property="properties.clusterNameOverride",
        kind="mutability", value="create_only", evidence="swagger-mutability",
    ))
    return cap


def test_updatable_never_enters_the_immutable_list(capacity) -> None:
    """**이 변경의 유일한 위험이 여기다.** 섞이면 정반대 답을 하게 된다."""
    props = [c.property for c in immutable_properties(capacity, CASSANDRA)]
    assert props == ["properties.clusterNameOverride"]
    assert "location" not in props


def test_the_ordinary_type_keeps_its_immutable_location(capacity) -> None:
    assert [c.property for c in immutable_properties(capacity, APIM)] == ["location"]


def test_updatable_is_stated_not_guessed(capacity) -> None:
    """짐작으로 바꿀 수 있다는 게 아니라 명세가 update를 허용 목록에 넣은 것이다."""
    found = next(
        c for c in capacity.for_type(CASSANDRA)
        if c.property == "location"
    )
    assert found.value == "updatable"
    assert found.is_fact


def test_schema_allows_the_new_value() -> None:
    """산출물 검증이 새 값을 통과시켜야 빌드가 저장된다."""
    import io
    import json
    from pathlib import Path

    schema = json.load(io.open(Path(__file__).resolve().parent.parent / "capacitykb/model/schema.json", encoding="utf-8"))
    text = json.dumps(schema, ensure_ascii=False)
    assert "updatable" in text


def test_label_says_who_said_it() -> None:
    """'변경 가능'만 적으면 우리 판단처럼 읽힌다 — 명세가 그렇게 표시했다고 밝힌다."""
    from app.deployment.capacitykb.agent_api import _describe

    text = _describe(Constraint(
        type_id=CASSANDRA, property="location", kind="mutability",
        value="updatable", evidence="swagger-mutability",
    ))
    flat = " ".join(text.split())
    assert "can be changed after creation" in flat
    assert "the spec marks update as allowed" in flat
