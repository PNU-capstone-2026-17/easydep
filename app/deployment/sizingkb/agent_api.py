"""에이전트용 사전 정의 질의 API (sizingkb).

**이 KB의 답은 대부분 "모른다"여야 정상이다.** 사이징은 사실이 아니라 판단이고,
담긴 것은 원본이 공식으로 적어 둔 변환 규칙뿐이다.

    ❌ "1000명이면 vCPU 2입니다"                      근거 없는 단정
    ✅ "/24면 AWS 기준 251대까지입니다"                공식에서 계산
    ✅ "이 소스의 웹서버 예시는 t3.small입니다"        참조점
"""

from __future__ import annotations

from pathlib import Path

from app.deployment.kbcommon.basis import describe
from app.deployment.kbcommon.display import evidence_name
from app.deployment.sizingkb.dataset import (
    all_rules,
    is_built,
    load_warnings,
    reserved_ips,
    rules_of,
    scopes,
)
from app.deployment.sizingkb.model import (
    MINIMUM,
    PRESET,
    REFERENCE_POINT,
    REQUIRED_COUNT,
    Rule,
    usable_ips,
)

_MISSING = (
    "No sizing-rule dataset found. "
    "Build it with `python -m sizingkb build --source tumblebug`."
)

#: 어떤 답에도 붙는다. 사이징은 근거가 있어도 **추정**이다.
_DISCLAIMER = (
    "※ Sizing varies widely with the nature of the workload (CPU time per request, "
    "cache hit rate, runtime). **Verify it with a load test.**"
)


def subnet_capacity(
    prefix_length: int, provider: str, *, output_dir: Path | str | None = None
) -> str:
    """서브넷 CIDR로 **몇 대나 띄울 수 있나**.

    `2^(32−prefix) − 예약IP`. 예약 수는 프로바이더마다 다르고 **원본이 적어 둔
    것만** 쓴다 — 모르면 계산하지 않고 모른다고 한다. 0으로 두면 251대 자리에
    256대라고 답하게 된다.
    """
    if not is_built(output_dir):
        return _MISSING
    if not 0 <= prefix_length <= 32:
        return f"Prefix length out of range: /{prefix_length} (0-32)"

    rule = reserved_ips(provider, output_dir)
    total = 1 << (32 - prefix_length)
    if rule is None:
        known = ", ".join(scopes("reserved_ips", output_dir)) or "none"
        return (
            f"A /{prefix_length} subnet has {total:,} addresses in total. But "
            f"**this dataset does not know how many reserved IPs '{provider}' takes** "
            "— that **does not mean there are none**, it means our source did not "
            "state it. Some are taken in practice, so do not use the total address "
            f"count as-is. Providers we know: {known}"
        )
    reserved = int(rule.value)
    usable = usable_ips(prefix_length, reserved)
    lines = [
        f"{provider} /{prefix_length} subnet: of {total:,} addresses, minus "
        f"{reserved} reserved, **{usable:,}** are usable.",
    ]
    if rule.note:
        lines.append(f"  reserved breakdown: {rule.note}")
    lines.append(f"  evidence: {evidence_name(rule.evidence)}")
    # **손으로 적은 값이면 그렇다고 말해야 한다.** 근거 라벨만 보이면
    # "원본에 명시됨"으로 읽혀 기계 판독 소스가 있는 것처럼 들린다.
    if rule.caveat:
        lines.append(f"  ⚠ {rule.caveat}")
    lines.append(_DISCLAIMER)
    return "\n".join(lines)


def _format(rule: Rule) -> str:
    unit = f" {rule.unit}" if rule.unit else ""
    return f"  - {rule.scope} · {rule.metric}: {rule.value}{unit}" + (
        f" — {rule.note}" if rule.note else ""
    )


def requirements(
    scope: str | None = None, *, output_dir: Path | str | None = None
) -> str:
    """최소치·개수 요구를 모아 본다 ("K8s 노드는 얼마부터?", "서브넷 몇 개?")."""
    if not is_built(output_dir):
        return _MISSING
    found = [
        r
        for kind in (MINIMUM, REQUIRED_COUNT)
        for r in rules_of(kind, scope, output_dir)
    ]
    if not found and scope:
        # scope로 못 찾으면 **내용으로** 한 번 더 본다. 클러스터 서브넷 규칙은
        # scope가 프로바이더('aws')라 '쿠버네티스'·'클러스터'로는 안 닿았다.
        from app.deployment.sizingkb.dataset import search_rules

        found = [
            r
            for r in search_rules(scope, output_dir)
            if r.kind in (MINIMUM, REQUIRED_COUNT)
        ]
    if not found:
        known = ", ".join(sorted({r.scope for r in all_rules(output_dir)}))
        return (
            f"No sizing requirement for '{scope}' in this dataset. "
            f"Scopes included: {known}"
        )
    lines = [f"Sizing requirements ({len(found)}):"]
    lines.extend(_format(r) for r in found)
    caveats = {r.caveat for r in found if r.caveat}
    for caveat in sorted(caveats):
        lines.append(f"⚠ {caveat}")
    lines.append(_DISCLAIMER)
    return "\n".join(lines)


def reference_points(
    keyword: str | None = None, *, output_dir: Path | str | None = None
) -> str:
    """워크로드 참조점 — **정답이 아니라 예시**다."""
    if not is_built(output_dir):
        return _MISSING
    found = list(rules_of(REFERENCE_POINT, output_dir=output_dir))
    if keyword:
        low = keyword.strip().lower()
        found = [
            r for r in found if low in r.scope.lower() or low in (r.note or "").lower()
        ]
    if not found:
        known = ", ".join(scopes(REFERENCE_POINT, output_dir))
        return (
            f"No reference point matches '{keyword}'. "
            f"Workloads included: {known or 'none'}"
        )
    lines = [
        f"Workload reference points ({len(found)}) — "
        "**these are this source's examples, not the right answer.**"
    ]
    for rule in found:
        lines.append(f"  - {rule.scope}: {rule.value}" + (f" ({rule.note})" if rule.note else ""))
    lines.append(_DISCLAIMER)
    return "\n".join(lines)


def container_presets(*, output_dir: Path | str | None = None) -> str:
    """컨테이너 규모 프리셋(nano~2xlarge). 원본 경고를 함께 낸다."""
    if not is_built(output_dir):
        return _MISSING
    found = rules_of(PRESET, output_dir=output_dir)
    if not found:
        return "No container preset in this dataset."
    by_name: dict[str, dict[str, str]] = {}
    for rule in found:
        by_name.setdefault(rule.scope.removeprefix("container:"), {})[rule.metric] = str(
            rule.value
        )
    lines = [f"Container size presets ({len(by_name)}):"]
    order = ["nano", "micro", "small", "medium", "large", "xlarge", "2xlarge"]
    for name in sorted(by_name, key=lambda n: order.index(n) if n in order else 99):
        cells = by_name[name]
        lines.append(
            f"  {name:8} requests cpu {cells.get('requests.cpu', '?')} / "
            f"memory {cells.get('requests.memory', '?')}"
            f"  · limits cpu {cells.get('limits.cpu', '?')} / "
            f"memory {cells.get('limits.memory', '?')}"
        )
    caveat = next((r.caveat for r in found if r.caveat), None)
    if caveat:
        lines.append(f"\n⚠ source note: {caveat}")
    lines.append("※ These are **container** sizes, not instance sizes.")
    lines.append(_DISCLAIMER)
    return "\n".join(lines)


def coverage_text(output_dir: Path | str | None = None) -> str:
    rules = all_rules(output_dir)
    by_kind: dict[str, int] = {}
    for rule in rules:
        by_kind[rule.kind] = by_kind.get(rule.kind, 0) + 1
    parts = ", ".join(f"{k} {n}" for k, n in sorted(by_kind.items()))
    warnings = load_warnings(output_dir)
    tail = f"\n⚠ {warnings[0]}" if warnings else ""
    return (
        f"{len(rules)} sizing rules ({parts}). **Conversions like "
        f"'requirement → vCPU' are not included** — there is no source for them.{tail}"
    )
