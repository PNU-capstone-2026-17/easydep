"""배포 다이어그램 생성 — 지식베이스(`app/deployment`) 호출부.

이전에는 LLM 자유 생성이었다: 설계 산출물들을 프롬프트에 넣고 PlantUML을 받아
문법만 검사했다. 클라우드 리소스 제약은 프롬프트에도 전제조건에도 없었다.

이제 `app/deployment`의 결정론 구성기가 설계 최종본(class·sequence·ER PlantUML +
api_spec)과 RESOURCE_SPEC을 읽어, 줄마다 근거(설계 산출물/설계자 지정/지식베이스/
추론)가 달린 배포 다이어그램을 만든다. **이 단계에는 LLM 호출이 없다.**

근거와 판정(예산 부합 여부 등)은 PlantUML 줄 주석(`'`)으로 **같은 문서**에
실린다 — 산출물 저장이 스테이지당 단일 문서라서다. 주석은 렌더링된 이미지에는
나오지 않지만 문서와 함께 저장·버전되어, 문서만 떼어 봐도 어느 줄이 추론인지
남는다.

2026-07-25 이전에는 지식베이스가 별도 저장소(agent-sdk)라 `pip install -e`가
필요했고, 없으면 이 함수가 하드 에러를 냈다. 같은 저장소가 되면서 그 실패 모드
자체가 사라져 임포트를 모듈 최상단에 둔다.

RESOURCE_SPEC이 아직 없으면(요구사항 에이전트가 생산을 시작하기 전) 제약 없이
구성되고, 그래서 무엇을 판정하지 못했는지가 문서 주석에 남는다 — 임의 기본값을
채우지 않는다. 같은 이유로 `PREREQUISITES`에도 넣지 않았다: 생산자가 없는 산출물을
전제로 걸면 배포 단계가 통째로 도달 불가가 된다. 요구사항 쪽이 RESOURCE_SPEC을
만들기 시작하면 그때 전제조건이 된다(합의 안건 1).
"""

from __future__ import annotations

from typing import Any

from app.deployment.nim_agent.design_tools import deployment_puml_from_easydep


def generate_deployment_puml(state: dict[str, Any]) -> str:
    api_spec = state.get("api_spec") or None
    name = ""
    if isinstance(api_spec, dict):
        name = str((api_spec.get("info") or {}).get("title") or "")
    return deployment_puml_from_easydep(
        name or "애플리케이션",
        api_spec=api_spec,
        class_puml=state.get("class_diagram_puml", ""),
        sequence_puml=state.get("sequence_diagram_puml", ""),
        erd_puml=state.get("erd_puml", ""),
        resource_spec=state.get("resource_spec") or None,
    )
