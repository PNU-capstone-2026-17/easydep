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
    ("서버 기본값:", "auto"),
    ("서버 자동 생성:", "auto"),
    ("disjunctive:", "choice"),
    ("선택 규칙:", "choice"),
    ("network 모드 조건부:", "conditional"),
    ("네트워크 모드 조건:", "conditional"),
    ("스킴 조건부:", "conditional"),
    ("스킴 조건:", "conditional"),
    # 쌍 호환: 조건이 간선의 한쪽이 아니라 (주체 속성 × 대상 속성) 쌍에 걸린다.
    # 판정(필수/선택)을 바꾸지 않고, 소비층에선 결정이 아니라 제약 검사가 된다.
    ("쌍 호환:", "detail"),
    ("두 자원의 호환 조건:", "detail"),
    # 이름 조건: 대상이 특정 이름이어야 한다(azure GatewaySubnet 실측).
    ("이름 조건:", "detail"),
    # 배치 조건: 대상들이 서로 어떻게 흩어져 있어야 하는가(aws EKS의 다른 AZ ≥2).
    # 개수가 아니라 분산이 조건이라 카디널리티와 따로 적는다.
    ("배치 조건:", "detail"),
    # 수명 조건: 같은 간선의 계약이 생성 시점과 이후로 갈린다(azure 노드풀 —
    # 생성 시 필수인데 이후엔 독립 CRUD). 존재 판정은 생성 시점 기준으로 두고,
    # 운영 시점의 여지는 술어가 나른다.
    ("수명 조건:", "detail"),
    ("수명주기 조건:", "detail"),
    # 동반 정리: 주체 삭제가 대상(합성물)을 함께 지운다 — 삭제 보호의 반대
    # 방향이라 lifecycle 소비가 deleteBefore가 아니라 cleanupCascades로 갈린다
    # (k8s 합성 라운드 실측). 존재 간선에 실리면 detail로 읽는다.
    ("동반 정리:", "detail"),
    # 무방비: 기능을 깨는 변이를 컨트롤 플레인이 막지 않는다 — 기능 의존
    # (question="function") 라운드의 술어. 기능 질문은 아직 closure가 소비하지
    # 않는다(어휘·측정까지 — 소비 배선은 후속). 분류 불변식 유지용 등록.
    ("무방비:", "detail"),
    # 원본 종류 반전: 같은 간선의 **대상 종류**가 CSP마다 다르다(aws AMI는
    # 디스크가 아니라 인스턴스에서 나온다). 판정을 바꾸지 않고 계획층에는
    # "무엇을 원본으로 잡을지"의 부가 조건으로 읽힌다.
    ("원본 종류 반전:", "detail"),
    ("CSP별 원본 차이:", "detail"),
    # 경유: 의존이 중간 자원을 통해 성립한다(aws EFS는 파일시스템 자체가
    # 아니라 mount target이 서브넷을 요구한다). 중간 자원을 어휘로 올리지
    # 않고 술어가 나른다 — 판정은 그대로고 계획층엔 부가 조건이다.
    ("마운트 타깃 경유:", "detail"),
    ("중간 자원을 통한 조건:", "detail"),
    ("별도 조건 없음", "detail"),
)

#: 부류 → **IDL 표현**(Martín-López, Segura, Ruiz-Cortés. *RESTest*, ICSOC 2020,
#: doi:10.1007/978-3-030-65310-1_33 — `isa-group/IDL`). 값이 `None`이면 **IDL로
#: 표현되지 않는다**는 뜻이고, 그 사실 자체가 결과다.
#:
#: ## 왜 매다나
#:
#: 위 부류는 **우리 구성**이라 "왜 이 분류인가"에 답할 것이 우리 판단뿐이었다.
#: IDL은 실제 웹 API에서 관측된 **파라미터 간 의존 일곱 종**(Requires · Or ·
#: OnlyOne · AllOrNone · ZeroOrOne · Arithmetic/Relational · Complex)을 위해
#: 만들어진 언어이고, 우리 술어 중 값 제약에 해당하는 것들이 거기에 그대로
#: 앉는다. **외부 좌표에 매다는 것이 우리 표를 다시 그리는 것보다 단단하다**
#: (`cloudkb/CLAUDE.md` §5의 근거 규율 · 논문 수준 합리성의 "외부 매핑").
#:
#: ## 매달리지 않는 것이 우리 자리다
#:
#: 셋이 `None`이고, 그 셋이 정확히 이 축의 새 기여와 겹친다 —
#: **집합 카디널리티·상이성**(다른 AZ ≥2)은 IDL이 파라미터 존재·값만 다루므로
#: 표현 대상이 아니고, **시간 축**(생성 시점 대 이후, 삭제 순서, 동반 정리)은
#: 아예 IDL 밖이다. `server-*`는 의존이 아니라 **서버의 기본값 행위**라 또 다른
#: 종류다.
#:
#: ## 이 매핑이 만든 실측 질문 (미해결)
#:
#: IDL은 `Or`(적어도 하나)와 `OnlyOne`(정확히 하나)을 **가른다.** 우리
#: `disjunctive:` 3건은 그 구별을 재지 않고 있었고, 형식을 갖추자 질문이 생겨
#: **셋 다 재서 닫았다**(2026-08-01) — 전부 배타였다:
#:
#:   azure loadBalancer  `FrontendIPConfigHasBothSubnetAndPublicIP`   (disj2)
#:   gcp   vm→image      HTTP 400 "Cannot specify both 'source' and
#:                       'initializeParams'"                          (disj5)
#:   azure vm→image      "Parameter 'osDisk.managedDisk.id' is not allowed"
#:                                                                    (disj6)
#:
#: **형식주의 채택이 실험 셋을 낳았다** — IDL이 그 구별을 강제하지 않았다면
#: 우리는 "셋 중 하나면 된다"로 두고 넘어갔을 것이다.
IDL_FORM: dict[str, str | None] = {
    # **3건 전부 배타로 측정됐다**(2026-08-01). 그래도 부류 기본값이 아니라
    # **주장의 `constraint.idl`이 진실**이다 — 새 disjunctive가 들어오면 다시
    # 재야 하고, 부류에 박아 두면 안 재고 물려받는다.
    "disjunctive:": "OnlyOne(...) — 다만 주장의 constraint.idl이 진실이다",
    "network 모드 조건부:": "Requires: IF mode=='custom' THEN subnet;",
    "스킴 조건부:": "Requires: IF scheme==<값> THEN <대상>;",
    "쌍 호환:": "Relational: <주체>.<속성> == <대상>.<속성>",
    "이름 조건:": "Relational: <대상>.name == '<이름>'",
    "배치 조건:": None,      # 집합 카디널리티·상이성 — IDL의 표현 대상이 아니다
    "수명 조건:": None,      # 시간 축 — IDL 밖
    "동반 정리:": None,      # 시간 축(삭제) — IDL 밖
    "무방비:": None,         # 컨트롤 플레인이 막지 않는 지대 — 입력 제약이 아니다
    "server-default:": None,   # 서버의 기본값 행위 — 의존이 아니다
    "server-implicit:": None,  # 서버의 합성 행위 — 의존이 아니다
    "원본 종류 반전:": None,   # 대상 종류의 CSP 차이 — 값 제약이 아니다
    "마운트 타깃 경유:": None,  # 중간 자원 경유 — 위상이지 입력 제약이 아니다
}


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
    #: 동반 정리 — (주체, 합성물). 주체 삭제가 합성물을 함께 지우므로 계획층은
    #: 합성물의 삭제 단계를 내면 안 된다(이미 없어 실패한다). deleteBefore와
    #: 기제가 반대라 섞지 않는다 — `required: true` 하나가 세 판정을 겸하다
    #: 어긋났던 진단과 같은 이유.
    cleanupCascades: tuple[tuple[str, str], ...]
    #: 기능 결속 — (주체, 대상, 무엇이 깨지나). **컨트롤 플레인이 막지 않는**
    #: 지대라서 생성·삭제 검사로는 안 잡힌다. 계획층에서는 운영 경고다:
    #: 이 대상을 떼면 apply는 성공하는데 서비스가 죽는다.
    functionalDeps: tuple[tuple[str, str, str], ...]


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
    # 기능 결속은 폐포 밖 자원(예: aws subnet→internetGateway)도 가리킬 수
    # 있다 — 경고를 잃지 않도록 대상은 scope 밖도 받는다(아래 functional).
    life = [c for c in rows
            if c["question"] == "lifecycle" and c["verdict"] == "holds"
            and c["subject"] in scope and c["object"] in scope]
    delete_before = tuple(sorted(
        (c["subject"], c["object"]) for c in life
        if not (c.get("predicate") or "").startswith("동반 정리:")))
    cascades = tuple(sorted(
        (c["subject"], c["object"]) for c in life
        if (c.get("predicate") or "").startswith("동반 정리:")))
    functional = tuple(sorted(
        (c["subject"], c["object"], c.get("note") or c.get("predicate") or "")
        for c in rows
        if c["question"] == "function" and c["verdict"] == "holds"
        and c["subject"] in scope))

    return Closure(
        anchor=anchor, csp=csp,
        required=tuple(Item(id=k, because=tuple(sorted(v["because"])),
                            detail=v["detail"])
                       for k, v in sorted(required.items())),
        createOrder=tuple(order),
        attachable=tuple(attachable[k] for k in sorted(attachable)),
        decisions=tuple(decisions),
        deleteBefore=delete_before,
        cleanupCascades=cascades,
        functionalDeps=functional,
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
