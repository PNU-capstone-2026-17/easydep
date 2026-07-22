"""에이전트용 사전 정의 질의 API (sizingkb).

**이 KB의 답은 대부분 "모른다"여야 정상이다.** 사이징은 사실이 아니라 판단이고,
담긴 것은 원본이 공식으로 적어 둔 변환 규칙뿐이다.

    ❌ "1000명이면 vCPU 2입니다"                      근거 없는 단정
    ✅ "/24면 AWS 기준 251대까지입니다"                공식에서 계산
    ✅ "이 소스의 웹서버 예시는 t3.small입니다"        참조점
"""

from __future__ import annotations

from pathlib import Path

from kbcommon.basis import describe
from kbcommon.display import evidence_name
from sizingkb.dataset import (
    all_rules,
    is_built,
    load_warnings,
    reserved_ips,
    rules_of,
    scopes,
)
from sizingkb.model import (
    MINIMUM,
    PRESET,
    REFERENCE_POINT,
    REQUIRED_COUNT,
    Rule,
    usable_ips,
)

_MISSING = (
    "사이징 규칙 데이터셋이 없습니다. "
    "`python -m sizingkb build --source tumblebug` 으로 빌드하세요."
)

#: 어떤 답에도 붙는다. 사이징은 근거가 있어도 **추정**이다.
_DISCLAIMER = (
    "※ 사이징은 워크로드 성격(요청당 CPU 시간·캐시 히트율·런타임)에 따라 크게 "
    "갈립니다. **부하 테스트로 검증하세요.**"
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
        return f"프리픽스 길이가 범위를 벗어납니다: /{prefix_length} (0~32)"

    rule = reserved_ips(provider, output_dir)
    total = 1 << (32 - prefix_length)
    if rule is None:
        known = ", ".join(scopes("reserved_ips", output_dir)) or "없음"
        return (
            f"/{prefix_length} 서브넷의 전체 주소는 {total:,}개입니다. 다만 "
            f"**'{provider}'의 예약 IP 수를 이 데이터셋이 모릅니다** — 예약이 없다는 "
            f"뜻이 아닙니다(AWS도 실제로 5개를 예약하는데 원본 표가 비어 있습니다). "
            f"아는 프로바이더: {known}"
        )
    reserved = int(rule.value)
    usable = usable_ips(prefix_length, reserved)
    lines = [
        f"{provider} /{prefix_length} 서브넷: 전체 {total:,}개 중 "
        f"예약 {reserved}개를 빼면 **{usable:,}개**를 쓸 수 있습니다.",
    ]
    if rule.note:
        lines.append(f"  예약 내역: {rule.note}")
    lines.append(f"  근거: {evidence_name(rule.evidence)}, {describe(rule.basis)}")
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
    if not found:
        known = ", ".join(sorted({r.scope for r in all_rules(output_dir)}))
        return (
            f"'{scope}' 에 대한 사이징 요구가 이 데이터셋에 없습니다. "
            f"담긴 범위: {known}"
        )
    lines = [f"사이징 요구 {len(found)}건:"]
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
            f"'{keyword}' 에 맞는 참조점이 없습니다. 담긴 워크로드: {known or '없음'}"
        )
    lines = [f"워크로드 참조점 {len(found)}건 — **정답이 아니라 이 소스의 예시입니다.**"]
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
        return "컨테이너 프리셋이 이 데이터셋에 없습니다."
    by_name: dict[str, dict[str, str]] = {}
    for rule in found:
        by_name.setdefault(rule.scope.removeprefix("container:"), {})[rule.metric] = str(
            rule.value
        )
    lines = [f"컨테이너 규모 프리셋 {len(by_name)}종:"]
    order = ["nano", "micro", "small", "medium", "large", "xlarge", "2xlarge"]
    for name in sorted(by_name, key=lambda n: order.index(n) if n in order else 99):
        cells = by_name[name]
        lines.append(
            f"  {name:8} 요청 cpu {cells.get('requests.cpu', '?')} / "
            f"메모리 {cells.get('requests.memory', '?')}"
            f"  · 상한 cpu {cells.get('limits.cpu', '?')} / "
            f"메모리 {cells.get('limits.memory', '?')}"
        )
    caveat = next((r.caveat for r in found if r.caveat), None)
    if caveat:
        lines.append(f"\n⚠ 원본 주석: {caveat}")
    lines.append("※ **컨테이너** 규모입니다 — 인스턴스 규모가 아닙니다.")
    lines.append(_DISCLAIMER)
    return "\n".join(lines)


def coverage_text(output_dir: Path | str | None = None) -> str:
    rules = all_rules(output_dir)
    by_kind: dict[str, int] = {}
    for rule in rules:
        by_kind[rule.kind] = by_kind.get(rule.kind, 0) + 1
    parts = ", ".join(f"{k} {n}건" for k, n in sorted(by_kind.items()))
    warnings = load_warnings(output_dir)
    tail = f"\n⚠ {warnings[0]}" if warnings else ""
    return (
        f"사이징 규칙 {len(rules)}건 ({parts}). **'요구사항 → vCPU' 같은 변환은 "
        f"담지 않았습니다** — 소스가 없습니다.{tail}"
    )
