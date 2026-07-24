"""patternkb 자료 모델."""

from __future__ import annotations

from dataclasses import dataclass

#: 이 축의 유일한 근거 라벨. basis는 영원히 inferred다 — 산문 지침은 검수해도
#: 클라우드 사실이 되지 않는다(`kbcommon/basis.py`에 그 규약이 적혀 있다).
EVIDENCE_ADVISORY = "pattern-advisory"

#: 모든 답에 붙는 고지. **어떤 출력 경로에서도 떼면 안 된다** — 떼는 순간
#: 설계 산문이 클라우드 사실처럼 읽힌다(이 저장소가 막아 온 실패의 산문판).
ADVISORY_NOTICE = (
    "※ 설계 지침이지 클라우드 사실이 아닙니다 — 값·한도·판정은 "
    "지식베이스 도구(kb_*/cap_*/cost_*)로 확인하세요."
)


@dataclass(frozen=True)
class Doc:
    """코퍼스 문서 하나. 인용에 필요한 좌표(경로·라이선스·저작자)가 본문과 함께 다닌다."""

    id: str
    title: str
    path: str
    source: str
    section: str
    license: str
    attribution: str
    url: str
    text: str

    @classmethod
    def from_dict(cls, record: dict) -> Doc:
        return cls(
            id=record["id"],
            title=record["title"],
            path=record["path"],
            source=record["source"],
            section=record["section"],
            license=record["license"],
            attribution=record["attribution"],
            url=record["url"],
            text=record["text"],
        )
