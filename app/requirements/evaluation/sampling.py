"""표본 뽑기 — 측정이 목록의 **앞부분만** 보지 않도록.

## 왜 한 줄짜리 함수에 모듈을 주나

같은 편향에 두 번 물렸기 때문이다. 둘 다 "앞에서 N개 자르기"였고, 둘 다 자른 뒤에야
편향이 드러났다:

  - `pure.extract` — cctns 문서의 앞 40개를 뽑았더니 NFR 34 / FR 6이 나왔다. 문서 앞부분이
    사용성·도움말 절이라 **표본이 문서 구조를 따라간 것**이었다.
  - `campaign.phase_stability` — 내부 입력 실행은 명세가 10~17개인데 앞 5개만 재고 있었다.
    유스케이스 순서가 요구사항 순서를 따르므로 같은 종류의 위치 편향이다. PURE 실행은
    명세가 3~5개라 상한이 걸리지 않아 **한쪽 표에만** 편향이 있었다 — 코퍼스 비교가
    코퍼스가 아니라 뽑기 차이를 재고 있었다는 뜻이다.

앞엣것을 고칠 때 뒤엣것을 같이 못 봤다. 사본을 두 벌 두면 다음에도 한쪽만 고친다.
"""
from __future__ import annotations

from typing import TypeVar

T = TypeVar("T")


def even_sample(items: list[T], limit: int) -> list[T]:
    """목록 전체에서 **고르게** `limit`개를 고른다.

    `limit`이 목록보다 크거나 같으면 **원본을 그대로** 돌려준다 — 상한이 걸리지 않는
    쪽의 측정이 바뀌지 않아야, 상한이 걸리는 쪽만 고쳤을 때 두 표를 나란히 놓을 수 있다.

    순서는 유지한다(첫 항목은 항상 포함). 무작위가 아니라 **결정론**이라, 같은 실행을 다시
    재면 같은 표본이 나온다 — 표본이 실행마다 달라지면 반복 측정이 반복이 아니게 된다.
    """
    if limit >= len(items) or limit <= 0:
        return items
    step = len(items) / limit
    return [items[int(i * step)] for i in range(limit)]
