"""AWS 하드웨어 사실(CPU·GPU 모델) 덧붙이기.

**왜 이 축이 생겼나.** 우리는 가속기 정보를 한 건도 갖고 있지 않았고, 그 빈칸에서
모델이 GPU 사양표를 통째로 지어냈다(`g5g`를 AMD라고 했다 — NVIDIA T4G다).
그리고 그것을 "우리 지식베이스에서 조회한 결과"라고 적었다.
"""

from __future__ import annotations

from perfkb.parsers.hardware import _cpu_fields, _gpu_fields, enrich


def test_zero_is_not_a_fact() -> None:
    """값이 없으면 칸을 만들지 않는다 — 0을 담으면 '없음'과 '모름'이 섞인다."""
    assert _cpu_fields({"vendor": "GenuineIntel", "speed": 0, "cores": 4}) == {
        "cpuVendor": "GenuineIntel", "cpuCores": 4,
    }
    assert _cpu_fields({}) == {}


def test_mixed_gpu_models_stay_a_list() -> None:
    """한 인스턴스에 모델이 섞여 있으면 **하나로 고르지 않는다.**

    고르는 순간 그건 사실이 아니라 짐작이다.
    """
    one = _gpu_fields([{"name": "NVIDIA T4G", "architecture": "Turing"}])
    assert one["gpuModel"] == "NVIDIA T4G" and one["gpuCount"] == 1

    mixed = _gpu_fields([{"name": "A"}, {"name": "B"}, {"name": "A"}])
    assert mixed["gpuModel"] == ["A", "B"], "섞인 모델을 하나로 골랐다"
    assert mixed["gpuCount"] == 3

    assert _gpu_fields([]) == {}
    assert _gpu_fields([{"architecture": "Turing"}]) == {}, "이름 없는 항목을 담았다"


def test_enrich_touches_only_matching_aws_specs() -> None:
    """aws만, 그리고 이름이 실제로 맞는 것만 건드린다."""
    specs = [
        {"provider": "aws", "specName": "g5g.xlarge"},
        {"provider": "aws", "specName": "g5g.xlarge"},   # 리전이 달라 레코드가 여럿
        {"provider": "gcp", "specName": "g5g.xlarge"},   # 이름이 같아도 남의 것
        {"provider": "aws", "specName": "없는타입"},
    ]
    table = {
        "g5g.xlarge": {
            "ran_at": "2025-12-10T19:55:05Z",
            "cpu": {"vendor": "ARM", "cores": 4},
            "nvidia_gpus": [{"name": "NVIDIA T4G", "architecture": "Turing"}],
        },
        "소스에만있는타입": {"cpu": {"vendor": "X"}},
    }
    report = enrich(specs, table)

    assert report.matched == 1
    assert report.unmatched == ["소스에만있는타입"]
    assert specs[0]["gpuModel"] == "NVIDIA T4G"
    assert specs[1]["gpuModel"] == "NVIDIA T4G", "같은 이름의 다른 리전 레코드를 빠뜨렸다"
    assert "gpuModel" not in specs[2], "gcp 레코드를 건드렸다"
    assert "gpuModel" not in specs[3]
    assert specs[0]["hardwareCheckedAt"] == "2025-12-10"
    assert specs[0]["hardwareEvidence"] == "ec2-hardware-probe"


def test_profile_shows_hardware_separately() -> None:
    """하드웨어는 성능 신호와 **따로 묶어** 보여준다. 소스가 다르기 때문이다."""
    from perfkb.agent_api import _describe

    text = _describe({
        "provider": "aws", "specName": "g5g.xlarge", "clockGHz": 2.5,
        "gpuModel": "NVIDIA T4G", "gpuCount": 1, "gpuArchitecture": "Turing",
        "hardwareCheckedAt": "2025-12-10", "hardwareEvidence": "ec2-hardware-probe",
    })
    assert "— 하드웨어 (2025-12-10 확인)" in text
    assert "NVIDIA T4G ×1, Turing" in text
