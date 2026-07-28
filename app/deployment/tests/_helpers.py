"""테스트가 함께 쓰는 작은 도구들.

## 왜 있나

`flat()`이 **테스트 파일 16개에 글자 하나 안 틀리고 똑같이** 적혀 있었다. 픽스처가
아니라 그냥 함수라 `conftest.py`로는 못 올린다(conftest는 픽스처를 주입하지, 이름을
주입하지 않는다). 그래서 여기 두고 명시적으로 import한다.

사본이 열여섯이면 열일곱 번째도 온다 — 새 테스트를 쓸 때 여기서 가져다 쓰면 된다.
"""

from __future__ import annotations


def flat(text: str) -> str:
    """줄바꿈·들여쓰기를 공백 하나로 눌러 문구 대조를 줄나눔에서 독립시킨다."""
    return " ".join(text.split())
