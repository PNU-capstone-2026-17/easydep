"""추적성 색인 — **누가 무엇을 커버한다고 주장하는가**를 한 곳에서 센다.

## 왜 필요했나 — 두 곳이 같은 사실을 다르게 셌다

추적 링크(`use_cases[].requirement_ids` / `nfr_ids` / `main_scenario[].covered_req_ids`)는
상태에 흩어져 있고, 그걸 굴려 집계하는 코드가 최소 두 벌 있었다:

  - `step2_usecases.check_coverage` — 파이프라인이 쓰는 커버리지 게이트
  - `rtm.build_rtm`                 — 저장 시점에 물질화되는 추적 매트릭스

둘이 "환각 참조"(없는 요구 id를 가리키는 것)를 **다르게 정의하고 있었고, 실제로 답이
갈렸다.** 같은 상태에서:

    check_coverage → ['NFR1']   # 실재하는 NFR인데 환각이라고 한다
    rtm            → ['NFR9']   # 진짜 없는 id. 이쪽이 맞다

원인은 `check_coverage`가 `requirement_ids`만 모아 **FR 목록하고만** 대조한 것이다:

  - UC가 NFR을 `requirement_ids`에 적으면 → 실재하는 id인데 환각으로 **오탐**
  - UC가 없는 id를 `nfr_ids`에 적으면     → 대조 대상이 아니라 **미탐**

하필 파이프라인이 쓰는 쪽이 틀린 쪽이었고, 그 값이 채점표(`compare.py`)의
`unknown_requirement_refs` 지표로도 나갔다. **환각 검출기가 환각을 세고 있었다.**

## 이 파일의 규율

집계는 여기서만 한다. `check_coverage`도 `build_rtm`도 여기서 파생한다 — 사본이 둘이면
정의가 갈리고, 갈린 것을 알아채는 데 이번처럼 오래 걸린다.

**링크 종류를 뭉개지 않는다.** `requirement_ids`(UC가 실현한다고 주장하는 FR)와
`nfr_ids`(UC를 한정하는 제약)는 뜻이 다르므로 따로 센다. 환각 판정만 둘을 합쳐서 본다 —
"이 id가 존재하는가"는 어느 칸에 적혔든 같은 질문이기 때문이다.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Traceability:
    """한 실행 상태의 추적 링크를 되읽기 좋게 뒤집어 둔 것."""

    #: 요구 id → 분류된 요구 레코드.
    by_id: dict[str, dict] = field(default_factory=dict)
    fr_ids: frozenset[str] = frozenset()
    nfr_ids: frozenset[str] = frozenset()
    #: 요구 id → 그것을 **실현한다고 주장하는** UC id들(`requirement_ids`).
    ucs_claiming: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: 요구 id → 그것을 **제약으로 붙인** UC id들(`nfr_ids`).
    ucs_constrained_by: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: 요구 id → 그것을 커버한다고 적힌 명세 스텝들(`"UC1.3"`). UC보다 정밀한 추적.
    steps_of: dict[str, tuple[str, ...]] = field(default_factory=dict)

    # -- 되읽기 ------------------------------------------------------------
    def ucs_of(self, req_id: str) -> tuple[str, ...]:
        """어느 칸으로든 이 요구를 건 UC들. 매트릭스가 보는 시야다."""
        merged = list(self.ucs_claiming.get(req_id, ()))
        merged += [u for u in self.ucs_constrained_by.get(req_id, ()) if u not in merged]
        return tuple(merged)

    @property
    def referenced_ids(self) -> frozenset[str]:
        """UC가 어느 칸으로든 참조한 id 전부."""
        return frozenset(self.ucs_claiming) | frozenset(self.ucs_constrained_by)

    @property
    def unknown_refs(self) -> tuple[str, ...]:
        """분류 목록에 **없는** id를 참조한 것 — 환각.

        `requirement_ids`·`nfr_ids`를 합쳐서 보고, 대조 대상은 FR 목록이 아니라 **알려진
        요구 전체**다. 둘 중 하나라도 빠뜨리면 오탐이나 미탐이 생긴다(모듈 docstring).
        """
        return tuple(sorted(self.referenced_ids - frozenset(self.by_id)))

    @property
    def covered_fr_ids(self) -> tuple[str, ...]:
        """어떤 UC가 실현을 주장한 FR."""
        return tuple(sorted(self.fr_ids & frozenset(self.ucs_claiming)))

    @property
    def orphan_fr_ids(self) -> tuple[str, ...]:
        """아무 UC도 주장하지 않은 FR — 누락 위험."""
        return tuple(sorted(self.fr_ids - frozenset(self.ucs_claiming)))

    @property
    def attached_nfr_ids(self) -> tuple[str, ...]:
        """어떤 UC에 제약으로 붙은 NFR."""
        return tuple(sorted(self.nfr_ids & frozenset(self.ucs_constrained_by)))

    @property
    def unattached_nfr_ids(self) -> tuple[str, ...]:
        """어디에도 안 붙은 NFR — 전역 제약 후보."""
        return tuple(sorted(self.nfr_ids - frozenset(self.ucs_constrained_by)))

    @property
    def coverage_ratio(self) -> float:
        """FR 중 커버된 비율. FR이 없으면 1.0(빈 입력을 실패로 읽지 않는다)."""
        if not self.fr_ids:
            return 1.0
        return round(len(self.covered_fr_ids) / len(self.fr_ids), 4)


def index(state: dict) -> Traceability:
    """상태에서 추적 색인을 만든다(순수 함수, LLM 없음)."""
    classified = state.get("classified") or []
    by_id = {r["id"]: r for r in classified}

    claiming: dict[str, list[str]] = {}
    constrained: dict[str, list[str]] = {}
    for uc in state.get("use_cases") or []:
        uc_id = uc.get("id", "?")
        for rid in uc.get("requirement_ids", []) or []:
            claiming.setdefault(rid, []).append(uc_id)
        for nid in uc.get("nfr_ids", []) or []:
            constrained.setdefault(nid, []).append(uc_id)

    steps: dict[str, list[str]] = {}
    for spec in state.get("use_case_specs") or []:
        uc_id = spec.get("use_case_id", "?")
        for step in spec.get("main_scenario", []) or []:
            for rid in step.get("covered_req_ids", []) or []:
                steps.setdefault(rid, []).append(f"{uc_id}.{step['step_number']}")

    return Traceability(
        by_id=by_id,
        fr_ids=frozenset(r["id"] for r in classified if r.get("type") == "FR"),
        nfr_ids=frozenset(r["id"] for r in classified if r.get("type") == "NFR"),
        ucs_claiming={k: tuple(v) for k, v in claiming.items()},
        ucs_constrained_by={k: tuple(v) for k, v in constrained.items()},
        steps_of={k: tuple(v) for k, v in steps.items()},
    )
