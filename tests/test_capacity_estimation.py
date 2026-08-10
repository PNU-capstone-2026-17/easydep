from app.core.orchestration.capacity_estimation import (
    GIB,
    estimate_capacity_floor,
    recommend_measured_capacity,
)


def test_measured_slo_point_produces_vm_and_disk_floors():
    result = estimate_capacity_floor(
        {
            "measurementId": "benchmark-1",
            "sustainableRpsPerInstance": 120,
            "p95LatencyMs": 80,
            "errorRate": 0.001,
            "p95CpuCores": 1.2,
            "p99RssBytes": 1.5 * GIB,
            "currentDataBytes": 2 * GIB,
            "bytesGrowthPerDurableWrite": 2048,
        },
        {
            "targetRps": 200,
            "maxP95LatencyMs": 150,
            "maxErrorRate": 0.01,
            "horizontallyScalable": True,
            "cpuUtilizationTarget": 0.70,
            "memoryHeadroomFactor": 1.25,
            "persistentDataRequired": True,
            "targetDurableWriteRps": 20,
            "retentionHours": 24 * 30,
            "diskHeadroomFactor": 1.30,
            "explicitMinimumDataDiskGiB": 20,
        },
    )

    assert result["status"] == "estimated"
    assert result["minimumInstances"] == 2
    assert result["resourceSpecPatch"]["minVCpu"] == 2
    assert result["resourceSpecPatch"]["minMemoryGiB"] == 2.0
    assert result["resourceSpecPatch"]["minDataDiskGiB"] >= 20


def test_single_instance_is_not_extrapolated_past_measured_capacity():
    result = estimate_capacity_floor(
        {
            "sustainableRpsPerInstance": 100,
            "p95LatencyMs": 50,
            "errorRate": 0,
            "p95CpuCores": 1,
            "p99RssBytes": GIB,
        },
        {
            "targetRps": 200,
            "maxP95LatencyMs": 100,
            "horizontallyScalable": False,
        },
    )

    assert result["status"] == "deferred"
    assert result["reason"] == "target_exceeds_measured_single_instance_capacity"


def test_slo_violating_measurement_is_not_used_for_sizing():
    result = estimate_capacity_floor(
        {
            "sustainableRpsPerInstance": 100,
            "p95LatencyMs": 300,
            "errorRate": 0.02,
        },
        {"targetRps": 80, "maxP95LatencyMs": 100, "maxErrorRate": 0.01},
    )

    assert result["status"] == "deferred"
    assert result["reason"] == "benchmark_point_violates_slo"


def test_persistent_disk_requires_growth_and_retention_evidence():
    result = estimate_capacity_floor(
        {
            "sustainableRpsPerInstance": 100,
            "p95LatencyMs": 50,
            "errorRate": 0,
            "p95CpuCores": 1,
            "p99RssBytes": GIB,
        },
        {
            "targetRps": 80,
            "maxP95LatencyMs": 100,
            "persistentDataRequired": True,
        },
    )

    assert result["status"] == "deferred"
    assert result["reason"] == "missing_disk_growth_measurement"


def test_measured_floor_drives_catalog_selection_and_instance_count():
    result = recommend_measured_capacity(
        {
            "measurementId": "measured-development-point",
            "sustainableRpsPerInstance": 120,
            "p95LatencyMs": 80,
            "errorRate": 0.001,
            "p95CpuCores": 1.2,
            "p99RssBytes": 1.5 * GIB,
        },
        {
            "targetRps": 200,
            "maxP95LatencyMs": 150,
            "maxErrorRate": 0.01,
            "horizontallyScalable": True,
        },
        {
            "provider": "aws",
            "region": "ap-northeast-2",
            "monthlyBudgetUSD": 500,
            "trafficPattern": "steady",
        },
    )

    assert result["status"] == "selected"
    assert result["effectiveResourceSpec"]["minVCpu"] == 2
    assert result["effectiveResourceSpec"]["minMemoryGiB"] == 2.0
    assert result["vmSelection"]["constraints"]["minimumVmCount"] == 2


def test_deferred_measurement_never_reaches_price_selection():
    result = recommend_measured_capacity(
        {},
        {},
        {"provider": "aws", "region": "ap-northeast-2"},
    )

    assert result["status"] == "deferred"
    assert result["vmSelection"] is None
