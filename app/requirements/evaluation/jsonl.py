"""측정이 쌓이는 파일 형식 — 한 줄에 한 행(JSONL).

## 왜 한 곳에 두나

측정은 자주 끊긴다(레이트 리밋·시간 상한·외부 종료). 그래서 이 저장소의 러너들은 전부
**행이 나올 때마다 즉시 append**하고, 다시 돌 때 **이미 한 칸은 건너뛴다.** 그 배관이
캠페인마다 다시 적혀 있었다 — 쓰기 4벌, 읽기 6벌.

사본이 갈라진 자리도 실제로 있었다: **잘린 마지막 줄**을 어떤 읽기는 건너뛰고 어떤
읽기는 그대로 죽었다. 실행이 외부에서 종료되면 마지막 줄은 잘려 있는 게 정상이라
(이 저장소에서 다섯 번 겪었다) 관용이 기본값이어야 한다.

## 무엇을 여기 두지 않나

**중복 판정과 다수결은 여기 없다.** 같은 칸을 두 번 잰 것을 어떻게 셀지는 측정마다
다르다 — `concern_report`는 겹쳐 뜬 러너를 방어하려고 **첫 행만** 남기고, 프로브 요약은
**마지막 행**을 남긴다(다시 잰 것이 최신이다). 그 판단을 여기로 올리면 한쪽이 조용히
다른 쪽 규칙으로 집계된다.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path


def append(path: Path, row: dict) -> None:
    """행 하나를 즉시 쓴다. **출력보다 먼저** — 화면이 죽어도 측정은 남는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.flush()


def rows(path: Path) -> Iterator[dict]:
    """쌓인 행들. 파일이 없으면 아무것도 내지 않는다.

    **잘린 마지막 줄은 건너뛴다.** 러너가 외부에서 종료되면 그 줄은 반쯤 쓰인 상태로
    남는데, 그건 예외 상황이 아니라 기본 상황이다(무인 실행이 다섯 번 끊겼다).
    그 칸은 다음 실행이 다시 잰다.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except ValueError:
            continue
