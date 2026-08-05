"""추적성 색인 — **누가 무엇을 커버한다고 주장하는가**를 한 곳에서 센다.

집계는 여기서만 한다. 파이프라인 커버리지와 저장되는 추적 스냅샷도 여기서 파생한다.
표나 문서를 렌더링하지 않고, 요구사항 ID가 어느 산출물에 연결되는지만 구조화해 제공한다.

**링크 종류를 뭉개지 않는다.** `requirement_ids`(실현 주장)와 `nfr_ids`(제약 부착)는 뜻이
달라 따로 센다. 환각 판정만 둘을 합쳐서 본다 — "이 id가 존재하는가"는 어느 칸에 적혔든
같은 질문이라서다.

## 왜 `app/core`에 있나

추적성은 요구사항 에이전트만의 것이 아니다 — 전 단계 산출물이 요구사항에 어떻게 닿는지가
과제 목표(전 과정 일관 기준)의 축이다.

**구현 엔진의 `traceability-matrix.csv`와는 다른 것이다**(2026-07-28 정정). 그쪽은
`source_artifact → generated_file` 출처 기록이고 한 실행에 고정되며, 여기는 요구 id로
색인된 커버리지다. 이름만 닮았고 칸은 하나도 안 겹친다 — **합치면 양쪽이 망가진다.**

입력이 `dict`인 것도 그래서다. 특정 에이전트의 `AgentState`를 알면 그 에이전트의 것이
되므로, **읽는 키만 알고 상태 타입은 모른다**(`classified` · `use_cases` ·
`use_case_specs`).
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
    #: 요구 id → 그것에서 파생된 배포 필요사항 id들.
    deployment_needs_of: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: 요구 id → 그것을 커버한다고 적힌 명세 스텝들(`"UC1.3"`). UC보다 정밀한 추적.
    steps_of: dict[str, tuple[str, ...]] = field(default_factory=dict)

    # -- 되읽기 ------------------------------------------------------------
    def ucs_of(self, req_id: str) -> tuple[str, ...]:
        """어느 칸으로든 이 요구를 건 UC들."""
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
        """Use Case 또는 배포 필요사항에 연결된 NFR."""
        attached = frozenset(self.ucs_constrained_by) | frozenset(self.deployment_needs_of)
        return tuple(sorted(self.nfr_ids & attached))

    @property
    def unattached_nfr_ids(self) -> tuple[str, ...]:
        """Use Case와 배포 필요사항 어디에도 연결되지 않은 NFR."""
        return tuple(sorted(self.nfr_ids - frozenset(self.attached_nfr_ids)))

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

    deployment: dict[str, list[str]] = {}
    for need_id, need in (state.get("deployment_needs") or {}).items():
        for requirement_id in need.get("requirementIds", []) or []:
            deployment.setdefault(requirement_id, []).append(need_id)

    return Traceability(
        by_id=by_id,
        fr_ids=frozenset(r["id"] for r in classified if r.get("type") == "FR"),
        nfr_ids=frozenset(r["id"] for r in classified if r.get("type") == "NFR"),
        ucs_claiming={k: tuple(v) for k, v in claiming.items()},
        ucs_constrained_by={k: tuple(v) for k, v in constrained.items()},
        deployment_needs_of={k: tuple(v) for k, v in deployment.items()},
        steps_of={k: tuple(v) for k, v in steps.items()},
    )


def build_requirement_trace(state: dict, verdicts: list[dict] | None = None) -> dict:
    """요구사항 ID를 중심으로 연결된 산출물을 구조화한다.

    별도 표의 행을 만들지 않는다. 각 요구사항을 키로 사용하므로 호출자는 특정 요구가
    어느 유스케이스·시나리오 스텝·배포 필요사항에 연결되는지 직접 조회할 수 있다.
    """
    trace = index(state)
    real_by_req: dict[str, list[bool]] = {}
    for verdict in verdicts or []:
        requirement_id = verdict.get("requirement_id")
        if isinstance(requirement_id, str):
            real_by_req.setdefault(requirement_id, []).append(bool(verdict.get("realized")))

    needs_by_req: dict[str, list[str]] = {}
    referenced_by_needs: set[str] = set()
    deployment_needs = state.get("deployment_needs") or {}
    for need_id, need in deployment_needs.items():
        for requirement_id in need.get("requirementIds", []):
            referenced_by_needs.add(requirement_id)
            if requirement_id in trace.by_id:
                needs_by_req.setdefault(requirement_id, []).append(need_id)

    requirements: dict[str, dict] = {}
    for requirement_id, requirement in trace.by_id.items():
        requirement_type = requirement.get("type")
        verdict_values = real_by_req.get(requirement_id)
        requirements[requirement_id] = {
            "type": requirement_type,
            "text": requirement.get("text", ""),
            "use_cases": list(trace.ucs_of(requirement_id)),
            "scenario_steps": list(trace.steps_of.get(requirement_id, ())),
            "deployment_needs": sorted(needs_by_req.get(requirement_id, [])),
            "qualifies": list(requirement.get("qualifies", [])),
            "realized": any(verdict_values) if verdict_values else None,
        }

    use_cases = {
        use_case["id"]: {
            "name": use_case.get("name", ""),
            "requirements": [
                requirement_id
                for requirement_id in (
                    list(use_case.get("requirement_ids", []))
                    + list(use_case.get("nfr_ids", []))
                )
                if requirement_id in trace.by_id
            ],
        }
        for use_case in state.get("use_cases") or []
        if use_case.get("id")
    }
    unknown_refs = sorted(
        set(trace.unknown_refs) | (referenced_by_needs - frozenset(trace.by_id))
    )
    return {
        "requirements": requirements,
        "use_cases": use_cases,
        "deployment_needs": deployment_needs,
        "unknown_refs": unknown_refs,
        "summary": {
            "requirements": len(requirements),
            "covered_functional_requirements": len(trace.covered_fr_ids),
            "orphan_functional_requirements": list(trace.orphan_fr_ids),
            "attached_nonfunctional_requirements": len(trace.attached_nfr_ids),
            "unattached_nonfunctional_requirements": list(trace.unattached_nfr_ids),
            "deployment_needs": len(deployment_needs),
        },
    }
