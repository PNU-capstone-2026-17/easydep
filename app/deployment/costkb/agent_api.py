"""에이전트용 사전 정의 질의 API (costkb).

graphkb/capacitykb의 agent_api와 같은 관례: 예외 대신 에이전트가 그대로 읽을 수 있는
한국어 텍스트를 반환한다.

다른 두 KB와 달리 `_MISSING_MESSAGE`("먼저 build 하세요")가 없다 — 번들 36건이 항상
폴백으로 있어 산출물이 없을 수가 없다. `costkb build`는 커버리지를 73k건으로 넓힐 뿐이다.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from costkb.dataset import (
    DEFAULT_ARCHITECTURE,
    count_unpriced,
    coverage,
    filter_specs,
    is_built,
    load_warning,
    provider_summary,
)

# 한 달 가동 시간 기준: 24h * 365d / 12 ≈ 730시간(상시 가동 가정).
HOURS_PER_MONTH = 730

_COST_DISCLAIMER = (
    "정가·컴퓨트 비용만 반영이며 스토리지/네트워크/관리형 서비스/약정할인은 미포함입니다."
)


def coverage_text(output_dir: Path | str | None = None) -> str:
    """데이터셋이 어디까지 커버하는지 — 조건 불만족 시 안내에 쓴다."""
    if is_built(output_dir):
        return "\n".join(
            f"  - {row['provider']}: {row['count']:,}건, 리전 {row['regions']}개, "
            f"vCPU 최대 {row['vcpu_max']}, 메모리 최대 {row['mem_max_gib']:g} GiB"
            for row in provider_summary(output_dir)
        )
    return "\n".join(
        f"  - {row['provider']} {row['region']}: {row['count']}건, "
        f"vCPU {row['vcpu_min']}~{row['vcpu_max']}, "
        f"메모리 {row['mem_min_gib']}~{row['mem_max_gib']} GiB"
        for row in coverage(output_dir)
    )


def _describe(spec: dict) -> str:
    hourly = spec["hourlyUSD"]
    price = f"${hourly:.4f}/h" if hourly is not None else "가격 미상"
    # 표시는 보정값(진실), 필터·판정은 미러값(MCP 일치). 둘이 다르면 밝힌다.
    mem = spec["memGiB"]
    actual = spec.get("memGiBActual", mem)
    mem_text = f"{actual:g} GiB"
    if actual != mem:
        mem_text += f" (지식베이스 기준값 {mem:g})"
    # 리전을 접었으면 몇 곳이 더 있는지 밝힌다. 숨기면 "이 스펙은 이 리전에만 있나?"로
    # 오해되고, 그건 접기가 만들어낸 거짓 정보다.
    folded = spec.get("_foldedRegions") or []
    where = spec["region"]
    if folded:
        where += f" 외 {len(folded)}개 리전"
    return (
        f"- {spec['provider'].upper()} {spec['specName']} ({where}): "
        f"{spec['vCPU']} vCPU / {mem_text}, {price}"
    )


def recommend_specs(
    vcpu_min: int = 2,
    mem_min_gib: float = 4,
    provider: str | None = None,
    region: str | None = None,
    sort_by: str = "cost",
    limit: int = 5,
    *,
    architecture: str | None = DEFAULT_ARCHITECTURE,
    output_dir: Path | str | None = None,
    annotate: Callable[[dict], str | None] | None = None,
    footer: Callable[[list[dict]], str | None] | None = None,
) -> str:
    """요구사항을 만족하는 VM 스펙 후보를 **시간당 단가까지만** 텍스트로 반환한다.

    ⚠️ **월 비용을 여기에 넣지 말 것.** 예전에는 후보마다 `≈ $121.47/월`을 함께 줬는데,
    그러면 모델이 월 비용을 이미 손에 쥔 상태가 되어 `estimate_monthly_cost` 도구가
    불필요해 보인다 — 실측으로 5회 중 5회 도구를 건너뛰고 직접 암산했다. 제거 후 5/5 호출.
    (사람이 읽는 `costkb/cli.py`의 표는 월 비용을 계속 보여준다 — 거기엔 다음 도구가 없다.)

    Args:
        annotate: 각 후보 스펙 dict를 받아 한 줄 주석을 반환하는 선택적 콜백.
            None을 반환하면 주석을 안 붙인다. **costkb는 이 주석이 무엇인지 모른다** —
            성능 경고(perfkb) 조인을 도구 계층에서 끼워넣기 위한 확장점일 뿐이다.
            KB끼리 import하지 않는 규약을 지키면서 축을 잇는 방법이다.
            **기호(`⚠`/`·`)도 콜백이 붙인다** — 예전엔 여기서 `⚠`를 하드코딩했는데,
            그건 "모든 주석은 경고"라는 의미 가정이라 "정보 없음" 같은 비경고 주석을
            달 수 없었다(결함 C4).
        footer: 후보 목록 전체를 받아 블록 끝 한 줄을 반환하는 선택적 콜백.
            annotate와 대칭이며 마찬가지로 costkb는 내용을 모른다. 후보마다 상태를
            적으면 노이즈라, "주석 없는 후보가 무슨 뜻인지"를 한 번만 밝히는 데 쓴다.
    """
    results = filter_specs(
        vcpu_min,
        mem_min_gib,
        provider,
        region,
        sort_by,
        limit,
        architecture=architecture,
        output_dir=output_dir,
    )
    if not results:
        return (
            "조건을 만족하는 스펙이 데이터셋에 없습니다. 이 데이터셋의 커버리지는 "
            f"다음과 같으니 조건을 조정하세요:\n{coverage_text(output_dir)}"
        )

    lines = []
    for spec in results:
        line = _describe(spec)
        note = annotate(spec) if annotate is not None else None
        if note:
            line += f"\n    {note}"
        lines.append(line)
    text = "추천 후보(온디맨드 정가, 시간당 단가):\n" + "\n".join(lines)

    unpriced = count_unpriced(
        vcpu_min, mem_min_gib, provider, region,
        architecture=architecture, output_dir=output_dir,
    )
    if unpriced:
        text += (
            f"\n\n※ 조건에 맞지만 가격 정보가 없는 후보가 {unpriced}건 더 있습니다. "
            "라이브 가격은 cb-tumblebug MCP로 확인하세요."
        )

    degraded = load_warning(output_dir)
    if degraded:
        # 조용한 폴백은 "커버리지가 왜 갑자기 좁아졌지?"를 미궁으로 만든다.
        text += f"\n\n⚠ {degraded}"

    tail = footer(results) if footer is not None else None
    if tail:
        text += f"\n\n※ {tail}"
    return (
        text
        + "\n\n월 비용은 estimate_monthly_cost 도구로 계산하세요 "
        "(대수·가동시간이 반영되고 한계 고지가 붙습니다). 직접 곱하지 마세요."
    )


def estimate_monthly_cost(
    hourly_usd: float,
    count: int = 1,
    hours_per_month: float = HOURS_PER_MONTH,
) -> str:
    """시간당 단가로 월 비용을 계산해 텍스트로 반환한다."""
    per_node = hourly_usd * hours_per_month
    total = per_node * count
    return (
        f"월 예상 비용: ${total:,.2f} "
        f"(대당 ${per_node:,.2f} × {count}대, {hours_per_month:.0f}h/월 기준). "
        f"{_COST_DISCLAIMER}"
    )
