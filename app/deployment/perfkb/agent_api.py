"""에이전트용 사전 정의 질의 API (perfkb).

다른 KB의 agent_api와 같은 관례: 예외 대신 에이전트가 그대로 읽을 수 있는 텍스트를 준다.

## 무엇을 경고로 삼는가 — 노이즈를 피한다

Phase 1 실빌드에서 세 발견의 빈도가 크게 갈렸다:

    not_sustained (상시 CPU 미보장)   aws 609 · azure 1,390 · gcp 182
    old_generation (구세대)           aws 2,085 (11%)
    burst_network ('Up to N Gigabit') aws 8,897 (47.9%)  ← 절반이라 경고로 쓰면 노이즈

`burst_network`는 AWS의 절반이라 추천마다 붙으면 무의미해진다 — 상세 프로파일(`describe`)
에서만 보여주고 **추천 경고에는 넣지 않는다.** 추천 경고는 *구매 결정을 바꾸는* 두 가지,
상시 CPU 미보장과 구세대만 다룬다.

**fail-open**: 성능 데이터가 없으면(빌드 안 됨, 번들 스펙이라 id 없음, 미추적 프로바이더)
경고 없이 조용히 넘어간다. 잘못 경고하는 것보다 침묵이 낫다 — costkb·capacitykb와 같은 원칙.
"""

from __future__ import annotations

from pathlib import Path

from perfkb.dataset import find, get_by_id

_OLD_GEN_NOTE = "구세대 인스턴스입니다 — 최신 세대에 더 나은 가격/성능이 있을 수 있습니다."


def recommend_warning(spec_id: str | None, output_dir: Path | str | None = None) -> str | None:
    """추천 후보에 붙일 성능 경고. 문제가 없거나 데이터가 없으면 None.

    구매 결정을 바꾸는 것만 — 상시 CPU 미보장, 구세대. 이 함수가 costkb 추천과 perfkb를
    잇는 조인 지점이다(도구 계층에서 호출).
    """
    if not spec_id:
        return None
    rec = get_by_id(spec_id, output_dir)
    if rec is None:
        return None

    parts: list[str] = []
    sustained = rec.get("sustainedCpu")
    if sustained and sustained["value"] is False:
        # note가 메커니즘(크레딧/공유코어/B계열)을 담고 있다. 없으면 일반 문구.
        parts.append(sustained.get("note") or "상시 CPU 성능이 보장되지 않습니다.")
    if rec.get("currentGeneration") is False:
        parts.append(_OLD_GEN_NOTE)
    return " ".join(parts) if parts else None


def _describe(rec: dict) -> str:
    lines = []
    sustained = rec.get("sustainedCpu")
    if sustained is not None:
        mark = "보장됨" if sustained["value"] else "보장 안 됨"
        conf = sustained["confidence"]
        hedge = "" if conf >= 1.0 else f" (이름 규칙 추론, 신뢰도 {conf})"
        lines.append(f"  상시 CPU 성능: {mark}{hedge}")
        if sustained.get("note"):
            lines.append(f"    ⚠ {sustained['note']}")
    for key, label, unit in (
        ("currentGeneration", "최신 세대", ""),
        ("clockGHz", "클럭", " GHz"),
        ("networkPerformance", "네트워크", ""),
        ("ebsBaselineMbps", "EBS 지속 대역폭", " Mbps"),
        ("ebsMaxMbps", "EBS 최대 대역폭", " Mbps"),
        ("acu", "ACU (Azure 내부 비교용)", ""),
        ("diskIops", "디스크 IOPS", ""),
    ):
        if key in rec:
            lines.append(f"  {label}: {rec[key]}{unit}")
    if rec.get("networkIsBurst"):
        lines.append("    ⚠ 네트워크 대역폭이 버스트('Up to')라 지속 값이 아닙니다.")
    return "\n".join(lines)


def instance_profile(
    provider: str, spec_name: str, output_dir: Path | str | None = None
) -> str:
    """한 스펙의 성능 프로파일을 텍스트로 반환한다.

    성능은 리전 불변이라 여러 리전에 같은 스펙이 있어도 하나만 설명한다.
    """
    found = find(provider=provider, spec_name=spec_name, output_dir=output_dir)
    if not found:
        return (
            f"{provider} {spec_name}의 성능 데이터가 없습니다. "
            "성능 지식베이스가 빌드되지 않았거나(python -m perfkb build), "
            "성능 신호를 추적하지 않는 프로바이더일 수 있습니다(aws/azure/gcp만 수록)."
        )
    return f"{provider} {spec_name} 성능 프로파일:\n{_describe(found[0])}"
