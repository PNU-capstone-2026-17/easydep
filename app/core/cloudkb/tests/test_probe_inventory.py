"""프로브 명단의 **구조적 불변식** — 모델을 부르지 않으므로 기본 스위트에서 돈다.

회귀 실행(`test_agent_regression.py`)은 옵트인이고 느리다. 그래서 "무엇을 태우는가"가
어긋나는 종류의 결함은 **몇 주 동안 안 보인다.** 실제로 그랬다 — 회귀 테스트가 태울
id를 자기 파일에 손으로 적어 두고 2026-07-24에서 멈췄고, 그 뒤에 늘어난 프로브 40여
건을 아무도 태우지 않았다. 옵트인 테스트는 그 사실을 알려 줄 수 없다. **켜지지 않는
검사는 없는 검사다.**

여기 있는 것은 전부 상수만 보는 검사라 API 키도 네트워크도 필요 없다.
"""

from __future__ import annotations

from app.core.cloudkb.tools.agent_probe import (
    PROBES,
    named_tools,
    regression_probes,
    tool_coverage,
)


def test_every_tool_is_named_by_some_probe() -> None:
    """도구를 하나 늘리면 프로브도 늘어야 한다 — **빈칸이 통과로 읽히지 않게.**

    지목은 `want_tools`·`want_any_tool`·`forbid_tools` 셋 중 아무 데나 있으면 된다.
    금지도 커버리지다(`web_search`를 요구하는 프로브는 하나지만 여럿이 금지로 지킨다).
    """
    _, unnamed = tool_coverage()
    assert not unnamed, (
        f"어느 프로브도 지목하지 않는 도구 {len(unnamed)}개: {', '.join(unnamed)}\n"
        "도구를 늘렸으면 그걸 부르는 프로브를 PROBES에 넣으세요. "
        "회귀가 안 보는 도구는 '통과'가 아니라 '안 본 것'입니다."
    )


def test_probe_ids_are_unique() -> None:
    ids = [p.id for p in PROBES]
    duplicates = {i for i in ids if ids.count(i) > 1}
    assert not duplicates, f"프로브 id 중복: {sorted(duplicates)} — --only가 갈린다"


def test_regression_excludes_only_with_a_reason() -> None:
    """회귀에서 빠지려면 **사유가 있어야 한다.**

    사유를 못 쓰면 제외가 아니라 방치다. `regression_skip`이 빈 문자열이면 자동으로
    포함되므로, 새 프로브는 아무것도 안 해도 회귀에 들어온다 — 그게 요점이다.
    """
    excluded = [p for p in PROBES if p.regression_skip]
    for probe in excluded:
        assert len(probe.regression_skip) > 10, (
            f"{probe.id}: 회귀 제외 사유가 너무 짧다 — 왜 빼는지 한 줄로 적으세요"
        )
    assert len(regression_probes()) == len(PROBES) - len(excluded)


def test_every_probe_says_what_it_protects() -> None:
    """`why`가 비면 실패했을 때 읽을 것이 없다."""
    for probe in PROBES:
        assert probe.why.strip(), f"{probe.id}: why가 비어 있다"


def test_english_probes_are_paired_with_real_korean_probes() -> None:
    """짝이 없으면 결과 해석이 안 된다 — "영어라서"인지 "원래 어려워서"인지 못 가린다."""
    from app.core.cloudkb.tools.probe_en import PAIRED_WITH, PROBES_EN

    ids = {p.id for p in PROBES}
    for english, korean in PAIRED_WITH.items():
        assert korean in ids, f"{english}의 짝 {korean}이 PROBES에 없다"
    assert {p.id for p in PROBES_EN} == set(PAIRED_WITH), "짝 표와 프로브가 어긋난다"


def test_english_probes_cover_every_tool_too() -> None:
    """**대상 언어로도 전부 물어본다.**

    도구 출력·판정문·고지는 영어이고, 실측상 한국어로 물어도 답은 영어로 온다
    (30칸 중 28칸). 그런데 2026-07-29까지 영어 프로브는 도구 **9/31**만 건드리고
    있었다 — 실제 사용 경로를 15%만 재고 있었다는 뜻이다.

    도구를 하나 늘리면 **두 언어 모두**에 프로브가 필요하다. 한쪽만 늘리면 그
    도구는 다른 언어에서 안 본 채로 남는다.
    """
    from app.core.cloudkb.tools.probe_en import PROBES_EN

    _, unnamed = tool_coverage(PROBES_EN)
    assert not unnamed, (
        f"영어로는 안 물어보는 도구 {len(unnamed)}개: {', '.join(unnamed)}\n"
        "짝지은 영어 프로브를 probe_en.py에 추가하세요 — 시스템의 대상 언어입니다."
    )


def test_named_tools_are_real_tools() -> None:
    """기대에 적힌 도구 이름이 **실재하는가.**

    이름을 잘못 적으면 `want_tools`가 영원히 실패하고, `forbid_tools`는 반대로
    영원히 통과한다 — **금지 쪽이 특히 위험하다**(오타 하나로 검사가 조용히 꺼진다).
    """
    from app.core.cloudkb.nim_agent.agent import LOCAL_TOOLS

    known = {t.name for t in LOCAL_TOOLS if getattr(t, "name", None)}
    unknown = sorted(named_tools() - known)
    assert not unknown, f"실재하지 않는 도구를 기대에 적었다: {unknown}"
