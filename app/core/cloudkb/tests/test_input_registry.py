"""레지스트리의 불변식 — **근거 없는 질문이 생길 수 없다.**

`input_registry`는 "사용자에게 무엇을 받아야 하는가"를 한 곳으로 모은 것이고,
그 값은 항목마다 **소비자(`opens`)와 근거(`basis`)를 들고 있다**는 데서 온다.
들고만 있고 아무도 안 보면 그건 장식이라, 여기서 좌표가 실재하는지 본다.

무엇을 지키나:

  - 소비자·근거 없는 항목은 만들 수 없다(`Ask.__post_init__`) — 그 방어가
    실제로 도는지.
  - `code:` 좌표의 파일과 이름이 실재하는가. **우리 사정을 근거로 적는 갈래라
    가장 헐거워질 수 있는 자리다.**
  - `claim:` 좌표가 claims.json에 실재하는가.
  - 스키마의 칸과 레지스트리 항목이 **양방향으로** 맞는가 — 안 묻기로 한 칸은
    `NOT_ASKED`에 사유가 있어야 한다. "빠뜨린 것"과 "안 묻기로 한 것"이
    구별되지 않으면 계약이 조용히 낡는다.
  - 결정 질문이 **손으로 적혀 있지 않은가**. depkb가 진실이고 여기는 사영이다.

`concern:` 좌표는 층이 갈려 여기서 못 본다(`app/core`는 `app/requirements`를
모른다). `tests/test_input_registry_concerns.py`가 본다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core import cloud_contract, input_registry
from app.core.cloudkb.appkb import contract as appkb_contract

_ROOT = Path(__file__).resolve().parents[3].parent


def test_an_ask_without_a_consumer_cannot_exist() -> None:
    """소비자 없는 칸을 만들려 하면 죽는다 — request.json이 적어 둔 규율."""
    with pytest.raises(ValueError, match="opens"):
        input_registry.Ask(
            id="x", question="?", opens="  ", tier=input_registry.CONTEXT,
            basis=(input_registry.Basis(input_registry.CODE, "a#b"),))


def test_an_ask_without_a_basis_cannot_exist() -> None:
    """근거 없는 질문은 우리 취향이지 지식이 아니다(cloudkb/CLAUDE.md §5)."""
    with pytest.raises(ValueError, match="근거가 없다"):
        input_registry.Ask(id="x", question="?", opens="something",
                           tier=input_registry.CONTEXT, basis=())


def test_every_code_basis_points_at_a_real_symbol() -> None:
    """`code:` 좌표의 파일과 이름이 실재한다.

    이 갈래는 **우리 코드가 이 값을 요구한다**는 뜻이라 외부 사실이 아니다.
    그래서 가장 헐거워질 수 있고, 그래서 여기서 가장 세게 본다.
    """
    for ask in input_registry.ASKS:
        for basis in ask.basis:
            if basis.kind != input_registry.CODE:
                continue
            path_part, _, symbol = basis.ref.partition("#")
            path = _ROOT / path_part
            assert path.exists(), f"{ask.id}: {path_part} 가 없다"
            assert symbol, f"{ask.id}: {basis.ref} 에 이름이 없다"
            assert symbol in path.read_text(encoding="utf-8"), (
                f"{ask.id}: {path_part} 안에 {symbol} 이 없다")


def test_every_claim_basis_points_at_a_real_claim() -> None:
    """`claim:` 좌표가 claims.json에 실재한다 — 실측을 인용했다면 그 실측이 있다."""
    doc = json.loads(
        (Path(appkb_contract.__file__).resolve().parents[1]
         / "depkb" / "claims.json").read_text(encoding="utf-8"))
    known = {f'{c["csp"]}/{c["subject"]}→{c["object"]}/{c["question"]}'
             for c in doc["claims"]}
    for ask in input_registry.ASKS:
        for basis in ask.basis:
            if basis.kind == input_registry.CLAIM:
                assert basis.ref in known, f"{ask.id}: 없는 주장 {basis.ref}"


def test_schema_fields_and_asks_agree_in_both_directions() -> None:
    """스키마의 칸 = 물어보는 칸 + 안 묻기로 한 칸. 남는 것도 모자란 것도 없다."""
    schema = cloud_contract.schema_fields()
    asked = set(input_registry.by_field())
    declined = set(input_registry.NOT_ASKED)
    assert asked <= schema, f"스키마에 없는 칸을 묻는다: {asked - schema}"
    assert schema == asked | declined, (
        f"분류 안 된 칸: {schema - asked - declined} — 물을 것인지 "
        "안 물을 것인지 정하고, 안 물 것이면 NOT_ASKED에 사유를 적어라")
    assert not (asked & declined), "같은 칸이 양쪽에 있다"


def test_every_required_schema_field_is_a_required_ask() -> None:
    """스키마가 필수라고 한 칸은 레지스트리에서도 필수다(판 표시는 제외).

    두 곳이 갈리면 사용자는 물어보지도 않은 칸 때문에 계약 미충족을 본다.
    """
    required = set(appkb_contract.request_schema().get("required", ()))
    required -= set(input_registry.NOT_ASKED)
    got = {a.spec_field
           for a in input_registry.ASKS if a.tier == input_registry.REQUIRED}
    assert required == got, f"스키마 필수 {required} vs 레지스트리 필수 {got}"


def test_decisions_are_derived_not_written_by_hand() -> None:
    """결정 질문은 `ASKS`에 없다 — depkb가 내고 여기는 사영이다.

    손으로 옮기면 새 실측이 결정을 하나 더 열 때 조용히 낡는다. 이 저장소가
    stages·프롬프트·인용에서 반복해서 물린 사본 문제와 같은 것이다.
    """
    assert not [a for a in input_registry.ASKS
                if a.tier == input_registry.DECISION]
    got = input_registry.asks_for("azure", ("vm", "loadBalancer"))
    decisions = [a for a in got if a.tier == input_registry.DECISION]
    assert decisions, "azure vm·loadBalancer는 결정을 연다(실측)"
    # 실측된 그 결정이 맞는지 — 좌표로 확인한다.
    refs = {b.ref for a in decisions for b in a.basis}
    assert "azure/vm→image/existence" in refs, refs


def test_decisions_do_not_appear_before_an_anchor_is_chosen() -> None:
    """선행조건이 실재한다 — 앵커를 모르면 결정을 물을 수 없다.

    계약은 평면이지만 **묻는 순서는 평면이 아니다.** 그 사실이 지금까지
    어디에도 없었다.
    """
    assert not [a for a in input_registry.asks_for("azure")
                if a.tier == input_registry.DECISION]
    assert not [a for a in input_registry.asks_for()
                if a.tier == input_registry.DECISION]


def test_anchor_choices_come_from_the_claims_not_from_a_list() -> None:
    """앵커 목록은 claims에서 나온다 — 손으로 적으면 다음 실측에서 어긋난다."""
    for csp, least in (("aws", 13), ("azure", 13), ("gcp", 14)):
        anchors = input_registry.anchors_for(csp)
        assert len(anchors) >= least, (csp, anchors)
        assert "k8sCluster" in anchors and "vm" in anchors
    # CSP마다 다르다는 것이 이 칸을 스키마 enum으로 못 박지 못하는 이유다.
    assert input_registry.anchors_for("aws") != input_registry.anchors_for("gcp")


def test_gaps_are_ordered_required_first() -> None:
    """되묻기 순서 — 필수를 먼저 낸다. 섞으면 사용자가 전부 필수로 읽는다."""
    tiers = [g.tier for g in input_registry.gaps({}, "aws")]
    order = [input_registry.REQUIRED, input_registry.SUGGESTED,
             input_registry.CONTEXT]
    assert tiers == sorted(tiers, key=order.index)


def test_the_question_and_the_reason_are_different_strings() -> None:
    """사용자에게 하는 말과 그것이 필요한 이유는 다른 것이다.

    예전에는 이유만 있어서 되묻기가 영어 근거 문장을 그대로 사용자에게 냈다.
    """
    for ask in input_registry.ASKS:
        assert ask.question != ask.opens, ask.id
        assert cloud_contract.question(ask.spec_field) == ask.question
        assert cloud_contract.why(ask.spec_field) == ask.opens


def test_missing_treats_the_floor_pair_as_one_question() -> None:
    """스펙 하한 둘은 한쪽만 있으면 더 안 묻는다 — **쌍은 이제 이것 하나다.**

    2026-08-01 제로베이스 재구성 전에는 쌍이 둘이었다(하한 둘 · 규모 신호 둘).
    규모 쪽은 두 칸이 같은 양의 **두 단위**였을 뿐이라 `scale{value,unit}` 한 칸이
    됐고, 그러면서 특수 처리가 절반으로 줄었다. 하한 둘은 남는다 — vCPU와 메모리는
    `resolve`가 축마다 따로 `max`로 거르는 **진짜 두 축**이다.
    """
    assert "minVCpu" not in cloud_contract.suggested_fields({"minMemoryGiB": 4})
    assert len(input_registry.PAIRS) == 1


def test_validate_says_why_a_required_field_is_needed() -> None:
    """모양 검증은 이유를 모르고, 레지스트리가 그것을 붙인다.

    **왜 없이 되물으면 사용자가 임의로 채운다** — 이 구조의 출발점이다.
    """
    problems = cloud_contract.validate({"schemaVersion": "2"})
    workloads = [p for p in problems if "workloads" in p]
    assert workloads and "폐포" in workloads[0], problems
