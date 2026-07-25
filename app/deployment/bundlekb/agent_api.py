"""에이전트용 사전 정의 질의 API (bundlekb).

다른 KB와 같은 관례: 예외 대신 에이전트가 그대로 읽을 수 있는 텍스트를 준다.

## 이 축의 답은 **두 겹**이다

    명시(stated)    "AVM 모듈이 이렇게 배포한다" · "tumblebug이 이걸 만든다"
    실측(observed)  "실제 템플릿 330개 중 100.0%에 있었다"

둘을 **한 줄에 섞으면 안 된다.** 섞는 순간 코퍼스 통계가 클라우드 사실처럼 읽힌다.
그래서 화면에서도 절을 나누고, 둘이 일치할 때만 **교차 확인** 표시를 붙인다
(`aws-cross-checked`와 같은 규율).

## 왜 '선택' 목록을 다 쏟지 않나

`roleAssignments`는 173개 모듈 중 148개에 선택으로 붙는다. 전부 보여주면
"이 리소스의 번들"이 아니라 "어디에나 붙는 것들의 목록"이 된다 — graphkb가
`EC2::Instance`에서 `KMS::ReplicaKey`까지 주던 실패 그대로다.
"""

from __future__ import annotations

from pathlib import Path

from app.deployment.bundlekb.dataset import (
    MIN_SAMPLES,
    all_bundles,
    bundles_for,
    companions_of,
    find_bundle,
    is_built,
    load_warnings,
)
from app.deployment.bundlekb.model import ALWAYS, OPTIONAL, REQUIRED
from app.deployment.kbcommon.basis import describe
from app.deployment.kbcommon.display import evidence_name

_MISSING = (
    "No resource-group dataset found. Build it with `python -m bundlekb build "
    "--source avm` (or tumblebug|aqt|aws-patterns)."
)

#: 명시 소스가 '필수'라 했는데 코퍼스도 이만큼 나오면 교차 확인으로 본다.
_AGREE_RATIO = 0.9

#: 화면에 쏟지 않을 선. 나머지는 개수로만 알린다.
_SHOW = 8

_TIER_LABEL = {
    ALWAYS: "always together",
    REQUIRED: "you must supply a value",
    OPTIONAL: "optional attachment",
}


def _short(type_id: str) -> str:
    return type_id.split("::", 1)[-1]


def _kinds(count: int) -> str:
    return f"{count} type" if count == 1 else f"{count} types"


def _named(member) -> str:
    """구성원 한 줄의 이름. **개수가 1이 아니면 반드시 붙인다.**

    안 붙이면 VM 34대짜리 템플릿이 "vm 1종"으로 읽힌다 — 사이징을 그 숫자로
    하게 되므로 값 자체보다 틀린 방향으로 아프다.
    """
    if member.count != 1:
        return f"{_short(member.type_id)} ×{member.count}"
    return _short(member.type_id)


def _corpus_index(type_id: str, output_dir: Path | str | None) -> dict[str, float]:
    return {c.type_id: c.ratio for c in companions_of(type_id, output_dir=output_dir)}


def resource_bundle(
    type_id: str, *, output_dir: Path | str | None = None
) -> str:
    """이 리소스를 고르면 **무엇이 따라오나**.

    `research.md` 문제 2가 요구하는 답이다 — "특정 리소스를 선택하는 경우 연계되는
    다양한 리소스 군을 획득할 수 있어야".
    """
    if not is_built(output_dir):
        return _MISSING
    found = bundles_for(type_id, output_dir)
    corpus = _corpus_index(type_id, output_dir)
    if not found and not corpus:
        return (
            f"No resource group centred on '{type_id}'. **That does not mean none "
            "exists, it means it is not in this dataset** — what is included is "
            "Azure (AVM, template corpus), AWS (Solutions Constructs), and "
            "core (cb-tumblebug)."
        )

    lines = [f"What you have to handle alongside {_short(type_id)}:"]

    # --- 1. 명시된 것 ---
    for tier in (ALWAYS, REQUIRED, OPTIONAL):
        rows = []
        for bundle in found:
            for member in bundle.by_tier(tier):
                if member.type_id.lower() == type_id.lower():
                    continue
                rows.append((member, bundle.name))
        if not rows:
            continue
        seen: dict[str, tuple[str, object]] = {}
        for member, source in rows:
            seen.setdefault(member.type_id, (source, member))
        lines.append(f"\n[{_TIER_LABEL[tier]}] {_kinds(len(seen))}")
        for member_id, (source, member) in list(seen.items())[:_SHOW]:
            mark = ""
            note = member.note
            ratio = corpus.get(member_id)
            if ratio is not None and tier in (ALWAYS, REQUIRED) and ratio >= _AGREE_RATIO:
                # 독립인 두 근거가 같은 답을 냈다.
                mark = f"  ✓ cross-checked (real templates {ratio:.0%})"
            detail = f" — {note}" if note else ""
            # **번들이 여럿이면 출처를 반드시 밝힌다.** 안 밝히면 특정 패턴에만
            # 있는 것(CosmosDB·복구 자격 증명 모음)이 이 리소스의 일반 요구처럼
            # 읽힌다 — 이 KB가 없애려던 바로 그 오독이다.
            origin = f"  ({source})" if len(found) > 1 else ""
            lines.append(f"  - {_named(member)}{detail}{origin}{mark}")
        if len(seen) > _SHOW:
            lines.append(f"  … and {len(seen) - _SHOW} more")

    # --- 2. 실측 (절을 나눈다) ---
    if corpus:
        samples = companions_of(type_id, output_dir=output_dir)[0].samples
        lines.append(
            f"\n[ratio at which they co-occurred in real templates] {samples} samples"
        )
        for member_id, ratio in list(corpus.items())[:_SHOW]:
            lines.append(f"  {ratio:6.1%}  {_short(member_id)}")
        lines.append(
            "  ※ **This is counted in the corpus; it does not mean the cloud "
            "enforces it.** The sample leans toward demos and tutorials."
        )

    if found:
        sources = sorted({evidence_name(b.evidence) for b in found})
        lines.append(f"\nEvidence: {', '.join(sources)}")
        for bundle in found:
            if bundle.caveat:
                lines.append(f"⚠ [{bundle.name}] {bundle.caveat}")
    warnings = load_warnings(output_dir)
    if warnings:
        lines.append(f"\n⚠ {warnings[0]}")
    return "\n".join(lines)


def describe_named_bundle(name: str, *, output_dir: Path | str | None = None) -> str:
    """이름으로 리소스 군 하나를 본다 (`sg-default`, `aws-apigateway-lambda` 등)."""
    if not is_built(output_dir):
        return _MISSING
    bundle = find_bundle(name, output_dir)
    if bundle is None:
        near = [
            b.name
            for b in all_bundles(output_dir)
            if name.strip().lower() in b.name.lower()
        ][:6]
        hint = f"\n  Similar names: {', '.join(near)}" if near else ""
        return f"Resource group '{name}' not found.{hint}"

    lines = [f"{bundle.name} ({bundle.provider})"]
    if bundle.description:
        lines.append(f"  {bundle.description}")
    for tier in (ALWAYS, REQUIRED, OPTIONAL):
        members = bundle.by_tier(tier)
        if not members:
            continue
        lines.append(f"\n[{_TIER_LABEL[tier]}] {_kinds(len(members))}")
        for member in members[:_SHOW]:
            detail = f" — {member.note}" if member.note else ""
            lines.append(f"  - {_named(member)}{detail}")
        if len(members) > _SHOW:
            lines.append(f"  … and {len(members) - _SHOW} more")
    lines.append(
        f"\nEvidence: {evidence_name(bundle.evidence)}, {describe(bundle.basis)}"
    )
    if bundle.caveat:
        lines.append(f"⚠ {bundle.caveat}")
    return "\n".join(lines)


def list_bundles(
    keyword: str | None = None, *, limit: int = 15, output_dir: Path | str | None = None
) -> str:
    """담긴 리소스 군 목록. 키워드로 좁힐 수 있다."""
    if not is_built(output_dir):
        return _MISSING
    found = list(all_bundles(output_dir))
    if keyword:
        low = keyword.strip().lower()
        found = [
            b
            for b in found
            if low in b.name.lower() or low in (b.description or "").lower()
        ]
    if not found:
        return f"No resource group matches '{keyword}'."
    shown = found[: max(1, limit)]
    lines = [f"{len(shown)} of {len(found)} resource groups:"]
    for bundle in shown:
        always = len(bundle.by_tier(ALWAYS))
        lines.append(
            f"  - {bundle.name} ({bundle.provider}) — {always} always together"
            f"{f' · {bundle.description[:56]}' if bundle.description else ''}"
        )
    return "\n".join(lines)


def coverage_text(output_dir: Path | str | None = None) -> str:
    """무엇이 담겼는지 한 눈에 — 빈 답이 나왔을 때 범위를 밝히는 데 쓴다."""
    bundles = all_bundles(output_dir)
    by_provider: dict[str, int] = {}
    for bundle in bundles:
        by_provider[bundle.provider] = by_provider.get(bundle.provider, 0) + 1
    parts = ", ".join(f"{p} {n}" for p, n in sorted(by_provider.items()))
    return (
        f"{len(bundles)} resource groups ({parts}). Co-occurrence was counted only "
        f"over the Azure template corpus, and only anchors with {MIN_SAMPLES} or more "
        f"samples are included."
    )
