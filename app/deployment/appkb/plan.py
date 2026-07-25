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


@dataclass
class DeploymentPlan:
    """구성기의 산출물. 다이어그램·검증·답변이 전부 이걸 읽는다."""

    name: str
    nodes: list[PlanNode] = field(default_factory=list)
    edges: list[PlanEdge] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    """**답하지 못한 것.** 비워 두면 부분 답이 완전한 답처럼 읽힌다."""

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
        }
