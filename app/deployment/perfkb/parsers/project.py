"""`spec_infos` 행 → 성능 레코드 (순수 투영).

이 파일은 덤프도 파일도 모른다 — dict를 받아 dict를 준다. 그래서 34MB 덤프 없이
행 모양 fixture로 전부 테스트할 수 있다(`tests/test_perfkb_projection.py`).

## sustainedCpu — 세 프로바이더를 하나로 묶지 않는 이유

사용자가 실제로 묻는 건 "이거 상시 성능이 보장돼?"다. 그런데 그 답을 얻는 경로가
프로바이더마다 **다른 개념**이라, `burstable` 같은 필드 하나로 묶으면 거짓말이 된다:

| | 메커니즘 | 근거 | 신뢰도 |
|---|---|---|---|
| AWS t계열 | CPU 크레딧 소진 시 baseline 저하 | `BurstablePerformanceSupported` (명시 필드) | 1.0 |
| GCP 공유코어 | vCPU 자체를 공유 (크레딧 아님) | `IsSharedCpu` (명시 필드) | 1.0 |
| Azure B계열 | 크레딧 모델 (AWS와 유사) | `Family` 이름 패턴 **추론** | 0.8 |

그래서 값(`value`)과 함께 **왜 그렇게 판단했는지**(`note`)와 **어떻게 알았는지**
(`evidence`/`basis`)를 항상 같이 준다. capacitykb와 같은 패턴이다.
costkb가 evidence를 안 두는 것과 대조된다 — 거긴 출처가 파일 단위로 균일한 미러라
레코드별 evidence가 죽은 필드가 되지만, 여기선 같은 필드가 1.0에서도 0.8에서도 온다.

## Azure B계열 판정의 취약점

`Family`는 100% 채워져 있지만 표기가 섞여 있다(`standardBsv2Family` vs
`StandardFXmsv2Family`). 실측상 B계열은 전부 소문자로 시작했지만 그건 **운**이므로
대소문자를 무시한다. 또한 이 판정은 "B로 시작하면 버스트"라는 이름 규칙에 기대므로,
B가 아닌 새 버스트 패밀리가 생기면 놓친다 — 그래서 신뢰도가 0.8이다.
"""

from __future__ import annotations

import re
from kbcommon.basis import basis_of

from .details import (
    go_bool,
    go_field,
    go_number,
    is_burst_bandwidth,
    parse_details,
)

# costkb와 같은 값이지만 **의도적으로 복제한다** — KB끼리는 서로 import하지 않는다
# (graphkb·capacitykb도 그렇다). 한 줄 중복이 패키지 간 결합보다 싸다.
SYSTEM_NAMESPACE = "system"

_AZURE_BURST_FAMILY = re.compile(r"^standardB", re.IGNORECASE)

_NOTE_AWS_BURST = "버스트 인스턴스 — CPU 크레딧이 소진되면 baseline 성능으로 떨어집니다."
_NOTE_GCP_SHARED = "공유 코어 — vCPU를 다른 인스턴스와 공유하므로 성능이 일정하지 않습니다."
_NOTE_AZURE_BURST = "B계열(버스트) — 크레딧 모델이라 상시 부하에서 성능이 떨어집니다."


def _sustained_cpu(provider: str, det: dict[str, str]) -> dict | None:
    """상시 CPU 성능이 보장되나. 판단 근거를 함께 반환한다. 모르면 None."""
    if provider == "aws":
        burst = go_bool(det.get("BurstablePerformanceSupported"))
        if burst is None:
            return None
        return {
            "value": not burst,
            "note": _NOTE_AWS_BURST if burst else None,
            "evidence": "aws-burstable-field",
            "basis": basis_of("aws-burstable-field"),
        }
    if provider == "gcp":
        shared = go_bool(det.get("IsSharedCpu"))
        if shared is None:
            return None
        return {
            "value": not shared,
            "note": _NOTE_GCP_SHARED if shared else None,
            "evidence": "gcp-shared-cpu-field",
            "basis": basis_of("gcp-shared-cpu-field"),
        }
    if provider == "azure":
        family = det.get("Family")
        if not family:
            return None
        burst = bool(_AZURE_BURST_FAMILY.match(family))
        return {
            "value": not burst,
            "note": _NOTE_AZURE_BURST if burst else None,
            # 이름 규칙 추론이라 짐작이다 — B가 아닌 버스트 패밀리가 생기면 놓친다.
            "evidence": "azure-family-name",
            "basis": basis_of("azure-family-name"),
        }
    return None  # 나머지 7개 프로바이더는 신호를 추적하지 못했다 — 모른다고 둔다


def _aws_fields(det: dict[str, str]) -> dict:
    ebs = det.get("EbsInfo")
    net = det.get("NetworkInfo")
    perf = go_field(net, "NetworkPerformance")
    return {
        "currentGeneration": go_bool(det.get("CurrentGeneration")),
        "clockGHz": go_number(det.get("ProcessorInfo"), "SustainedClockSpeedInGhz"),
        "threadsPerCore": go_number(det.get("VCpuInfo"), "DefaultThreadsPerCore"),
        "networkPerformance": perf,
        "networkIsBurst": is_burst_bandwidth(perf),
        "ebsBaselineMbps": go_number(ebs, "BaselineBandwidthInMbps"),
        "ebsMaxMbps": go_number(ebs, "MaximumBandwidthInMbps"),
        "ebsBaselineIops": go_number(ebs, "BaselineIops"),
        "ebsMaxIops": go_number(ebs, "MaximumIops"),
        "bareMetal": go_bool(det.get("BareMetal")),
    }


def _azure_fields(det: dict[str, str]) -> dict:
    def num(key: str) -> float | None:
        raw = det.get(key)
        try:
            return float(raw) if raw not in (None, "") else None
        except ValueError:
            return None

    return {
        # ACU는 37.7%만 채워져 있고 결측이 세대로 설명되지 않는다(로드맵 4-2절).
        # 없으면 "성능이 나쁘다"가 아니라 "모른다"다.
        "acu": num("ACUs"),
        "threadsPerCore": num("vCPUsPerCore"),
        "diskIops": num("UncachedDiskIOPS"),
        "cachedDiskIops": num("CombinedTempDiskAndCachedIOPS"),
        "acceleratedNetworking": go_bool(det.get("AcceleratedNetworkingEnabled")),
        "premiumIO": go_bool(det.get("PremiumIO")),
        "family": det.get("Family") or None,
    }


def _gcp_fields(det: dict[str, str]) -> dict:
    def num(key: str) -> float | None:
        raw = det.get(key)
        try:
            return float(raw) if raw not in (None, "") else None
        except ValueError:
            return None

    return {
        "maxPersistentDisks": num("MaximumPersistentDisks"),
        "maxPersistentDiskGB": num("MaximumPersistentDisksSizeGb"),
        # GCP는 세대를 명시하지 않는다. 이름 접두(n1/c4…)로 추론할 수 있지만
        # 그건 추측이라 Phase 1에서는 하지 않는다 — currentGeneration은 null로 둔다.
        "vendorDescription": det.get("Description") or None,
    }


_BY_PROVIDER = {"aws": _aws_fields, "azure": _azure_fields, "gcp": _gcp_fields}


def project_row(row: dict, *, namespace: str | None = SYSTEM_NAMESPACE) -> dict | None:
    """`spec_infos` 행 → 성능 레코드. 성능 신호가 하나도 없으면 None.

    Args:
        row: spec_infos 행 dict.
        namespace: 이 namespace의 행만 쓴다. None이면 전체.
    """
    if namespace is not None and (row.get("namespace") or "").strip() != namespace:
        return None
    provider = (row.get("provider_name") or "").strip().lower()
    spec_id = (row.get("id") or "").strip()
    spec_name = (row.get("csp_spec_name") or "").strip()
    if not provider or not spec_id or not spec_name:
        return None

    det = parse_details(row.get("details"))
    if not det:
        return None

    record: dict = {"id": spec_id, "provider": provider, "specName": spec_name}
    sustained = _sustained_cpu(provider, det)
    if sustained is not None:
        record["sustainedCpu"] = sustained
    record.update(_BY_PROVIDER.get(provider, lambda _: {})(det))

    # None 필드는 버린다 — "모른다"를 저장할 필요는 없다(없는 키 = 모름).
    record = {k: v for k, v in record.items() if v is not None}
    # id/provider/specName 말고 실제 성능 신호가 하나도 없으면 레코드가 무의미하다.
    if len(record) <= 3:
        return None
    return record
