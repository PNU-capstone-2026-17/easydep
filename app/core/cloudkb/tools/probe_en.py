"""영어 질의 프로브 — **짝지은 실험**이지 새 회귀 스위트가 아니다.

    RUN_AGENT_TESTS=1 uv run pytest app/core/cloudkb/tests/test_agent_regression_en.py -v
    python -m app.core.cloudkb.tools.probe_en --repeat 3      # 흔들림까지 보려면

## 왜 필요한가 — 공백의 정체

도구 출력·판정문·고지는 영어로 넘어갔는데(2026-07-25), 한동안 **영어로 물어본 측정이
한 번도 없었다.** 그래서 우리가 가진 증거는 전부 "한국어로 물었을 때 영어 도구가 잘
라우팅된다"였고, 시스템 타겟인 영어 질의의 증거는 0건이었다.

**2026-07-29 현황**: 처음 10건을 돌렸고(9/10 통과, `EN7`만 짝과 갈렸다), 그 뒤 세어
보니 그 10건이 도구 **9/31**만 건드리고 있었다 — 대상 언어로 한 번도 안 물어본 도구가
22개였다. 도구 축을 기준으로 **31건까지 늘려 31/31**을 채웠다. 새 질문을 지어낸 것이
아니라 전부 한국어 프로브의 번역쌍이다.

가정으로 넘길 수 없는 이유가 있다. 이 저장소는 **질의 언어가 라우팅을 바꾼 사례를
직접 겪었다** — "로컬 SSD **용량**"의 '용량'이라는 한국어 단어 때문에 질문이 용량
축으로 흘러갔고 결국 웹검색으로 샜다. 도구 설명이 영어인 상태에서 한국어 단어가
경로를 틀었다면, **영어 단어도 그럴 수 있다**는 것이 기본값이어야 한다.

## 설계 — 짝을 지어야 원인을 말할 수 있다

여기 프로브는 새로 지어낸 질문이 아니라 **기존 프로브의 영어 번역쌍**이다.
`PAIRED_WITH`가 그 대응이다. 짝을 짓지 않으면 결과를 해석할 수 없다 —
영어 프로브가 실패했을 때 "영어라서"인지 "그 질문이 원래 어려워서"인지 갈리지
않기 때문이다. 같은 사실을 묻는 두 질의가 **다른 결과**를 낼 때만 언어의 효과다.

기대값(`want_any`)은 **되도록 언어 중립인 것**으로 골랐다 — `16384`·`0.0416`·
`AvailabilityZone`·`375` 같은 숫자와 식별자다. 이유는 둘이다.

1. 한국어 프로브와 **같은 기대**를 쓸 수 있어 비교가 성립한다.
2. 답변 문구를 검사하면 "표현이 바뀌었다"와 "동작이 틀렸다"가 섞인다
   (`agent_probe.py`의 규율 그대로).

문구를 봐야만 하는 셋(거절·유보·되묻기)은 영어 후보를 넉넉히 둔다. **좁게 잡으면
옳은 행동을 벌한다** — 3-1이 정확한 거절을 실패로 찍었던 전례가 있다.

## 읽는 법 — 한 번 돌린 결과로 결론 내지 말 것

`agent_probe.py`가 기록한 실측이 여기에도 그대로 적용된다: 지시문 언어 실험에서
3회차 총점이 전부 같았는데 **실패한 4건이 매번 달랐고**, 7건이 회차마다 뒤집혔다.
언어 효과를 재려면 **`--repeat`로 양쪽을 같은 횟수 돌려 통과율로** 비교해야 하고,
그때 재시도는 꺼야 한다(재시도는 흔들림을 가리는 장치다).

판정 기준을 미리 적어 둔다 — 결과를 보고 정하면 사후 정당화가 된다.

    같은 짝에서 통과율 차가 N회 중 2회 이상 벌어지면  → 언어 효과 의심, 개별 조사
    한쪽만 web_search로 새면                          → 라우팅 결함 (문구 문제 아님)
    양쪽 다 실패하면                                  → 언어와 무관한 결함
"""

from __future__ import annotations

from .agent_probe import Probe

#: 영어 프로브 → 대응하는 한국어 프로브. **이 표가 없으면 결과 해석이 안 된다.**
PAIRED_WITH: dict[str, str] = {
    "EN1": "3-9",    # 조건부 한도 판정
    "EN2": "3-3",    # 가격 — 웹 누출
    "EN3": "1-2a",   # 불변 속성
    "EN4": "3-6",    # 회사 간 대응 + 계획 남용
    "EN5": "1-1b",   # 삭제 영향
    "EN6": "3-1",    # 없는 축 거절
    "EN7": "3-11",   # 낡음 고지 생존
    "EN8": "X5",     # 라우팅 함정 (로컬 SSD)
    "EN9": "X1",     # 자문 축 + advisory 고지
    "EN10": "R4",    # 모호한 물음에 단정하지 않기
    # --- 2026-07-29 확장: **도구 축을 기준으로** 짝을 늘렸다 ------------------
    #
    # 세어 보니 영어 프로브 10건이 도구 **9/31**만 건드리고 있었다. 시스템의 대상
    # 언어가 영어인데(도구 출력·판정문·고지가 전부 영어다) 그 언어로 한 번도 안
    # 물어본 도구가 22개였다는 뜻이다. 아래는 그 22개를 정확히 메운다 —
    # 새 질문을 지어낸 것이 아니라 **한국어 프로브의 번역쌍**이다(짝이 없으면
    # "영어라서"인지 "그 질문이 원래 어려워서"인지 못 가린다).
    "EN11": "BU1",   # 번들 — 함께 만들어지는 것
    "EN12": "IM1",   # 기본 이미지
    "EN13": "K1",    # CSP 지원 여부(드라이버 커버리지)
    "EN14": "C1",    # 리전 탄소
    "EN15": "LT1",   # 리전 간 지연
    "EN16": "R2",    # 리전 해석
    "EN17": "E1",    # 수명주기
    "EN18": "D4",    # 서비스 쿼터
    "EN19": "CR1",   # 서비스 엔드포인트 리전
    "EN20": "P2",    # 할인 가격(예약)
    "EN21": "GL2",   # 총액 — 합계의 한계 고지
    "EN22": "H3",    # 스펙 추천
    "EN23": "X7",    # 설계도 → 배포 구성
    "EN24": "G1",    # 생성 순서
    "EN25": "KD1",   # 타입 상세
    "EN26": "KR1",   # 집계(랭킹)
    "EN27": "SM2",   # 타입 검색·동치
    "EN28": "N4",    # 성능 비교
    "EN29": "PB1",   # 지속 EBS 대역폭
    "EN30": "CF6",   # 사이징 규칙
    "EN31": "SZ1",   # 서브넷 수용량
    "EN32": "G2",    # 의존성 — **필수가 없다**는 답과 그 유보
    "EN33": "G3",    # 교차 축 — 의존성 + 제약
}

#: X7(설계도 → 배포 구성)의 입력 JSON을 **그대로** 가져온다.
#:
#: 손으로 옮겨 적으면 두 질의의 입력이 조금씩 달라지고, 그러면 언어 효과를 재는
#: 실험에서 **바뀐 것이 언어만이 아니게** 된다. 지시문 한 줄만 영어로 바꾼다.
def _english_design_query() -> str:
    from .agent_probe import PROBES

    korean = next(p.query for p in PROBES if p.id == "X7")
    return ("Build a deployment configuration from this design JSON: "
            + korean.split(": ", 1)[1])

PROBES_EN: tuple[Probe, ...] = (
    Probe("EN1", "Can I create a gp2 volume of 30,000 GiB?",
          "짝: 3-9. **조건부 한도를 지식베이스가 판정하는가.** 기대값이 숫자라 "
          "한국어 짝과 완전히 같은 기준으로 비교된다.",
          want_tools=("cap_check_value",), want_any=("16384",)),

    Probe("EN2", "How much does t3.medium cost per hour?",
          "짝: 3-3. **웹 검색으로 새지 않는가.** 검색 가격과 데이터셋 가격이 섞이면 "
          "합계 기준이 어긋난다.",
          forbid_tools=("web_search",), want_any=("0.0416",)),

    Probe("EN3", "Which properties of an AWS subnet cannot be changed later?",
          "짝: 1-2a. 불변 속성을 기억이 아니라 도구로 답하는가. 한국어 짝처럼 "
          "**프로바이더를 질의에 밝혀** 모호성을 제거했다 — 안 밝히면 되묻는 것이 "
          "옳은 답이라 이 검사의 본연이 흐려진다.\n"
          "**후보 추가(2026-07-28 실측)**: 3회 중 1회가 표로 답하면서 식별자를 "
          "`Availability Zone`으로 **띄어 썼다.** 맞는 답인데 실패로 찍혔다 — "
          "식별자를 그대로 복사할 것이라는 기대가 틀렸다.",
          want_tools=("cap_resource_constraints",),
          want_any=("AvailabilityZone", "vailability Zone")),

    Probe("EN4", "What is the Azure equivalent of an AWS VPC?",
          "짝: 3-6. **단순 조회에 계획 도구를 쓰지 않는가.** 사후 합리화가 되어 "
          "실행 순서를 오해시킨다.",
          want_tools=("kb_equivalent_types",), forbid_tools=("record_plan",)),

    Probe("EN5", "What is affected if I delete an AWS VPC?",
          "짝: 1-1b. 삭제 영향을 도구로 조회하는가. 답이 466줄짜리 요약이라 "
          "**요약 뒤의 근거 꼬리말까지** 살아남는지도 함께 본다.",
          want_tools=("kb_deletion_impact",)),

    Probe("EN6", "List the VMs currently running in my account.",
          "짝: 3-1. **없는 축을 거절하는가.** 지식베이스로 메우면 없는 배포 상태를 "
          "지어내는 것이다. 거절하는 말은 여러 가지라 후보를 넓게 둔다 — 좁히면 "
          "옳은 행동을 벌한다(한국어 짝에서 실제로 그랬다).",
          no_tools=True,
          want_any=("annot", "an't", "nable to", "ot available", "ot supported",
                    "o access", "on't have", "o not have", "ot connected",
                    "no live", "not track", "outside")),

    Probe("EN7", "Which GCP ContainerCluster properties are immutable?",
          "짝: 3-11. **낡은 값이라는 고지가 답변까지 살아남는가.** 값만 옮기고 "
          "경고를 빼면 사용자는 검증된 최신값이라고 믿는다. 도구가 내는 문장은 "
          "'a Terraform provider snapshot taken on 2023-09-26 … may be outdated'다.",
          want_tools=("cap_resource_constraints",),
          want_any=("2023", "napshot", "utdated", "ut of date", "tale")),

    Probe("EN8", "What is the local SSD capacity of a GCP a2-ultragpu-1g?",
          "짝: X5. **라우팅 함정의 영어판.** 한국어에서 '용량'이라는 단어가 질문을 "
          "용량 축으로 흘려보내 웹검색으로 샌 전례가 있다. 영어 'capacity'도 같은 "
          "일을 하는지 본다 — **이 프로브가 이 벌의 존재 이유에 가장 가깝다.**",
          want_any_tool=("perf_instance_profile", "cost_describe_spec"),
          forbid_tools=("web_search",), want_any=("375",)),

    Probe("EN9", "Are there known patterns for designing retry logic?",
          "짝: X1. 자문 축으로 라우팅되는가. advisory 고지가 붙는 것은 도구 출력이 "
          "보장하므로 여기서는 **경로**만 본다.",
          want_tools=("pattern_search",)),

    Probe("EN10", "What is the region code for Tokyo?",
          "짝: R4. **모호한 물음에 하나로 단정하지 않는가.** 도쿄는 프로바이더마다 "
          "코드가 다르다(aws ap-northeast-1 · azure japaneast · gcp asia-northeast1 · "
          "ibm jp-tok). 옳은 답이 둘이라 도구 호출을 강요하지 않는다 — 되묻거나, "
          "여럿을 밝히거나.",
          tools_optional=True, forbid_tools=("web_search",),
          want_any=("hich provider", "hich cloud", "aries by provider",
                    "iffers by provider", "epends on the provider",
                    "japaneast", "asia-northeast1", "jp-tok")),

    # --- 2026-07-29 확장 (도구 축 22개를 메운다) -----------------------------
    #
    # 기대값은 짝과 **같은 것**을 쓰되 한국어 후보는 뺐다. 문구 후보를 언어마다
    # 다르게 잡으면 통과율 차이가 '언어 효과'인지 '기대가 달라서'인지 못 가린다.

    Probe("EN11", "If I create just one VM on Azure, what else is created with it?",
          "짝: BU1. 번들 축 — 하나를 고르면 무엇이 따라오는가.",
          want_tools=("bundle_lookup",), forbid_tools=("web_search",),
          want_any=("etwork interface", "etworkInterface", "NIC")),

    Probe("EN12", "Which image should I use to launch an arm64 VM in AWS Seoul?",
          "짝: IM1. 이미지 축. 기대값이 식별자(`ami-`)라 언어 중립이다.",
          want_tools=("cap_basic_image",), forbid_tools=("web_search",),
          want_any=("ami-", "arm64")),

    Probe("EN13", "Can I create a Kubernetes cluster on KT Cloud?",
          "짝: K1. **'못 한다'가 아니라 '도구 커버리지가 없다'로 답하는가.** "
          "이 구분이 언어를 건너 살아남는지 본다.",
          want_tools=("cap_csp_supports",), forbid_tools=("web_search",),
          want_any=("river", "ooling", "overage", "CB-Spider", "cb-spider")),

    Probe("EN14", "Which GCP region has the lowest carbon emissions?",
          "짝: C1. 탄소 축 — 리전 식별자가 기대값이라 언어 중립이다.",
          want_tools=("cap_region_carbon",), forbid_tools=("web_search",),
          want_any=("europe-north2", "northamerica-northeast1", "gCO2")),

    Probe("EN15", "Which other region is closest to AWS Seoul?",
          "짝: LT1. 지연 축.",
          want_tools=("cap_region_latency",), forbid_tools=("web_search",),
          want_any=("koreacentral", "asia-northeast3", "ap-seoul", "ms")),

    Probe("EN16", "We are deploying to AWS Tokyo — what is the region code?",
          "짝: R2. 리전 해석. **짝처럼 `want_any`를 두지 않는다** — 답의 모양이 "
          "여럿이고, 여기서 지킬 것은 코드를 기억에서 쓰지 않고 도구로 받는가다.",
          want_tools=("cap_resolve_region",), forbid_tools=("web_search",)),

    Probe("EN17", "Is EKS 1.28 still supported?",
          "짝: E1. 수명주기 축.",
          want_tools=("cap_service_lifecycle",), forbid_tools=("web_search",),
          want_any=("2024", "nd of support", "end date", "End date", "upport", "OL")),

    Probe("EN18", "Show me the quotas related to Azure Key Vault.",
          "짝: D4. **가진 것은 내놓고 없는 것은 없다고 하는가.**",
          want_tools=("cap_service_quota",), forbid_tools=("web_search",),
          want_any=("64",)),

    Probe("EN19", "Which regions have an endpoint for the ec2 service?",
          "짝: CR1. 서비스 엔드포인트 리전.",
          want_tools=("cap_service_regions",), forbid_tools=("web_search",),
          want_any=("ap-northeast-2", "us-east-1")),

    Probe("EN20", "What is the hourly price of Azure Standard_D2s_v5 in koreasouth "
                  "with a 3-year reservation?",
          "짝: P2. 할인 축 — **단위 칸이 거짓말하는 자리**(원본은 기간 총액을 주면서 "
          "'1 Hour'라고 적는다). 환산이 언어를 건너 살아남는지 본다.",
          want_tools=("cost_discount_pricing",), forbid_tools=("web_search",),
          want_any=("0.04", "0.0436", "eserved")),

    Probe("EN21", "How much in total for one t3.medium VM in AWS Seoul? "
                  "Just give me the number.",
          "짝: GL2. **숫자만 달라는 압박에도 합계의 한계 고지가 남는가.**",
          want_any_tool=("resource_guideline", "cost_describe_spec",
                         "cost_estimate_monthly"),
          forbid_tools=("web_search",),
          want_any=("otal", "ot include", "o total", "ot an actual bill",
                    "o price axis", "ot reflected")),

    Probe("EN22", "Which GPU instances can I use in AWS ap-northeast-2?",
          "짝: H3. 스펙 추천 축.",
          want_tools=("cost_recommend_specs",), forbid_tools=("web_search",),
          want_any=("g4dn", "g5g", "g5.", "g6")),

    Probe("EN23", _english_design_query(),
          "짝: X7. **입력 JSON은 짝과 바이트까지 같다** — 지시문 한 줄만 영어다. "
          "설계도에서 배포 구성으로 가는 축이 언어에 흔들리는지 본다.",
          want_tools=("design_to_deployment",), forbid_tools=("web_search",),
          want_any=("ecommend", "erverless")),

    Probe("EN24", "What must exist before I can create an AWS EC2 instance?",
          "짝: G1. 생성 순서 축.",
          want_tools=("kb_creation_order",), forbid_tools=("web_search",),
          want_any=("subnet", "Subnet", "vNet", "tumblebug")),

    Probe("EN25", "What exactly does a GCP ComputeInstance reference?",
          "짝: KD1. 타입 상세 축.",
          want_tools=("kb_describe_type",), forbid_tools=("web_search",),
          want_any=("ComputeDisk", "ComputeNetwork", "ComputeAddress")),

    Probe("EN26", "Which five AWS resource types affect the most other types "
                  "when deleted?",
          "짝: KR1. **집계 질문을 집계 도구로** — 하나씩 조회하면 턴 한도를 넘는다.",
          want_tools=("kb_rank_types",), forbid_tools=("web_search",),
          want_any=("IAM::Role", "IAM Role", "199")),

    Probe("EN27", "Is there object storage like S3 on IBM and OpenStack as well?",
          "짝: SM2. 타입 검색·동치 축.",
          want_any_tool=("kb_equivalent_types", "kb_search_types"),
          forbid_tools=("web_search",),
          want_any=("cos_bucket", "objectstorage", "bject storage", "bject Storage")),

    Probe("EN28", "Compare the performance of m5.large and c6a.large.",
          "짝: N4. 성능 비교 — **승자를 뽑지 않고 축별로 나열하는가.**",
          want_tools=("perf_compare",), forbid_tools=("web_search",),
          want_any=("revious generation", "revious-generation", "lder generation")),

    Probe("EN29", "Which AWS instances have a sustained EBS bandwidth of "
                  "4000 Mbps or more?",
          "짝: PB1. **최대와 지속을 가르는가.**",
          want_tools=("perf_specs_by_ebs_baseline",), forbid_tools=("web_search",),
          want_any=("baseline", "sustained")),

    Probe("EN30", "How many subnets do I need to create one Kubernetes cluster "
                  "on AWS?",
          "짝: CF6. 사이징 규칙 — **클라우드가 정한 값이 아니라 도구의 규칙**이라는 "
          "구분이 답에 살아야 한다.",
          want_tools=("sizing_rules",), forbid_tools=("web_search",),
          want_any=("requiredSubnetCount", "2 subnets", "two subnets")),

    Probe("EN31", "How many VMs fit in a single /24 subnet on AWS?",
          "짝: SZ1. 서브넷 수용량 — 공식에서 계산되는 결정론적 답(251).",
          want_tools=("sizing_subnet_capacity",), forbid_tools=("web_search",),
          want_any=("251",)),

    Probe("EN32", "What must exist before I can create an AWS RDS DB instance?",
          "짝: G2. **'필수가 없다'는 답이 살아남는가.** 실측상 `AWS::RDS::DBInstance`는 "
          "스키마가 필수 선행을 하나도 표시하지 않고(선택 7종뿐), 도구는 그 사실과 "
          "함께 유보를 낸다. 기억으로 'VPC·서브넷 그룹이 먼저 필요하다'고 **필수로 "
          "단정**하면 선택을 필수로 승격한 것이다. EN24(EC2)가 필수가 있는 쪽이라 짝이다.",
          want_tools=("kb_creation_order",), forbid_tools=("web_search",),
          want_any=("optional", "o prerequisite", "not required", "ot mark",
                    "n practice")),

    Probe("EN33", "I want to run RDS on AWS — list what has to exist first and "
                  "which properties cannot be changed later.",
          "짝: G3. **축을 엮는 질의** — 의존성(graphkb) + 제약(capacitykb). 도구 둘을 "
          "다 부르는지로 본다(한쪽 축만 답하면 실패다). 실측: 불변 31개 · 선행 후보 7종.",
          want_tools=("kb_creation_order", "cap_resource_constraints"),
          forbid_tools=("web_search",),
          want_any=("CharacterSetName", "DBClusterIdentifier", "recreate",
                    "annot be changed")),
)


def paired_ids() -> tuple[tuple[str, str], ...]:
    """(영어 프로브, 한국어 짝) 목록 — 비교 실행을 짜는 쪽에서 쓴다."""
    return tuple(PAIRED_WITH.items())


if __name__ == "__main__":  # pragma: no cover - 수동 실행 경로
    import asyncio
    import json
    import sys

    from ..kbcommon.console import use_utf8
    from .agent_probe import run_probes

    use_utf8()
    repeat = 3
    if "--repeat" in sys.argv:
        repeat = int(sys.argv[sys.argv.index("--repeat") + 1])

    print(
        "영어 프로브는 짝지은 한국어 프로브와 **같은 조건으로** 돌려야 뜻이 있습니다.\n"
        f"  python -m app.core.cloudkb.tools.agent_probe --repeat {repeat} --only "
        + ",".join(PAIRED_WITH.values())
        + "\n한쪽만 돌린 결과로는 언어 효과를 말할 수 없습니다.\n",
        file=sys.stderr,
    )

    tally: dict[str, int] = {p.id: 0 for p in PROBES_EN}
    for _ in range(repeat):
        # 재시도는 끈다 — 여기서 재려는 것이 바로 그 흔들림이다.
        for r in asyncio.run(run_probes(PROBES_EN, max_turns=25, retries=0)):
            if not r.error and not r.failures:
                tally[r.probe.id] += 1
    print(json.dumps(
        {pid: f"{n}/{repeat} (짝 {PAIRED_WITH[pid]})" for pid, n in tally.items()},
        ensure_ascii=False, indent=2,
    ))
