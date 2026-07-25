"""산출물 해석은 CWD와 무관해야 한다.

easydep(다른 CWD의 FastAPI 프로세스)에 임포트됐을 때 상대 `Path("output")`이
CWD 기준으로 해석돼 **일부 KB만 조용히 미빌드 상태**가 됐다 — costkb는 값을
줬는데 graphkb·bundlekb는 죽어서, 배포 구성이 svcmap 대응과 네트워크 계층을
통째로 "미결"이라 답했다(실측 2026-07-24). 반쯤 죽는 것이 다 죽는 것보다
나쁘다 — 답이 그럴듯해서 아무도 못 알아챈다.
"""

from __future__ import annotations

from pathlib import Path

from app.deployment.kbcommon import artifact


def test_relative_output_dir_is_repo_anchored(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)  # easydep에서 임포트된 상황
    found = artifact.resolve(Path("output"), "svcmap-graph.json")
    assert found is not None
    assert Path(found).is_absolute()


def test_explicit_non_default_dir_still_gets_no_fallback(tmp_path) -> None:
    """빈 tmp를 명시하는 테스트들("미빌드 상태" 검사)이 기대는 계약 — 커밋된
    데이터가 슬쩍 끼어들면 그 검사가 검사하려던 상황이 사라진다."""
    assert artifact.resolve(tmp_path, "svcmap-graph.json") is None
