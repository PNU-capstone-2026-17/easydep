"""콘솔 출력 인코딩 — **한 곳에서 고친다.**

## 왜 있나

윈도우 콘솔이 cp949라 `—`·`→` 같은 글자 하나에 `print`가 `UnicodeEncodeError`를 낸다.
이 저장소에서는 그것 때문에 **3시간짜리 측정이 진행 로그 한 줄에서 죽었다** — 파일은
utf-8로 쓰고 있었으니 잃은 것은 순전히 화면 출력 때문이었다.

고치는 방법은 한 줄인데, 그 한 줄이 **여섯 군데에 흩어져 있었다**(`cli.py` ·
`run_pipeline.py` · `apply_feedback.py` · `evaluation/campaign.py` ·
`evaluation/concern_campaign.py` · `evaluation/__main__.py`). 그래서 새 진입점을 만들
때마다 다시 물렸다 — `concern-labels`가 산출물을 다 쓰고 나서 요약 한 줄에 죽은 것이
가장 최근이다. 사본이 여섯이면 일곱 번째도 온다.

## 규율

**실패해도 조용히 넘어간다.** 파이프·리다이렉트면 `reconfigure`가 없을 수 있고, 출력
인코딩을 못 바꾸는 것은 명령을 세울 이유가 아니다. `errors="replace"`를 함께 주는 것도
같은 이유다 — 못 그리는 글자가 있어도 줄은 나가야 한다.
"""
from __future__ import annotations

import contextlib
import sys


def use_utf8_stdout() -> None:
    """표준 출력을 utf-8로 돌린다. 못 하면 그냥 둔다(진입점에서 한 번 부른다)."""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
