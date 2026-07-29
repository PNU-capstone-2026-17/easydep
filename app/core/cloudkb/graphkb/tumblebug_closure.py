"""자원 하나를 고르면 **무엇이 딸려오나** — 연계 리소스 군의 판정.

과제 문제 ②(*"특정 리소스를 선택하는 경우 연계되는 다양한 리소스 군을 획득할 수
있어야"*)에 대한 답이 이 파일이다. 답의 형태는 목록이 아니라 **판정 절차**다.

## 절차

    1. 앵커에서 시작해 **필수 창출 간선**만 따라간다.
    2. `operation` 경로는 따라가지 않는다 — 연산의 인자는 생성 의존이 아니다.
    3. CSP 조건표가 벤더 중립 판정을 **덮는다**(있으면 그것이 진실이다).
    4. 카탈로그(`spec`·`image`)는 **만들 것이 아니라 고를 것**이라 따로 담는다.
    5. 자동 생성 관측이 붙은 것은 cb-tumblebug이 알아서 채운다 — **사람이 정할 것이
       아니다.** 남는 것이 사용자·설계자가 결정해야 하는 목록이다.

5번이 이 절차의 값이다. 딸려오는 것을 세는 것만으로는 *"그래서 내가 뭘 정해야 하는데"*에
답하지 못한다. 판정 근거는 `docs/tumblebug-resource-dependency-2026-07-29.md`.

## 이 절차가 답하지 않는 것

**언제 만드는가**(순서)는 여기서 안 낸다. 폐포는 집합이고 순서는 위상 정렬이라
다른 함수다. 그리고 순서를 물으려면 `nodeGroupsOnCreation` 같은 동시성 요구까지
봐야 하는데, 그건 CSP 특화라 지금 층에서 최소한만 들고 있다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_ARTIFACT = Path(__file__).resolve().parent / "parsers" / "tumblebug_resources.json"


@lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads(_ARTIFACT.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Companion:
    """딸려오는 자원 하나."""

    id: str
    #: 몇 개가 필요한가. CSP 조건이 있으면 그쪽이 이긴다(aws의 k8s 서브넷 2).
    count: int
    #: cb-tumblebug이 알아서 만드는가. 거짓이면 **사람이 정해야 한다.**
    automatic: bool
    #: 무엇 때문에 딸려왔나 — 근거를 되짚을 수 있게 남긴다.
    because: tuple[str, ...]


@dataclass(frozen=True)
class Closure:
    anchor: str
    provider: str
    #: 만들어져야 하는 것.
    created: tuple[Companion, ...] = ()
    #: 골라야 하는 것(카탈로그). 만들어지는 것이 아니다.
    chosen: tuple[str, ...] = ()

    @property
    def decisions(self) -> tuple[Companion, ...]:
        """**사람이 정해야 하는 것만.** 자동으로 채워지는 것은 뺀다."""
        return tuple(c for c in self.created if not c.automatic)


def _required_here(edge: dict, provider: str, conditions: dict) -> bool:
    """이 프로바이더에서 정말 필수인가.

    **CSP 조건표가 벤더 중립 판정을 덮는다.** `sqlDb→vNet`은 cb-tumblebug 스키마에
    `validate:"required"`가 없어 중립 판정으로는 선택이지만 aws에서는 필수다
    (`RequiredCSPResourceForSqlDB`). 조건표를 필터로만 쓰면 이 간선이 폐포에서
    통째로 사라진다 — 실제로 그렇게 나와서 고친 자리다.
    """
    key = f"{edge['from']}->{edge['to']}.required"
    if key in conditions:
        return bool(conditions[key].get(provider))
    if edge["cspScoped"] and provider not in edge["cspScoped"]:
        return False
    return bool(edge["required"])


def closure(anchor: str, provider: str) -> Closure:
    """`anchor`를 만들려면 무엇이 함께 있어야 하나.

    Args:
        anchor: 자원 id (`node` · `k8sCluster` · `sqlDb` …).
        provider: `aws` · `azure` · `gcp` 중 하나. 경계 밖은 조건을 안 들고 있다.
    """
    data = _data()
    known = {r["id"] for r in data["resources"]}
    if anchor not in known:
        raise KeyError(f"모르는 자원이다: {anchor}. 아는 것: {sorted(known)}")

    conditions = data["cspConditional"]
    catalog = {r["id"] for r in data["resources"] if r["kind"] == "catalog"}

    outgoing: dict[str, list[dict]] = {}
    for edge in data["edges"]:
        if edge["methods"] and set(edge["methods"]) <= {"operation"}:
            continue
        if not _required_here(edge, provider, conditions):
            continue
        outgoing.setdefault(edge["from"], []).append(edge)

    created: dict[str, dict] = {}
    chosen: set[str] = set()
    seen, queue = {anchor}, [anchor]
    while queue:
        cur = queue.pop(0)
        for edge in outgoing.get(cur, []):
            target = edge["to"]
            if target in catalog:
                chosen.add(target)
                continue
            count = (conditions.get(f"{cur}->{target}.minCount", {}).get(provider)
                     or edge.get("minCardinalityByCsp", {}).get(provider) or 1)
            entry = created.setdefault(
                target, {"count": 1, "because": set(), "automatic": False})
            entry["count"] = max(entry["count"], count)
            entry["because"].add(cur)
            # **관측이 근거다.** 자동으로 채워진다는 것은 우리가 붙인 등급이 아니라
            # `CreateSharedResourceWithOptions` 호출을 실제로 본 관측의 성질이다.
            entry["automatic"] = entry["automatic"] or any(
                o.get("autoCreated") for o in edge["observations"])
            if target not in seen:
                seen.add(target)
                queue.append(target)

    return Closure(
        anchor=anchor, provider=provider,
        created=tuple(
            Companion(id=name, count=v["count"], automatic=v["automatic"],
                      because=tuple(sorted(v["because"])))
            for name, v in sorted(created.items())
        ),
        chosen=tuple(sorted(chosen)),
    )


def describe(anchor: str, provider: str) -> str:
    """사람이 읽는 한 문단 — 계획서·되묻기에 그대로 실을 수 있게."""
    c = closure(anchor, provider)
    if not c.created and not c.chosen:
        return (f"`{anchor}` on {provider}: nothing comes with it — it depends on no "
                f"other resource.")
    lines = [f"`{anchor}` on {provider} brings {len(c.created)} resource(s):"]
    for comp in c.created:
        many = f" ×{comp.count}" if comp.count > 1 else ""
        who = "created for you" if comp.automatic else "**you must decide it**"
        lines.append(f"  - {comp.id}{many} — {who} (required by {', '.join(comp.because)})")
    if c.chosen:
        lines.append(f"  and you must choose: {', '.join(c.chosen)}")
    if c.decisions:
        lines.append(
            f"  → decisions left to you: {', '.join(x.id for x in c.decisions)}")
    return "\n".join(lines)
