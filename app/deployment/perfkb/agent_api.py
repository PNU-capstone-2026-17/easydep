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

from perfkb.dataset import find, get_by_id, load_perf

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


# 프로바이더별로 비교 가능한 축. **교집합이 아니라 프로바이더 전용**이라는 게 핵심 —
# ACU는 Azure에만, 클럭·EBS는 AWS에만 있어서 프로바이더 간 비교가 불가능하다.
_COMPARE_AXES = {
    "aws": [("clockGHz", "클럭(GHz)", "높을수록"), ("ebsBaselineMbps", "EBS 지속 대역폭(Mbps)", "높을수록"),
            ("ebsBaselineIops", "EBS 지속 IOPS", "높을수록")],
    "azure": [("acu", "ACU", "높을수록"), ("diskIops", "디스크 IOPS", "높을수록")],
    "gcp": [("maxPersistentDisks", "최대 영구 디스크 수", "높을수록")],
}


def compare(
    provider: str, spec_names: list[str], output_dir: Path | str | None = None
) -> str:
    """**같은 프로바이더** 스펙들을 축별로 나란히 놓는다. 승자를 선언하지 않는다.

    왜 승자를 안 뽑나: "더 빠르다"는 워크로드에 달렸다(CPU 바운드 vs IO 바운드).
    그래서 축별 수치만 나열하고 판단은 맡긴다 — capacitykb가 판정 보류를 두는 것과 같은 정직함.

    프로바이더 간 비교(AWS vs Azure)는 이 함수로 **할 수 없다**. 파라미터가 단일 provider라
    구조적으로 막혀 있고, 축 자체가 프로바이더 전용이라 비교 기준이 없다.
    """
    provider = provider.lower()
    recs, missing = [], []
    for name in spec_names:
        found = find(provider=provider, spec_name=name, output_dir=output_dir)
        (recs.append(found[0]) if found else missing.append(name))

    if len(recs) < 2:
        got = [r["specName"] for r in recs]
        return (
            f"비교하려면 {provider}의 스펙이 2개 이상 필요합니다. "
            f"찾음: {got or '없음'} / 못 찾음: {missing or '없음'}. "
            "성능 데이터가 없거나(빌드 필요) 프로바이더가 달라 비교할 수 없습니다."
        )

    lines = [f"{provider} 스펙 성능 비교 ({', '.join(r['specName'] for r in recs)}):"]

    def _row(label: str, values: list[str], incomplete: bool = False) -> str:
        cells = " / ".join(f"{r['specName']}={v}" for r, v in zip(recs, values))
        return f"  {label}: {cells}" + (" (일부 값이 없어 비교 불완전)" if incomplete else "")

    # 상시 CPU는 모든 프로바이더 공통 축이다.
    sustained = []
    for r in recs:
        s = r.get("sustainedCpu")
        sustained.append("보장" if s and s["value"] else "미보장" if s else "모름")
    lines.append(_row("상시 CPU 성능", sustained))

    for key, label, _ in _COMPARE_AXES.get(provider, []):
        vals = [r.get(key) for r in recs]
        if all(v is None for v in vals):
            continue  # 아무도 이 축 값이 없으면 행을 만들지 않는다
        cells = [str(v) if v is not None else "모름" for v in vals]
        lines.append(_row(label, cells, incomplete=any(v is None for v in vals)))

    lines.append(
        "※ '더 빠르다'는 워크로드(CPU 바운드/IO 바운드)에 따라 다르므로 승자를 단정하지 "
        "않습니다. 프로바이더 간 비교는 기준 축이 달라 불가능합니다."
    )
    return "\n".join(lines)


def specs_meeting_ebs_baseline(
    min_mbps: float, limit: int = 10, output_dir: Path | str | None = None
) -> str:
    """AWS 스펙 중 **지속(baseline) EBS 대역폭**이 기준 이상인 것을 찾는다.

    함정 방지: 사람들이 보는 "최대 대역폭"은 버스트라 지속되지 않는다. 이 필터는 baseline만
    본다. **AWS 전용** — Azure는 IOPS로, GCP는 별도 디스크로 표현해 축이 다르다.
    """
    records = load_perf(output_dir) or []
    hits = {}  # specName → baseline (리전 불변이라 dedup)
    for r in records:
        if r["provider"] != "aws":
            continue
        base = r.get("ebsBaselineMbps")
        if base is not None and base >= min_mbps:
            hits[r["specName"]] = (base, r.get("ebsMaxMbps"))
    if not hits:
        return (
            f"지속 EBS 대역폭 {min_mbps:g} Mbps 이상인 AWS 스펙을 찾지 못했습니다. "
            "성능 데이터가 빌드됐는지, 기준이 너무 높지 않은지 확인하세요."
        )
    ranked = sorted(hits.items(), key=lambda kv: -kv[1][0])[: max(1, limit)]
    lines = [f"지속 EBS 대역폭 {min_mbps:g} Mbps 이상 AWS 스펙 {len(hits)}종 중 상위 {len(ranked)}:"]
    for name, (base, mx) in ranked:
        mx_text = f", 버스트 최대 {mx:g}" if mx else ""
        lines.append(f"  - {name}: 지속 {base:g} Mbps{mx_text}")
    lines.append("※ 가격·크기는 cost_recommend_specs로 확인하세요. 여기 값은 지속 대역폭입니다.")
    return "\n".join(lines)
