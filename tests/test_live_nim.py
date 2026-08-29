"""실제 NIM 엔드포인트를 호출하는 통합 테스트 (옵트인).

기본적으로 스킵된다. 실행하려면 유효한 .env(API_KEY) 준비 후:
    RUN_LIVE_TESTS=1 python -m pytest tests/test_live_nim.py

주의: 네트워크·비용·LLM 비결정성이 있어 CI 기본 실행에서는 제외한다.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_TESTS") != "1",
    reason="라이브 NIM 테스트는 RUN_LIVE_TESTS=1 일 때만 실행",
)


def test_live_concrete_classification():
    # 라이브 테스트는 실제 키가 필요하므로 conftest의 더미 키를 무시하도록
    # 실제 .env가 로드된 설정을 새로 만든다.
    from app.requirements.orchestration.graph import start_analysis

    payload = start_analysis(
        ["Users must be able to log in with email and password."],
        "live-concrete",
    )
    # 구체 입력이라 대개 완료되지만, 모델이 되물을 수도 있으므로 둘 다 허용.
    assert payload["status"] in ("completed", "need_clarification")
    if payload["status"] == "completed":
        assert len(payload["requirements"]) >= 1
        assert payload["requirements"][0]["type"] in ("FR", "NFR")
