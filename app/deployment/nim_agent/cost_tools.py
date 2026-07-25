"""클라우드 인스턴스 스펙·가격(costkb) 질의 도구(@function_tool).

지식 차원 분담:
- `kb_*`(graphkb)    — 타입 간 의존성: 무엇이 무엇을 필요로 하나
- `cap_*`(capacitykb) — 용량·제약: 무엇이 허용되나 / 한도 / 바꿀 수 있나
- `cost_*`(이 파일)   — 스펙·가격: 무엇을 살 수 있고 얼마인가
- cb-tumblebug MCP    — 현재 상태·실행: 지금 무엇이 떠 있나 / 실제로 만들기

**MCP와의 관계**: MCP의 `recommend_vm_spec`은 `cost_recommend_specs`와 **같은 질문에
답하는 다른 소스**다(축이 다른 게 아니다). 이쪽은 서버·자격증명 없이 항상 동작하는
기준선이고, MCP가 연결돼 있으면 라이브 카탈로그라 더 정확하므로 그쪽이 우선이다.

**계획 게이트**: 카탈로그의 cloud_sizing은 "스펙 추천 → 비용 추정"으로 이어지는 다단계
작업이라, record_plan이 먼저 기록돼야 실행된다. 프롬프트로는 순서가 지켜지지 않고
(모델 3종 모두 실패) NIM은 tool_choice도 무시하므로, 도구가 직접 거부한다.
자세한 배경은 `nim_agent/session.py` 참고.
"""

from __future__ import annotations

from agents import RunContextWrapper, function_tool

from app.deployment.costkb import agent_api
from app.deployment.costkb.agent_api import HOURS_PER_MONTH
from app.deployment.perfkb import agent_api as perf_api
from app.deployment.perfkb import dataset as perf_dataset

from .session import SessionState


def _perf_note(spec: dict) -> perf_api.PerfNote:
    """조인 1회. 산출물이 깨져 있어도 추천 전체가 죽지 않게 감싼다.

    perfkb 로드는 스키마 검증까지 하므로 잘린 JSON·스키마 불일치에서 예외가 난다.
    성능 경고는 부가 정보일 뿐이라 여기서 삼키고 "모른다"로 떨어뜨린다.
    """
    try:
        return perf_api.recommend_note(
            spec.get("provider", ""), spec.get("specName", ""), spec.get("id")
        )
    except Exception:
        return perf_api.PerfNote(perf_api.NOTE_NOT_BUILT)


def _perf_annotate(spec: dict) -> str | None:
    """추천 후보에 성능 소견을 붙인다 — costkb×perfkb 조인 지점.

    여기(도구 계층)에서 조인하므로 두 KB는 서로 import하지 않는다. 기호도 여기서
    정한다 — costkb는 이 문자열이 경고인지 고지인지 모른다.

    후보 5건마다 "확인됨"을 적으면 노이즈라, **경고와 정보 없음만** 줄을 만든다.
    침묵의 의미는 `_perf_footer`가 블록 끝에서 한 번 밝힌다.
    """
    note = _perf_note(spec)
    if note.status == perf_api.NOTE_WARN:
        return f"⚠ {note.text}"
    return f"· {note.text}" if note.text else None


def _perf_footer(specs: list[dict]) -> str | None:
    """후보 목록 끝에 붙는 한 줄 — "주석 없음"이 무슨 뜻인지 밝힌다.

    이게 없으면 (a)성능 확인됨 (b)perfkb 미빌드 두 경우의 출력이 같아진다. 예전에는
    (c)레코드 없음 (d)미추적 프로바이더까지 네 경우가 전부 같았다(결함 C4).
    """
    notes = [_perf_note(spec) for spec in specs]
    if all(n.status == perf_api.NOTE_NOT_BUILT for n in notes):
        try:
            damaged = perf_dataset.load_warning()
        except Exception:
            damaged = None
        if damaged:
            # 손상과 미빌드는 사용자가 할 일이 다르다 — 하나는 지우고 다시, 하나는 그냥 빌드.
            return f"Could not attach performance warnings — {damaged}"
        return (
            "The performance knowledge base is missing, so performance traps "
            "(burst, previous generation) were not checked — python -m perfkb build"
        )
    # **"주석이 없는 후보"라고 뭉뚱그리면 안 된다.** IBM처럼 레코드는 있는데
    # 버스트·세대 신호가 없는 프로바이더가 섞이면 그 후보도 "확인됨"으로 읽힌다 —
    # 우리가 확인한 적 없는 것을 확인했다고 말하는 셈이다.
    partial = any(n.status == perf_api.NOTE_PARTIAL for n in notes)
    confirmed = any(n.status == perf_api.NOTE_OK for n in notes)
    if confirmed and partial:
        return (
            "Only candidates with no ⚠ mark and no · mark are performance-confirmed "
            "(sustained CPU guaranteed, current generation). "
            "A · does not mean we confirmed it."
        )
    if confirmed:
        return (
            "Candidates with no annotation are performance-confirmed "
            "(sustained CPU guaranteed, current generation)."
        )
    if partial:
        # 후보가 **전부** partial이면 위 두 갈래에 안 걸려 꼬리말이 통째로 사라진다.
        # 그러면 다시 침묵이 안전 신호로 읽힌다 — 결함 C4가 되돌아오는 자리다.
        return (
            "For these candidates the source carries no signal to judge burst or "
            "generation, so **performance was not confirmed.** No warning does not "
            "mean nothing is wrong."
        )
    return None

_PLAN_REQUIRED = (
    "STOP. Record a plan with record_plan first. Cloud resource sizing is a "
    "multi-step task — per-component sizing → spec recommendation → cost total — so "
    "it runs only after a plan is on record. Call record_plan, then call this tool "
    "again."
)


def _needs_plan(ctx: RunContextWrapper[SessionState]) -> bool:
    """계획 게이트가 닫혀 있는지. 세션 상태가 없으면 게이트를 적용하지 않는다."""
    return isinstance(ctx.context, SessionState) and not ctx.context.has_plan


@function_tool
def cost_recommend_specs(
    ctx: RunContextWrapper[SessionState],
    vcpu_min: int = 2,
    mem_min_gib: float = 4,
    provider: str | None = None,
    region: str | None = None,
    sort_by: str = "cost",
    limit: int = 5,
    architecture: str = "x86_64",
    require_accelerator: bool = False,
) -> str:
    """Recommend VM spec candidates meeting the requirements, with hourly rates.

    No credentials are required. If the performance knowledge base (perfkb) is
    built, performance warnings are attached to the candidates — they point out
    traps such as burst instances or previous generation specs that **looking at
    price alone would miss**.

    **A plan must be recorded with record_plan before this runs.**

    Args:
        vcpu_min: Minimum vCPU count.
        mem_min_gib: Minimum memory (GiB).
        provider: 'aws' | 'azure' | 'gcp' | 'tencent' | 'alibaba' | 'ibm' | 'ncp' |
            'kt' | 'nhn' | 'openstack'. If unset, all providers.
        region: Region substring (e.g. 'us-east', 'ap-northeast'). If unset, all.
        sort_by: 'cost' (cheapest first, default) | 'vcpu' | 'memory'.
        limit: Number of candidates to return (default 5).
        architecture: 'x86_64' (default) | 'arm64' | 'any'. This is the same default
            as cb-tumblebug's live recommendation, so changing it may make the
            result differ from the live one.
        require_accelerator: If True, keeps **only specs with an accelerator (GPU
            etc.)**. A question like "tell me the GPU instances I can use in this
            region" is answered in one call with this plus `region`. For GPU
            **model names**, use perf_instance_profile. Some GPU instances are
            arm64, so architecture='any' may be needed.
    """
    if _needs_plan(ctx):
        print("\n[spec recommend] no plan → refused")
        return _PLAN_REQUIRED
    arch = None if architecture.lower() == "any" else architecture
    print(
        f"\n[spec recommend] vcpu>={vcpu_min}, mem>={mem_min_gib}GiB, "
        f"provider={provider or 'any'}, region={region or 'any'}, "
        f"arch={arch or 'any'}, sort={sort_by}"
        + (", accelerator only" if require_accelerator else "")
    )
    return agent_api.recommend_specs(
        vcpu_min, mem_min_gib, provider, region, sort_by, limit,
        architecture=arch, require_accelerator=require_accelerator,
        annotate=_perf_annotate, footer=_perf_footer,
    )


@function_tool
def cost_estimate_monthly(
    ctx: RunContextWrapper[SessionState],
    hourly_usd: float,
    count: int = 1,
    hours_per_month: float = HOURS_PER_MONTH,
    running_total_usd: float = 0.0,
) -> str:
    """Compute the monthly cost from an hourly rate (node count, uptime applied).

    **A plan must be recorded with record_plan before this runs.**
    Use the unit price that cost_recommend_specs gave you (do not mix it with
    prices from web search). Never do the monthly arithmetic in your head —
    always compute it with this tool.

    **That includes the total across components.** For the second and later
    components pass `running_total_usd` — the monthly cost you already got back
    for the earlier ones — and this tool returns the running total too. The
    total is the number the user sets a budget against, so it must come from
    here, not from adding the components up yourself.

    Args:
        hourly_usd: Hourly USD unit price of one instance.
        count: Number of instances (default 1).
        hours_per_month: Monthly running hours (default 730 = always on).
        running_total_usd: Monthly cost already computed for the other
            components. Omit for the first component.
    """
    if _needs_plan(ctx):
        print("\n[cost estimate] no plan → refused")
        return _PLAN_REQUIRED
    total = hourly_usd * hours_per_month * count
    print(
        f"\n[cost estimate] ${hourly_usd}/h × {hours_per_month}h × {count} nodes "
        f"= ${round(total, 2)}/month"
    )
    return agent_api.estimate_monthly_cost(
        hourly_usd, count, hours_per_month, running_total_usd
    )


#: 할인 축이 있는 프로바이더. **여기 없으면 "할인이 없다"가 아니라 "안 담았다"다.**
_DISCOUNT_PROVIDERS = ("gcp", "azure")


# azure 할인 축이 저장소에 없는 것이 기본인 이유: 재배포 허가가 없어 커밋하지 않는다.
@function_tool
def cost_discount_pricing(
    provider: str, spec_name: str, region: str | None = None
) -> str:
    """Look up spot, reserved and committed prices — a separate axis.

    It is separate because the mirror holds **on-demand list price only**.
    The source and what is included differ by provider:

    - `gcp`   — spot · 1-year/3-year committed (Cyclenerd price table). Its
      on-demand is a **different snapshot** from our mirror, so for regions that
      disagree the tool also states the "baseline on-demand".
    - `azure` — spot · 1-year/3-year reserved · 1-year/3-year savings plans
      (Azure Retail Prices API). **This axis is by default not in the
      repository.** If it is missing, the tool tells you the command that fetches
      it, so pass it through as-is — it does not mean "there is no discount".

    **Other providers are not included.** Using this tool on aws etc. answers
    "not included", and that **does not mean AWS has no spot — it means we did
    not include it**. Pass that distinction through as-is.

    Args:
        provider: 'gcp' | 'azure'. **Required** — the source differs by provider.
        spec_name: Spec name. e.g. 'e2-standard-4', 'Standard_D2s_v5'.
        region: Region (optional). Without it, a few representative regions are
            shown.
    """
    key = provider.strip().lower()
    print(f"\n[cost query] discount pricing: {key} {spec_name!r} region={region!r}")
    if key == "gcp":
        return agent_api.discount_pricing(spec_name, region)
    if key == "azure":
        return agent_api.azure_discount_pricing(spec_name, region)
    return (
        f"Discount (spot / reserved) pricing for '{provider}' is not in this dataset. "
        f"Only {', '.join(_DISCOUNT_PROVIDERS)} are included. "
        "**This does not mean that provider has no discounts — it means we did not "
        "include them.**"
    )


# 이 도구가 있기 전에는 이름으로 묻는 질문이 웹 검색으로 샜습니다.
# provider 인자가 필요한 경우: 실측상 이름이 겹치는 것은 `m1.*` 4종뿐.
@function_tool
def cost_describe_spec(
    spec_name: str, provider: str | None = None, region: str | None = None
) -> str:
    """Look up **one** instance by name — vCPU, memory, price, regions.

    Use this for a question like "how many GiB of memory does n2-highmem-8 have?".
    `cost_recommend_specs` is a condition filter, so it cannot find a spec when
    you only know its name.

    Without a region you get a **price range** (the unit price differs by region).
    Performance characteristics (burst, generation, CPU/GPU model) are in
    `perf_instance_profile`.

    Args:
        spec_name: Instance name. e.g. 'n2-highmem-8', 't3.medium', 'Standard_D2_v5'.
        provider: Needed only when the name collides across providers.
        region: When you want the unit price of one region only.
    """
    print(f"\n[cost query] describe spec: {spec_name!r} provider={provider!r} region={region!r}")
    text = agent_api.describe_spec(spec_name, provider, region)
    return text + _perf_hint(spec_name, provider)


def _perf_hint(spec_name: str, provider: str | None) -> str:
    """성능 축에 이 스펙의 프로파일이 있으면 그리로 가리킨다.

    **축을 늘리는 것과 축에 닿게 하는 것은 다른 일이다** — 카탈로그 답만 주면
    "이게 버스트인가", "구세대인가"를 모른 채 고르게 된다. KB끼리 import하지
    않는 규약이라 이 조인은 도구 계층에서 한다(`capacity_tools._perf_pointer`와 같다).
    """
    try:
        from app.deployment.costkb.dataset import find_by_name
        from app.deployment.perfkb.agent_api import hardware_facts, recommend_note

        rows = find_by_name(spec_name, provider)
        if not rows:
            return ""
        prov = rows[0]["provider"]
        name = rows[0]["specName"]
        note = recommend_note(prov, name)
        hardware = hardware_facts(prov, name)
    except Exception:
        return ""

    out = ""
    if note.status == "warn" and note.text:
        out += f"\n\n⚠ {note.text}"
    elif note.status not in ("no_record", "untracked", "not_built"):
        out += (
            f"\n\n※ Performance characteristics (burst or not · generation · EBS "
            f"bandwidth) are in perf_instance_profile('{prov}', '{name}')."
        )
    # **하드웨어는 가리키지 말고 실어 준다.** 이름 조회 도구를 만들자 모델이 GPU
    # 질문에도 이걸 부르기 시작했고, 답은 근거가 있었지만 성능 축에만 있는
    # 아키텍처(`Turing`)와 정확한 SKU(`A100-SXM4-40GB`)가 답에서 사라졌다.
    # 가리키기만 하면 모델은 이미 답을 얻었다고 보고 더 안 부른다.
    if hardware:
        out += f"\n\nHardware (performance axis): {hardware}"
    return out


COST_TOOLS = [
    cost_recommend_specs,
    cost_estimate_monthly,
    cost_discount_pricing,
    cost_describe_spec,
]
