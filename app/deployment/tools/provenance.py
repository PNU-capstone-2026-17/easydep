"""표본을 만든 실행의 **출처 한 조각** — 어느 커밋에서 돌았나.

`appkb/samples/README.md`가 표본에 요구하는 셋은 누가·언제·무엇을 넣었나이고,
"누가"에는 **어느 커밋에서**가 들어간다. 시각만으로는 부족하다 — 같은 입력이라도
그 사이에 어댑터나 프롬프트가 바뀌면 다른 물건이 나온다(이 저장소가 소스마다 핀을
박고 프롬프트 지문을 찍는 것과 같은 이유).

**모르면 모른다고 남긴다.** git이 없거나 저장소가 아니면 `None`이고, 그 부재가
`RUN.json`에 그대로 적힌다 — 지어낸 해시보다 빈 칸이 낫다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]


def git_head() -> dict[str, str | bool] | None:
    """`{"commit": ..., "dirty": ...}` 또는 `None`.

    `dirty`가 참이면 **커밋되지 않은 변경 위에서 돈 것**이라 그 해시만으로는
    재현되지 않는다. 이것도 사실이라 함께 남긴다.
    """
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_ROOT, capture_output=True,
            text=True, timeout=10, check=True).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=_ROOT, capture_output=True,
            text=True, timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return {"commit": commit, "dirty": bool(status.strip())}
