"""테스트가 함께 쓰는 작은 도구들.

## 왜 있나

`flat()`이 **테스트 파일 16개에 글자 하나 안 틀리고 똑같이** 적혀 있었다. 픽스처가
아니라 그냥 함수라 `conftest.py`로는 못 올린다(conftest는 픽스처를 주입하지, 이름을
주입하지 않는다). 그래서 여기 두고 명시적으로 import한다.

사본이 열여섯이면 열일곱 번째도 온다 — 새 테스트를 쓸 때 여기서 가져다 쓰면 된다.
"""

from __future__ import annotations

import io
import tarfile
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 타입만 — 헬퍼가 KB를 런타임에 끌어오지 않게 한다.
    from app.core.cloudkb.graphkb.model import Edge, Graph


def flat(text: str) -> str:
    """줄바꿈·들여쓰기를 공백 하나로 눌러 문구 대조를 줄나눔에서 독립시킨다."""
    return " ".join(text.split())


def find_edges(graph: Graph, from_id: str, to_id: str) -> list[Edge]:
    """두 노드 사이의 엣지들. 파서 테스트 넷이 같은 정의를 갖고 있었다."""
    return [e for e in graph.edges if e.from_id == from_id and e.to_id == to_id]


def write_tar(path: Path, members: Mapping[str, str | bytes]) -> Path:
    """이름 → 내용으로 `.tar.gz` 하나를 만든다.

    파서 테스트마다 이 여섯 줄(`TarInfo` → `size` → `addfile`)을 다시 적고 있었다.
    **회원 경로 규칙은 각 테스트가 정한다** — 업스트림 저장소마다 디렉터리 모양이 달라서
    그건 공유할 수 있는 것이 아니다. 공유되는 건 "문자열을 tar 회원으로 넣는 법"뿐이다.
    """
    with tarfile.open(path, "w:gz") as archive:
        for name, body in members.items():
            raw = body.encode("utf-8") if isinstance(body, str) else body
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    return path
