"""폐포(depkb.closure)의 구조적 불변식 — 검증 주장의 소비가 규율을 지키는가.

값의 옳음은 claims.json(과 그 실험들)이 진다. 여기서 지키는 것은 소비 층의
규율이다: 근거 없는 항목 금지 · 필수/선택의 겹침 금지 · CSP별 답의 차이 보존 ·
모르는 것 소비 거부.
"""

from __future__ import annotations

import pytest

from app.core.dependency import closure, describe


def test_azure_vm_needs_the_nic_chain() -> None:
    """azure VM 폐포 = network→subnet→nic 사슬. 순서는 필수 간선의 위상 정렬."""
    c = closure("vm", "azure")
    assert {i.id for i in c.required} == {"nic", "subnet", "network"}
    assert c.createOrder == ("network", "subnet", "nic", "vm")
    assert {a.id for a in c.attachable} == {"disk", "firewall", "publicIp",
                                            "iamRole", "customImage"}
    assert not any(a.autoFilled for a in c.attachable), (
        "azure 선택 자원엔 서버 대체 실측이 없다 — autoFilled는 측정된 대체에만"
    )


def test_aws_vm_requires_exactly_the_image() -> None:
    """aws VM 폐포의 필수는 **image 하나**다(2026-07-31 image 라운드로 갱신 —
    그 전 어휘에선 공집합이었다). 인프라는 전부 서버 대체(기본 VPC·default
    SG·ENI 암묵·AMI 루트 볼륨)이고, 사람이 정해야 하는 유일한 생성 인자가
    무엇으로 부팅할 것인가다."""
    c = closure("vm", "aws")
    assert {i.id for i in c.required} == {"image"}
    assert c.createOrder == ("image", "vm")
    auto = {a.id for a in c.attachable if a.autoFilled}
    assert auto == {"subnet", "firewall", "nic", "disk"}
    assert {a.id for a in c.attachable if not a.autoFilled} == {
        "sshKey", "iamRole", "customImage"}, (
        "sshKey·iamRole·customImage는 서버가 채워 주지 않는다 — 사람이 정한다"
    )


def test_gcp_vm_flips_the_modality_and_surfaces_the_condition() -> None:
    """gcp VM 폐포: disk·nic 필수(양상 반전의 소비측), nic→subnet은 조건부
    결정으로 사람에게 올라온다 — 지식이 아니라 배선이 문제였다는 그 자리에
    이제 조건이 실려 간다."""
    c = closure("vm", "gcp")
    assert {i.id for i in c.required} == {"nic", "disk"}
    conds = [d for d in c.decisions if d.kind == "conditional"]
    assert any(d.about == "nic→subnet" for d in conds)


def test_azure_lb_choice_reaches_the_human() -> None:
    """azure LB의 3항 선언 술어는 선택 결정으로 나온다 — 폐포가 대신 고르지
    않는다(어느 것이든 근거 없이 고르면 그건 우리 발명이다)."""
    c = closure("loadBalancer", "azure")
    assert any(d.kind == "choice" for d in c.decisions)


def test_every_required_item_carries_its_claims() -> None:
    """근거 없는 항목 금지 — 모든 필수 항목은 자기를 만든 간선을 들고 다닌다."""
    for csp in ("azure", "aws", "gcp"):
        c = closure("vm", csp)
        for item in c.required:
            assert item.because, f"{csp} {item.id}: 근거가 없다"


def test_required_and_attachable_never_overlap() -> None:
    """필수로 딸려온 것을 선택지로 다시 세지 않는다 — 겹치면 계획서가 같은
    자원을 두 번 말한다(실제로 azure subnet이 겹쳤다가 잡힌 자리)."""
    for csp in ("azure", "aws", "gcp"):
        for anchor in ("vm", "loadBalancer", "nic", "subnet"):
            c = closure(anchor, csp)
            overlap = {i.id for i in c.required} & {a.id for a in c.attachable}
            assert not overlap, f"{csp}/{anchor}: 겹침 {overlap}"


def test_delete_constraints_are_measured_pairs_only() -> None:
    """삭제 제약은 실측된 생명주기 주장만 싣는다 — 총순서를 지어내지 않는다."""
    c = closure("vm", "azure")
    assert ("nic", "subnet") in c.deleteBefore
    assert ("vm", "disk") in c.deleteBefore


def test_synthesis_cleanup_is_not_a_delete_constraint() -> None:
    """k8s 합성 라운드(2026-07-31)의 소비 규율 — 동반 정리는 deleteBefore가
    아니라 cleanupCascades다. 섞으면 계획층이 '이미 없는 합성물의 삭제 단계'를
    낸다. `required: true` 하나가 세 판정을 겸하다 어긋났던 진단과 같은 이유로
    기제를 필드에서 가른다."""
    for csp in ("azure", "gcp", "aws"):
        c = closure("k8sService", csp)
        assert ("k8sService", "loadBalancer") in c.cleanupCascades, csp
        assert ("k8sService", "loadBalancer") not in c.deleteBefore, csp
        lb = next(a for a in c.attachable if a.id == "loadBalancer")
        assert lb.autoFilled, f"{csp}: 합성 실측이 autoFilled로 읽혀야 한다"
    # aws는 1·2라운드에서 전제 부재로 미측정이었다가 완결 라운드(IRSA까지
    # 갖춤)에서 닫혔다 — 이제 3사 전부 동반 정리다.
    for csp in ("azure", "gcp", "aws"):
        c = closure("k8sPvc", csp)
        assert ("k8sPvc", "disk") in c.cleanupCascades, csp


def test_functional_deps_surface_as_operational_warnings() -> None:
    """기능 의존(2026-07-31)의 소비 규율 — 존재·생명주기 검사로는 안 잡히는
    지대다. apply는 성공하는데 서비스가 죽으므로 **운영 경고**로 나른다.
    azure VM 폐포에는 nic→publicIp·subnet→firewall이 걸려 있다."""
    c = closure("vm", "azure")
    pairs = {(s, o) for s, o, _ in c.functionalDeps}
    assert ("nic", "publicIp") in pairs
    assert ("subnet", "firewall") in pairs
    assert all(why.strip() for _, _, why in c.functionalDeps), (
        "경고에는 근거 문장이 있어야 한다"
    )
    # 기능 결속은 삭제 순서가 아니다 — deleteBefore와 섞이면 안 된다.
    assert ("subnet", "firewall") not in c.deleteBefore


def test_unknown_csp_and_anchor_fail_loudly() -> None:
    with pytest.raises(KeyError):
        closure("vm", "ncp")
    with pytest.raises(KeyError):
        closure("quantumComputer", "azure")


def test_describe_renders_for_every_known_cell() -> None:
    """describe는 아는 (앵커×CSP) 전부에서 예외 없이 문단을 낸다."""
    for csp in ("azure", "aws", "gcp"):
        for anchor in ("vm", "nic", "subnet", "network", "loadBalancer"):
            text = describe(anchor, csp)
            assert anchor in text and csp in text


# --- 술어 분류 어휘의 규율 (2026-08-01) -----------------------------------------

def test_no_class_classifies_nothing() -> None:
    """**빈 범주 금지.** 분류하는 것이 없는 칸은 분류가 아니라 자리다.

    `EXTERNAL 스킴 실측`이 정확히 그랬다 — 0건을 분류하면서 어휘에 앉아 있었다.
    `docs/ARCHITECTURE.md`가 경계하는 "임의 사전"이 분류 어휘에서 나타난 꼴이고,
    이 저장소가 `test_scope.py`로 다른 축에서 이미 막고 있던 것이다.
    """
    import json
    from pathlib import Path

    from app.core.cloudkb.depkb import closure as mod

    doc = json.loads((Path(mod.__file__).with_name("claims.json"))
                     .read_text(encoding="utf-8"))
    predicates = [c.get("predicate") or "" for c in doc["claims"]]
    empty = [prefix for prefix, _ in mod.PREDICATE_CLASSES
             if not any(p.startswith(prefix) for p in predicates)]
    assert not empty, f"분류하는 것이 없는 부류: {empty}"


def test_every_class_is_a_class_not_a_sentence() -> None:
    """부류 이름은 `이름:` 꼴이다 — **한 사례의 원문 조각이 아니다.**

    `("ALB는", "detail")`이 그랬다. 분류가 안 되는 술어를 만났을 때 부류를 만드는
    대신 그 문장의 앞부분을 어휘에 넣은 자국이고, 그러면 분류 체계에 사례가 섞여
    표로 낼 수 없게 된다(모듈 docstring의 "분류 불가능하면 죽는다"를 우회한 것).
    """
    from app.core.cloudkb.depkb import closure as mod

    bad = [prefix for prefix, _ in mod.PREDICATE_CLASSES
           if not prefix.endswith(":")]
    assert not bad, f"부류가 아니라 문장 조각이다: {bad}"


def test_every_class_is_anchored_to_or_excluded_from_idl() -> None:
    """부류마다 **외부 형식주의와의 관계**를 밝힌다 — 표현되거나, 왜 안 되거나.

    `PREDICATE_CLASSES`는 우리 구성이라 "왜 이 분류인가"에 답할 것이 우리 판단
    뿐이었다. IDL(RESTest, ICSOC'20)은 실제 웹 API에서 관측된 파라미터 간 의존
    일곱 종을 위해 만들어진 언어이고, 값 제약에 해당하는 우리 술어가 거기 앉는다.

    `None`은 **표현 안 됨**이고 그 자체가 결과다 — 지금 여덟이 그렇고, 그중
    카디널리티·시간 축이 이 축의 새 기여와 겹친다.
    """
    from app.core.cloudkb.depkb import closure as mod

    classes = {prefix for prefix, _ in mod.PREDICATE_CLASSES}
    assert classes == set(mod.IDL_FORM), (
        f"매핑 누락 {classes - set(mod.IDL_FORM)} · "
        f"잉여 {set(mod.IDL_FORM) - classes}")
    expressible = {k for k, v in mod.IDL_FORM.items() if v}
    assert "disjunctive:" in expressible and "쌍 호환:" in expressible
    # 시간 축은 IDL 밖이다 — 되살리려면 IDL이 그것을 담는다는 근거가 먼저다.
    for outside in ("수명 조건:", "동반 정리:", "배치 조건:"):
        assert mod.IDL_FORM[outside] is None, outside


def test_the_or_versus_onlyone_question_was_measured_not_assumed() -> None:
    """형식화가 낸 질문을 **재서 닫았다**(2026-08-01) — 부류가 아니라 주장 단위로.

    IDL은 `Or`(적어도 하나)와 `OnlyOne`(정확히 하나)을 가르는데 우리는 그 구별을
    재지 않고 있었다. `azure loadBalancer`에 subnet과 publicIp를 **함께** 주니
    컨트롤 플레인이 `FrontendIPConfigHasBothSubnetAndPublicIP`로 거부했다 →
    `OnlyOne`. 대조군(하나씩만)은 같은 경로로 섰다.

    **부류 수준에서 정하지 않는다** — 주장마다 갈리기 때문이다. 같은 날 gcp
    `vm→image`도 닫았고(REST로 직접: *"Cannot specify both 'source' and
    'initializeParams'"*, HTTP 400), **azure `vm→image`는 아직 미판정**이다
    (preflight는 통과했는데 통과는 이 저장소에서 증거가 아니다).

    3건 중 2건이 배타로 확정됐다고 나머지를 그렇게 적으면 안 된다 — 그것이
    이 테스트가 막는 것이다.
    """
    import json
    from pathlib import Path

    from app.core.cloudkb.depkb import closure as mod

    assert "주장" in (mod.IDL_FORM["disjunctive:"] or "")
    doc = json.loads((Path(mod.__file__).with_name("claims.json"))
                     .read_text(encoding="utf-8"))
    lb = next(c for c in doc["claims"]
              if c["csp"] == "azure" and c["subject"] == "loadBalancer"
              and "|" in c["object"])
    assert lb["constraint"] == {"exclusive": True, "idl": "OnlyOne"}
    codes = {e.get("code") for e in lb["evidence"]}
    assert "FrontendIPConfigHasBothSubnetAndPublicIP" in codes, codes
    gcp = next(c for c in doc["claims"]
               if c["csp"] == "gcp" and c["subject"] == "vm"
               and c["object"] == "image")
    assert gcp["constraint"] == {"exclusive": True, "idl": "OnlyOne"}
    # **재지 않은 것은 재지 않았다고 남는다.** azure `vm→image`가 그 자리다.
    azure_image = next(c for c in doc["claims"]
                       if c["csp"] == "azure" and c["subject"] == "vm"
                       and c["object"] == "image")
    assert not azure_image.get("constraint"), (
        "azure vm→image의 배타성을 쟀다면 이 테스트를 고쳐라 — preflight 통과는 "
        "이 저장소에서 증거가 아니다")
