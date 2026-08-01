"""배포 후 검증 스위트 — **컨트롤 플레인이 안 막는 것을 잡는 자리**의 불변식.

과제 문제 ③이 요구하는 네 번째 단계(테스트)를 채우는 산출물이다. 재료는 기능
결속 14건이고, 그 축은 다른 둘과 오라클이 다르다 — 컨트롤 플레인이 **막지
않으므로** apply 전 검사로는 영영 안 잡히고 배포 후 확인 말고는 방법이 없다.

여기서 지키는 것:

  - 신호는 **주장이 나른다**(`claims.json`의 `signal`). 술어 산문을 파싱하지
    않는다 — 파싱하면 규칙 사본이 둘이 된다.
  - **두 목록이 어긋나면 죽는다.** 주장의 신호와 점검 방법이 따로 늘면 주장은
    있는데 점검이 없고, 그 침묵이 "확인할 것 없음"으로 읽힌다.
  - 점검은 **자기를 낳은 주장의 좌표를 들고 다닌다.** 왜 확인하는지가 "우리가
    그렇게 생각해서"가 아니라 실측이라야 한다.
  - 계획에 없는 자원의 점검을 내지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core import deploy_checks
from app.core.cloudkb.depkb import build_claims

_CLAIMS = Path(build_claims.__file__).with_name("claims.json")


def test_every_function_claim_declares_a_signal() -> None:
    """기능 주장에 신호가 없으면 **그 결속을 무엇으로 쟀는지** 말할 수 없다.

    빌드가 이미 막지만(`build_claims.build`), 산출물 쪽에서도 본다 — 빌드를
    건너뛰고 손으로 고친 claims가 들어오는 경우가 있다.
    """
    doc = json.loads(_CLAIMS.read_text(encoding="utf-8"))
    functions = [c for c in doc["claims"] if c["question"] == "function"]
    assert len(functions) == 15, len(functions)
    missing = [f'{c["csp"]}/{c["subject"]}→{c["object"]}'
               for c in functions if not c.get("signal")]
    assert not missing, missing
    assert all(c["signal"] in build_claims.SIGNALS for c in functions)


def test_the_signal_list_and_the_recipe_list_agree() -> None:
    """**한쪽만 늘면 조용히 빈다.** 주장은 있는데 점검이 안 나오는 상태다."""
    declared = set(build_claims.SIGNALS)
    known = set(deploy_checks._HOW)
    assert declared == known, (
        f"신호만 있고 방법이 없다: {declared - known} · "
        f"방법만 있고 쓰는 주장이 없다: {known - declared}")
    # 그리고 실제로 주장에 실린 신호가 그 목록 안이어야 한다.
    assert deploy_checks.signals() <= declared


def test_a_check_carries_the_claim_that_motivates_it() -> None:
    """점검의 근거는 실측이라야 한다 — 좌표 없이 나가지 않는다."""
    found = deploy_checks.checks_for("aws", {"vm", "publicIp", "iamRole"})
    assert found
    for check in found:
        assert len(check.because) == 3
        assert check.evidence, check
        assert all("/" in e for e in check.evidence)


def test_it_only_checks_what_the_plan_actually_places() -> None:
    """안 놓는 자원의 점검을 내면 실행하는 사람이 그걸 찾다가 시간을 버린다."""
    only_vm = deploy_checks.checks_for("aws", {"vm", "publicIp"})
    subjects = {c.because[1] for c in only_vm} | {c.because[2] for c in only_vm}
    assert "internetGateway" not in subjects or "subnet" in subjects
    assert not deploy_checks.checks_for("aws", set())


def test_the_recipe_carries_the_trap_the_experiment_hit() -> None:
    """**함정이 점검에 실린다.** 실험이 물린 것을 다음 사람이 또 물면 안 된다.

    DNS는 캐시가 상실을 세 시간 가렸고, 볼륨 쓰기는 페이지 캐시와 파이프가
    가렸다. 그 둘은 방법을 모르면 **거짓 통과**가 나는 종류다.
    """
    dns = deploy_checks._HOW["dns-resolution"]
    assert "캐시" in dns[3]
    volume = deploy_checks._HOW["volume-write"]
    assert "oflag=direct" in volume[3] and "파이프" in volume[3]


def test_an_unknown_signal_is_reported_not_dropped() -> None:
    """**모르는 신호를 조용히 버리지 않는다** — 침묵이 "확인할 것 없음"이 된다."""
    saved = deploy_checks._HOW.pop("imds-credentials")
    try:
        found = deploy_checks.checks_for("aws", {"vm", "iamRole"})
        orphan = [c for c in found if c.where == "unknown"]
        assert orphan, found
        assert "점검 방법이 없다" in orphan[0].what
    finally:
        deploy_checks._HOW["imds-credentials"] = saved


@pytest.mark.parametrize("csp,least", [("aws", 4), ("azure", 4), ("gcp", 3)])
def test_each_csp_produces_checks(csp: str, least: int) -> None:
    """3사 모두 점검이 나온다 — 다만 **개수가 고르지 않다**(커버리지 불균등)."""
    everything = {"vm", "publicIp", "firewall", "subnet", "internetGateway",
                  "iamRole", "disk", "nic", "network", "loadBalancer",
                  "globalDns", "k8sCluster", "k8sService"}
    doc = deploy_checks.build(csp, everything)
    assert len(doc["checks"]) >= least, doc["checks"]
    # 침묵을 "문제없다"로 읽지 않게 하는 문장이 함께 나간다.
    assert "'문제없다'는 뜻은 아니다" in doc["_scope"]


def test_the_artifact_carries_the_checks_down_the_chain() -> None:
    """**사슬을 탄다** — `cloud` 산출물에 실려 구현 단계까지 내려간다."""
    import copy

    from app.core.cloud_artifact import build as build_artifact
    from app.core.cloudkb.nim_agent.design_tools import compose
    from app.core.cloudkb.tools.intake_report import _design_from, _read

    root = (Path(__file__).resolve().parents[1] / "appkb" / "samples"
            / "lecture-platform")
    spec, _ = _read(root, "requirements/resource_spec.json")
    design, _ = _design_from(root, spec if isinstance(spec, dict) else None)
    doc = copy.deepcopy(design)
    doc["requirements"]["provider"] = "aws"
    art = build_artifact(compose(doc), doc)
    checks = art["_deployChecks"]["checks"]
    assert checks, art["_deployChecks"]
    assert {c["signal"] for c in checks} >= {"inbound-tcp"}
