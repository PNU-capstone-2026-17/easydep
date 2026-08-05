"""평가 — 변경이 산출물을 나아지게 했는지 판정할 근거.

  - `scorecard.py`  실행 하나를 **규칙 단위로** 채점하고, 두 채점표의 증감을 낸다.
  - `seeded.py`     결함을 알고 심어 **검사기 자체의 눈금**을 확인한다(정적 5건 + 의미 12건).
  - `semantic.py`   의미 규칙 눈금을 N회 반복으로 재는 라이브 측정.

## 왜 눈금이 따로 필요한가

채점표만 있으면 "scope creep 0건"이 무슨 뜻인지 모른다 — 정말 없는 것과 검사기가 못 잡는
것이 같은 값이다. 심어 둔 결함이 그 둘을 가른다.

## 결정론 층과 아닌 층을 나눠 둔다

    python -m app.requirements.evaluation seeded              # 정적 눈금 (LLM·키 없이, CI 게이트)
    python -m app.requirements.evaluation score <run_dir>     # 채점표 (LLM 없이)
    python -m app.requirements.evaluation diff <before> <after>
    python -m app.requirements.evaluation semantic            # 의미 눈금 (**실제 LLM**, 게이트 아님)

앞의 셋은 LLM을 부르지 않아 CI에서 매번 돈다. `semantic`은 판정이 결정론이 아니라 게이트가
될 수 없고(한 번 실패가 코드 잘못이라고 말할 수 없다), 대신 `tests/test_live_evaluation.py`가
`RUN_LIVE_TESTS=1`에서 **눈금이 죽지 않았는지**만 확인한다.

실행 자체를 만드는 것은 이 패키지의 일이 아니다 — `python -m app.requirements.run_pipeline`이
`artifacts/runs/<run-id>/`를 남기고, 여기서는 그것을 읽는다.
"""
