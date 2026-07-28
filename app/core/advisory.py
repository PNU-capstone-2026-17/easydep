"""설계 지침 고지의 접근점 — **사본을 없앤다.**

`patternkb`(Azure 아키텍처 센터 + 12-factor)에서 나온 답에는 *"설계 지침이지 클라우드
사실이 아니다"*라는 고지가 붙는다. 그 문자열과 근거 라벨은 `app/deployment/patternkb`가
정의하고, 요구사항 쪽 클라우드 관심사 축도 같은 것을 쓴다.

## 왜 접근점인가 (사본이 아니라)

**정의가 이미 다른 곳에 산다** — `app/core/__init__.py`의 판정식 그대로 접근점만 둔다.

한동안은 사본이었다. `app/requirements`가 `app/deployment` 없이 돌아야 한다는 규약
때문이었고, 그래서 `knowledge/verify_concerns.py`에 **사본이 갈라졌는지 대조하는 검사**가
따로 있었다. 그 규약은 2026-07-28에 `app/core`가 생기며 "문을 하나로 좁힌다"로 바뀌었고,
사본을 둘 이유도 그때 사라졌다 — 대조는 사본을 없애 주지 않는다.
"""
from __future__ import annotations

from app.deployment.patternkb import model as _patternkb

#: 이 축의 유일한 근거 라벨. basis는 **영원히 `inferred`**다 — 설계 산문은 사람이
#: 검수해도 클라우드 사실이 되지 않는다.
EVIDENCE: str = _patternkb.EVIDENCE_ADVISORY

#: 관심사·패턴을 실은 모든 출력에 붙는 고지. **어떤 출력 경로에서도 떼면 안 된다.**
ADVISORY_NOTICE: str = _patternkb.ADVISORY_NOTICE
