"""클라우드 네이티브 관심사 축의 규율 — 지식베이스와 링크 단계.

이 파일이 지키는 것:
  - 관심사는 **코퍼스 좌표에 매달려 있다.** 이 검사가 CI에서 돌기 때문에 "임의 사전"이
    되지 않는다(`docs/cloud-native-requirements.md` §4가 금지한 것).
  - **소비자 없는 관심사는 없다** — `app/core/cloudkb/appkb`가 제약 칸에 세운 규율.
  - `unaddressed`(판정했는데 안 걸림)와 `unjudged`(판정할 수단이 없었음)가 섞이지 않는다.
  - 고지(`ADVISORY_NOTICE`)가 산출물에서 떨어지지 않는다.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.requirements import prompts
from app.requirements.agent.steps import step_cloud
from app.requirements.knowledge import basis, concerns, verify_concerns

# 배포 KB로 가는 문이 하나로 남아 있는지는 `tests/test_core_layer.py`가 지킨다.
# 같은 사실을 두 곳에서 검사하면 한쪽만 고쳐질 때 둘이 다른 말을 하게 된다.


# --- 지식베이스의 규율 -------------------------------------------------------
def test_concern_ids_are_unique_and_prefixed():
    ids = [c.id for c in concerns.CONCERNS]
    assert len(ids) == len(set(ids))
    assert all(i.startswith("cn.") for i in ids)


def test_every_concern_can_be_verified():
    for concern in concerns.CONCERNS:
        # 좌표가 없으면 대조를 못 받는다 = 우리가 지어낸 문장과 구별할 수 없다.
        assert concern.doc_id.strip(), concern.id
        assert concern.probe, f"{concern.id}: 대조할 열쇠 구절이 없다"
        assert concern.citation.strip(), concern.id
        # 질문이지 규범이 아니다.
        assert concern.question.rstrip().endswith("?"), concern.id


def test_every_concern_answers_why_it_is_cloud_specific():
    """**배제 기준 E2.** 전통 개발에서도 똑같이 성립하면 이 축의 신규성 주장이 무너진다.

    필드를 비워 둘 수 있게 하면 "일단 넣고 나중에 채우자"가 되고, 그 순간 목록이 무엇을
    담는 곳인지가 흐려진다. 답을 못 적는 후보는 관심사가 아니다.
    """
    for concern in concerns.CONCERNS:
        assert len(concern.cloud_specific.strip()) > 40, (
            f"{concern.id}: 왜 클라우드 때문에 요구사항이 되는지가 없다"
        )


def test_each_concern_rests_on_a_distinct_document():
    """**입도의 트립와이어이지 기준이 아니다.**

    문헌(Nickerson 외 2013)의 기준은 *대상 구별*(요구사항을 갈라 주는가)이지 출처 구별이
    아니다. 같은 문서를 둘이 인용한다는 사실 자체는 아무것도 증명하지 않는다 — 다만
    값싸고 일찍 울린다. 실제로 첫 판의 `cn.observability`를 이게 잡았다.

    진짜 기준(분화)은 코퍼스에 대고 재는 것이라 단위 테스트가 아니라 §6.6의 측정이다.
    """
    docs = [c.doc_id for c in concerns.CONCERNS]
    dupes = sorted({d for d in docs if docs.count(d) > 1})
    assert not dupes, f"같은 문서를 근거로 삼은 관심사들이 있다: {dupes}"


def test_a_concern_without_a_consumer_is_scope_not_exclusion():
    """소비자 유무는 **범위 표시**이지 선정 기준이 아니다.

    선정에 쓰면 "근거가 없어서 뺐다"와 "우리 코드가 안 읽어서 뺐다"가 구별되지 않는다.
    소비자가 없어도 근거(좌표)는 반드시 있어야 한다 — 그게 둘을 가르는 지점이다.
    """
    for concern in concerns.CONCERNS:
        if concern.consumer is None:
            assert concern.doc_id and concern.probe, concern.id
        else:
            assert concern.consumer.strip(), concern.id


def test_scope_boundary_is_recorded_not_left_as_silence():
    """**"안 한 것"과 "안 하기로 한 것"을 가른다.**

    2026-07-28 감사 전에는 `consumer=None` 하나가 두 뜻을 겸했다 — *"이을 건데 아직"*과
    *"배포 계획이 소비할 수 없다"*. 그 상태로 리포트를 내면 목록이 **영원히 줄지 않는
    숙제**처럼 읽히고, 이 저장소가 다른 축에서 지켜 온 규율(구현 범위와 근거를 섞지
    않는다)이 여기서만 깨진다.

    갈린 뒤의 계약은 셋이다.
    """
    for concern in concerns.CONCERNS:
        # ① 경계는 사유를 갖는다. 빈 문자열은 "경계가 아니다"이지 "이유를 안 적었다"가
        #    아니어야 한다.
        if concern.out_of_scope:
            assert concern.out_of_scope.strip(), concern.id
            # ② 소비자와 경계를 겸할 수 없다 — 받아 주는 칸이 있는데 소비 불가는 모순이다.
            assert concern.consumer is None, (
                f"{concern.id}: 소비자가 있는데 경계로 표시됐다"
            )
        # ③ 경계여도 **목록에서 빼지 않는다.** 요구사항 단계에서 물을 값은 그대로이고,
        #    근거(좌표)도 그대로 있어야 한다.
        assert concern.doc_id and concern.probe, concern.id


def test_the_three_way_split_covers_every_concern():
    """세 갈래가 **겹치지도 새지도 않는다.** 합이 전체와 같아야 한다.

    수를 여기 적어 두는 이유: 갈래가 움직이면 그건 설계 결정이므로 **기록을 강제**한다.
    A갈래(구조를 바꾸는 것)를 이으면 `handoff`가 늘고 `noted`가 준다 — 그때 이 단언을
    고치는 것이 곧 "무엇을 이었는지" 남기는 일이 된다.
    """
    wired = [c for c in concerns.CONCERNS if c.consumer]
    pending = [c for c in concerns.CONCERNS if not c.consumer and not c.out_of_scope]
    boundary = [c for c in concerns.CONCERNS if c.out_of_scope]

    assert len(wired) + len(pending) + len(boundary) == len(concerns.CONCERNS)
    # **2026-08-01에 움직였다**: 연결 7→6 · 경계 8→9. `cn.stateless-process`가
    # 소비자를 잃었다 — `RESOURCE_SPEC.stateless`가 계약에서 빠졌고, 그 값의
    # 유일한 판정이 서버리스 적합인데 서버리스가 범위 밖이다. **관심사 자체는
    # 유효하다**(질문은 여전히 요구사항 단계의 것이다) — 다만 우리 계획이 그
    # 답을 소비할 자리가 없어서 경계로 옮겼다.
    assert (len(wired), len(pending), len(boundary)) == (6, 14, 9), (
        f"갈래가 움직였다 — 연결 {len(wired)} · 예정 {len(pending)} · 경계 {len(boundary)}"
    )


def test_declared_consumers_are_real_resource_spec_fields():
    """**소비자는 오늘 실재해야 한다** — 이 검사가 없으면 `handoff`가 조용히 부푼다.

    처음 판에서는 "설계 인계"·"perfkb" 같은 말을 소비자로 적었는데, 요구사항→설계 통로는
    `app/design/api.py`의 `EXTERNAL_STAGES` 넷뿐이고 관심사를 받는 자리는 없었다.
    `multiZone`을 받아 놓고 아무도 안 읽던 결함을 그대로 반복한 것이다.

    칸 목록은 **스키마에서 읽는다**(`app/core`를 거쳐서) — 손으로 적어 두면 스키마가
    바뀔 때 이 검사가 거짓이 된다.
    """
    from app.core import cloud_contract

    fields = cloud_contract.schema_fields()

    for concern in concerns.CONCERNS:
        if concern.consumer is None:
            continue
        prefix, _, spec = concern.consumer.partition(".")
        assert prefix == "RESOURCE_SPEC", f"{concern.id}: 알 수 없는 소비자 {concern.consumer}"
        # `a|b`는 둘 중 하나면 되는 칸(계약이 택1로 요구한다).
        for name in spec.split("|"):
            assert name in fields, f"{concern.id}: RESOURCE_SPEC에 {name} 칸이 없다"


def test_this_axis_can_never_claim_to_be_stated():
    """설계 산문은 검수해도 클라우드 사실이 되지 않는다 — `stated`로 오를 길이 없다."""
    for concern in concerns.CONCERNS:
        assert concern.evidence in basis.BASIS_OF_EVIDENCE, concern.id
        assert basis.basis_of(concern.evidence) == basis.INFERRED, concern.id
        assert concern.hedged, concern.id


def test_iso_mapping_uses_the_2023_vocabulary():
    """매핑이 조용히 옛 품질 모델을 가리키지 않게 한다(2011판은 이름이 다르다)."""
    for concern in concerns.CONCERNS:
        for characteristic in concern.iso25010:
            assert characteristic in concerns.ISO25010, f"{concern.id}: {characteristic}"


def test_the_standard_gap_is_computed_not_asserted():
    """표준 쪽 공백은 문서 표가 아니라 목록에서 나와야 한다 — 표는 목록을 안 따라온다."""
    gap = concerns.unmapped_characteristics()
    assert set(gap) <= set(concerns.ISO25010)
    # 지금 값은 둘이다. 이 수를 고정하지 않는 이유는 목록이 자라면 바뀌어야 해서다 —
    # 다만 **전부 비는** 것은 매핑이 안 걸렸다는 뜻이므로 그건 막는다.
    assert len(gap) < len(concerns.ISO25010)


def test_signals_are_lowercase():
    """열쇠말 매칭은 소문자화한 문장에 건다 — 대문자 열쇠말은 영원히 안 걸린다."""
    for concern in concerns.CONCERNS:
        assert all(s == s.lower() for s in concern.signals), concern.id


def test_citations_match_the_committed_corpus():
    """**CI에서 도는 인용 대조.** 도서 인용(`verify_citations`)이 못 하던 것이다."""
    failed = [v.concern_id for v in verify_concerns.verify() if not v.ok]
    assert not failed, f"코퍼스와 맞지 않는 좌표: {failed}"


def test_the_advisory_notice_is_the_patternkb_constant_itself():
    """**사본이 아니라 같은 객체다.**

    예전에는 사본이었고 갈라짐을 대조로 잡았다(`verify_concerns.notice_matches`).
    지금은 `app/core/advisory.py`를 거쳐 원본을 그대로 받으므로 갈라질 수가 없다 —
    대조 대신 **같은 것인지**를 검사한다. 누가 여기에 문자열을 다시 적으면 이 검사가 문다.
    """
    from app.core.cloudkb.patternkb import model as patternkb

    assert concerns.ADVISORY_NOTICE is patternkb.ADVISORY_NOTICE
    assert concerns.EVIDENCE is patternkb.EVIDENCE_ADVISORY


def test_prompt_carries_every_concern_and_the_notice():
    block = prompts.CONCERN_LINKER_SYSTEM
    for concern in concerns.CONCERNS:
        assert concern.id in block, concern.id
    assert concerns.ADVISORY_NOTICE in block


# --- 링크 단계 ---------------------------------------------------------------
def _state(*requirements: tuple[str, str]) -> dict:
    return {
        "classified": [
            {"id": rid, "text": text, "type": "NFR"} for rid, text in requirements
        ]
    }


def test_signal_layer_needs_no_llm(monkeypatch):
    """결정론 층은 스위치와 무관하게 돌고 LLM을 부르지 않는다."""
    monkeypatch.setattr(step_cloud.settings, "concern_linker_llm", False)
    monkeypatch.setattr(
        step_cloud, "_ask_llm", lambda *a, **k: pytest.fail("LLM을 부르면 안 된다")
    )

    result = step_cloud.link_cloud_concerns(
        _state(("NFR1", "All user data must stay inside the Korea region."))
    )["cloud_concerns"]

    by_id = {i["concern_id"]: i for i in result["items"]}
    assert by_id["cn.data-residency"]["status"] == step_cloud.COVERED
    assert by_id["cn.data-residency"]["covered_by"] == ["NFR1"]
    assert by_id["cn.data-residency"]["judged_by"] == [step_cloud.BY_SIGNAL]


def test_signals_do_not_match_inside_other_words(monkeypatch):
    """`"log"`가 `login`·`catalog`에 걸리던 오탐(입력 11종에서 21건, 2026-07-27).

    방향이 나쁜 오탐이다 — 잘못 걸린 관심사는 `covered`가 되어 **인계 목록에서 사라진다.**
    없는 것을 드러내려는 층이 없는 것을 덮는다.
    """
    monkeypatch.setattr(step_cloud.settings, "concern_linker_llm", False)

    # 실제 입력에서 나온 문장이다(bank_of_anthos FR-14). `"tb"`가 `outbound` 안에 있어
    # 부분 문자열 시절에는 용량 관심사가 걸렸다.
    inside = step_cloud._signal_links(
        [{"id": "FR1", "text": "cancel a pending outbound transfer within five minutes"}]
    )
    assert "cn.service-limits" not in inside

    # 낱말로 서 있으면 걸린다 — 막는 것은 낱말 **안쪽** 매칭뿐이다.
    outside = step_cloud._signal_links(
        [{"id": "FR1", "text": "the archive shall retain up to 40 tb of telemetry"}]
    )
    assert outside["cn.service-limits"] == ["FR1"]


@pytest.mark.parametrize("text", [
    "shopper shall browse the product catalog",   # catalog ⊅ log
    "user login attempts are recorded",            # login  ⊅ log
    "a chronological history of past orders",      # chronological ⊅ log
    "cancel a pending outbound transfer",          # outbound ⊅ tb
])
def test_no_signal_fires_inside_a_longer_word(text):
    assert not step_cloud._signal_links([{"id": "FR1", "text": text}])


@pytest.mark.parametrize("text", [
    "rate limiting on login attempts to stop credential stuffing",
    "issue a time-limited access token for authenticated sessions",
])
def test_signals_are_not_general_words(text):
    """**열쇠말은 그 관심사의 답이 되는 문장에만 나타나야 한다.**

    `cn.service-limits`가 묻는 것은 볼륨인데 `"limit"`은 볼륨과 무관한 문장에도 붙는다.
    낱말 경계로는 못 막는 종류의 오탐이라 열쇠말 자체를 골라내야 한다.
    """
    assert "cn.service-limits" not in step_cloud._signal_links(
        [{"id": "FR1", "text": text}]
    )


@pytest.mark.parametrize("text,expected", [
    ("the platform shall emit prometheus metrics", "cn.operational-signal"),
    ("the service shall be stateless across sessions", "cn.stateless-process"),
    ("the system shall rotate credentials quarterly", "cn.config-externalised"),
    ("shall scale across multiple cloud regions", "cn.data-residency"),
    ("shall handle transient failures with backoff", "cn.transient-fault"),
])
def test_inflected_forms_still_fire(text, expected):
    """단어 경계만 세우면 `metrics`·`sessions`가 통째로 빠진다(실제 입력에서 실측)."""
    assert expected in step_cloud._signal_links([{"id": "FR1", "text": text}])


def test_a_concern_without_signals_is_unjudged_not_unaddressed(monkeypatch):
    """**이 축의 핵심 규율.** 물어보지 않은 것을 "안 다뤄졌다"고 적으면 그건 단정이다."""
    monkeypatch.setattr(step_cloud.settings, "concern_linker_llm", False)

    result = step_cloud.link_cloud_concerns(_state(("NFR1", "irrelevant")))["cloud_concerns"]
    by_id = {i["concern_id"]: i for i in result["items"]}

    # `cn.disposability`에는 열쇠말이 없다(특정 단어로 쓰이지 않는다).
    assert by_id["cn.disposability"]["status"] == step_cloud.UNJUDGED
    assert "cn.disposability" in result["unjudged"]
    assert "cn.disposability" not in result["handoff"]
    # 열쇠말이 있는데 안 걸린 것은 판정된 것이다.
    assert by_id["cn.data-residency"]["status"] == step_cloud.UNADDRESSED
    assert "cn.data-residency" in result["handoff"]


def test_llm_layer_needs_a_majority(monkeypatch):
    """한 표만 받은 링크는 안 붙는다 — 링크는 붙이기 쉽고, 붙으면 인계에서 사라진다."""
    from app.requirements.schemas import ConcernLink, ConcernLinkage

    monkeypatch.setattr(step_cloud.settings, "concern_linker_llm", True)
    monkeypatch.setattr(step_cloud.settings, "concern_linker_votes", 3)

    ballots = iter([
        ConcernLinkage(links=[ConcernLink(concern_id="cn.disposability",
                                          requirement_ids=["NFR1"])]),
        ConcernLinkage(links=[ConcernLink(concern_id="cn.disposability",
                                          requirement_ids=["NFR1"])]),
        ConcernLinkage(links=[ConcernLink(concern_id="cn.disposability",
                                          requirement_ids=["NFR2"])]),
    ])
    monkeypatch.setattr(step_cloud, "_ask_llm", lambda *a, **k: next(ballots))

    result = step_cloud.link_cloud_concerns(
        _state(("NFR1", "a"), ("NFR2", "b"))
    )["cloud_concerns"]
    by_id = {i["concern_id"]: i for i in result["items"]}

    assert by_id["cn.disposability"]["covered_by"] == ["NFR1"]  # 2/3만 살아남는다
    assert by_id["cn.disposability"]["judged_by"] == [step_cloud.BY_LLM]


def test_llm_answers_are_grounded(monkeypatch):
    """없는 요구 id·없는 관심사를 댄 답은 버린다(검증자의 `grounding`과 같은 규율)."""
    from app.requirements.schemas import ConcernLink, ConcernLinkage

    monkeypatch.setattr(step_cloud.settings, "concern_linker_llm", True)
    monkeypatch.setattr(step_cloud.settings, "concern_linker_votes", 1)
    monkeypatch.setattr(step_cloud, "_ask_llm", lambda *a, **k: ConcernLinkage(links=[
        ConcernLink(concern_id="cn.disposability", requirement_ids=["FR-999"]),
        ConcernLink(concern_id="cn.invented", requirement_ids=["NFR1"]),
    ]))

    result = step_cloud.link_cloud_concerns(_state(("NFR1", "a")))["cloud_concerns"]
    by_id = {i["concern_id"]: i for i in result["items"]}

    assert "cn.invented" not in by_id
    assert by_id["cn.disposability"]["covered_by"] == []
    # 판정은 받았으므로 `unjudged`가 아니다 — 답이 왔고, 그 답이 "없다"였다.
    assert by_id["cn.disposability"]["status"] == step_cloud.UNADDRESSED


def test_a_failed_llm_call_does_not_become_unaddressed(monkeypatch):
    """호출이 다 실패하면 판정을 못 얻은 것이다. 그걸 "안 다뤄졌다"로 적지 않는다."""
    monkeypatch.setattr(step_cloud.settings, "concern_linker_llm", True)
    monkeypatch.setattr(step_cloud.settings, "concern_linker_votes", 2)

    def boom(*_a, **_k):
        raise RuntimeError("endpoint down")

    monkeypatch.setattr(step_cloud, "_ask_llm", boom)

    result = step_cloud.link_cloud_concerns(_state(("NFR1", "a")))["cloud_concerns"]
    by_id = {i["concern_id"]: i for i in result["items"]}
    assert by_id["cn.disposability"]["status"] == step_cloud.UNJUDGED


def test_the_artifact_carries_the_notice_and_never_calls_them_defects():
    result = step_cloud.link_cloud_concerns(_state(("NFR1", "a")))["cloud_concerns"]
    assert result["notice"] == concerns.ADVISORY_NOTICE
    # 미충족은 인계 항목이다. 결함 목록(`issues`)을 내면 되돌아가기가 이걸 쫓게 된다.
    assert "issues" not in result
    assert set(result["handoff"]) <= set(concerns.all_ids())


# --- 사람 라벨 계기의 규율 ---------------------------------------------------
# `concern_linker_llm` 기본값은 두 층 중 무엇이 참인지에 달려 있고, 그건 판정자끼리
# 대조해서는 안 나온다. 그래서 계기가 지켜야 하는 것은 하나다 — **눈가림이 새지 않는 것.**
def _ballot(domain, links, judged=None):
    return {
        "cell": f"{domain}|chunk0|r0", "domain": domain, "chunk": 0, "repeat": 0,
        "answered": True, "links": links, "judged": judged or list(links),
    }


def _labelling(monkeypatch, llm_links, signal_links, texts):
    from app.requirements.evaluation import concern_labels, concern_report

    monkeypatch.setattr(concern_report, "load", lambda _d: [_ballot("d1", llm_links)])
    monkeypatch.setattr(
        concern_report, "signal_baseline", lambda: {"d1": signal_links}
    )
    monkeypatch.setattr(concern_labels, "_texts", lambda: {"d1": texts})
    return concern_labels


def test_the_label_file_never_says_which_layer_proposed_a_link(monkeypatch, tmp_path):
    """항목 파일에 층이 보이면 눈가림이 아니고, 그러면 라벨이 층 비교에 쓸 수 없다."""
    labels = _labelling(
        monkeypatch,
        llm_links={"cn.scale-out": ["FR1"], "cn.traffic-shape": ["FR2"]},
        signal_links={"cn.traffic-shape": ["FR2"], "cn.event-record": ["FR3"]},
        texts={"FR1": "a", "FR2": "b", "FR3": "c"},
    )
    items, key = labels.build(tmp_path, controls=1)

    blob = str(items)
    for layer in (labels.LAYER_LLM, labels.LAYER_SIGNAL, labels.LAYER_BOTH):
        assert layer not in blob
    # 관심사 id도 층의 흔적이 될 수 있다(열쇠말 없는 관심사는 LLM 층만 낸다).
    assert not any("concern_id" in item for item in items["items"])
    assert {i["item_id"] for i in items["items"]} == set(key["key"])


def test_the_same_ballots_give_the_same_file(monkeypatch, tmp_path):
    """라벨은 여러 번에 나눠 붙인다. 파일이 흔들리면 앞서 붙인 라벨이 다른 항목에 붙는다."""
    kwargs = dict(
        llm_links={"cn.scale-out": ["FR1"]},
        signal_links={"cn.event-record": ["FR3"]},
        texts={"FR1": "a", "FR3": "c"},
    )
    labels = _labelling(monkeypatch, **kwargs)
    first, first_key = labels.build(tmp_path)
    labels = _labelling(monkeypatch, **kwargs)
    second, second_key = labels.build(tmp_path)
    assert first == second and first_key == second_key


def test_controls_come_from_links_both_layers_agreed_on(monkeypatch, tmp_path):
    """대조 항목이 분쟁 링크에서 나오면 대조가 아니다 — 라벨러 점검이 무너진다."""
    labels = _labelling(
        monkeypatch,
        llm_links={"cn.scale-out": ["FR1"], "cn.event-record": ["FR3"]},
        signal_links={"cn.event-record": ["FR3"]},
        texts={"FR1": "a", "FR3": "c"},
    )
    _items, key = labels.build(tmp_path, controls=5)
    controls = [c for c in key["key"].values() if c["layer"] == labels.LAYER_BOTH]
    assert [(c["concern_id"], c["requirement_id"]) for c in controls] == [
        ("cn.event-record", "FR3")
    ]


def test_unsure_is_not_counted_as_wrong(monkeypatch, tmp_path):
    """모르겠다는 답을 오답으로 세면 그건 정밀도가 아니라 라벨러의 확신을 재는 것이 된다."""
    labels = _labelling(
        monkeypatch,
        llm_links={"cn.scale-out": ["FR1", "FR2"]},
        signal_links={"cn.event-record": ["FR3"]},
        texts={"FR1": "a", "FR2": "b", "FR3": "c"},
    )
    items, key = labels.build(tmp_path, controls=0)
    by_layer = {}
    for item in items["items"]:
        by_layer.setdefault(key["key"][item["item_id"]]["layer"], []).append(item)
    by_layer[labels.LAYER_LLM][0]["verdict"] = "yes"
    by_layer[labels.LAYER_LLM][1]["verdict"] = "unsure"

    score = labels.score(items, key)
    llm = score["per_layer"][labels.LAYER_LLM]
    assert llm["judged"] == 1 and llm["precision"] == 1.0
    # 안 붙인 라벨도 분모에 들어가지 않는다 — 진행 중인 라벨링이 좋아 보이면 안 된다.
    assert score["per_layer"][labels.LAYER_SIGNAL]["precision"] is None
    # `unsure`는 **답한 것**이라 남은 항목이 아니다. 남은 것은 손대지 않은 열쇠말 항목 하나뿐.
    assert score["remaining"] == 1
