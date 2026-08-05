"""네 축을 **한 답에 엮는** 도구 — `research.md` 목표 2가 요구하는 그것.

## 왜 도구 계층인가

KB끼리는 import하지 않는다(단방향 규약). 그래서 축을 엮는 일은 여기서만 할 수 있고,
`cost_tools._perf_annotate`(costkb × perfkb)가 이미 그 자리다. 이 파일은 같은 방식으로
**bundlekb × graphkb × costkb × perfkb** 넷을 잇는다.

## 무엇이 없어서 이게 필요했나

목표 2의 근거 문장은 두 요구를 한 문장에 겹쳐 둔다.

> 특정 리소스를 선택하는 경우 **연계되는 다양한 리소스 군을 획득**할 수 있어야 하며,
> 사용자가 클라우드 리소스를 선택할 수 있는 **가이드라인(비용, 성능 등)** 제공이 필요하다

앞 절은 `bundle_lookup`이, 뒤 절은 `cost_recommend_specs`가 각각 답했는데
**둘을 잇는 답이 없었다.** "VM 하나 만들면 NIC·디스크가 딸린다"까지는 답하면서
"그 구성이 얼마"는 답하지 못했다.

## 다리

    번들 멤버  aws::AWS::EC2::Instance
       ↓  graphkb  core_concept()      근거 cb-spider-driver (짐작·검수됨)
    core::vm
       ↓  core-graph  references via specId (required)   근거 swagger-field
    core::spec  =  costkb 스펙 카탈로그   →  hourlyUSD  +  perfkb 경고

**다리를 건너면 근거 등급이 떨어진다.** `core::vm`을 직접 물으면 안 건너고, 벤더
타입에서 오면 사람이 맞춘 대응을 한 번 거친다. `CoreLink.hedged`가 그것이고 답에 밝힌다.

## 합계를 내지 않는다

다리가 값을 붙여 주는 것은 **VM 하나**다. 나머지 멤버(vNet·서브넷·보안그룹·키·이미지)는
이 데이터셋에 **가격 축이 아예 없다**. 모르는 것을 0으로 두고 더하면 실제보다 낮은
숫자가 나오고, 사람은 그 숫자로 예산을 정한다. 서브넷 예약 IP를 몰라서 256으로
답하던 것과 같은 실패이고, 그때보다 아프다.

그래서 **합계가 아니라 가격표**를 낸다 — 값을 매길 수 있는 것은 단가와 함께, 못 매기는
것은 이름과 이유와 함께.
"""

from __future__ import annotations

from agents import RunContextWrapper, function_tool

from app.core.cloudkb.bundlekb import dataset as bundle_dataset
from app.core.cloudkb.bundlekb.model import ALWAYS, OPTIONAL, REQUIRED
from app.core.cloudkb.costkb import dataset as cost_dataset
from app.core.cloudkb.costkb.agent_api import HOURS_PER_MONTH
from app.core.cloudkb.graphkb import agent_api as graph_api
from app.core.cloudkb.graphkb.agent_api import concepts_with_spec, core_concept
from app.core.cloudkb.kbcommon.display import evidence_name
from app.core.cloudkb.perfkb import agent_api as perf_api

from .cost_tools import _perf_note
from .session import SessionState


def graph_default():
    """기본 그래프 경로를 **부를 때** 읽는다.

    모듈 상수를 기본 인자로 굳히면 테스트가 경로를 갈아끼워도 안 먹는다 —
    함수 정의 시점에 값이 박히기 때문이다.
    """
    return graph_api.DEFAULT_OUTPUT_DIR


_TIER_LABEL = {
    ALWAYS: "always created together",
    REQUIRED: "you must supply a value",
    OPTIONAL: "optional attachment",
}


def _short(type_id: str) -> str:
    return type_id.split("::", 1)[-1]


def _pick_spec(
    provider: str, region: str | None, spec_name: str | None, vcpu: int,
    mem_gib: float, *, output_dir=None,
) -> tuple[dict | None, str | None]:
    """값을 붙일 스펙 하나. 이름을 주면 그것, 아니면 조건에 맞는 최저가.

    **못 고른 이유를 함께 돌려준다** — 빈 결과에 이유가 없으면 사용자는 vCPU만
    낮추다 끝난다(리전 이름이 범인일 때 실제로 그랬다).
    """
    if spec_name:
        found = cost_dataset.find_by_name(spec_name, provider=provider,
                                          output_dir=output_dir)
        if not found:
            near = cost_dataset.name_suggestions(spec_name, output_dir=output_dir)
            hint = f" Similar names: {', '.join(near)}" if near else ""
            return None, f"Spec '{spec_name}' not found.{hint}"
        if region:
            same = [s for s in found if s.get("region") == region]
            if not same:
                where = sorted({s.get("region", "?") for s in found})[:5]
                return found[0], (
                    f"'{spec_name}' is not in {region}, so the {found[0].get('region')} "
                    f"unit price is shown (regions it is in: {', '.join(where)})."
                )
            found = same
        return found[0], None

    candidates = cost_dataset.filter_specs(
        vcpu_min=vcpu, mem_min_gib=mem_gib, provider=provider, region=region,
        sort_by="cost", limit=1, output_dir=output_dir,
    )
    if not candidates:
        return None, (
            f"No spec with vCPU {vcpu} · memory {mem_gib}GiB or more was found in "
            f"{provider}{f'/{region}' if region else ''}. "
            "Check the region code or the criteria."
        )
    return candidates[0], None


def _priced_lines(member, spec: dict) -> list[str]:
    """구성원 한 줄의 값. 성능 경고는 **값과 같은 자리에** 붙인다.

    개수가 여럿이면 곱한 값도 적는다. **이건 합계가 아니다** — 아는 단가에 아는
    개수를 곱한 것이라 모르는 것을 0으로 두는 일이 없다.
    """
    times = f" ×{member.count}" if member.count != 1 else ""
    head = (
        f"  {_short(member.type_id)}{times}  "
        f"{spec.get('provider')} {spec.get('region')} {spec.get('specName')}"
        f"  {spec.get('vCPU')} vCPU / {spec.get('memGiB')} GiB"
    )
    hourly = spec.get("hourlyUSD")
    if not hourly:
        return [head + "  unit price unknown (this region's price is not in the catalog)"]
    # 시간 기준(730)을 **답에 함께 싣는다.** 안 실으면 모델이 그 숫자를 기억에서
    # 꺼내 쓰고, 주장 대조에 "도구 출력에 없는 값"으로 걸린다(실측에서 걸렸다).
    lines = [head + f"  ${hourly:.4f}/h"]
    if member.count == 1:
        lines.append(
            f"      about ${hourly * HOURS_PER_MONTH:,.2f}/month ({HOURS_PER_MONTH} hours)"
        )
    else:
        total = hourly * member.count
        lines.append(
            f"      {member.count} × ${hourly:.4f}/h = ${total:.4f}/h"
            f"  (about ${total * HOURS_PER_MONTH:,.2f}/month, {HOURS_PER_MONTH} hours)"
        )
    note = _perf_note(spec)
    if note.status == perf_api.NOTE_WARN:
        lines.append(f"      ⚠ {note.text}")
    elif note.text:
        lines.append(f"      · {note.text}")
    return lines


def _guideline(
    type_id: str,
    provider: str,
    region: str | None,
    spec_name: str | None,
    vcpu: int,
    mem_gib: float,
    plan: str | None = None,
    *,
    output_dir=None,
) -> str:
    """축을 엮은 답 한 벌. 텍스트 조립만 하고 판정은 각 KB가 한다.

    **한 번에 한 계획만 본다.** 번들이 여럿인데 다 늘어놓으면 `core::vm`에서
    17벌이 나오고 그중 16벌은 전 리전 연결 시험 템플릿이다 — 같은 VM 값이
    17번 찍히면서 서로 다른 계획이 한 계획처럼 읽힌다.
    """
    link = core_concept(type_id, output_dir=output_dir or graph_default())
    lookup = link.core_id if link else type_id

    if plan:
        bundle = bundle_dataset.find_bundle(plan, output_dir)
        if bundle is None:
            return f"Resource group '{plan}' not found."
    else:
        bundle = bundle_dataset.default_bundle_for(lookup, output_dir)
    if bundle is None:
        if link is None:
            return (
                f"We do not know what '{type_id}' is in the core layer. The cost and "
                "performance axes attach per core concept only, so with no mapping no "
                "value can be attached. **This does not mean that resource is free** "
                "— it means this dataset has no price axis for it."
            )
        return (
            f"There is no resource group centred on '{type_id}'. **That does not mean "
            "none exists — it means it is not in this dataset.**"
        )

    priceable = concepts_with_spec(str(output_dir or graph_default()))

    def core_of(member_type: str) -> str | None:
        link = core_concept(member_type, output_dir=output_dir or graph_default())
        return link.core_id if link else None

    # 단가는 **한 번만** 고른다 — 구성원마다 고르면 같은 조회를 반복하면서
    # 실패 메시지도 구성원 수만큼 반복된다.
    spec, why = None, None
    if any(core_of(m.type_id) in priceable for m in bundle.members):
        spec, why = _pick_spec(
            provider, region, spec_name, vcpu, mem_gib, output_dir=output_dir
        )

    lines = [f"If you pick {_short(type_id)} — the resource group and its values  [{bundle.name}]"]
    priced: list[str] = []
    unpriced: dict[str, list[str]] = {}
    # **"값이 안 붙는다"의 이유가 둘인데 뜻이 반대다.** 가격 축이 아예 없는 것과,
    # 축은 있는데 이 조건으로 못 고른 것을 한 칸에 넣으면 앞의 것이 "조건을 바꾸면
    # 나온다"로, 뒤의 것이 "이건 원래 공짜"로 읽힌다.
    unresolved: list[str] = []
    for member in bundle.members:
        core_id = core_of(member.type_id)
        times = f" ×{member.count}" if member.count != 1 else ""
        if core_id not in priceable:
            unpriced.setdefault(_TIER_LABEL[member.tier], []).append(
                f"{_short(member.type_id)}{times}"
            )
        elif spec is None:
            unresolved.append(f"{_short(member.type_id)}{times}")
        else:
            priced.extend(_priced_lines(member, spec))

    if priced:
        lines.append("\n[What can be given a value]")
        lines.extend(priced)
    if unresolved:
        lines.append("\n[Would get a value, but none could be picked this time]")
        lines.append(f"  {' · '.join(unresolved)} — {why}")
    if unpriced:
        lines.append("\n[What cannot be given a value — this dataset has no price axis]")
        for label, names in unpriced.items():
            lines.append(f"  ({label}) {' · '.join(names)}")
        # **실측에서 모델이 이 칸을 "무료"로 옮겼다.** 도구가 "모른다"고 한 것을
        # 답변이 "0원"이라 단언한 것이라, 모르는 것을 0으로 채우는 그 실패다.
        # 값과 떼어 놓으면 안 되는 문장이라 칸 바로 아래에 붙인다.
        lines.append(
            "  ※ **This does not mean free.** It means this dataset has no price axis "
            "for that resource; it may well be billed (e.g. public IP · data transfer)."
        )
    elif why and not unresolved:
        lines.append(f"\n⚠ {why}")

    lines.append(
        "\n※ **No total is produced.** Treating the entries above that have no value "
        "as 0 and summing would come out lower than reality. Even the values shown are "
        "VM on-demand list price only — storage, data transfer, and managed services "
        "are not included."
    )
    lines.append(f"※ Resource group evidence: {evidence_name(bundle.evidence)}")
    if bundle.caveat:
        lines.append(f"⚠ [{bundle.name}] {bundle.caveat}")
    if link is not None and link.hedged:
        lines.append(
            f"⚠ The '{_short(type_id)}' → {link.core_id} mapping is **a guess "
            "(reviewed)**. A person matched it by reading the cb-spider driver; the "
            "source never declared the relation — and the values were attached "
            "through that mapping."
        )
    others = [b.name for b in bundle_dataset.bundles_for(lookup, output_dir)
              if b.id != bundle.id]
    if others:
        lines.append(
            f"※ There are also {len(others)} other plans centred on the same resource "
            f"({', '.join(others[:5])}{' …' if len(others) > 5 else ''}). "
            "Give a name and the answer will use that plan."
        )
    return "\n".join(lines)


@function_tool
async def resource_guideline(
    ctx: RunContextWrapper[SessionState],
    resource_type: str,
    provider: str,
    region: str = "",
    spec_name: str = "",
    vcpu_min: int = 2,
    mem_min_gib: float = 4,
    plan: str = "",
) -> str:
    """Pick one resource and get, in a single answer, **what comes with it and
    which of those get a value attached**. It weaves together the resource group
    (bundlekb), unit prices (costkb), and performance warnings (perfkb).

    Use it when the question asks for **both the group and the value**, as in
    "what do I need to bring up one VM, and how much is it?". If only the group
    is asked, bundle_lookup is right; if only the value,
    cost_recommend_specs.

    **No total is produced** — some members have no price axis, so summing would
    come out lower than reality. That fact comes with the answer; pass it
    through as-is.

    Args:
        resource_type: 'core::vm', 'AWS::EC2::Instance', etc. A vendor type is
            moved to the core tier to attach values, and the answer states that
            the mapping is a guess.
        provider: The provider to price against (aws, azure, gcp, alibaba, …).
            **Required** — without it there is no way to decide whose catalog
            the unit price comes from.
        region: The region **code**. For a place name, convert it first with
            cap_resolve_region. **May be left empty** — then the answer takes
            the cheapest across all regions and states which region that was.
            Do not skip this tool and answer from memory just because you do
            not know the region.
        spec_name: If the spec name is already decided (t3.medium, etc.). Empty
            means the cheapest matching the criteria.
        vcpu_min: Minimum vCPU, used when spec_name is absent.
        mem_min_gib: Minimum memory in GiB, used when spec_name is absent.
        plan: Plan (resource group) name. Empty answers with the default
            behavior and, if other plans exist, tells you their names. Feed a
            name printed at the end of that answer back in as-is.
    """
    return _guideline(
        resource_type.strip(),
        provider.strip().lower(),
        region.strip() or None,
        spec_name.strip() or None,
        vcpu_min,
        mem_min_gib,
        plan.strip() or None,
    )


GUIDELINE_TOOLS = [resource_guideline]
