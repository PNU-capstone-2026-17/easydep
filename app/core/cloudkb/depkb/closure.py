"""폐포 — 검증된 주장만으로 "자원 하나를 고르면 무엇이 따라오나"에 답한다.

과제 문제 ②(*"특정 리소스를 선택하는 경우 연계되는 다양한 리소스 군"*)의
depkb 판, `graphkb/tumblebug_closure.py`의 후계다. 다른 점이 본질이다:

- **근거가 도구 그래프가 아니라 3사 실측 주장(claims.json)이다.** 모든 항목이
  자기를 만든 주장(간선)을 들고 다닌다.
- **CSP가 1급 인자다.** 같은 앵커의 폐포가 CSP마다 다르고(양상 반전 실측),
  그 다름이 이 함수의 값이다 — aws의 VM 폐포는 필수가 **공집합**이다(전부
  서버 대체). 그것은 버그가 아니라 측정 결과다.
- **모르는 것은 소비를 거부한다.** unknown 판정 간선을 만나면 죽는다 — 어휘가
  자라면 실험이 따라와야 하고, 조용히 추측으로 채우지 않는다.

술어의 소비 분류(PREDICATE_CLASSES)는 **우리 구성**이다 — 분류 불가능한 술어가
들어오면 죽는다(perfkb field_map 규율).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_ARTIFACT = Path(__file__).resolve().parent / "claims.json"

#: 술어 접두 → 소비 방식. **우리 구성** — 새 술어 꼴은 여기 분류를 늘려야 한다.
#:   auto: 생략하면 서버가 채운다(사람이 안 정해도 선다)
#:   choice: 선택지 중 하나를 사람이 정해야 한다
#:   conditional: 조건(모드·스킴)에 따라 필수가 갈린다 — 사람이 조건을 정한다
#:   detail: 판정을 바꾸지 않는 부가 조건(카디널리티·배치 등)
PREDICATE_CLASSES: tuple[tuple[str, str], ...] = (
    ("server-default:", "auto"),
    ("server-implicit:", "auto"),
    ("disjunctive:", "choice"),
    ("network 모드 조건부:", "conditional"),
    ("스킴 조건부:", "conditional"),
    ("ALB는", "detail"),
    ("EXTERNAL 스킴 실측", "detail"),
    # 쌍 호환: 조건이 간선의 한쪽이 아니라 (주체 속성 × 대상 속성) 쌍에 걸린다.
    # 판정(필수/선택)을 바꾸지 않고, 소비층에선 결정이 아니라 제약 검사가 된다.
    ("쌍 호환:", "detail"),
)


def _classify(predicate: str | None) -> str | None:
    if not predicate:
        return None
    for prefix, kind in PREDICATE_CLASSES:
        if predicate.startswith(prefix):
            return kind
    raise ValueError(f"분류 없는 술어다 — PREDICATE_CLASSES를 늘려라: {predicate}")


@dataclass(frozen=True)
class Item:
    """반드시 실재해야 하는 것."""

    id: str
    #: 어느 간선(주장) 때문인가 — 근거를 되짚는 열쇠.
    because: tuple[str, ...]
    #: 판정을 바꾸지 않는 부가 조건(예: "ALB는 서로 다른 AZ의 서브넷 ≥2").
    detail: str = ""


@dataclass(frozen=True)
class Attachable:
    """붙일 수 있으나 없어도 서는 것."""

    id: str
    #: 참이면 생략 시 서버가 채운다 — 사람이 정하지 않아도 된다(실측된 대체).
    autoFilled: bool
    detail: str = ""


@dataclass(frozen=True)
class Decision:
    """사람이 정해야 하는 것 — 선택지 또는 조건."""

    about: str
    kind: str  #: "choice" | "conditional"
    detail: str


@dataclass(frozen=True)
class Closure:
    anchor: str
    csp: str
    #: 앵커가 서기 위해 실재해야 하는 것(이행적).
    required: tuple[Item, ...]
    #: 생성 순서 — 필수 존재 간선의 위상 정렬(앵커 포함, 앞이 먼저).
    createOrder: tuple[str, ...]
    attachable: tuple[Attachable, ...]
    decisions: tuple[Decision, ...]
    #: 삭제 제약 — (먼저 지울 것, 그 다음). **실측된 생명주기 주장만** 싣는다.
    deleteBefore: tuple[tuple[str, str], ...]


@lru_cache(maxsize=1)
def _claims() -> list[dict]:
    return json.loads(_ARTIFACT.read_text(encoding="utf-8"))["claims"]


def closure(anchor: str, csp: str) -> Closure:
    """`anchor`를 `csp`에 만들려면 무엇이 함께 있어야 하나."""
    rows = [c for c in _claims() if c["csp"] == csp]
    if not rows:
        known = sorted({c["csp"] for c in _claims()})
        raise KeyError(f"모르는 CSP다: {csp}. 아는 것: {known}")
    if not any(anchor in (c["subject"], c["object"]) for c in rows):
        known = sorted({c["subject"] for c in rows})
        raise KeyError(f"{csp}에서 모르는 자원이다: {anchor}. 아는 것: {known}")

    exist = [c for c in rows if c["question"] == "existence"]
    for c in exist:
        if c["verdict"] == "unknown":
            raise ValueError(
                f"판정 없는 간선을 소비할 수 없다: {csp} "
                f"{c['subject']}→{c['object']} — 실험이 먼저다"
            )

    required: dict[str, dict] = {}
    attachable: dict[str, Attachable] = {}
    decisions: list[Decision] = []
    seen, queue = {anchor}, [anchor]
    while queue:
        cur = queue.pop(0)
        for c in exist:
            if c["subject"] != cur:
                continue
            kind = _classify(c.get("predicate"))
            label = f"{c['subject']}→{c['object']}"
            if kind == "choice":
                decisions.append(Decision(
                    about=label, kind="choice", detail=c["predicate"]))
                continue
            if kind == "conditional":
                decisions.append(Decision(
                    about=label, kind="conditional", detail=c["predicate"]))
                attachable.setdefault(c["object"], Attachable(
                    id=c["object"], autoFilled=False, detail=c["predicate"]))
                continue
            if c["verdict"] == "required":
                entry = required.setdefault(
                    c["object"], {"because": set(), "detail": ""})
                entry["because"].add(label)
                if kind == "detail":
                    entry["detail"] = c["predicate"]
                if c["object"] not in seen:
                    seen.add(c["object"])
                    queue.append(c["object"])
            else:  # optional
                if c["object"] in seen:
                    continue  # 이미 필수로 딸려온 것을 선택지로 다시 세지 않는다
                attachable.setdefault(c["object"], Attachable(
                    id=c["object"], autoFilled=(kind == "auto"),
                    detail=c.get("predicate") or ""))

    # 생성 순서 — 필수 간선만으로 위상 정렬(결정적: 이름순 타이브레이크)
    nodes = sorted(seen)
    edges = {(c["subject"], c["object"]) for c in exist
             if c["verdict"] == "required"
             and c["subject"] in seen and c["object"] in seen}
    order: list[str] = []
    remaining = set(nodes)
    while remaining:
        ready = sorted(n for n in remaining
                       if not any((n, o) in edges for o in remaining))
        assert ready, f"필수 간선에 순환이 있다: {sorted(remaining)}"
        order.append(ready[0])
        remaining.remove(ready[0])

    scope = seen | set(attachable)
    delete_before = tuple(sorted(
        (c["subject"], c["object"]) for c in rows
        if c["question"] == "lifecycle" and c["verdict"] == "holds"
        and c["subject"] in scope and c["object"] in scope))

    return Closure(
        anchor=anchor, csp=csp,
        required=tuple(Item(id=k, because=tuple(sorted(v["because"])),
                            detail=v["detail"])
                       for k, v in sorted(required.items())),
        createOrder=tuple(order),
        attachable=tuple(attachable[k] for k in sorted(attachable)),
        decisions=tuple(decisions),
        deleteBefore=delete_before,
    )


def describe(anchor: str, csp: str) -> str:
    """사람이 읽는 한 문단 — 계획서·되묻기에 그대로 실을 수 있게."""
    c = closure(anchor, csp)
    lines = [f"`{anchor}` on {csp}:"]
    if c.required:
        lines.append("  must exist first: " + ", ".join(
            i.id + (f" ({i.detail})" if i.detail else "") for i in c.required))
        lines.append("  create order: " + " -> ".join(c.createOrder))
    else:
        lines.append("  nothing must pre-exist — the cloud fills every gap.")
    for a in c.attachable:
        tag = "auto-filled if omitted" if a.autoFilled else "attach if you need it"
        lines.append(f"  optional: {a.id} — {tag}")
    for d in c.decisions:
        lines.append(f"  you must decide ({d.kind}): {d.about} — {d.detail}")
    for first, then in c.deleteBefore:
        lines.append(f"  deletion: remove {first} before {then}")
    return "\n".join(lines)
