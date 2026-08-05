"""구조화 출력 LLM 호출 — 모든 설계 산출물이 공유하는 단 하나의 LLM 배관.

**왜 구조화 출력만 쓰나.** 예전에는 산출물마다 방식이 갈렸다. 클래스·ERD는 LLM에게
Pydantic 스키마를 강제해 구조화된 모델을 받고 그것을 결정론적으로 렌더했지만, 시퀀스·
API·배포는 LLM이 PlantUML/JSON 텍스트를 직접 쓰고 그것을 파싱했다. 후자는 문법 오류가
날 수 있으니 validate→repair 루프가 필요했고, 피드백이 렌더된 텍스트를 편집하므로
"모델과 그림이 어긋나는" 상태가 원천적으로 가능했다.

지금은 다섯 산출물 모두 이 함수 하나를 거친다. LLM은 **언제나 스키마에 맞는 JSON만**
내놓고, 그림/명세는 그 모델에서 결정론적으로 렌더된다. 그래서 수리 루프가 사라지고,
피드백은 항상 모델을 편집하며, 모델과 산출물이 어긋날 수 없다.
"""
from __future__ import annotations

import os
import queue
import threading
from typing import Any, Type

from pydantic import BaseModel


def run_with_wall_timeout(callable_obj):
    """벽시계 타임아웃. 클라이언트 타임아웃이 걸리지 않는 지연(연결 후 무응답 등)을 막는다."""
    timeout_seconds = float(os.getenv("LLM_WALL_TIMEOUT_SECONDS", "150"))
    result_queue: queue.Queue = queue.Queue(maxsize=1)

    def target():
        try:
            result_queue.put((True, callable_obj()))
        except Exception as error:  # noqa: BLE001 - 호출 스레드로 그대로 올린다
            result_queue.put((False, error))

    thread = threading.Thread(target=target, daemon=True)
    thread.start()

    try:
        ok, result = result_queue.get(timeout=timeout_seconds)
    except queue.Empty as error:
        raise TimeoutError(
            f"LLM request timed out after {timeout_seconds:g} seconds."
        ) from error

    if ok:
        return result

    raise result


def parse_structured(
    messages: list[dict[str, str]],
    schema: Type[BaseModel],
) -> dict[str, Any]:
    """LLM에게 schema를 강제해 구조화 결과를 받고 dict로 돌려준다.

    temperature/seed를 고정하는 것은 같은 입력이 같은 모델을 내도록 하기 위해서다 —
    산출물이 재현되지 않으면 피드백이 무엇을 고쳤는지 알 수 없다.
    """
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv()

    client = OpenAI(
        base_url=os.getenv("BASE_URL"),
        api_key=os.getenv("API_KEY"),
        timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "0")),
    )

    response = run_with_wall_timeout(
        lambda: client.chat.completions.parse(
            model=os.getenv("DESIGN_AGENT_MODEL", "openai/gpt-oss-120b"),
            messages=messages,
            temperature=0,
            seed=42,
            response_format=schema,
        )
    )
    return response.choices[0].message.parsed.model_dump()


def focus_note(targets: set[str] | None) -> str:
    """"이 항목만 고쳐라"를 프롬프트에 얹는 문구. 대상이 없으면 빈 문자열.

    **이건 지시일 뿐이고 보장이 아니다.** 실제 보장은 코드가 한다 —
    `app/design/nodes/artifact.py`의 merge_model 이 비대상 항목에 대해서는 LLM 출력을
    아예 읽지 않는다. 이 문구는 대상이 잘 고쳐지도록 초점을 좁혀줄 뿐이다.
    """
    if not targets:
        return ""
    listed = ", ".join(sorted(targets))
    return (
        "\n\n[Scope]\n"
        f"Change ONLY these elements: {listed}.\n"
        "Return every other element exactly as given — same names, same fields, same "
        "order. Adding a new element is allowed when the change genuinely requires one."
    )


def revision_messages(
    system_prompt: str,
    context_label: str,
    context_text: str,
    model_label: str,
    current_model: dict[str, Any],
    feedback: str,
    targets: set[str] | None = None,
) -> list[dict[str, str]]:
    """피드백 수정 프롬프트의 공통 뼈대: 맥락 + 현재 모델 + 사용자 피드백 (+ 범위).

    다섯 산출물의 리바이저가 모두 같은 모양이므로 여기 한 번만 적는다.
    """
    import json

    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"[{context_label}]\n{context_text}\n\n"
                f"[{model_label}]\n"
                f"{json.dumps(current_model, ensure_ascii=False, indent=2)}\n\n"
                f"[User Feedback]\n{feedback}"
                + focus_note(targets)
            ),
        },
    ]
