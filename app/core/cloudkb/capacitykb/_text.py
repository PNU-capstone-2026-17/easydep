"""답변 문구의 작은 조각들 — 여러 표면이 함께 쓴다.

`agent_api`와 `_endpoints`가 같은 복수형 처리를 쓴다. 사본을 두면 "1건"과 "1건들"이
파일마다 갈리므로 한 곳에 둔다.
"""

from __future__ import annotations


def _plural(n: int, one: str, many: str) -> str:
    """수에 맞는 단위 문구. 영어 복수형만 다룬다."""
    return f"{n} {one if n == 1 else many}"


def _recreating(n: int) -> str:
    """되만들기를 유발하는 속성 개수 문구."""
    if n == 1:
        return "1 property that recreates the resource when changed"
    return f"{n} properties that recreate the resource when changed"
