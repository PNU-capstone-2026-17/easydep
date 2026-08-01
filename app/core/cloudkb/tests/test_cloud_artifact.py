"""`cloud` 산출물 — **사슬의 마지막 이음매**가 실제로 이어지는가.

구현 단계는 이 산출물을 기다리고 있었는데(`OPTIONAL_DESIGN_INPUTS`에 이름이
있고 `deployment_renderer`가 읽는다) **아무도 내지 않았다**(2026-08-01 실측:
실물 표본의 `design/`에 없다). 그래서 요구사항→YAML 사슬이 거기서 끊겼다.

여기서 지키는 것:

  - 설계 신호에서 나온 계획이 **하류 렌더러를 통과한다**(끝까지 도는가).
  - **모르는 칸을 채우지 않는다.** 채우면 그 값이 우리 주장이 된다.
  - 실측(순서·대기·경고)이 산출물에 **실려 나간다** — 하류가 아직 안 읽어도
    사슬의 이 지점에서 지식이 사라지면 안 된다.
  - 담을 수 없는 계획은 **그렇다고 말한다**(VM 계획·비azure).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.cloud_artifact import build
from app.core.cloudkb.nim_agent.design_tools import compose
from app.core.cloudkb.tools.intake_report import _design_from, _read

_SAMPLE = (Path(__file__).resolve().parents[1] / "appkb" / "samples"
           / "lecture-platform")


def _design(csp: str = "azure", *, kubernetes: bool = True) -> dict:
    spec, _ = _read(_SAMPLE, "requirements/resource_spec.json")
    design, problems = _design_from(_SAMPLE, spec if isinstance(spec, dict) else None)
    assert design is not None, problems
    doc = copy.deepcopy(design)
    doc.setdefault("requirements", {})["provider"] = csp
    if kubernetes:
        for component in doc.get("components", []):
            component["deployHint"] = {"compute": "kubernetes",
                                       "reason": "관리형 k8s로 운영"}
    return doc


def test_the_artifact_carries_the_measurement() -> None:
    """**실측이 사슬을 타고 나간다.** 하류가 아직 안 읽어도 산출물엔 있어야 한다.

    순서·완료 대기·서버의 몫은 사람이 apply할 때 필요한 것이고, 여기서 빠지면
    그 지식이 사슬의 이 지점에서 사라진다.
    """
    design = _design()
    art = build(compose(design), design, name="lecture-platform")
    measured = art["_measured"]
    assert measured["anchors"] == ["k8sCluster"], measured["anchors"]
    assert "k8sCluster" in measured["createOrder"]
    assert [w for w in measured["waitFor"] if w["id"] == "k8sCluster"]
    assert {x["id"] for x in measured["doNotCreate"]} >= {"subnet"}


def test_it_does_not_fill_what_it_does_not_know() -> None:
    """모르는 칸을 채우면 **그 값이 우리 주장이 된다.**

    `readinessPath: "/healthz"`를 우리가 쓰면 "이 앱에 그 엔드포인트가 있다"는
    말이 되는데 우리는 모른다. 렌더러에 기본값이 있으므로 비우면 그쪽이 선다.
    """
    design = _design()
    art = build(compose(design), design)
    cluster = art["resources"][0]
    assert "containerPort" not in cluster["networking"]
    for workload in cluster["workloads"]:
        assert "probes" not in workload
        assert "monitoring" not in workload
    # **비운 이유가 함께 나간다** — 빈 칸이 "해당 없음"으로 읽히면 안 된다.
    assert art["_omitted"]["workloads[].probes"]


def test_a_vm_plan_says_the_downstream_cannot_take_it() -> None:
    """하류는 관리형 k8s만 읽는다 — **우리 한계가 아니라 하류 스키마의 범위**다."""
    design = _design(kubernetes=False)
    art = build(compose(design), design)
    assert not art["resources"]
    assert any("관리형 쿠버네티스" in x for x in art["_unsupported"]), art["_unsupported"]


def test_a_non_azure_plan_says_the_downstream_only_reads_arm() -> None:
    """3사를 재놓고 하류가 azure만 알아본다 — 침묵하지 않고 적는다."""
    design = _design(csp="gcp")
    art = build(compose(design), design)
    assert any("azure ARM" in x for x in art["_unsupported"]), art["_unsupported"]


def test_the_chain_actually_reaches_manifests(tmp_path) -> None:
    """**이 파일의 요점** — 설계 산출물에서 나온 계획이 매니페스트까지 간다.

    2026-08-01 첫 측정: 매니페스트 6개(namespace·deployment·service·ingress·
    network-policy·service-account) + Dockerfile, 소스 대조 오류 0.
    """
    from app.implementation.engine.deployment_renderer import render_deployment

    design = _design()
    art = build(compose(design), design, name="lecture-platform")
    cloud = tmp_path / "cloud.json"
    cloud.write_text(json.dumps(art, ensure_ascii=False), encoding="utf-8")
    spec = SimpleNamespace(
        name="lecture-platform",
        inputs={"cloud": cloud, "deployment": tmp_path / "absent.puml",
                "deploymentIntent": tmp_path / "absent.json"})
    report = render_deployment(tmp_path, spec)

    rendered = set(report["renderedFiles"])
    assert "application/Dockerfile" in rendered
    assert any(f.endswith("deployment.yaml") for f in rendered), rendered
    # **우리가 준 것이 실제로 읽혔다는 증거** — 렌더러가 그 사실을 기록한다.
    assert report["sourceEvidence"]["cloudResourceSpecification"] is True
    # 그리고 우리 산출물이 워크로드 이름을 정했다(빈 dict였다면 기본값이 섰다).
    assert [w["name"] for w in report["intent"]["workloads"]] == ["app"]
    # **소스 대조가 통과해야 한다** — 우리가 낸 cloud와 렌더러가 추론한 의도가
    # 어긋나면 여기서 잡힌다.
    conformance = report["sourceConformance"]
    assert conformance["status"] == "SUCCEEDED", conformance
    assert not conformance["errors"], conformance["errors"]
    assert "cloud-resource-spec" in conformance["checked"]


def test_the_bridge_records_which_links_are_only_name_matches() -> None:
    """k8s 셋은 **이름 일치**로 이었다 — 스키마 결속과 등급이 다르다.

    `vocabulary.AWS_TYPES`는 9종뿐이라 k8s가 다리에 없었고, 그래서 쿠버네티스
    계획이 통째로 대조 밖으로 빠졌다(앵커 0·실측 전부 빔). `core::<이름>`을
    지렛대로 이었는데 근거가 **이름뿐**이라, 목록을 상수로 고정해 조용히 늘지
    않게 한다.
    """
    from app.core import plan_crosscheck as pc

    assert pc.NAME_MATCHED == ("k8sCluster", "k8sNodeGroup", "customImage")
    bridge = pc._bridge()
    for csp in ("aws", "azure", "gcp"):
        assert "k8sCluster" in bridge[csp].values(), csp


@pytest.mark.parametrize("csp", ["aws", "azure", "gcp"])
def test_every_csp_produces_an_artifact_even_if_downstream_cannot_read_it(csp) -> None:
    """산출물은 3사 전부 나온다. 못 읽는 것은 하류 쪽 사정이고 그렇게 적힌다."""
    design = _design(csp=csp)
    art = build(compose(design), design)
    assert art["provider"] == csp
    assert art["_measured"]["anchors"] == ["k8sCluster"], art["_measured"]
