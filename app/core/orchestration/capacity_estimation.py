"""부하 측정 증거를 VM·디스크 최소 용량 하한으로 변환한다."""

from __future__ import annotations

import math
from typing import Any

from app.core.orchestration.vm_selection import select_vm_candidates

GIB = 1024**3


def _positive(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def estimate_capacity_floor(
    measurement: dict[str, Any], target: dict[str, Any]
) -> dict[str, Any]:
    """측정 범위를 넘겨 추측하지 않고 보수적인 리소스 하한을 계산한다."""
    achieved_rps = _positive(measurement.get("sustainableRpsPerInstance"))
    target_rps = _positive(target.get("targetRps"))
    p95_latency = _positive(measurement.get("p95LatencyMs"))
    maximum_p95 = _positive(target.get("maxP95LatencyMs"))
    error_rate = measurement.get("errorRate")
    maximum_error_rate = target.get("maxErrorRate", 0.01)
    required = {
        "sustainableRpsPerInstance": achieved_rps,
        "targetRps": target_rps,
        "p95LatencyMs": p95_latency,
        "maxP95LatencyMs": maximum_p95,
    }
    if any(value is None for value in required.values()):
        return {
            "schemaVersion": "easydep-capacity-floor/v1",
            "status": "deferred",
            "reason": "missing_load_or_slo_measurement",
            "required": required,
        }
    if not isinstance(error_rate, (int, float)) or not isinstance(
        maximum_error_rate, (int, float)
    ):
        return {
            "schemaVersion": "easydep-capacity-floor/v1",
            "status": "deferred",
            "reason": "missing_error_rate_measurement",
        }
    if p95_latency > maximum_p95 or float(error_rate) > float(maximum_error_rate):
        return {
            "schemaVersion": "easydep-capacity-floor/v1",
            "status": "deferred",
            "reason": "benchmark_point_violates_slo",
            "observed": {
                "p95LatencyMs": p95_latency,
                "errorRate": float(error_rate),
            },
        }

    horizontally_scalable = target.get("horizontallyScalable") is True
    if target_rps > achieved_rps and not horizontally_scalable:
        return {
            "schemaVersion": "easydep-capacity-floor/v1",
            "status": "deferred",
            "reason": "target_exceeds_measured_single_instance_capacity",
            "needsQuestion": True,
        }
    minimum_instances = max(1, math.ceil(target_rps / achieved_rps))
    cpu_utilization_target = _positive(target.get("cpuUtilizationTarget")) or 0.70
    p95_cpu_cores = _positive(measurement.get("p95CpuCores"))
    p99_rss_bytes = _positive(measurement.get("p99RssBytes"))
    if p95_cpu_cores is None or p99_rss_bytes is None:
        return {
            "schemaVersion": "easydep-capacity-floor/v1",
            "status": "deferred",
            "reason": "missing_cpu_or_memory_measurement",
        }
    memory_headroom = _positive(target.get("memoryHeadroomFactor")) or 1.25
    minimum_vcpu = max(1, math.ceil(p95_cpu_cores / min(cpu_utilization_target, 1.0)))
    minimum_memory_gib = math.ceil((p99_rss_bytes * memory_headroom / GIB) * 4) / 4

    disk = _disk_floor(measurement, target)
    if disk.get("status") == "deferred" and target.get("persistentDataRequired") is True:
        return {
            "schemaVersion": "easydep-capacity-floor/v1",
            "status": "deferred",
            "reason": disk["reason"],
        }
    resource_patch: dict[str, Any] = {
        "minVCpu": minimum_vcpu,
        "minMemoryGiB": minimum_memory_gib,
    }
    if disk.get("minimumDataDiskGiB") is not None:
        resource_patch["minDataDiskGiB"] = disk["minimumDataDiskGiB"]
    return {
        "schemaVersion": "easydep-capacity-floor/v1",
        "status": "estimated",
        "method": "measured-slo-point-with-headroom",
        "minimumInstances": minimum_instances,
        "resourceSpecPatch": resource_patch,
        "disk": disk,
        "evidence": {
            "measurementId": measurement.get("measurementId"),
            "sustainableRpsPerInstance": achieved_rps,
            "p95LatencyMs": p95_latency,
            "errorRate": float(error_rate),
            "p95CpuCores": p95_cpu_cores,
            "p99RssBytes": p99_rss_bytes,
            "cpuUtilizationTarget": cpu_utilization_target,
            "memoryHeadroomFactor": memory_headroom,
        },
        "limitations": [
            "동일 이미지·런타임·부하 형태에서 측정한 개발 하한이다.",
            "클라우드 VM 세대·CPU 모델·네트워크 차이는 실제 배포 후 재측정해야 한다.",
        ],
    }


def recommend_measured_capacity(
    measurement: dict[str, Any],
    target: dict[str, Any],
    resource_spec: dict[str, Any],
    deployment_needs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """측정 하한을 기존 가격 카탈로그 선택기에 손실 없이 전달한다."""
    floor = estimate_capacity_floor(measurement, target)
    if floor.get("status") != "estimated":
        return {
            "schemaVersion": "easydep-measured-capacity-recommendation/v1",
            "status": "deferred",
            "capacityFloor": floor,
            "vmSelection": None,
        }

    merged_spec = {**resource_spec, **floor["resourceSpecPatch"]}
    needs = dict(deployment_needs or {})
    needs["measured-capacity"] = {
        "decision": "accepted",
        "metadata": {"minimum_instances": floor["minimumInstances"]},
    }
    selection = select_vm_candidates(merged_spec, needs)
    return {
        "schemaVersion": "easydep-measured-capacity-recommendation/v1",
        "status": selection["status"],
        "capacityFloor": floor,
        "effectiveResourceSpec": merged_spec,
        "vmSelection": selection,
        "costScope": "on-demand-compute-list-price-only",
    }


def _disk_floor(measurement: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    if target.get("persistentDataRequired") is not True:
        return {"status": "not-required", "minimumDataDiskGiB": None}
    current_bytes = _positive(measurement.get("currentDataBytes"))
    bytes_per_write = _positive(measurement.get("bytesGrowthPerDurableWrite"))
    write_rps = _positive(target.get("targetDurableWriteRps"))
    retention_hours = _positive(target.get("retentionHours"))
    if None in (current_bytes, bytes_per_write, write_rps, retention_hours):
        return {"status": "deferred", "reason": "missing_disk_growth_measurement"}
    disk_headroom = _positive(target.get("diskHeadroomFactor")) or 1.30
    projected = current_bytes + bytes_per_write * write_rps * retention_hours * 3600
    minimum_gib = max(1, math.ceil(projected * disk_headroom / GIB))
    explicit_floor = _positive(target.get("explicitMinimumDataDiskGiB"))
    if explicit_floor is not None:
        minimum_gib = max(minimum_gib, math.ceil(explicit_floor))
    return {
        "status": "estimated",
        "minimumDataDiskGiB": minimum_gib,
        "projectedBytesBeforeHeadroom": math.ceil(projected),
        "diskHeadroomFactor": disk_headroom,
    }
