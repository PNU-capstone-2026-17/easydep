"""제품 평가 manifest 여러 개를 완주율과 분포 보고서로 합친다."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from statistics import median
from typing import Any

JsonObject = dict[str, Any]

_PROVENANCE_FIELDS = (
    "profile",
    "targetStage",
    "commit",
    "settingsDigest",
    "declaredModel",
    "declaredProvider",
    "datasetPartition",
    "serverConfigurationVerified",
)


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    """작은 표본도 양 끝값을 과장하지 않는 선형 보간 percentile을 계산한다."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return float(ordered[lower] * (1 - fraction) + ordered[upper] * fraction)


def _distribution(values: Sequence[float], total_runs: int) -> JsonObject:
    """측정된 표본 수를 함께 표시해 실패 실행이 조용히 빠지는 일을 막는다."""
    return {
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "sampleCount": len(values),
        "unavailableCount": max(0, total_runs - len(values)),
    }


def _load(paths: Iterable[Path]) -> list[JsonObject]:
    manifests: list[JsonObject] = []
    for path in paths:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise TypeError(f"manifest는 JSON 객체여야 합니다: {path}")
        manifests.append(value)
    return manifests


def _provenance(manifest: Mapping[str, Any]) -> JsonObject:
    """서로 비교해도 되는 실행인지 판단할 출처 정보를 꺼낸다.

    provider, model, settings는 CLI가 서버에 적용하거나 서버에서 확인한
    값이 아니다. 실행자가 비교용으로 붙인 label이므로, 보고서에서는
    declared 접두어를 사용해 이 차이를 드러낸다.
    """
    profile = manifest.get("profile")
    profile_map = profile if isinstance(profile, Mapping) else {}
    environment = manifest.get("environment")
    environment_map = environment if isinstance(environment, Mapping) else {}
    dataset = manifest.get("dataset")
    dataset_map = dataset if isinstance(dataset, Mapping) else {}
    return {
        "profile": profile_map.get("name"),
        "targetStage": profile_map.get("targetStage"),
        "commit": environment_map.get("commit"),
        "settingsDigest": environment_map.get("settingsDigest"),
        "declaredModel": environment_map.get("model"),
        "declaredProvider": environment_map.get("provider"),
        "datasetPartition": dataset_map.get("partition"),
        # 기존 manifest에 필드가 없으면 현재 CLI 동작과 같이
        # 서버에서 검증하지 않은 값으로 해석한다.
        "serverConfigurationVerified": bool(
            environment_map.get("serverConfigurationVerified", False)
        ),
    }


def _shared_provenance(manifests: Sequence[Mapping[str, Any]]) -> JsonObject:
    """집계 조건이 다른 manifest를 실수로 합치지 않도록 검사한다."""
    if not manifests:
        return dict.fromkeys(_PROVENANCE_FIELDS)
    provenances = [_provenance(manifest) for manifest in manifests]
    mixed: list[str] = []
    for field in _PROVENANCE_FIELDS:
        values = {json.dumps(item[field], ensure_ascii=False) for item in provenances}
        if len(values) > 1:
            mixed.append(field)
    if mixed:
        fields = ", ".join(mixed)
        raise ValueError(
            "비교 조건이 다른 manifest는 한 보고서로 합칠 수 없습니다. "
            f"조건별로 나눠 집계하세요: {fields}"
        )
    return provenances[0]


def aggregate_manifests(paths: Iterable[Path]) -> JsonObject:
    """완주율, 실패 단계, 시간·token·repair 분포를 한 JSON으로 만든다."""
    manifests = _load(paths)
    provenance = _shared_provenance(manifests)
    total = len(manifests)
    completed = sum(manifest.get("status") == "COMPLETED" for manifest in manifests)
    stage_failures = Counter[str]()
    wall_values: list[float] = []
    token_values: list[float] = []
    repair_values: list[float] = []
    unavailable_reasons = Counter[str]()
    cache = Counter[str]()
    provider_errors = Counter[str]()
    stage_wall: defaultdict[str, list[float]] = defaultdict(list)

    for manifest in manifests:
        failure = manifest.get("firstFailure")
        if isinstance(failure, Mapping):
            stage_failures[str(failure.get("stage") or "unknown")] += 1
        wall = manifest.get("wallSeconds")
        if isinstance(wall, (int, float)):
            wall_values.append(float(wall))
        else:
            unavailable_reasons["전체 wall time이 없습니다."] += 1

        llm = manifest.get("llm")
        llm_map = llm if isinstance(llm, Mapping) else {}
        tokens = llm_map.get("totalTokens")
        if isinstance(tokens, (int, float)):
            token_values.append(float(tokens))
        else:
            unavailable_reasons["전체 token 사용량이 없습니다."] += 1
        repairs = llm_map.get("repairs")
        repair_map = repairs if isinstance(repairs, Mapping) else {}
        repair_total = repair_map.get("total")
        if isinstance(repair_total, (int, float)):
            repair_values.append(float(repair_total))
        else:
            unavailable_reasons["repair 횟수가 없습니다."] += 1
        for reason in llm_map.get("measuredUnavailable") or []:
            unavailable_reasons[str(reason)] += 1
        for section, counter in (("cache", cache), ("providerErrors", provider_errors)):
            values = llm_map.get(section)
            if isinstance(values, Mapping):
                for key, value in values.items():
                    if isinstance(value, (int, float)):
                        counter[str(key)] += int(value)

        # 하나의 run이 재개되면 같은 단계가 여러 attempt에 나눕 수 있다.
        # 각 attempt를 별도 표본으로 세면 자주 재개된 run이 분포에 더 큰
        # 영향을 준다. 그러므로 run 안에서 단계별 시간을 먼저 더한다.
        run_stage_wall: defaultdict[str, float] = defaultdict(float)
        timings = manifest.get("stageTimings")
        if isinstance(timings, list):
            for timing in timings:
                if not isinstance(timing, Mapping):
                    continue
                elapsed = timing.get("wallSeconds")
                if isinstance(elapsed, (int, float)):
                    run_stage_wall[str(timing.get("stage") or "unknown")] += float(
                        elapsed
                    )
        for stage, elapsed in run_stage_wall.items():
            stage_wall[stage].append(elapsed)

    reported_stages = set(stage_wall)
    target_stage = provenance.get("targetStage")
    if isinstance(target_stage, str) and target_stage:
        # 모든 run이 목표 단계 전에 실패해도 그 단계를 보고서에 남겨야
        # "0초" 문제와 "측정하지 못함" 문제를 구분할 수 있다.
        reported_stages.add(target_stage)

    return {
        "schemaVersion": "easydep-product-evaluation-report/v1",
        "provenance": {
            **provenance,
            "metadataMeaning": (
                "provider, model, settingsDigest는 CLI에 제공한 비교용 label이며 "
                "서버에 적용됐는지 확인한 값이 아닙니다."
            ),
        },
        "runCount": total,
        "completedCount": completed,
        "completionRate": (completed / total) if total else None,
        "stageFailures": dict(sorted(stage_failures.items())),
        "stageFailureRates": {
            stage: count / total
            for stage, count in sorted(stage_failures.items())
        }
        if total
        else {},
        "wallSeconds": _distribution(wall_values, total),
        "totalTokens": _distribution(token_values, total),
        "repairMedian": median(repair_values) if repair_values else None,
        "repairSampleCount": len(repair_values),
        "stageWallSeconds": {
            stage: _distribution(stage_wall.get(stage, []), total)
            for stage in sorted(reported_stages)
        },
        "cache": dict(sorted(cache.items())),
        "providerErrors": dict(sorted(provider_errors.items())),
        "measuredUnavailableReasons": dict(sorted(unavailable_reasons.items())),
        "runs": [
            {
                "runId": manifest.get("runId"),
                "dataset": (manifest.get("dataset") or {}).get("id")
                if isinstance(manifest.get("dataset"), Mapping)
                else None,
                "status": manifest.get("status"),
                "finalStage": manifest.get("finalStage"),
                **_provenance(manifest),
            }
            for manifest in manifests
        ],
    }
