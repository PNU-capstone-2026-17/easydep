"""동적 테스트를 만들 때 기준으로 삼을 기능 요구사항을 읽는다.

요구사항 단계는 분류된 요구사항 목록을 ``REFINE_REQ`` 산출물로 저장한다. 테스트 단계는
생성된 구현에서 요구사항을 다시 추측하지 않고 이 산출물을 읽는다. 구현에서 테스트 기준을
다시 만들면 잘못 생성된 구현을 그대로 정답으로 받아들일 수 있기 때문이다.

현재 저장 형식은 ``[{"id": "FR1", "text": ..., "type": "FR"}, ...]`` 형태의 목록이다.
정확한 상태 타입은 ``app/requirements/contracts/state.py``에 정의되어 있다.
"""

from __future__ import annotations

from typing import Any

from app.repositories.artifact_repository import AppNotFound, load_state


class RequirementsUnavailable(Exception):
    """테스트 기준으로 사용할 저장된 요구사항 분석이 없음을 나타낸다."""


def _as_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict) and item.get("text")]


def functional_requirements(app_id: str) -> list[dict[str, Any]]:
    """앱에 가장 최근에 저장된 기능 요구사항 목록을 반환한다.

    NFR은 별도의 동적 NFR 단계가 사용하므로 제외한다. 기능 테스트에 함께 넣으면 일반 실행으로
    판단할 수 없는 부하나 지연 시간을 검증하게 된다. ``type``이 없는 항목은 아직 분류되지 않은
    요구사항일 수 있으므로 유지한다.
    """
    try:
        state = load_state(app_id)
    except AppNotFound as error:
        raise RequirementsUnavailable(f"Unknown app id: {app_id}") from error

    items = _as_items(state.get("refined_requirements"))
    if not items:
        raise RequirementsUnavailable(
            f"App {app_id} has no stored requirements analysis (REFINE_REQ)."
        )

    functional = [
        item
        for item in items
        if str(item.get("type") or "FR").upper() != "NFR"
    ]
    return functional
