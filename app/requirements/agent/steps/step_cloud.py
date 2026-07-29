"""클라우드 네이티브 관심사 커버리지 — **사용자가 쓰지 않은 요구사항**을 드러낸다.

## 이 단계가 있는 이유

과제 목표 ②(클라우드 가이드라인)와 요구사항 분석이 만나는 자리가 지금까지 **배포
다이어그램 하나뿐**이었다. 다이어그램은 늦고(요구사항·유스케이스·명세가 모두 굳은 뒤),
"이 앱이 무상태인가"를 적을 칸도 없다. 조사(`docs/cloud-native-requirements.md` §3)가
이 공백을 세 갈래 중 **B**로 지목했다.

## `check_coverage`와 무엇이 같고 무엇이 다른가

같은 것: 집계는 집합 연산이라 결정론이다. 다른 것: **링크를 누가 만드는가.**

`check_coverage`는 유스케이스가 `requirement_ids`를 **들고 다니기** 때문에 아무것도
추론하지 않는다. 관심사에는 그런 링크가 없다 — 누군가 만들어야 하고, 그게 이 단계다.
그래서 정직한 선은 여기다: **링크는 추론이고 흔들린다. 집계는 그 위에서 결정론이다.**

## 두 층, 그리고 세 번째 상태

  - **결정론 층** — 관심사의 `signals`가 요구사항 문장에 있는가. 값싸고 안 흔들린다.
  - **LLM 층** — 열쇠말이 못 보는 것을 묻는다. `settings.concern_linker_llm`로 켠다.

그래서 관심사의 상태는 셋이다. `covered`(링크가 있다) · `unaddressed`(판정했는데 링크가
없다) · **`unjudged`(판정할 수단이 없었다)**. 세 번째가 핵심이다 — 신호가 없는 관심사를
LLM 층 없이 "안 다뤄졌다"고 적으면 그건 측정이 아니라 단정이다. `check_specs`가
`semantic_status`로, 검증자가 `unexamined_rules`로 지키는 것과 같은 규율이다.

## 결함이 아니다

미충족은 **인계 항목**이지 결함이 아니다. 사용자가 안 쓴 것을 결함이라 부르면 오탐이
대부분이 된다(§6). 그래서 이 단계는 `issues`를 내지 않고 되돌아가기의 대상도 아니다.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from langchain_core.messages import HumanMessage, SystemMessage

from app.requirements import prompts
from app.requirements.agent.llm import invoke_structured
from app.requirements.agent.state import AgentState
from app.requirements.common import telemetry
from app.requirements.common.state_contract import contract
from app.requirements.config import settings
from app.requirements.knowledge import concerns
from app.requirements.schemas import ConcernLink, ConcernLinkage

#: 관심사 상태. `unaddressed`와 `unjudged`를 가르는 것이 이 단계의 규율이다.
COVERED = "covered"
UNADDRESSED = "unaddressed"
UNJUDGED = "unjudged"

#: 링크를 만든 층. 산출물이 "무엇으로 알았는지"를 잃지 않게 함께 남긴다.
BY_SIGNAL = "signal"
BY_LLM = "llm"

#: 열쇠말 어간 뒤에 허용하는 영어 굴절 접미사. **닫힌 집합이다** — 늘리려면 근거가
#: 있어야 하고, 늘릴 때마다 오탐 위험이 함께 올라간다(`_signal_patterns` docstring).
_SUFFIX = r"(?:s|es|ed|ing)?"


@lru_cache(maxsize=1)
def _signal_patterns() -> dict[str, re.Pattern[str]]:
    """관심사 → 열쇠말 매칭식.

    **영문 열쇠말은 단어 경계로 본다.** 부분 문자열로 봤더니 `"log"`가 `login`·`catalog`·
    `chronological`에 걸렸다 — 입력 11종에서 21건이었다(2026-07-27 실측). 오탐의 방향이
    나쁘다: 잘못 걸린 관심사는 `covered`가 되어 **인계 목록에서 조용히 사라진다.**
    없는 것을 드러내려고 만든 층이 없는 것을 덮는 셈이다.

    다만 경계만 세우면 **반대 방향으로 샌다** — `\\bmetric\\b`는 "metrics"에 안 걸린다.
    실제 입력에서 `metrics`·`sessions`·`credentials`·`regions`·`failures`가 그렇게 빠졌다.
    그래서 어간 뒤에 **닫힌 굴절 접미사 집합**을 허용한다(`_SUFFIX`). 형태소 분석기나
    스테머를 쓰지 않는 이유는 결정론과 감사 가능성이다 — 무엇이 걸리고 무엇이 안 걸리는지가
    정규식 한 줄에서 읽혀야 한다.

    접미사를 열어도 원래 오탐이 돌아오지 않는다. 21건은 전부 **낱말 안쪽** 매칭이었고
    (`catalog`의 `log`), 어간 앞의 `\\b`가 그걸 막는다. `login`도 그대로 막힌다 —
    `in`은 접미사 집합에 없다.

    한국어는 단어 경계를 못 쓴다(조사가 붙어 `\\b로그\\b`는 "로그를"에 안 걸린다). 그래서
    비-ASCII 열쇠말만 부분 문자열로 두고, 대신 **모호한 토큰을 목록에서 뺀다** — 이 규율
    때문에 `"건"`이 빠졌다(조건·사건·건강에 걸린다).
    """
    patterns: dict[str, re.Pattern[str]] = {}
    for concern in concerns.CONCERNS:
        if not concern.signals:
            continue
        parts = [
            rf"\b{re.escape(s)}{_SUFFIX}\b" if s.isascii() else re.escape(s)
            for s in concern.signals
        ]
        patterns[concern.id] = re.compile("|".join(parts))
    return patterns


def _signal_links(classified: list[dict]) -> dict[str, list[str]]:
    """열쇠말이 걸린 요구사항. 결정론이고 LLM을 부르지 않는다."""
    links: dict[str, list[str]] = {}
    for concern_id, pattern in _signal_patterns().items():
        hit = [
            item["id"]
            for item in classified
            if pattern.search((item.get("text") or "").lower())
        ]
        if hit:
            links[concern_id] = hit
    return links


def _ask_llm(
    classified: list[dict], only: tuple[str, ...] | None = None
) -> ConcernLinkage:
    """링크를 한 번 묻는다. 요구사항은 id와 문장만 준다(유형·추적 링크는 판단을 흐린다)."""
    listing = [{"id": item["id"], "text": item.get("text", "")} for item in classified]
    return invoke_structured(
        ConcernLinkage,
        [
            SystemMessage(content=prompts.concern_linker_system_for(only)),
            HumanMessage(
                content=f"[REQUIREMENTS]\n{json.dumps(listing, ensure_ascii=False)}"
            ),
        ],
    )


def _one_ballot(classified: list[dict]) -> ConcernLinkage | None:
    """표 한 장 — 청크 설정에 따라 한 번에 묻거나 나눠 물어 **한 장으로 합친다.**

    청크 하나가 실패해도 나머지는 살린다(형제를 버리지 않는다 — 검증자와 같은 규율).
    그 청크의 관심사들은 이 표에서 판정이 없고, 그건 `unjudged`로 드러난다. 전부 실패하면
    표가 아예 없는 것으로 친다 — 빈 표를 한 장으로 세면 "아무 링크도 없다"는 답이 되어
    **판정을 못 얻은 것이 판정 결과로 승격한다.**
    """
    groups = concerns.chunks(settings.concern_linker_chunk)
    links: list[ConcernLink] = []
    answered = False
    for group in groups:
        only = None if len(groups) == 1 else group
        try:
            links.extend(_ask_llm(classified, only).links)
            answered = True
        except Exception as exc:  # noqa: BLE001 - 청크 하나 실패로 표를 버리지 않는다
            telemetry.record_degradation(
                "cloud_concerns.linker.chunk", f"{group[0]}…: {type(exc).__name__}: {exc}"
            )
    return ConcernLinkage(links=links) if answered else None


def _llm_links(classified: list[dict]) -> tuple[dict[str, list[str]], set[str]]:
    """`(과반으로 확정된 링크, 판정을 받은 관심사 id)`.

    **과반인 이유는 링크가 없는 답을 믿어야 하기 때문이다.** 한 번 물어 빈 답이 나오면
    "안 다뤄졌다"인지 "이번 실행이 놓쳤다"인지 구별할 수 없다. 표를 모으면 전자는 계속
    비어 있고 후자는 흔들린다.

    호출이 하나 실패해도 나머지 표는 살린다(검증자와 같은 규율 — 형제를 버리지 않는다).
    표를 하나도 못 얻으면 판정된 관심사가 없고, 그 사실이 `unjudged`로 나간다.
    """
    known_ids = {item["id"] for item in classified}
    votes: dict[str, dict[str, int]] = {}
    examined: dict[str, int] = {}
    ballots = 0

    for _ in range(max(1, settings.concern_linker_votes)):
        linkage = _one_ballot(classified)
        if linkage is None:
            telemetry.record_degradation("cloud_concerns.linker", "표를 한 장도 못 얻었다")
            continue
        ballots += 1
        seen_here: set[str] = set()
        for link in linkage.links:
            if link.concern_id not in concerns.BY_ID or link.concern_id in seen_here:
                # 목록에 없는 관심사를 지어냈거나 같은 관심사를 두 번 답했다. 버리되
                # 조용히 버리지 않는다 — 근거 없는 지적을 버리는 `grounding`과 같은 규율.
                telemetry.record_degradation(
                    "cloud_concerns.unknown_concern", f"판정에 없는 관심사: {link.concern_id}"
                )
                continue
            seen_here.add(link.concern_id)
            examined[link.concern_id] = examined.get(link.concern_id, 0) + 1
            tally = votes.setdefault(link.concern_id, {})
            for req_id in link.requirement_ids:
                if req_id not in known_ids:
                    telemetry.record_degradation(
                        "cloud_concerns.unknown_requirement",
                        f"{link.concern_id}: 없는 요구 id {req_id}",
                    )
                    continue
                tally[req_id] = tally.get(req_id, 0) + 1

    if not ballots:
        return {}, set()

    # 문턱은 **그 관심사가 실제로 판정된 횟수**의 과반이다. 전체 표 수로 재면, 어떤 표에서
    # 통째로 빠진 관심사가 판정된 관심사보다 불리해진다(빠진 것은 반대표가 아니다).
    links = {}
    for concern_id, tally in votes.items():
        threshold = examined.get(concern_id, 0) // 2 + 1
        agreed = sorted(req_id for req_id, count in tally.items() if count >= threshold)
        if agreed:
            links[concern_id] = agreed
    return links, set(examined)


@contract("link_cloud_concerns", requires=("classified",), produces=("cloud_concerns",))
def link_cloud_concerns(state: AgentState) -> dict:
    """요구사항을 클라우드 네이티브 관심사에 걸고, 걸리지 않은 것을 인계 항목으로 낸다."""
    classified = list(state.get("classified") or [])

    signal_links = _signal_links(classified)
    llm_links: dict[str, list[str]] = {}
    llm_examined: set[str] = set()
    if settings.concern_linker_llm:
        llm_links, llm_examined = _llm_links(classified)

    items = []
    for concern in concerns.CONCERNS:
        by_signal = signal_links.get(concern.id, [])
        by_llm = llm_links.get(concern.id, [])
        covered_by = sorted(set(by_signal) | set(by_llm))

        # 판정을 받았는가 — **링크가 있었는가와 다른 질문이다.** 열쇠말이 있는 관심사는
        # 안 걸려도 판정된 것이고(찾아봤다), 열쇠말이 없는 관심사는 LLM이 답했을 때만
        # 판정된 것이다.
        judged = bool(concern.signals) or concern.id in llm_examined
        if covered_by:
            status = COVERED
        elif judged:
            status = UNADDRESSED
        else:
            status = UNJUDGED

        items.append({
            "concern_id": concern.id,
            "question": concern.question,
            "citation": concern.citation,
            "consumer": concern.consumer,
            # 빈 문자열이면 "경계가 아니다"이지 "이유를 안 적었다"가 아니다 —
            # 경계인 관심사만 사유를 갖는다(`knowledge/concerns.py`).
            "out_of_scope": concern.out_of_scope,
            "evidence": concern.evidence,
            "status": status,
            "covered_by": covered_by,
            "judged_by": sorted(
                ([BY_SIGNAL] if by_signal else []) + ([BY_LLM] if by_llm else [])
            ),
        })

    # 미충족을 **세 갈래로 가른다.**
    #
    #   `handoff`      답이 흘러 들어갈 칸이 오늘 실재한다(`RESOURCE_SPEC`의 칸).
    #                  뒤 단계가 자동으로 받는다.
    #   `noted`        받아 줄 기계가 **아직** 없다. 근거는 있고 사람은 읽는다. (예정)
    #   `out_of_scope` 배포 계획이 **소비할 수 없다** — 설계·구현·운영의 것이다. (경계)
    #
    # 셋을 합치면 "자동으로 이어지는 것"과 "리포트에만 있는 것"이 같아 보이고, 그 상태로
    # 인계 목록을 내면 도구가 실제보다 많은 것을 약속하는 셈이 된다.
    #
    # **뒤 둘을 가르는 것이 2026-07-28에 추가됐다.** 그전에는 `consumer=None` 하나가
    # "이을 건데 아직"과 "안 이을 것"을 겸해서, 리포트를 읽는 사람이 **"안 한 것"과
    # "안 하기로 한 것"을 구별할 수 없었다.** 소비자 없는 관심사를 **선정에서** 빼지 않는
    # 이유는 또 따로다 — 빼면 "근거가 없어서 뺐다"와 "우리 코드가 안 읽어서 뺐다"가
    # 구별되지 않는다(`knowledge/concerns.py`).
    unaddressed = [
        i["concern_id"] for i in items if i["status"] == UNADDRESSED and i["consumer"]
    ]
    noted = [
        i["concern_id"] for i in items
        if i["status"] == UNADDRESSED and not i["consumer"] and not i["out_of_scope"]
    ]
    out_of_scope = [
        i["concern_id"] for i in items
        if i["status"] == UNADDRESSED and i["out_of_scope"]
    ]
    unjudged = [i["concern_id"] for i in items if i["status"] == UNJUDGED]
    if unjudged:
        # 조용히 두면 "다 봤는데 12건 중 8건만 걸렸다"로 읽힌다. 실제로는 일부를 못 물어본
        # 것이고, 그 사실은 산출물 안에도 계측에도 남아야 한다.
        telemetry.record_degradation(
            "cloud_concerns.unjudged",
            f"판정 수단이 없어 넘어간 관심사 {len(unjudged)}건: {unjudged}",
        )

    return {
        "cloud_concerns": {
            # 고지는 산출물에 실려 나간다 — 이 목록은 설계 지침에서 나온 것이지
            # 클라우드 사실이 아니다(`knowledge/concerns.py`).
            "notice": concerns.ADVISORY_NOTICE,
            "layers": [BY_SIGNAL] + ([BY_LLM] if settings.concern_linker_llm else []),
            "items": items,
            # 인계 항목. **결함 목록이 아니다** — 설계가 정해야 할 것들이다.
            "handoff": unaddressed,
            # 근거는 있으나 이 시스템이 **아직** 안 읽는 관심사의 미충족. 인계와 섞지
            # 않는다 — 섞으면 "우리가 소화할 수 있는 것"과 "문헌이 말하는 것"의 차이가
            # 사라진다.
            "noted": noted,
            # 배포 계획이 **소비할 수 없는** 관심사의 미충족. `noted`와 섞지 않는다 —
            # 섞으면 "아직 안 이은 것"과 "안 잇기로 한 것"이 같아 보이고, 그러면 이
            # 목록이 영원히 줄지 않는 숙제처럼 읽힌다. 사유는 각 item의 `out_of_scope`.
            "out_of_scope": out_of_scope,
            "unjudged": unjudged,
            "coverage_ratio": (
                round(sum(1 for i in items if i["status"] == COVERED) / len(items), 4)
                if items else 1.0
            ),
        },
        "phase": "cloud_concerns",
    }
