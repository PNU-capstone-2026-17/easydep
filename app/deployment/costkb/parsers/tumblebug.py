"""cb-tumblebug `spec_infos` 행 → costkb 레코드 투영 (순수 함수).

**왜 이 소스인가 — 미러여야 하기 때문이다.**
우리 에이전트의 런타임 경로는 cb-tumblebug MCP의 `recommend_vm_spec`이고, 코드를 추적하면:

    recommend_vm_spec (tb-mcp.py)
      → POST /recommendSpec              (server.go)
      → infra.RecommendSpec
      → resource.FilterSpecsByRange      (평범한 GORM WHERE/ORDER BY)
      → spec_infos                       (spec.go에 raw SQL로 하드코딩)

MCP는 컬럼을 **그대로 투영만** 한다(호출 시점 계산 0). 그래서 같은 테이블에서 빌드하면
오프라인 기준선과 라이브 경로가 같은 세계를 본다.

**AWS/Azure 공개 API에서 직접 빌드하면 오히려 불일치가 생긴다.** 아래 메모리 버그가 그
증거다 — 라이브 MCP는 버그 있는 값으로 필터링하므로, 우리만 "정확"하면 같은 질문에 다른
답을 내게 된다.

## 상위 버그를 일부러 재현한다

CB-Spider의 `ConvertMBToMiBInt64`는 `mb * 1000 / 1024`로 비율을 제곱이 아니라 한 번만
적용하고, 그것도 이미 MiB인 값에 적용한다. 순효과는 `참값 / 1.024`다.

덤프 73,083행 실측으로 확인한 영향 범위 (`×1.024 하면 정수가 되는 비율`):

    gcp   77.6%   azure 64.2%    ← 버그 영향
    aws    0.0%   tencent 0.0%   ibm 0.0%   ncp 0.0%   nhn 0.0%
    alibaba 0.0%  kt 0.0%        openstack 0.0%        ← 정상

즉 **gcp/azure만** 보정 대상이다(코드 추적 결과와 실측이 일치). `memGiB`는 미러값 그대로
두어 필터·판정이 MCP와 일치하게 하고, `memGiBActual`에 보정값을 병기해 사람이 볼 때는
진실을 보여준다. **`memGiB`를 "고치면" 미러가 깨진다.**
"""

from __future__ import annotations

import collections
from typing import Any

SOURCE_NOTE = (
    "cb-tumblebug의 spec_infos 테이블(assets.dump.gz) 미러입니다. 에이전트의 라이브 경로인 "
    "cb-tumblebug MCP recommend_vm_spec이 같은 테이블을 읽으므로 두 경로의 답이 일치합니다. "
    "memGiB는 Tumblebug 기준값이며, 상위 CB-Spider 버그로 GCP·Azure는 실제보다 2.4% 낮습니다"
    "(memGiBActual이 보정값). 가격은 스냅샷이라 시간이 지나면 드리프트하며 실제 청구서가 "
    "아닙니다. 라이브 정확도가 필요하면 cb-tumblebug MCP를 쓰세요."
)

# spec_infos의 namespace. 실측상 73,083행 전부 'system'이라 사실상 no-op이지만,
# REST 핸들러가 model.SystemCommonNs를 하드코딩하므로 명시적으로 맞춰 둔다.
SYSTEM_NAMESPACE = "system"

# CB-Spider 버그(ConvertMBToMiBInt64)의 영향을 받는 프로바이더. 코드 추적 + 덤프 실측 일치.
_MEMORY_BUG_PROVIDERS = frozenset({"gcp", "azure"})
_MEMORY_BUG_FACTOR = 1.024

# 스키마 enum과 같아야 한다. 새 프로바이더가 오면 조용히 흘리지 않고 경고한다.
KNOWN_PROVIDERS = frozenset(
    {"aws", "azure", "gcp", "tencent", "alibaba", "ibm", "ncp", "kt", "nhn", "openstack"}
)


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    number = _num(value)
    return None if number is None else int(number)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def correct_memory(provider: str, mem_gib: float) -> float:
    """상위 버그로 낮게 기록된 메모리를 복원한다 (gcp/azure만).

    보정 대상이 아닌 프로바이더는 그대로 돌려준다 — 실측상 정상이기 때문이다.
    """
    if provider in _MEMORY_BUG_PROVIDERS:
        corrected = mem_gib * _MEMORY_BUG_FACTOR
        # 62.5 * 1.024 = 64.0 처럼 정수로 떨어지는 게 정상. 부동소수 잡음만 정리한다.
        rounded = round(corrected)
        return float(rounded) if abs(corrected - rounded) < 1e-6 else round(corrected, 4)
    return mem_gib


def project_row(row: dict) -> dict | None:
    """spec_infos 행 하나를 costkb 레코드로. 쓸 수 없는 행은 None."""
    provider = _text(row.get("provider_name"))
    region = _text(row.get("region_name"))
    spec_name = _text(row.get("csp_spec_name"))
    vcpu = _int(row.get("v_cpu"))
    mem = _num(row.get("memory_gi_b"))

    if not (provider and region and spec_name) or not vcpu or vcpu < 1:
        return None
    if mem is None or mem <= 0:
        return None

    # Tumblebug의 정렬 SQL이 `CASE WHEN cost_per_hour > 0 ... ELSE 999999`이므로
    # 0도 '가격 미상'이지 무료가 아니다. 실측상 0이 6건 실재한다.
    cost = _num(row.get("cost_per_hour"))
    hourly = cost if cost is not None and cost > 0 else None

    record: dict[str, Any] = {
        "id": _text(row.get("id")),
        "provider": provider,
        "region": region,
        "specName": spec_name,
        "vCPU": vcpu,
        "memGiB": mem,
        "memGiBActual": correct_memory(provider, mem),
        "hourlyUSD": hourly,
        "architecture": _text(row.get("architecture")),
        "infraType": _text(row.get("infra_type")),
        "diskSizeGB": _num(row.get("disk_size_gb")),
        "acceleratorType": _text(row.get("accelerator_type")),
        "acceleratorModel": _text(row.get("accelerator_model")),
        "acceleratorCount": _int(row.get("accelerator_count")),
        "acceleratorMemoryGB": _num(row.get("accelerator_memory_gb")),
    }
    return {k: v for k, v in record.items() if v is not None or k == "hourlyUSD"}


def project_rows(rows, *, namespace: str | None = SYSTEM_NAMESPACE) -> tuple[list[dict], dict]:
    """행들을 레코드로 투영하고 감사 통계를 함께 반환한다.

    Args:
        rows: spec_infos 행 dict의 이터러블.
        namespace: 이 namespace의 행만 쓴다. None이면 전체.

    Returns:
        (레코드 목록, 감사 통계). 통계는 빌드 요약과 상위 버그 감시에 쓴다.
    """
    specs: list[dict] = []
    stats: dict[str, Any] = {
        "total_rows": 0,
        "skipped_namespace": 0,
        "skipped_invalid": 0,
        "unknown_providers": collections.Counter(),
        "memory_audit": collections.defaultdict(
            lambda: {"n": 0, "integral": 0, "bug_fingerprint": 0}
        ),
    }

    for row in rows:
        stats["total_rows"] += 1
        if namespace is not None and _text(row.get("namespace")) != namespace:
            stats["skipped_namespace"] += 1
            continue
        record = project_row(row)
        if record is None:
            stats["skipped_invalid"] += 1
            continue
        provider = record["provider"]
        if provider not in KNOWN_PROVIDERS:
            stats["unknown_providers"][provider] += 1

        # 상위 버그 감시: ×1.024로 정수가 되면 버그 지문. 새 프로바이더가 버그 영향권에
        # 들어오면 여기서 드러난다 (보정 대상을 코드로 못 박아 두었으므로).
        audit = stats["memory_audit"][provider]
        mem = record["memGiB"]
        audit["n"] += 1
        if mem == int(mem):
            audit["integral"] += 1
        elif abs(mem * _MEMORY_BUG_FACTOR - round(mem * _MEMORY_BUG_FACTOR)) < 1e-6:
            audit["bug_fingerprint"] += 1

        specs.append(record)

    stats["memory_audit"] = dict(stats["memory_audit"])
    return specs, stats


def build_dataset(rows, *, namespace: str | None = SYSTEM_NAMESPACE) -> tuple[dict, dict]:
    """`costkb/schema.json` 모양의 데이터셋과 감사 통계를 만든다."""
    specs, stats = project_rows(rows, namespace=namespace)
    return {"_note": SOURCE_NOTE, "specs": specs}, stats


def format_audit(stats: dict) -> str:
    """빌드 요약용 텍스트 — 특히 메모리 버그 영향 범위를 눈에 보이게."""
    lines = [
        f"행 {stats['total_rows']:,} → 레코드 {stats['total_rows'] - stats['skipped_namespace'] - stats['skipped_invalid']:,}"
        f" (namespace 제외 {stats['skipped_namespace']:,}, 무효 {stats['skipped_invalid']:,})",
        "프로바이더별 메모리 감사 (bug=×1.024하면 정수 → 상위 버그 지문):",
    ]
    for provider, audit in sorted(
        stats["memory_audit"].items(), key=lambda kv: -kv[1]["n"]
    ):
        n = audit["n"]
        flag = " ← 보정 적용" if provider in _MEMORY_BUG_PROVIDERS else ""
        lines.append(
            f"  {provider:10} n={n:6,}  정수 {audit['integral'] / n:5.1%}  "
            f"bug {audit['bug_fingerprint'] / n:5.1%}{flag}"
        )
    if stats["unknown_providers"]:
        lines.append(
            f"⚠️ 스키마에 없는 프로바이더: {dict(stats['unknown_providers'])} "
            "→ costkb/schema.json의 provider enum과 tumblebug.py의 KNOWN_PROVIDERS를 넓히세요."
        )
    return "\n".join(lines)
