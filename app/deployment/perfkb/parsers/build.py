"""행 묶음 → 데이터셋 + 감사 통계.

감사가 있는 이유는 costkb와 같다 — **커버리지 공백이 조용히 지나가면 안 된다.**
성능은 특히 그렇다. "신호가 없다"를 "문제가 없다"로 읽으면 t3a.medium을 그대로 1순위로
추천하게 된다. 그래서 빌드할 때마다 프로바이더별로 *무엇을 알고 무엇을 모르는지*를
숫자로 보여준다.
"""

from __future__ import annotations

import collections
from typing import Any

from .project import SYSTEM_NAMESPACE, project_row

SOURCE_NOTE = (
    "cb-tumblebug spec_infos의 details 컬럼(CSP 원본 응답) 미러입니다. costkb와 같은 행에서 "
    "나오며 id로 조인됩니다. **프로바이더 간 성능 비교는 불가능합니다** — ACU는 Azure에만, "
    "클럭은 AWS에만 있습니다. Azure ACU는 37.7%만 채워져 있고 결측이 세대로 설명되지 않으므로, "
    "값이 없으면 '느리다'가 아니라 '모른다'입니다. 상시 CPU 판정은 프로바이더마다 다른 "
    "메커니즘에서 오므로 레코드마다 근거(evidence)와 그 성격(basis)을 함께 담습니다."
)

# 성능 신호를 추적한 프로바이더. 나머지 7개는 details 구조를 조사하지 않아 '모름'으로 둔다.
KNOWN_PROVIDERS = frozenset({"aws", "azure", "gcp"})

_SIGNALS = ("sustainedCpu", "currentGeneration", "clockGHz", "acu", "ebsBaselineMbps")


def project_rows(rows, *, namespace: str | None = SYSTEM_NAMESPACE) -> tuple[list[dict], dict]:
    """행들을 성능 레코드로 투영하고 감사 통계를 함께 반환한다."""
    records: list[dict] = []
    stats: dict[str, Any] = {
        "total_rows": 0,
        "skipped_no_signal": 0,
        "untracked_providers": collections.Counter(),
        "coverage": collections.defaultdict(collections.Counter),
        "findings": collections.defaultdict(collections.Counter),
    }

    for row in rows:
        stats["total_rows"] += 1
        provider = (row.get("provider_name") or "").strip().lower()
        record = project_row(row, namespace=namespace)
        if record is None:
            stats["skipped_no_signal"] += 1
            if provider and provider not in KNOWN_PROVIDERS:
                stats["untracked_providers"][provider] += 1
            continue

        records.append(record)
        cov = stats["coverage"][record["provider"]]
        cov["n"] += 1
        for signal in _SIGNALS:
            if signal in record:
                cov[signal] += 1

        # 실제로 경고를 낳는 것들 — 이 숫자가 0이면 파서가 고장난 것이다.
        find = stats["findings"][record["provider"]]
        sustained = record.get("sustainedCpu")
        if sustained and sustained["value"] is False:
            find["not_sustained"] += 1
        if record.get("currentGeneration") is False:
            find["old_generation"] += 1
        if record.get("networkIsBurst") is True:
            find["burst_network"] += 1

    return records, stats


def build_dataset(rows, *, namespace: str | None = SYSTEM_NAMESPACE) -> tuple[dict, dict]:
    """`perfkb/schema.json` 모양의 데이터셋과 감사 통계를 만든다."""
    records, stats = project_rows(rows, namespace=namespace)
    return {"_note": SOURCE_NOTE, "specs": records}, stats


def format_audit(stats: dict) -> str:
    """빌드 요약 — 무엇을 알고 무엇을 모르는지를 숫자로."""
    lines = [
        f"행 {stats['total_rows']:,} → 레코드 "
        f"{stats['total_rows'] - stats['skipped_no_signal']:,} "
        f"(성능 신호 없어 제외 {stats['skipped_no_signal']:,})",
        "프로바이더별 신호 커버리지:",
    ]
    for provider, cov in sorted(stats["coverage"].items(), key=lambda kv: -kv[1]["n"]):
        n = cov["n"]
        parts = [f"{s} {cov[s] / n:5.1%}" for s in _SIGNALS if cov[s]]
        lines.append(f"  {provider:8} n={n:6,}  " + "  ".join(parts))

    lines.append("경고를 낳는 발견(0이면 파서 고장 의심):")
    for provider, find in sorted(stats["findings"].items()):
        if find:
            detail = ", ".join(f"{k} {v:,}" for k, v in sorted(find.items()))
            lines.append(f"  {provider:8} {detail}")

    if stats["untracked_providers"]:
        lines.append(
            f"미추적 프로바이더(성능 신호 없음): {dict(stats['untracked_providers'])} "
            "→ 의도된 공백입니다. 필요해지면 parsers/project.py에 투영을 추가하세요."
        )
    return "\n".join(lines)
