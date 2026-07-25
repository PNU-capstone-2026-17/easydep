"""클라우드 리소스 용량·제약(capacitykb) 질의 도구(@function_tool).

지식 차원 분담:
- `kb_*`(graphkb)   — 타입 간 의존성: 무엇이 무엇을 필요로 하나
- `cap_*`(이 파일)  — 용량·제약: 무엇이 허용되나 / 한도 / 바꿀 수 있나
- `cost_*`(costkb) — 인스턴스 카탈로그·가격: 무엇을 살 수 있고 얼마인가
- cb-tumblebug MCP — 현재 상태·실행: 지금 무엇이 떠 있나 / 실제로 만들기

용량 한도는 LLM이 특히 자주 지어내는 영역이라(예: "EBS 최대 16TB"),
에이전트 instructions에서 반드시 이 도구를 쓰도록 지시한다.
"""

from __future__ import annotations

from agents import function_tool

from capacitykb import agent_api


# 실측에서 에이전트가 바로 이 질문에 도구를 하나도 부르지 않고 "리전별 제공 여부는
# 조회되지 않는다"고 답했다. 그런 말은 어디에도 없었지만, **된다는 말도 없었다.**
@function_tool
def cap_check_value(
    resource_type: str,
    property_name: str | None = None,
    value: str | None = None,
    context: str | None = None,
) -> str:
    """What a property may hold — and, if you name a value, whether it is allowed.

    Two modes, one question ("can this property take this?"):

    - **`value` given → a verdict.** Prefer this whenever the user asks whether
      something is possible. If you instead read a table and compare it
      yourself, the knowledge base does not vouch for that comparison.
      Whether an instance type is usable in a given region is judged here too —
      `('AWS::EC2::Instance', 'InstanceType', 'p5.48xlarge',
      context='Region=af-south-1')`.
    - **`value` omitted → the limits, allowed values (enum), pattern, and
      default.** Omit `property_name` as well to see the whole type's
      constraints. Allowed values that differ per region live here too:
      `('AWS::EC2::Instance', 'InstanceType')` lists the instance types for all
      38 regions with their conditions.

    A constraint the source did not state (one extracted from prose) is never
    used as grounds to reject a value; it is reported for reference only.

    Args:
        resource_type: Type name. e.g. 'AWS::EC2::Volume',
            'aws::AWS::Lambda::Function',
            'Microsoft.ContainerService/managedClusters'.
        property_name: Property name. e.g. 'Size', 'Timeout',
            'EphemeralStorage/Size'. Omit to see the whole type.
        value: The value to set. A number is read as a number. e.g. '100000',
            'gp3'. Omit to see what is allowed instead of judging one value.
        context: Other properties decided alongside it. Join `name=value` pairs
            with commas, as in `'VolumeType=gp2'`, `'Region=af-south-1'`.
            **If the limit or the allowed values depend on something else, this
            is required for a verdict** — the EBS volume size cap differs by
            type (gp2 16,384 / gp3 65,536 / standard 1,024 GiB), and allowed
            instance types differ across all 38 regions.
            Without it, the tool lists "which condition gives which value" and
            tells you what it needs.
    """
    if value is None:
        # 값이 없으면 **판정할 것이 없다** — 한도와 허용값을 보여 준다.
        # 예전에는 이것이 cap_property_limits·cap_allowed_values 두 도구였는데,
        # 키가 (타입, 속성)으로 같고 답하는 질문도 같아 결정 지점만 늘렸다.
        where = f"{resource_type}" + (f".{property_name}" if property_name else "")
        print(f"\n[capacity query] what is allowed: {where}")
        parts = [agent_api.property_limits(resource_type, property_name)]
        if property_name:
            parts.append(agent_api.allowed_values(resource_type, property_name))
        return "\n\n".join(parts) + _perf_pointer(resource_type)

    if not property_name:
        return (
            "To judge a value I need the property name — "
            f"which property of {resource_type} is this value for?"
        )
    parsed = _parse_context(context)
    shown = f" ({context})" if parsed else ""
    print(f"\n[capacity query] value verdict: {resource_type}.{property_name} = {value!r}{shown}")
    return agent_api.check(resource_type, property_name, _coerce(value), context=parsed)


def _parse_context(text: str | None) -> dict | None:
    """`'VolumeType=gp2, Tier=Premium'` → `{"VolumeType": "gp2", "Tier": "Premium"}`.

    모델이 채우는 칸이라 형식을 느슨하게 받는다. 못 알아들은 조각은 버리되,
    하나도 못 읽으면 None을 돌려 "문맥 없음"과 같게 만든다 — 반쯤 읽은 문맥으로
    판정하면 어느 조건이 적용됐는지 아무도 모른다.
    """
    if not text:
        return None
    out: dict[str, str] = {}
    for chunk in text.replace(";", ",").split(","):
        name, sep, value = chunk.partition("=")
        if sep and name.strip() and value.strip():
            out[name.strip()] = value.strip()
    return out or None


#: **어느 타입이 "인스턴스 종류"를 고르는 자리인가.** 3줄짜리 사람 검수표다.
#: 규칙으로 유도하려 하면 짐작이 되고, 이건 짐작할 게 아니라 아는 사실이다.
_COMPUTE_TYPES = {
    "aws::AWS::EC2::Instance": "aws",
    "azure::Microsoft.Compute/virtualMachines": "azure",
    "gcp::ComputeInstance": "gcp",
}


def _perf_pointer(resource_type: str) -> str:
    """"이 타입의 하드웨어는 성능 축에 있다"는 한 줄. 아니면 빈 문자열.

    **축을 늘리는 것과 축에 닿게 하는 것은 다른 일이다.** 실측에서
    "g5g.xlarge에 어떤 GPU가 달렸어?"를 물었더니 모델이 용량 축을 뒤지다
    `AWS::EC2::Instance.Gpu`라는 없는 속성까지 조회하고 웹으로 나갔다.
    그때 우리는 GPU 571건을 쥐고 있었는데, **다른 축에** 있었다.

    같은 질문을 "몇 개, 어떤 모델이야?"로 물으면 성능 도구로 바로 갔다.
    문구 하나 차이로 갈리므로 **양쪽 문이 다 알아야 한다.**

    graphkb→capacitykb 때와 같이 도구 계층에 둔다 — 단방향 규약상 capacitykb는
    perfkb를 import할 수 없고, 양쪽을 다 보는 층은 여기뿐이다.
    """
    try:
        from capacitykb.agent_api import load_merged
        from capacitykb.query import resolve_type
        from perfkb.agent_api import hardware_summary

        capacity = load_merged()
        if capacity is None:
            return ""
        type_id = resolve_type(capacity, resource_type)
        provider = _COMPUTE_TYPES.get(type_id)
        if not provider:
            return ""
        summary = hardware_summary(provider)
    except Exception:
        return ""
    if not summary:
        return ""
    return (
        "\n  ※ **Hardware that differs per instance type**, such as the CPU or "
        f"GPU model, is not on this axis — it is on the performance axis ({summary}).\n"
        f"     See perf_instance_profile('{provider}', '<instance type>')."
    )



# **도구 셋을 하나로 접었다(2026-07-25).** 셋 다 인자가 `resource_type` 하나뿐이라
# 모델이 "불변 속성? 비밀값? 작업 시간?"을 매번 갈라야 했다 — 문헌이 최다 실패
# 모드로 꼽는 **모호한 결정 지점**이고, 실제로 오늘 고친 실패가 대부분 오라우팅이었다.
# 출력은 커지지만(합쳐서 252~753 tok) 호출 한 번이 요청 하나(입력 ≈12,000 tok)를
# 아끼므로 총량은 오히려 준다.
@function_tool
def cap_resource_constraints(resource_type: str) -> str:
    """All deployment-time constraints of one resource type, in a single call:

    - **which properties force a delete-and-recreate when changed** (needed for
      any change plan)
    - **which are write-only secrets** set at deploy time that the API cannot
      read back (passwords, keys, connection strings) — advise managing those
      separately, e.g. in a key vault, since a lost value cannot be recovered
    - **whether create / delete / update is long-running**, plus the available
      actions, so deployment scripts can set timeouts and split steps

    Secrets and operation duration are carried only by the Azure specification
    today. For other providers the answer says "not tracked" — **that is not the
    same as "none", and "the source does not say" is not "it is fast"**. Pass
    those distinctions through as-is.

    Args:
        resource_type: Type name. e.g. 'AWS::EC2::Subnet',
            'Microsoft.DBforMySQL/flexibleServers', 'gcp::ComputeDisk'.
    """
    print(f"\n[capacity query] resource constraints: {resource_type}")
    return "\n\n".join((
        agent_api.immutable(resource_type),
        agent_api.secrets(resource_type),
        agent_api.operation_time(resource_type),
    ))



@function_tool
def cap_service_quota(keyword: str) -> str:
    """Find account/subscription-level caps (service quotas) by keyword.

    e.g. "how many subnets per vNet?" → keyword='subnet'.
    Quota data is currently included for Azure only (AWS/GCP have no public
    machine-readable source).

    Args:
        keyword: Search term. e.g. 'subnet', 'virtual network', 'vCPU'.
    """
    print(f"\n[capacity query] service quota: {keyword!r}")
    return agent_api.service_quota(keyword)


@function_tool
def cap_resolve_region(place: str, provider: str | None = None) -> str:
    """Turn a human place name into a region code. Knows 10 providers.

    **Call this before passing a region to any other tool.** The other tools are
    indexed by region code, so passing a plain place name like 'Seoul' finds
    nothing even when the data exists.

    **Seoul has a different code in every provider** — aws `ap-northeast-2`,
    gcp `asia-northeast3`, azure `koreacentral`, tencent `ap-seoul`.
    So for a GCP question pass `provider='gcp'` and use that code. Passing
    another provider's code yields "not found" even when the data exists.

    Args:
        place: A place or region name, in **either Korean or English**, or a
            region code. e.g. '서울', 'Seoul', '도쿄', 'Tokyo', 'ap-northeast-2'.
            Pass the user's wording straight through — do not translate it first.
        provider: Provider to narrow to (optional). aws · azure · gcp · alibaba ·
            tencent · ibm · ncp · kt · nhn · openstack.
    """
    print(f"\n[capacity query] resolve region: {place!r} provider={provider!r}")
    from envkb.regions import region_lookup

    return region_lookup(place, provider)


@function_tool
def cap_service_regions(service: str) -> str:
    """See which regions have an endpoint for this service.

    **Note**: a region missing from the list does not mean "unusable", it means
    "this data does not say". Do not answer that it is unavailable.

    Args:
        service: CFN type (`AWS::EC2::Instance`) or SDK service name (`ec2`).
    """
    print(f"\n[capacity query] service regions: {service!r}")
    return agent_api.where_available(service)


@function_tool
def cap_region_carbon(provider: str, region: str | None = None) -> str:
    """Carbon intensity of a region (gCO2eq/kWh). Without a region, listed
    cleanest first.

    Use it to answer "at equal performance, which region emits less carbon".
    Only aws · azure · gcp are included today — for other providers say "not
    tracked", not "none".

    **Do not compare providers against each other.** GCP figures are published
    values while AWS and Azure are estimates, so the methodologies differ. Pass
    the limitation notice the tool attaches through to the user as-is.

    Args:
        provider: aws · azure · gcp.
        region: Region code (optional). e.g. 'asia-northeast3',
            'ap-northeast-2'.
    """
    from envkb import carbon

    print(f"\n[capacity query] region carbon: {provider!r} region={region!r}")
    return carbon.describe(provider, region)


@function_tool
def cap_region_latency(source_region: str, target_region: str | None = None) -> str:
    """Round-trip network latency between regions (ms). Without a target,
    listed nearest first.

    Use it for questions like "how much latency does going multi-region add?"
    or "which region is nearest to Seoul?". **Cross-provider pairs exist too**
    — AWS↔Azure in the same city is 3.3 ms.

    ⚠️ **Not a vendor-guaranteed SLA — a value cb-tumblebug measured by
    launching VMs.** The source carries no measurement time, so do not say "it
    is still this today".

    Args:
        source_region: `provider-region` form. e.g. 'aws-ap-northeast-2'.
        target_region: Same form (optional). Given, only that pair.
    """
    from envkb import latency

    print(f"\n[capacity query] region latency: {source_region!r} → {target_region!r}")
    return latency.describe(source_region, target_region)


# 실측상 Linux만 있고 Windows 기본 이미지는 없습니다.
@function_tool
def cap_basic_image(
    provider: str, region: str | None = None, architecture: str | None = None
) -> str:
    """Return the **default OS image** to use in this region.

    Answers "what image ID do I need to launch a VM?". The resource-family tools
    say "an image is required" but cannot say which one, so this tool fills that
    gap.

    ⚠️ **Match the architecture to the spec.** An x86_64 image on an arm64 spec
    such as `g5g.xlarge` will not boot. `cost_describe_spec` tells you a spec's
    architecture.

    **Not the "best image" — the one cb-tumblebug picked as its default.** It is
    neither vendor-recommended nor the newest. Only Linux is present; there is
    no default Windows image.

    Args:
        provider: aws · azure · gcp · alibaba · ibm · ncp · nhn · tencent · kt.
        region: Region code (optional). 'common' when region-agnostic, as with
            Azure.
        architecture: 'x86_64' | 'arm64' (optional).
    """
    from envkb import images

    print(
        f"\n[capacity query] basic image: {provider!r} region={region!r} arch={architecture!r}"
    )
    return images.describe(provider, region, architecture)


@function_tool
def cap_service_lifecycle(service: str, version: str | None = None) -> str:
    """End-of-support date of a managed service version. "How long can EKS 1.28
    be used?".

    You may ask by product name (`amazon-eks`) or by resource type
    (`AWS::EKS::Cluster`).
    **"End date undetermined" is not "ended"** — it is simply not set yet, so
    pass it through as-is. For a service that is not included, the answer is
    "not in this source", not "it has no end date".

    Args:
        service: Product name or resource type. e.g. 'amazon-eks',
            'AWS::RDS::DBInstance'.
        version: Version (optional). e.g. '1.28', '8.0'.
    """
    from envkb import lifecycle

    print(f"\n[capacity query] lifecycle: {service!r} version={version!r}")
    return lifecycle.describe(service, version)


# `cap_operation_time`은 `cap_resource_constraints`로 접었다 — 인자가 같고
# (`resource_type`) 배포 계획을 세울 때 불변 속성·비밀값과 **함께** 필요한 사실이다.


@function_tool
def cap_csp_supports(csp: str | None = None, resource: str | None = None) -> str:
    """Whether the multi-cloud tool (cb-spider) handles that resource on this
    CSP.

    **This is the tool's coverage, not a fact about the cloud.** Unsupported
    does **not** mean "that CSP lacks the feature"; it means "this tool cannot
    handle it in one uniform way" — always pass that distinction through as-is.
    Whether the CSP offers the service is not what this tool answers.

    Args:
        csp: Provider. aws · azure · gcp · alibaba · tencent · ibm · ncp · nhn ·
            kt · ktclassic · openstack · oracle. Omit for the list of CSPs
            handled.
        resource: Core resource. vNet · subnet · securityGroup · sshKey · vm ·
            nlb · k8sCluster · k8sNodeGroup · dataDisk · customImage.
    """
    from envkb import cbspider

    print(f"\n[capacity query] CSP support: csp={csp!r} resource={resource!r}")
    return cbspider.describe(csp, resource)


def _coerce(raw: str) -> float | str:
    """LLM이 문자열로 넘긴 값을 가능하면 숫자로 바꾼다."""
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return raw
    return int(number) if number.is_integer() else number


CAPACITY_TOOLS = [
    # 한도·허용값 조회는 cap_check_value의 "값 없음" 모드로 접었다 —
    # 키가 (타입, 속성)으로 같고 답하는 질문도 같아 결정 지점만 늘렸다.
    cap_check_value,
    cap_resource_constraints,
    cap_service_quota,
    cap_resolve_region,
    cap_service_regions,
    cap_region_carbon,
    cap_region_latency,
    cap_basic_image,
    cap_service_lifecycle,
    cap_csp_supports,
]
