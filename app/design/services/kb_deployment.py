"""배포 다이어그램 생성 — agent-sdk(지식베이스) 삽입점 (2026-07-24).

이전에는 LLM 자유 생성이었다: 설계 산출물들을 프롬프트에 넣고 PlantUML을 받아
문법만 검사했다. 클라우드 리소스 제약은 프롬프트에도 전제조건에도 없었다.

이제 agent-sdk의 결정론 구성기가 설계 최종본(class·sequence·ER PlantUML +
api_spec)과 RESOURCE_SPEC을 읽어, 줄마다 근거(설계 산출물/설계자 지정/지식베이스/
추론)가 달린 배포 다이어그램을 만든다. **이 단계에는 LLM 호출이 없다.**

근거와 판정(예산 부합 여부 등)은 PlantUML 줄 주석(`'`)으로 **같은 문서**에
실린다 — 산출물 저장이 스테이지당 단일 문서라서다. 주석은 렌더링된 이미지에는
나오지 않지만 문서와 함께 저장·버전되어, 문서만 떼어 봐도 어느 줄이 추론인지
남는다.

agent-sdk가 같은 파이썬 환경에 설치돼 있어야 한다:

    pip install -e <agent-sdk 저장소 경로>   # 패키지명 nim-agent

RESOURCE_SPEC이 아직 없으면(요구사항 에이전트가 생산을 시작하기 전) 제약 없이
구성되고, 그래서 무엇을 판정하지 못했는지가 문서 주석에 남는다 — 임의 기본값을
채우지 않는다.
"""

from __future__ import annotations

from typing import Any


def generate_deployment_puml(state: dict[str, Any]) -> str:
    try:
        from nim_agent.design_tools import deployment_puml_from_easydep
    except ImportError as exc:
        raise RuntimeError(
            "배포 다이어그램은 agent-sdk(지식베이스)가 만듭니다 — 같은 파이썬 "
            "환경에 설치돼 있지 않습니다. agent-sdk 저장소에서 "
            "`pip install -e .`로 설치하세요."
        ) from exc

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
