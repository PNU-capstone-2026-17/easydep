"""리소스 의존 폐포의 접근점 — "이걸 고르면 무엇이 따라오나"를 여는 문.

정의와 근거는 `app/core/cloudkb/depkb`에 있다(3사 실측 주장 `claims.json`과
그 소비 절차 `closure.py`). 여기서는 상류(요구사항·설계 에이전트)가 쓸 수 있게
열어 줄 뿐, 지식을 복사하지 않는다 — `app/core`는 *누가 부를 수 있는가*,
`app/core/cloudkb`는 *무엇이 참인가*.

계약 셋을 그대로 물려받는다:

- **CSP가 1급 인자다.** 같은 앵커의 답이 CSP마다 다르고(aws VM은 필수가
  공집합 — 실측), 그 다름이 값이다.
- **모든 항목이 근거(간선 주장)를 들고 다닌다** — 계획서가 "왜"를 되짚을 수 있다.
- **모르는 것은 죽는다** — 판정 없는 간선을 만나면 추측 대신 예외다.
"""
from __future__ import annotations

from app.core.cloudkb.depkb.closure import (  # noqa: F401
    Attachable,
    Closure,
    Decision,
    Item,
    closure,
    describe,
)
