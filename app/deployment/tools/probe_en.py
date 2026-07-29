"""영어 질의 프로브 — **짝지은 실험**이지 새 회귀 스위트가 아니다.

    RUN_AGENT_TESTS=1 uv run pytest app/deployment/tests/test_agent_regression_en.py -v
    python -m app.deployment.tools.probe_en --repeat 3      # 흔들림까지 보려면

## 왜 필요한가 — 공백의 정체

도구 출력·판정문·고지는 영어로 넘어갔는데(2026-07-25), **영어로 물어본 측정이 한
번도 없다.** 지금까지의 프로브 64건이 전부 한국어 질의다. 그래서 우리가 가진
증거는 전부 "한국어로 물었을 때 영어 도구가 잘 라우팅된다"이고, **시스템 타겟인
영어 질의에서 같은 결과가 나온다는 증거는 0건**이다.

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
}

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
        f"  python -m app.deployment.tools.agent_probe --repeat {repeat} --only "
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
