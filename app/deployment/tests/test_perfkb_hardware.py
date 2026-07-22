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


# --- 축 간 교차 참조 (용량 → 성능) ---


def test_capacity_points_at_perf_for_compute_types() -> None:
    """용량 축에서 하드웨어를 물으면 성능 축을 가리킨다.

    실측: "g5g.xlarge에 어떤 GPU가 달렸어?"에 모델이 용량 축을 뒤지다
    `AWS::EC2::Instance.Gpu`라는 없는 속성까지 조회하고 웹으로 나갔다. 그때
    우리는 GPU 571건을 쥐고 있었는데 **다른 축에** 있었다. 같은 질문을
    "몇 개, 어떤 모델이야?"로 물으면 성능 도구로 바로 갔다 — 문구 하나 차이다.
    """
    from nim_agent.capacity_tools import _perf_pointer

    text = _perf_pointer("AWS::EC2::Instance")
    assert "perf_instance_profile" in text, "성능 축을 안 가리킨다"
    assert "GPU" in text


def test_perf_pointer_is_silent_for_other_types() -> None:
    """인스턴스 종류를 고르는 자리가 아니면 조용하다 — 줄마다 붙으면 노이즈다."""
    from nim_agent.capacity_tools import _perf_pointer

    assert _perf_pointer("AWS::EC2::Volume") == ""
    assert _perf_pointer("존재하지않는타입") == ""


def test_hardware_summary_counts_only_real_hardware() -> None:
    """하드웨어 근거가 있는 레코드만 센다."""
    from perfkb.agent_api import hardware_summary

    text = hardware_summary("aws")
    assert text and "GPU 있는 것" in text
    assert hardware_summary("존재하지않는프로바이더") is None


# --- 가속기 필터 (cost_recommend_specs) ---


def test_accelerator_filter_uses_costkb_own_field() -> None:
    """가속기 필터는 **costkb 자기 필드**로 건다 — 축을 이을 필요가 없다.

    처음엔 perfkb의 `gpuModel`로 조인하려 했다. 근거는 "costkb의
    `acceleratorCount`가 GPU 인스턴스에서도 0"이라는 것이었는데 **틀렸다** —
    ap-northeast-2 첫 행 하나(GPU 없는 인스턴스)를 보고 일반화했다.
    실제로는 GPU 스펙 571행 전부 1 이상이고, costkb 쪽이 **엄격히 더 넓다**
    (296종/9개 프로바이더 vs perfkb 50종/aws만, perfkb에만 있는 것 0종).
    """
    import json

    cost = json.load(open("output/tumblebug-cost.json", encoding="utf-8"))["specs"]
    perf = json.load(open("output/tumblebug-perf.json", encoding="utf-8"))["specs"]
    by_perf = {(s["provider"], s["specName"]) for s in perf if s.get("gpuModel")}
    by_cost = {(s["provider"], s["specName"]) for s in cost if s.get("acceleratorCount")}
    assert by_perf <= by_cost, "perfkb에만 있는 가속기 스펙이 생겼다 — 필터 근거를 다시 보라"
    assert len({p for p, _ in by_cost}) > 1, "costkb 가속기 신호가 aws 한 곳뿐이다"


def test_accelerator_filter_runs_before_sorting(tmp_path) -> None:
    """필터를 **정렬·자르기보다 먼저** 걸어야 한다.

    나중에 걸면 상위 N을 고른 뒤라 가속기 후보가 이미 잘려 나간다. 실제
    ap-northeast-2에서도 저가 상위권은 전부 t 계열(가속기 없음)이다.

    실제 미러가 아니라 **직접 만든 데이터**로 검사한다 — 테스트는 번들 36건
    모드로 묶여 있고, 거기엔 가속기 스펙이 없다.
    """
    import json

    from costkb.dataset import BUILT_FILENAME, _load_cached, filter_specs

    def spec(name, hourly, accel):
        return {
            "id": name, "provider": "aws", "region": "test-1", "specName": name,
            "vCPU": 4, "memGiB": 8.0, "hourlyUSD": hourly,
            "architecture": "x86_64", "infraType": "vm",
            "acceleratorCount": accel, "acceleratorMemoryGB": 0.0,
        }

    dataset = {
        "_note": "테스트용",
        "specs": [spec("cheap-1", 0.01, 0), spec("cheap-2", 0.02, 0),
                  spec("gpu-1", 5.00, 1), spec("gpu-2", 6.00, 2)],
    }
    (tmp_path / BUILT_FILENAME).write_text(
        json.dumps(dataset, ensure_ascii=False), encoding="utf-8"
    )
    _load_cached.cache_clear()

    plain = filter_specs(0, 0, "aws", None, "cost", 2, architecture=None,
                         output_dir=tmp_path)
    accel = filter_specs(0, 0, "aws", None, "cost", 2, architecture=None,
                         require_accelerator=True, output_dir=tmp_path)
    _load_cached.cache_clear()

    assert [s["specName"] for s in plain] == ["cheap-1", "cheap-2"]
    assert [s["specName"] for s in accel] == ["gpu-1", "gpu-2"], (
        "가속기 후보가 상위 2건 밖이라 잘렸다 — 필터가 자르기 뒤에 걸렸다는 뜻"
    )


def test_empty_result_says_the_accelerator_condition_is_on() -> None:
    """가속기 조건 때문에 비었으면 그걸 밝힌다 — 안 밝히면 엉뚱한 데를 조정한다."""
    from costkb import agent_api as cost

    text = cost.recommend_specs(500, 0, "aws", "ap-northeast-2", "cost", 3,
                                architecture=None, require_accelerator=True)
    assert "가속기가 달린 것만" in text
