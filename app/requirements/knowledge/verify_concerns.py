"""관심사의 코퍼스 좌표를 대조한다 — **CI에서 도는 검사**.

    python -m app.requirements.knowledge.verify_concerns

## `verify_citations`와 무엇이 다른가

같은 일을 하는데 **돌 수 있는 자리가 다르다.** 도서 인용 대조는 로컬 사본
(`materials/Usecase_Knowledge/`, gitignore)이 있어야 해서 자동 검사가 될 수 없었다 —
사본을 가진 사람이 생각날 때 돌리는 명령이다. 패턴 코퍼스는 저장소 안에 커밋돼 있어
(`app/deployment/data/pattern-corpus.json.gz`) **아무 데서나 돈다.**

그래서 이 축에서는 "인용을 손으로 적으면 틀린다"는 문제가 사람의 규율이 아니라 기계로
막힌다. 규칙 축이 못 얻은 것이고, 관심사를 이 코퍼스에 매달기로 한 이유 중 하나다.

## 왜 여기서만 `app/deployment`를 import하는가

`app/requirements`는 `app/deployment` 없이 돌아야 한다(`knowledge/basis.py`). 그 규약이
지키는 것은 **런타임 경로**다 — 파이프라인 어느 단계도 배포 KB를 끌고 오면 안 된다.
이 모듈은 파이프라인이 아니라 개발·CI 도구이고, 어디에서도 import되지 않는다(그 사실을
`tests/test_common_isolation.py` 계열의 격리 검사가 지킨다).

읽는 것은 KB 자신의 로더다. 코퍼스 형식이 바뀌면 우리가 따라 바뀌는 편이 낫고,
`artifact.resolve`가 `output/` → 저장소의 `data/*.gz` 순으로 찾아 주므로 빌드 없이 돈다.

## 무엇을 보는가

  1. 관심사가 가리키는 문서가 코퍼스에 실재하는가.
  2. `probe` 구절이 그 문서 본문에 실제로 있는가 — 좌표가 맞는지 보는 열쇠다.
  3. **고지 문구가 갈라지지 않았는가.** `concerns.ADVISORY_NOTICE`는 patternkb의 같은
     상수의 사본이다(import할 수 없어서). 사본은 갈라지는데, 갈라지면 요구사항 쪽 고지가
     배포 쪽과 다른 말을 하게 된다.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.requirements.knowledge import concerns


@dataclass(frozen=True)
class Verdict:
    """관심사 하나의 좌표 대조 결과."""

    concern_id: str
    doc_id: str
    #: 코퍼스에 그 문서가 없다. `missing`보다 앞선 실패다.
    doc_found: bool
    #: 문서 본문에서 찾지 못한 열쇠 구절들.
    missing: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.doc_found and not self.missing


def load_corpus() -> dict[str, str]:
    """코퍼스 문서 id → 본문(소문자).

    빈 코퍼스는 성공이 아니라 실패다 — 문서가 하나도 없으면 모든 대조가 "문서 없음"으로
    떨어지는데, 그 상태를 그냥 통과시키면 검사가 있는 것이 없는 것보다 나쁘다.
    """
    from app.deployment.patternkb.dataset import all_docs  # 런타임 경로가 아니다

    docs = all_docs(None)
    if not docs:
        raise SystemExit(
            "패턴 코퍼스가 비어 있다 — app/deployment/data/pattern-corpus.json.gz 를 확인하라."
        )
    return {d.id: d.text.lower() for d in docs}


def notice_matches() -> bool:
    """고지 문구가 patternkb의 원본과 같은가."""
    from app.deployment.patternkb.model import ADVISORY_NOTICE  # 런타임 경로가 아니다

    return concerns.ADVISORY_NOTICE == ADVISORY_NOTICE


def verify(corpus: dict[str, str] | None = None) -> list[Verdict]:
    """관심사 전부를 대조한다. `probe`가 없는 관심사는 없다(그 규율은 테스트가 지킨다)."""
    texts = load_corpus() if corpus is None else corpus

    verdicts: list[Verdict] = []
    for concern in concerns.CONCERNS:
        text = texts.get(concern.doc_id)
        if text is None:
            verdicts.append(Verdict(concern.id, concern.doc_id, doc_found=False))
            continue
        missing = tuple(key for key in concern.probe if key.lower() not in text)
        verdicts.append(Verdict(concern.id, concern.doc_id, doc_found=True, missing=missing))
    return verdicts


def main() -> int:
    verdicts = verify()
    failed = [v for v in verdicts if not v.ok]
    for verdict in verdicts:
        mark = "OK  " if verdict.ok else "FAIL"
        line = f"{mark} {verdict.concern_id}  ({verdict.doc_id})"
        if not verdict.doc_found:
            line += "  <- 코퍼스에 그 문서가 없다"
        elif verdict.missing:
            line += f"  <- 본문에 없다: {list(verdict.missing)}"
        print(line)

    if not notice_matches():
        print("\nFAIL 고지 문구가 patternkb의 ADVISORY_NOTICE와 갈라졌다.")
        failed.append(Verdict("(advisory notice)", "-", doc_found=False))

    print(f"\n대조 {len(verdicts)}건 · 실패 {len(failed)}건")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
