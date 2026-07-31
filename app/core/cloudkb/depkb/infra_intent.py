"""인프라 의도 — 검증된 주장에서 **계획이 소비할 산출물**을 만든다.

`deployment-intent`(k8s 층)의 클라우드 판이다. 지금 사슬에는 클라우드 자원 층이
통째로 없고(요구사항 → 설계 → 배포 의도 → manifest), 이 모듈이 그 자리를 채운다.
계획: `document/archive/infra-intent-plan-2026-07-31.md` P1.

## 세 가지 규율

- **모르면 내지 않는다.** `closure`가 unknown 간선에 죽는 성질을 그대로 물려받는다.
- **대신 정하지 않는다.** 선택(LB 프론트엔드)·조건(네트워크 모드)은 `decisions`로
  올려보낸다. 근거 없이 고르면 그건 우리 발명이다.
- **서버가 채우는 것은 침묵하지 않는다.** `autoFilled`에 고지 문장을 붙인다 —
  말하지 않으면 사용자가 통제를 잃는다(실측된 대체만 여기 온다).

## 문장은 우리 구성이다

`_NOTICE`·`_QUESTION`은 술어를 사람이 읽는 말로 옮긴 것이고 **우리 구성**이다.
분류 불가 술어에는 죽는다(`closure.PREDICATE_CLASSES`와 같은 규율). 판정·근거
자체는 claims에서 그대로 온다.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .closure import PREDICATE_CLASSES, Closure, _claims, _classify, closure

SCHEMA_VERSION = "easydep-infra-intent/v1alpha1"

#: 술어 부류 → 고지 문장 틀(서버가 채우는 것). **우리 구성.**
_NOTICE: dict[str, str] = {
    "server-default": "{object}을(를) 정하지 않으면 {csp}가 기본값으로 채웁니다",
    "server-implicit": "{object}은(는) {csp}가 자동으로 만듭니다 — 계획에 넣지 않아도 됩니다",
}

#: 결정 종류 → 질문 문장 틀. **우리 구성.**
_QUESTION: dict[str, str] = {
    "choice": "{subject}에 붙일 것을 하나 고르세요 — {object} 중 무엇으로 할까요?",
    "conditional": "{subject}의 {object}는 조건에 따라 필수가 갈립니다 — 어느 쪽으로 할까요?",
}


@dataclass(frozen=True)
class Resource:
    id: str
    role: str  #: "required" | "attachable"
    because: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class AutoFilled:
    id: str
    notice: str


@dataclass(frozen=True)
class Decision:
    about: str
    kind: str
    question: str


@dataclass(frozen=True)
class Constraint:
    """계획이 어겨서는 안 되는 규칙 — 판정을 바꾸지 않는 술어(detail 부류)."""

    kind: str  #: "쌍 호환" | "배치 조건" | "이름 조건" | "수명 조건" | "카디널리티"
    subject: str
    object: str
    rule: str


@dataclass(frozen=True)
class InfraIntent:
    schemaVersion: str
    csp: str
    region: str
    anchors: tuple[str, ...]
    resources: tuple[Resource, ...]
    createOrder: tuple[str, ...]
    deleteBefore: tuple[tuple[str, str], ...]
    #: 동반 정리 — (주체, 합성물). 주체 삭제가 합성물을 함께 지운다(실측).
    #: deleteBefore와 기제가 반대라 섞지 않는다(closure와 같은 이유).
    cleanupCascades: tuple[tuple[str, str], ...]
    #: 기능 결속 — (주체, 대상, 무엇이 깨지나). 컨트롤 플레인이 막지 않으므로
    #: 검사가 아니라 **운영 경고**로 나른다.
    functionalDeps: tuple[tuple[str, str, str], ...]
    autoFilled: tuple[AutoFilled, ...]
    decisions: tuple[Decision, ...]
    constraints: tuple[Constraint, ...]
    provenance: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=1)


def _sentence(table: dict[str, str], kind: str, **kw) -> str:
    template = table.get(kind)
    if template is None:
        raise ValueError(f"문장 틀이 없는 부류다 — 표를 늘려라: {kind}")
    return template.format(**kw)


def _constraints_for(csp: str, ids: set[str]) -> tuple[Constraint, ...]:
    """detail 부류 술어를 제약으로 뽑는다 — 계획이 검사해야 할 규칙."""
    out: list[Constraint] = []
    for c in _claims():
        if c["csp"] != csp or not c.get("predicate"):
            continue
        # 존재 질문만 — 생명주기 술어(동반 정리 등)는 계획 시점 제약이 아니다.
        # 이 필터가 없으면 '동반 정리' 문장이 카디널리티 검사로 오독된다
        # (배선 중 실제로 났던 결함 — 생명주기 술어가 생기며 드러났다).
        if c["question"] != "existence":
            continue
        if _classify(c["predicate"]) != "detail":
            continue
        if c["subject"] not in ids or c["object"].split("|")[0] not in ids:
            continue
        # 술어에 부류 접두가 없는 것도 있다("ALB는 …") — 그때 앞부분을 부류로
        # 쓰면 규칙 문장이 통째로 부류명이 된다. 접두표에서 찾고, 없으면
        # 일반 부류로 둔다.
        prefix = next((p.rstrip(":") for p, _ in PREDICATE_CLASSES
                       if c["predicate"].startswith(p)), "")
        head, _, rest = c["predicate"].partition(":")
        kind = prefix if prefix.endswith(("조건", "호환")) else "카디널리티"
        rule = rest.strip() if rest.strip() else c["predicate"].strip()
        out.append(Constraint(kind=kind, subject=c["subject"],
                              object=c["object"], rule=rule))
    return tuple(sorted(set(out), key=lambda x: (x.subject, x.object, x.kind)))


def _merge(closures: list[Closure], csp: str
           ) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """여러 앵커의 순서·삭제 제약을 합친다.

    **각 폐포의 인덱스를 섞으면 안 된다.** 폐포마다 길이가 달라 인덱스가
    의미상 비교 불가이고, 실제로 위상 순서를 뒤집는 조합이 있다(A=(X,Y),
    B=(Z,X)이면 min-rank가 X를 Z 앞에 놓는데 Z→X가 필수다). 그래서 노드
    합집합 위에서 **다시 위상 정렬한다** — 결정적이도록 이름순 타이브레이크.
    """
    nodes = {n for c in closures for n in c.createOrder}
    edges = {(cl["subject"], cl["object"]) for cl in _claims()
             if cl["csp"] == csp and cl["question"] == "existence"
             and cl["verdict"] == "required"
             and cl["subject"] in nodes and cl["object"] in nodes}
    order: list[str] = []
    remaining = set(nodes)
    while remaining:
        ready = sorted(n for n in remaining
                       if not any((n, o) in edges for o in remaining))
        assert ready, f"필수 간선에 순환이 있다: {sorted(remaining)}"
        order.append(ready[0])
        remaining.remove(ready[0])
    pairs = sorted({p for c in closures for p in c.deleteBefore})
    return tuple(order), tuple(pairs)


def build(anchors: list[str], csp: str, region: str) -> InfraIntent:
    """앵커들에서 인프라 의도를 만든다. unknown 간선을 만나면 죽는다."""
    if not anchors:
        raise ValueError("앵커가 없다 — 무엇을 고를지 정해지지 않으면 계획도 없다")
    closures = [closure(a, csp) for a in anchors]

    resources: dict[str, Resource] = {}
    autofilled: dict[str, AutoFilled] = {}
    decisions: list[Decision] = []
    for anchor, c in zip(anchors, closures):
        resources.setdefault(anchor, Resource(id=anchor, role="anchor"))
        for item in c.required:
            prev = resources.get(item.id)
            because = tuple(sorted(set(item.because) | set(
                prev.because if prev else ())))
            resources[item.id] = Resource(id=item.id, role="required",
                                          because=because, detail=item.detail)
        for att in c.attachable:
            if att.id in resources and resources[att.id].role != "attachable":
                continue
            resources.setdefault(att.id, Resource(id=att.id, role="attachable",
                                                  detail=att.detail))
            if att.autoFilled:
                kind = _classify(att.detail)
                autofilled.setdefault(att.id, AutoFilled(
                    id=att.id, notice=_sentence(_NOTICE, att.detail.split(":")[0],
                                                object=att.id, csp=csp)))
        for d in c.decisions:
            subject, _, obj = d.about.partition("→")
            decisions.append(Decision(
                about=d.about, kind=d.kind,
                question=_sentence(_QUESTION, d.kind, subject=subject,
                                   object=obj.replace("|", " 또는 "))))

    # **필수가 이긴다.** 한 앵커에서 서버가 채워 주더라도 다른 앵커가 명시적으로
    # 요구하면 사용자가 정해야 한다 — 고지만 남기고 자동으로 두면 계획에서
    # 빠진다(앵커 둘을 합칠 때 실제로 azure subnet이 그랬다).
    autofilled = {k: v for k, v in autofilled.items()
                  if resources[k].role == "attachable"}
    order, delete_pairs = _merge(closures, csp)
    ids = set(resources)
    return InfraIntent(
        schemaVersion=SCHEMA_VERSION, csp=csp, region=region,
        anchors=tuple(anchors),
        resources=tuple(resources[k] for k in sorted(resources)),
        createOrder=order, deleteBefore=delete_pairs,
        cleanupCascades=tuple(sorted(
            {p for c in closures for p in c.cleanupCascades})),
        functionalDeps=tuple(sorted(
            {t for c in closures for t in c.functionalDeps})),
        autoFilled=tuple(autofilled[k] for k in sorted(autofilled)),
        decisions=tuple(decisions),
        constraints=_constraints_for(csp, ids),
        provenance={
            "claims": str(Path(__file__).with_name("claims.json").name),
            "oracleLayer": "apply",
            "note": ("판정은 컨트롤 플레인 실험에서 왔다. 문장(고지·질문)과 "
                     "제약 분류는 우리 구성이다."),
        },
    )
