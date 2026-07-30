"""간선의 `questions`·`authorities` — 관측에서 **기계적으로** 도출한다.

`required: true/false` 하나가 세 판정(존재 의존인가 · 누구의 요구인가 · 어느
CSP인가)을 겸하고 있었고, 외부 실물 대조에서 실제로 틀린 셋(sshKey · spec/image ·
azure 방향)이 전부 그 압축에서 나왔다. 여기서 앞의 둘을 간선에 갈라 싣는다.
계획: `document/archive/dependency-analysis-plan-2026-07-30.md` P1.

## 이 대응표는 우리 구성이다

관측의 `form`(어떤 종류의 코드/데이터인가)과 `source`(어느 저장소인가)는 근거지만,
**그것을 어느 질문·권위로 읽을지는 우리가 정한 것**이다. 그래서 규율 둘:

- 질문의 어휘는 진실 문서 §7의 셋(존재·기능·생명주기)에서 가져온다 — 새 분류를
  만들지 않는다. **기능 의존은 여기 없다**: 어떤 관측 형태도 "B 없이 A가 제 역할을
  하는가"를 말하지 않으므로, 명시적 공백으로 남는다(§7 결정).
- 반례가 나오면 이 표를 고친다(계획 T1). 표가 모르는 형태·출처가 들어오면
  **조용히 넘기지 않고 죽는다** — perfkb `field_map`과 같은 규율이다.

## 왜 간선의 1급 필드로 실어도 되는가 (D1~D9 전례와의 차이)

`D1~D9` 증거층은 라벨이 인용을 가리는 구조라 걷어냈다
(`test_our_own_grading_scheme_is_not_back`). 여기는 반대 방향이다 — 필드가 관측을
**대체하지 않고 관측에서 재계산 가능**해야 하며, 그 정합을
`test_tumblebug_resources.py`가 강제한다. 저장된 값과 재계산이 어긋나면 테스트가
죽는다. 즉 이 필드는 등급이 아니라 **관측의 사영(projection)**이다.

`authorities`에 `cloud`는 여기서 나오지 않는다 — CB 소스만 봐서는 클라우드의
요구인지 알 수 없다. `cloud` 승격은 벤더 스키마 원문·드라이버 코드 대조(계획 P2)의
일이고, 그 전까지 모든 간선의 권위는 도구(tumblebug/spider) 층에 머문다.
"""

from __future__ import annotations

#: 관측 형태 → 그 형태가 답할 수 있는 질문 (진실 문서 §7의 어휘).
#: None = 어떤 질문도 혼자서는 답하지 못한다(보강 전용).
FORM_TO_QUESTION: dict[str, str | None] = {
    # 생성 요청이 B의 참조를 요구한다 → B 없이 A 생성이 성립하는가의 증거
    "요청 스키마 필드": "existence",
    # 생성 코드가 B의 존재를 먼저 확인한다
    "생성 전 존재 확인 코드": "existence",
    # 벤더 중립 인터페이스가 A에 B 참조 슬롯을 선언한다
    "CSP 중립 인터페이스": "existence",
    # 프로바이더별 자산표가 생성 조건(개수·필수)을 적는다
    "프로바이더별 자산 데이터": "existence",
    # A의 생성 경로가 B의 id를 경로에 요구한다 (예: node/{id}/snapshot)
    "REST 경로 중첩": "existence",
    # 생성 시 B가 없으면 만들어 채운다 — 의존이 있되 자동 충족된다는 증거
    "자동 생성 코드": "existence",
    # B 삭제가 A의 존재 때문에 거부되거나 연쇄된다
    "삭제 보호 코드": "lifecycle",
    # 순서는 의존을 함의하지 않는다 (test_ordering_alone_never_makes_an_edge)
    "운영 스크립트 순서": None,
}

#: 관측 출처(저장소) → 권위. `cloud`는 여기 없다 — P2(벤더 원문 대조)의 일이다.
SOURCE_TO_AUTHORITY: dict[str, str] = {
    "cb-tumblebug": "tumblebug",
    "cb-spider": "spider",
}


def questions_of(edge: dict) -> list[str]:
    """이 간선의 관측들이 답하는 질문의 집합 (정렬된 목록).

    모르는 형태가 나오면 KeyError — 표를 늘리는 결정을 사람이 하게 만든다.
    """
    found = {FORM_TO_QUESTION[o["form"]] for o in edge["observations"]}
    return sorted(q for q in found if q is not None)


def authorities_of(edge: dict) -> list[str]:
    """이 간선을 관측한 저장소들의 권위 (정렬된 목록)."""
    found = set()
    for o in edge["observations"]:
        repo = o["source"].split()[0]
        found.add(SOURCE_TO_AUTHORITY[repo])
    return sorted(found)
