"""배포 계획의 자료 모델 — **모든 칸에 근거가 달린다.**

이 모듈은 **KB를 import하지 않는다.** 계획을 만드는 것(조인)은 도구 계층의 일이고
(`nim_agent/design_tools.py`), 여기는 그 결과의 모양과 규율만 정한다. 그래야 도형
생성·검증을 KB 없이 테스트할 수 있다.

## 왜 근거가 칸마다 붙나

배포 다이어그램은 **생성물**이다. 지금까지 축들은 "원본이 이렇게 말했다"를 옮겼지만
여기서는 우리가 새 그림을 만든다 — 그러면 어느 선이 설계도에서 왔고 어느 선이 우리
추론인지 구분이 사라진다. 그 구분이 사라지는 순간 이 저장소가 막아 온 것(짐작을
사실처럼 말하기)이 그림의 형태로 돌아온다.

    design      설계 산출물이 그렇게 말했다        (OpenAPI에 경로가 있다)
    designer    설계자가 지정했다 (deployHint)     — 우리 판단이 아니다
    kb          지식베이스가 답했다                 (svcmap·costkb)
    inferred    우리가 신호에서 추론했다            (async 메시지 → 큐)

`inferred`는 답에 반드시 유보가 붙는다(`needs_hedge`와 같은 규율).
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 근거 종류. 순서가 곧 강도다 — 앞이 셀수록 우리가 만든 몫이 적다.
ORIGIN_DESIGN = "design"
ORIGIN_DESIGNER = "designer"
ORIGIN_KB = "kb"
ORIGIN_INFERRED = "inferred"
ORIGINS = (ORIGIN_DESIGN, ORIGIN_DESIGNER, ORIGIN_KB, ORIGIN_INFERRED)

_HEDGED = {ORIGIN_INFERRED, ORIGIN_DESIGNER}

ORIGIN_LABEL = {
    ORIGIN_DESIGN: "design artifact",
    ORIGIN_DESIGNER: "specified by the designer",
    ORIGIN_KB: "knowledge base",
    ORIGIN_INFERRED: "we inferred",
}


def needs_hedge(origin: str) -> bool:
    """답에 유보를 붙여야 하는가.

    **`designer`도 유보 대상이다.** 설계자가 "이건 VM"이라 적었어도 그건 주장이지
    검증된 사실이 아니다 — 상류로 배포 결정을 옮기면 환각이 검사 없는 곳으로
    이사할 뿐이라는 계약의 판단(`appkb/schema.json`의 deployHint)과 같은 결이다.
    """
    return origin in _HEDGED


@dataclass(frozen=True)
class Note:
    """계획에 붙는 한 줄. 근거 없이 붙는 말은 없다."""

    text: str
    origin: str
    source: str = ""
    """어디서 왔는지 더 좁게 — 산출물 id, 도구 이름, 근거 라벨."""

    def __post_init__(self) -> None:
        if self.origin not in ORIGINS:
            raise ValueError(f"unknown origin: {self.origin!r} (allowed: {ORIGINS})")


@dataclass(frozen=True)
class PlanNode:
    """배포 계획의 노드 하나 — 컴퓨트든 관리형 서비스든 외부 시스템이든."""

    id: str
    label: str
    role: str
    """`compute` | `managed` | `shared` | `ingress` | `external` | `actor`.

    도형이 모양을 여기서 정한다. `shared`는 네트워크·키·이미지처럼 **연결당 공유라
    계획에 한 벌만 있는 것**이다(bundlekb가 답한다). `ingress`는 공개 노출
    컴포넌트 앞의 진입점(로드밸런서) — 노출 서비스마다 하나라 shared가 아니다.
    """

    origin: str
    archetype: str = ""
    """`app::` 개념 이름(관리형일 때). 빈 값이면 컴퓨트·외부다."""

    type_id: str = ""
    """정해졌다면 벤더 타입 id. **선택지가 여럿이면 비운다** — 하나를 임의로
    고르는 것이 이 저장소가 막아 온 실패다. 후보는 `candidates`에 둔다."""

    candidates: tuple[str, ...] = ()
    notes: tuple[Note, ...] = ()

    hourly_usd: float | None = None
    """값이 붙은 노드의 시간당 단가(USD). **판정용 기계 값**이다 — 예산 대조가
    노트 문장("$0.0468/h")을 되파싱하게 두면 문구 하나에 판정이 흔들린다.
    None은 0이 아니라 "값이 없다"다 — 더하는 쪽이 그 구분을 지켜야 한다."""

    host: str = ""
    """이 노드가 **어디서 도는가** — 실행 환경의 이름(`VM · t3a.medium` 등).

    UML 배포 다이어그램의 뼈대는 **Node ← «deploy» ← Artifact**다. 컴포넌트는
    노드가 아니라 **노드 위에 배포되는 아티팩트**이므로, 컴포넌트 이름(`label`)과
    실행 환경 이름(여기)이 갈려야 그림이 그 삼각형을 그릴 수 있다.

    한동안 둘이 한 상자였다(2026-07-28 감사에서 지적). 그래서 "한 VM에 두 컴포넌트"도
    "k8s의 노드풀/파드/컨테이너 3층"도 표현할 수 없었고, 그림이 배포도라기보다
    **리소스 목록**에 가까웠다.

    비어 있으면 렌더러가 컴포넌트를 감싸지 않고 그대로 그린다 — 실행 환경을 모르면서
    아는 척하지 않는다.
    """

    replicas: int | None = None
    """이 노드를 **몇 개** 놓는가. `None`은 1이 아니라 **정해지지 않았다**는 뜻이다.

    계획은 "How many instances you need is not something this knowledge base can
    decide"라고 노트로 말해 왔는데, **그림에는 상자가 하나뿐이라 1대처럼 읽혔다.**
    노트는 그림과 같이 안 다닌다 — 그림은 잘려 돌아다닌다(범례에 유보를 넣은 것과
    같은 이유). 그래서 미정을 그림에도 남긴다.

    근거 없는 사이징은 이 저장소가 막아 온 것이므로(9장) **우리가 수를 정하지
    않는다.** 설계자가 정하면 그 수가 들어오고, 아니면 `None`으로 남아 `×?`로
    그려진다. 노드/아티팩트 분리(한 VM에 여러 컴포넌트)는 아직 없다 — 그건 별도
    과제이고, 이 칸은 **미정을 미정이라고 그리는 것**까지만 한다.
    """

    placement: str = "unknown"
    """이 노드가 **어디에 놓이는가** — 다이어그램 중첩의 근거.

        "<노드 id>"  그 노드 **안에** 그린다
        "none"       담기지 않는다는 것을 **안다** (최상위 · 외부 시스템 · 행위자)
        "unknown"    **배치를 모른다** (기본값)

    `"unknown"`이 기본값인 것이 요점이다. 그림에서 상자가 밖에 있으면 "밖에 있다"는
    주장으로 읽히는데, 관리형 서비스는 사실 **우리가 모르는 것**이다 — 실측(2026-07-28)
    결과 `contained_in` 축은 **네트워크 배치가 아니다**(Azure는 ARM 이름 계층, GCP는
    프로젝트 소속, AWS는 0건). "RDS가 서브넷에 산다"를 아는 축이 이 저장소에 없다.

    그래서 셋을 가른다. 부재를 "밖"으로 승격하지 않는 것이 이 저장소의 규율이고
    (`basis.py`·`perfkb`가 같은 이유로 상태를 늘렸다), 그림에서만 예외일 이유가 없다.

    **누가 채우나**: 축을 합치는 일은 도구 계층의 몫이라(kb-book 3장의 단방향 규약)
    `appkb`가 `graphkb`를 부를 수 없다. 구성기(`nim_agent/design_tools.py`)가 그래프
    축에 물어 여기 담고, 렌더러는 계획만 읽는다.
    """

    def __post_init__(self) -> None:
        if self.origin not in ORIGINS:
            raise ValueError(f"unknown origin: {self.origin!r}")
        if self.type_id and self.candidates:
            raise ValueError(
                f"{self.id}: the type is fixed but a candidate list is also set — only one"
            )


@dataclass(frozen=True)
class PlanEdge:
    """노드 사이의 선. **모든 선에 근거가 있다.**"""

    from_id: str
    to_id: str
    label: str
    origin: str
    async_: bool = False

    def __post_init__(self) -> None:
        if self.origin not in ORIGINS:
            raise ValueError(f"unknown origin: {self.origin!r}")


@dataclass(frozen=True)
class Measured:
    """3사 실측이 이 계획에 대해 아는 것 — **시간축과 무방비 지대.**

    노드·선만으로는 담을 수 없는 것들이다. 계획은 공간(무엇이 어디에)을 그리는데
    실측이 아는 것 절반은 **시간**(무엇을 먼저)과 **컨트롤 플레인이 막지 않는
    자리**(떼면 조용히 깨지는 것)라서, 그림에 자리가 없어 통째로 유실됐다.

    2026-08-01 대조에서 표본 3종 × 3사 모두 같은 항목이 빠졌다 — 앱과 무관한
    구조적 유실이었다(`app/core/plan_crosscheck.py`).

    **근거는 전부 `apply` 층 실측이다.** 다른 칸들처럼 origin을 달지 않는 이유는
    이 묶음 전체가 한 출처(depkb claims)이기 때문이고, 그 사실을 `csp`·`anchors`가
    말한다 — 어느 CSP의 어느 앵커에서 나온 것인지가 곧 좌표다.
    """

    csp: str
    #: 이 계획에서 앵커로 읽힌 자원(폐포의 입력).
    anchors: tuple[str, ...] = ()
    #: 만드는 순서. 앞의 것이 뒤의 것보다 먼저다.
    create_order: tuple[str, ...] = ()
    #: (먼저 지울 것, 그다음 지울 것). 어기면 삭제가 거부된다.
    delete_before: tuple[tuple[str, str], ...] = ()
    #: (자원, 연산, 완료 신호, 확신도). **미표시는 '동기'가 아니다** — 안 재본 것이다.
    wait_for: tuple[tuple[str, str, str, str], ...] = ()
    #: (자원, 사유, 술어 부류). `server-implicit`은 만들면 이중 생성이고
    #: `server-default`는 안 정하면 서버가 채운다 — **다른 사실이다.**
    do_not_create: tuple[tuple[str, str, str], ...] = ()
    #: (주체, 대상, 무엇이 깨지나). 컨트롤 플레인이 **막지 않는** 지대라
    #: 생성·삭제 검사로는 안 잡힌다.
    operational_warnings: tuple[tuple[str, str, str], ...] = ()
    #: (주체, 대상, 규칙, 기계가 볼 수 있는 몫). 넷째가 `None`이면 산문뿐이라
    #: 사람이 판정하고, 있으면 대조기가 실제로 본다 — **산문을 파싱하지 않는다.**
    checks: tuple[tuple[str, str, str, dict | None], ...] = ()
    #: 계획에 있는데 우리가 아무 말도 못 하는 자원. **침묵을 '문제없다'로 읽지 말 것.**
    unmeasured: tuple[str, ...] = ()
    # `reused`(기존 자원 인계)는 2026-08-02 범위 결정으로 걷었다 — 신규 앱 개발
    # 전제라 인계받을 자원이 없다(계약 existingResources 제거와 한 몸).


@dataclass
class DeploymentPlan:
    """구성기의 산출물. 다이어그램·검증·답변이 전부 이걸 읽는다."""

    name: str
    nodes: list[PlanNode] = field(default_factory=list)
    edges: list[PlanEdge] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    """**답하지 못한 것.** 비워 두면 부분 답이 완전한 답처럼 읽힌다."""
    measured: Measured | None = None
    """3사 실측 사영. `None`이면 **안 붙인 것**이지 문제없다는 뜻이 아니다."""

    def node(self, node_id: str) -> PlanNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    @property
    def hedged_count(self) -> int:
        return sum(1 for n in self.nodes if needs_hedge(n.origin)) + sum(
            1 for e in self.edges if needs_hedge(e.origin)
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "nodes": [
                {
                    "id": n.id, "label": n.label, "role": n.role,
                    "origin": n.origin, "archetype": n.archetype,
                    "typeId": n.type_id, "candidates": list(n.candidates),
                    "hourlyUSD": n.hourly_usd,
                    "placement": n.placement,
                    "notes": [
                        {"text": x.text, "origin": x.origin, "source": x.source}
                        for x in n.notes
                    ],
                }
                for n in self.nodes
            ],
            "edges": [
                {
                    "from": e.from_id, "to": e.to_id, "label": e.label,
                    "origin": e.origin, "async": e.async_,
                }
                for e in self.edges
            ],
            "notes": [
                {"text": x.text, "origin": x.origin, "source": x.source}
                for x in self.notes
            ],
            "unresolved": list(self.unresolved),
            # `None`과 빈 묶음을 가른다 — 앞은 "안 붙였다", 뒤는 "붙였는데 없다".
            "measured": None if self.measured is None else {
                "csp": self.measured.csp,
                "anchors": list(self.measured.anchors),
                "createOrder": list(self.measured.create_order),
                "deleteBefore": [list(p) for p in self.measured.delete_before],
                "waitFor": [
                    {"id": i, "op": o, "doneSignal": s, "confidence": c}
                    for i, o, s, c in self.measured.wait_for],
                "doNotCreate": [
                    {"id": i, "why": w, "kind": k}
                    for i, w, k in self.measured.do_not_create],
                "operationalWarnings": [
                    {"subject": s, "object": o, "breaks": b}
                    for s, o, b in self.measured.operational_warnings],
                "checks": [
                    {"subject": s, "object": o, "rule": r, "machine": m}
                    for s, o, r, m in self.measured.checks],
                "unmeasured": list(self.measured.unmeasured),
            },
        }
