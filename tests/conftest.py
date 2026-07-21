"""pytest 공통 설정 및 픽스처.

앱 모듈을 임포트하기 전에 환경변수를 세팅해 테스트를 헤르메틱하게 만든다:
  - API_KEY: 목킹된 LLM만 쓰므로 더미 값(실제 .env 키가 없어도 임포트 성공).
  - ENABLE_BERT_VERIFY=false: torch 미설치/네트워크 없이도 그래프가 돌게.
os.environ가 .env 값보다 우선하므로(pydantic-settings 우선순위) 여기 값이 이긴다.
"""
import json
import os
from pathlib import Path

# 라이브(RUN_LIVE_TESTS=1) 테스트는 실제 .env(API_KEY)를 그대로 쓰도록 두고,
# 그 외에는 더미 키 + BERT 비활성으로 헤르메틱하게 만든다.
if os.getenv("RUN_LIVE_TESTS") != "1":
    os.environ.setdefault("API_KEY", "test-key-not-used")
    os.environ["ENABLE_BERT_VERIFY"] = "false"
    # 헤르메틱 테스트는 LLM 의미 검증을 끈다(정적 체크 + 반성 루프만 목킹으로 검증).
    os.environ["ENABLE_SEMANTIC_VALIDATOR"] = "false"

import pytest  # noqa: E402

# ---------------------------------------------------------------------------
# 입력 데이터셋 로더 (inputs/*.json) — 테스트와 러너가 공유하는 단일 소스.
# ---------------------------------------------------------------------------
DATASETS_DIR = Path(__file__).parent.parent / "inputs"


def dataset_names() -> list[str]:
    """inputs/*.json 의 데이터셋 이름(파일 stem) 목록."""
    return sorted(p.stem for p in DATASETS_DIR.glob("*.json"))


def load_dataset(name: str) -> dict:
    """이름으로 데이터셋 JSON을 로드한다. {'name','description','classified':[...]}"""
    return json.loads((DATASETS_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def scripted_llm(monkeypatch):
    """step1의 invoke_structured를 주어진 응답 시퀀스로 대체한다.

    사용: scripted_llm([Assessment(...), ClassificationResult(...)])
    노드가 호출하는 순서대로 응답을 반환한다.
    """

    def _install(responses):
        seq = iter(responses)

        def fake(schema, messages):
            return next(seq)

        monkeypatch.setattr(
            "app.requirements.agent.steps.step1_requirements.invoke_structured", fake
        )

    return _install


@pytest.fixture
def fake_bert(monkeypatch):
    """step1의 BERT 검증을 결정적 함수로 대체한다.

    사용: fake_bert(lambda text: ("FR", 0.9))
    """

    def _install(fn):
        monkeypatch.setattr(
            "app.requirements.agent.steps.step1_requirements.bert_available", lambda: True
        )
        monkeypatch.setattr(
            "app.requirements.agent.steps.step1_requirements.classify_bert", fn
        )

    return _install
