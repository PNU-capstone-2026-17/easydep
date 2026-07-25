"""하드웨어 사실은 **가리키지 말고 실어 준다.**

이름 조회 도구(`cost_describe_spec`)를 만들자 모델이 GPU 질문에도 그걸 부르기
시작했다. 답 자체는 근거가 있었다 — 비용 축에도 `acceleratorModel`이 있어서
"NVIDIA A100 8개"까지는 나온다. **사라진 것은 성능 축에만 있는 것**이었다:
아키텍처(`Turing`·`Ampere`)와 정확한 SKU(`A100-SXM4-40GB`).

가리키기만 하면 모델은 이미 답을 얻었다고 보고 더 부르지 않는다. 그래서 **어느
도구를 부르든 아는 것이 실리게** 했다.
"""

from __future__ import annotations

import json

import pytest

from perfkb import dataset
from perfkb.agent_api import hardware_facts

GPU = {
    "id": "aws+us-east-1+g5g.xlarge", "provider": "aws", "specName": "g5g.xlarge",
    "sustainedCpu": {"value": True, "evidence": "aws-non-burstable-inferred",
                     "basis": "inferred"},
    "gpuModel": "NVIDIA T4G", "gpuCount": 1, "gpuArchitecture": "Turing",
    "hardwareCheckedAt": "2025-12-10", "hardwareEvidence": "ec2-hardware-probe",
}
CPU_ONLY = {
    "id": "aws+us-east-1+t3.medium", "provider": "aws", "specName": "t3.medium",
    "sustainedCpu": {"value": False, "evidence": "aws-burstable-field", "basis": "stated"},
    "cpuModel": "Intel(R) Xeon(R) Platinum 8259CL CPU @ 2.50GHz",
    "hardwareCheckedAt": "2025-12-10", "hardwareEvidence": "ec2-hardware-probe",
}
BARE = {
    "id": "gcp+us-central1+n2-highmem-8", "provider": "gcp", "specName": "n2-highmem-8",
    "sustainedCpu": {"value": True, "evidence": "gcp-dedicated-cpu-inferred",
                     "basis": "inferred"},
}


@pytest.fixture
def built(tmp_path):
    (tmp_path / "tumblebug-perf.json").write_text(
        json.dumps({"_note": "테스트", "specs": [GPU, CPU_ONLY, BARE], "_source": []}),
        encoding="utf-8",
    )
    dataset.clear_caches()
    yield tmp_path
    dataset.clear_caches()


def test_architecture_is_the_part_only_we_have(built) -> None:
    """**핵심.** 비용 축은 모델명까지만 준다 — 아키텍처는 여기에만 있다."""
    text = hardware_facts("aws", "g5g.xlarge", built)
    assert "Turing" in text
    assert "NVIDIA T4G" in text and "×1" in text


def test_checked_date_travels_with_the_fact(built) -> None:
    """하드웨어는 다른 소스에서 왔고 잘 안 바뀐다 — 언제 확인한 것인지가 필요하다."""
    assert "2025-12-10" in hardware_facts("aws", "g5g.xlarge", built)


def test_cpu_only_spec_still_reports(built) -> None:
    text = hardware_facts("aws", "t3.medium", built)
    assert "CPU" in text and "GPU" not in text


def test_no_hardware_means_none_not_empty_string(built) -> None:
    """붙일 게 없으면 아무것도 안 붙인다 — 빈 줄이 답에 끼면 노이즈다."""
    assert hardware_facts("gcp", "n2-highmem-8", built) is None


def test_unknown_spec_is_none(built) -> None:
    assert hardware_facts("aws", "does-not-exist", built) is None


def test_probe_can_accept_either_tool() -> None:
    """한 사실을 두 도구가 답할 수 있으면 **경로가 아니라 사실**을 고정해야 한다."""
    from tools.agent_probe import Probe

    probe = Probe("T", "q", "why", want_any_tool=("a", "b"))
    assert probe.failures(["b"], "") == []
    assert probe.failures(["c"], "") == ["기대 도구 미호출(택1): a, b"]
