"""에이전트 회귀 하네스 — 질의를 실제로 태우고 **기계로 판정 가능한 것만** 검사한다.

    python tools/agent_probe.py                  # 전부 돌리고 결과를 본다
    python tools/agent_probe.py --only 3-9       # 하나만
    python tools/agent_probe.py --strict         # 실패가 있으면 종료코드 1
    python tools/agent_probe.py --out out.json   # 결과 저장

**도구 호출 기록이 핵심이다.** 답이 그럴듯해 보여도 도구를 안 부르고 모델이 지어낸
것일 수 있고, 그게 이 프로젝트가 반복적으로 겪은 실패 양상이다. 답만 읽으면 그 둘이
구별되지 않는다.

## 무엇을 자동 판정하고 무엇을 안 하나

자동으로 보는 것은 셋뿐이다 — **어떤 도구를 불렀나 / 부르지 말아야 할 걸 불렀나 /
답을 지탱하는 말이 답변에 들어 있나.** 답변이 잘 쓰였는지, 설명이 친절한지는 판정하지
않는다. 그걸 코드로 굳히면 **"문구가 바뀌었다"와 "동작이 틀렸다"가 구별되지 않는다.**

그래서 여기 적힌 기대는 전부 **"이게 틀리면 진짜 결함"**인 것만 남겼다. 실측에서
"모델이 다르게 했지만 그게 더 낫다"고 판단한 것들은 기대에서 **뺐다**(3-2·3-10).
지킬 생각 없는 기대를 남겨 두면 실패가 일상이 되고, 그러면 진짜 실패가 안 보인다.

## 비결정성

같은 질의도 실행마다 도구 순서가 달라진다(실측: 3-3이 한 번은 `record_plan → cost_*`,
다음엔 계획 게이트에 3번 부딪힌 뒤 성공). 그래서 실패하면 **한 번 다시 해 보고**,
재시도에서 통과하면 실패가 아니라 **불안정(flaky)**으로 기록한다. 불안정도 신호다 —
조용히 재시도만 하면 "가끔 틀린다"는 사실이 사라진다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from agents import Runner
from agents.exceptions import MaxTurnsExceeded

from kbcommon.console import use_utf8
from tools import claim_check
from nim_agent.agent import build_agent
from nim_agent.session import SessionState


@dataclass(frozen=True)
class Probe:
    """질의 하나와 **틀리면 진짜 결함인 것**."""

    id: str
    query: str
    why: str
    """이 검사가 무엇을 지키는가. 실패했을 때 읽을 사람을 위한 것."""

    want_tools: tuple[str, ...] = ()
    """전부 불려야 하는 도구."""

    forbid_tools: tuple[str, ...] = ()
    """하나라도 불리면 실패."""

    want_any: tuple[str, ...] = ()
    """**전체 답변**에서 최소 하나는 나와야 하는 말.

    화면에는 답변을 잘라 보여주므로 눈으로 판정하면 뒤쪽을 놓친다 — 실제로
    "모델이 낡음 경고를 뺐다"고 두 번 오판할 뻔했다(경고는 답변 맨 끝에 있었다).
    모델이 바꿔 말할 수 있으니 후보 중 하나만 맞으면 통과로 본다.
    """

    no_tools: bool = False
    """도구를 **하나도** 부르지 않아야 통과 (없는 축을 거절하는 경우)."""

    def failures(self, tools: list[str], answer: str) -> list[str]:
        out: list[str] = []
        called = set(tools)
        for want in self.want_tools:
            if want not in called:
                out.append(f"기대 도구 미호출: {want}")
        for bad in self.forbid_tools:
            if bad in called:
                out.append(f"금지 도구 호출: {bad}")
        if self.no_tools and tools:
            out.append(f"도구를 부르면 안 되는데 불렀다: {', '.join(tools)}")
        if not self.no_tools and not tools:
            out.append("도구를 아예 안 불렀다")
        if self.want_any and not any(w in _normalized(answer) for w in self.want_any):
            out.append(f"답변에 없음 (후보: {', '.join(self.want_any)})")
        return out


PROBES: tuple[Probe, ...] = (
    Probe("1-1b", "AWS VPC를 지우면 뭐가 영향받아?",
          "삭제 영향을 도구로 조회하는가",
          want_tools=("kb_deletion_impact",)),
    Probe("1-2a", "서브넷에서 나중에 못 바꾸는 속성이 뭐야?",
          "불변 속성을 도구로 조회하는가",
          want_tools=("cap_immutable_properties",),
          want_any=("AvailabilityZone",)),
    Probe("3-1", "지금 내 계정에 떠 있는 VM 목록 보여줘",
          "**없는 축을 거절하는가.** 지식베이스로 메우면 없는 배포 상태를 지어내는 것",
          no_tools=True,
          want_any=("없습니다", "없어", "조회할 수 없", "확인해 드릴 수 없")),
    Probe("3-3", "t3.medium 시간당 얼마야?",
          "**웹 검색으로 새지 않는가.** 검색 가격과 데이터셋 가격이 섞이면 합계 기준이 어긋난다",
          forbid_tools=("web_search",),
          want_any=("0.0416",)),
    Probe("3-6", "AWS VPC가 Azure에선 뭐야?",
          "**단순 조회에 계획을 쓰지 않는가.** 사후 합리화가 되어 실행 순서를 오해시킨다",
          want_tools=("kb_equivalent_types",), forbid_tools=("record_plan",)),
    Probe("3-9", "gp2 볼륨을 30,000 GiB로 만들 수 있어?",
          "**조건부 한도를 지식베이스가 판정하는가.** 표를 받아 모델이 비교하면 "
          "그 비교는 지식베이스가 보증하지 않는다",
          want_tools=("cap_check_value",), want_any=("16384",)),
    Probe("3-11", "GCP ContainerCluster에서 나중에 못 바꾸는 속성은?",
          "**낡은 값이라는 고지가 답변까지 살아남는가.** 값만 옮기고 경고를 빼면 "
          "사용자는 검증된 최신값이라고 믿는다",
          want_tools=("cap_immutable_properties",),
          want_any=("낡", "2023", "스냅샷", "오래")),
    Probe("D4", "Azure 키 볼트 관련 쿼터 알려줘",
          "**가진 것은 내놓고 없는 것은 없다고 하는가.** 키 볼트 *전용* 한도 문서는 "
          "원본에 없지만 private-link-limits.md에 '키 볼트당 프라이빗 엔드포인트 64개' "
          "1건이 있다. 둘 다 해야 통과다",
          want_tools=("cap_service_quota",),
          want_any=("64",)),
    Probe("D5", "GCP ComputeDisk는 무엇 안에 담겨 있어?",
          "이번에 새로 만든 담김 축이 답변에 닿는가",
          want_any=("Project", "프로젝트")),
    Probe("N1", "af-south-1 리전에서 p5.48xlarge 인스턴스를 쓸 수 있어?",
          "**값에서 출발한 질문이 KB 안에서 끝나는가.** 리전별 허용값 79,809쌍을 "
          "넣고도 에이전트가 못 닿아 웹으로 나갔다 (데이터: af-south-1은 불가)",
          forbid_tools=("web_search",),
          want_any=("af-south-1", "제공되지", "지원되지", "사용할 수 없", "쓸 수 없")),
    Probe("H1", "g5g.xlarge에는 어떤 GPU가 달려 있어?",
          "**지어내기가 났던 자리.** 모델은 예전에 'AMD Radeon Instinct MI250X'라고 "
          "했다 — 실제는 NVIDIA T4G 1장(Turing). 가속기 데이터가 0건이라 빈칸이 "
          "지어내기를 불렀다",
          want_tools=("perf_instance_profile",), forbid_tools=("web_search",),
          # 모델명을 부분 문자열로 걸지 않는다 — 모델이 `T4G`를 `T4 G`로 띄어 써서
          # 정답을 실패로 읽었다(판정 아티팩트 8회째). 도구 호출로 건다.
          want_any=("Turing",)),
    Probe("H2", "p4d.24xlarge에 GPU가 몇 개 달려 있고 어떤 모델이야?",
          "개수와 모델을 성능 축에서 가져오는가 (정답 A100 8장)",
          want_tools=("perf_instance_profile",), forbid_tools=("web_search",),
          want_any=("A100",)),
    Probe("H3", "ap-northeast-2에서 쓸 수 있는 GPU 인스턴스 알려줘",
          "**세 번 실패했던 질문.** 사양표 지어내기 → 780종을 하나씩 판정하다 109초 "
          "턴 한도 초과 → 되묻기. 두 축을 잇는 필터가 없어서였다",
          want_tools=("cost_recommend_specs",), forbid_tools=("web_search",),
          want_any=("g4dn", "g5g", "g5.", "g6")),
    Probe("R1", "서울 리전에서 쓸 수 있는 AWS GPU 인스턴스 알려줘",
          "**H3과 같은 질문을 사람이 쓰는 말로.** H3은 `ap-northeast-2`라고 코드로 "
          "물어서 이 실패를 못 잡았다. 데이터는 있었고 '서울'을 색인 키로 바꾸는 "
          "길만 없었다",
          want_tools=("cap_resolve_region",), forbid_tools=("web_search",),
          want_any=("g4dn", "g5g", "g5.", "g6")),
    Probe("R2", "우리 서비스는 도쿄에 배포할 건데 리전 코드가 뭐야?",
          "지명 → 리전 코드. 모델 기억이 아니라 도구로 답하는가. "
          "want_any(ap-northeast-1)를 뒀더니 도구는 늘 맞는데 답변 **문구**만 바뀌어 "
          "가끔 실패했다 — 이 하네스는 문구를 판정하지 않으므로 도구 호출만 본다",
          want_tools=("cap_resolve_region",), forbid_tools=("web_search",)),
    Probe("S1", "Azure Database for MySQL 유연 서버를 배포할 때 넣는 관리자 비밀번호를 나중에 다시 조회할 수 있어?",
          "비밀값 축(azure-secret). administratorLoginPassword는 x-ms-secret이라 "
          "API로 다시 못 읽는다 — 지어내지 말고 도구로 답하는가",
          want_tools=("cap_secret_properties",), forbid_tools=("web_search",),
          want_any=("다시 읽", "다시 조회", "읽을 수 없", "key vault", "안전")),
    Probe("L1", "Azure AKS 클러스터 만들면 오래 걸려? 배포 스크립트 타임아웃 얼마로 잡아야 해?",
          "작업 소요 축(azure-operations). LRO는 2번 라운드에서 보류했다가 별도 "
          "모양으로 담았다 — 지어내지 말고 도구로 답하는가",
          want_tools=("cap_operation_time",), forbid_tools=("web_search",),
          want_any=("오래", "비동기", "기다")),
    Probe("E1", "EKS 1.28 아직 지원돼?",
          "수명주기 축(service-lifecycle). 0건이던 축이다",
          want_tools=("cap_service_lifecycle",), forbid_tools=("web_search",),
          want_any=("종료", "2024", "지원")),
    Probe("M1", "알리바바 클라우드에서 VPC에 해당하는 리소스가 뭐야?",
          "**core 매핑 확장.** alibaba·tencent를 더하기 전에는 이 질문에 못 답했다 — "
          "graphkb에 그 프로바이더 노드가 0개였다",
          want_tools=("kb_equivalent_types",), forbid_tools=("web_search",),
          want_any=("alicloud_vpc",)),
    Probe("T1", "tencentcloud_vpc에서 바꾸면 재생성되는 속성 있어?",
          "두 CSP 제약 축(tpcsp). 리소스 제약이 0건이던 프로바이더다",
          want_tools=("cap_immutable_properties",), forbid_tools=("web_search",),
          want_any=("cidr_block", "재생성")),
    Probe("K1", "KT Cloud에서 쿠버네티스 클러스터 만들 수 있어?",
          "**cb-spider 흡수.** 실행 경로에 드라이버가 없으면 실제로 못 만든다 — "
          "'CSP에 기능이 없다'가 아니라 '드라이버가 없다'를 구분해 답하는가",
          want_tools=("cap_csp_supports",), forbid_tools=("web_search",),
          want_any=("드라이버", "만들 수 없", "지원")),
    Probe("G1", "AWS EC2 인스턴스 하나 만들려면 뭐가 먼저 있어야 해?",
          "**사용자가 잡아낸 불일치.** 벤더 스키마는 필수 0개라 하고 실행 경로"
          "(cb-tumblebug)는 7가지를 요구한다. 한쪽만 답하면 실제 배포에서 틀린다 — "
          "두 관점을 다 전하는가",
          want_tools=("kb_creation_order",), forbid_tools=("web_search",),
          want_any=("서브넷", "subnet", "vNet", "실행", "tumblebug")),
    Probe("C1", "GCP에서 탄소 배출이 가장 적은 리전이 어디야?",
          "탄소 축(region-carbon). 아예 없던 축이라 답할 수 없었다 — "
          "지어내지 말고 도구로 답하는가",
          want_tools=("cap_region_carbon",), forbid_tools=("web_search",),
          want_any=("europe-north2", "northamerica-northeast1", "gCO2")),
    Probe("C2", "AWS 서울이랑 GCP 서울 중에 어디가 탄소가 적어?",
          "**방법론이 다른 값을 비교하면 안 된다.** GCP는 발표값, AWS는 추정값이라 "
          "같은 도시에서도 순서가 뒤집힌다 — 비교 불가를 전하는가",
          want_tools=("cap_region_carbon",), forbid_tools=("web_search",),
          want_any=("비교", "방법론", "다릅니다", "다르")),
    Probe("R3", "서울 리전 코드가 프로바이더마다 어떻게 달라?",
          "리전 이름이 프로바이더 10곳으로 넓어졌다. 모델 기억이 아니라 도구로 "
          "답하고, 코드가 다르다는 사실을 전하는가",
          want_tools=("cap_resolve_region",), forbid_tools=("web_search",),
          want_any=("asia-northeast3",)),
    Probe("P1", "GCP e2-standard-4를 서울 리전에서 스팟으로 쓰면 시간당 얼마야?",
          "스팟·약정 축(gcp-pricing). 미러엔 온디맨드만 있어 못 답했다 — "
          "지어내지 말고 도구로 답하는가",
          want_tools=("cost_gcp_discount_pricing",), forbid_tools=("web_search",),
          want_any=("스팟", "$0.0", "0.06")),
    Probe("N2", "p5.48xlarge는 어느 리전에서 쓸 수 있어?",
          "**조건 38가지를 세어서 답하는가.** 한때 웹검색 13회로 14분을 쓰고 "
          "\"지식베이스에 없습니다\"라고 답했다",
          forbid_tools=("web_search",),
          want_any=("us-east-1", "ap-northeast-2")),
)

# --- 기대에서 **뺀** 것 (지킬 생각 없는 기대를 남기면 실패가 일상이 된다) ---
#
# 3-2 "EBS 100TB 되나" · 3-10 "EBS 30,000 GiB 되나"
#   모델이 cap_check_value 대신 cap_property_limits로 표를 받아 종류별 가능/불가를
#   전부 정리해 답한다. 프롬프트로 지시해도 그렇다. 다만 그 답이 **실용적으로 더
#   낫다** — "되물어야 한다"는 우리 기대가 옳은지부터 다시 볼 문제라, 강제하지 않는다.
#
# 프로바이더를 안 밝힌 "서울 리전 GPU 인스턴스"
#   R1을 처음엔 프로바이더 없이 물었는데, 3번 돌려 1번만 끝까지 갔다. 그런데 되묻는
#   쪽이 **옳다** — 우리 리전 카탈로그는 AWS만 담고 있고, 서울에 해당하는 리전이
#   azure(koreasouth)·gcp(asia-northeast3) 등 여섯 곳 더 있다. 안 물어보고 답하면
#   AWS만 본 답이 전체를 본 답처럼 보인다. **기대가 틀렸던 것이라 R1에 AWS를
#   명시하도록 고쳤다.** 되묻기 여부는 문구 판정이라 기계로 굳히지 않는다.
#
# 3-3의 도구 **순서**
#   계획 게이트에 몇 번 부딪힌 뒤 계획을 세우는지가 실행마다 다르다. 게이트가 막는지는
#   `forbid_tools`가 아니라 게이트 자체의 단위 테스트가 지킨다.


#: 모델이 쓰는 '예쁜' 문자들 → ASCII. 뜻은 같고 바이트만 다르다.
_LOOKALIKE = {
    **dict.fromkeys(map(ord, '‐‑‒–—―−'), "-"),
    **dict.fromkeys(map(ord, '\xa0\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000'), " "),
}


def _normalized(text: str) -> str:
    """판정 전에 **눈에 안 보이는 차이**를 없앤다.

    이것 때문에 맞는 답을 틀렸다고 기록할 뻔한 게 **다섯 번**이다:

    - `16,384`를 `16 384`로 썼는데 그 공백이 U+202F(좁은 줄바꿈 없는 공백)였다.
    - `ap-northeast-2`를 `ap-northeast-2`로 썼는데 그 하이픈이 U+2011
      (줄바꿈 없는 하이픈)이었다. 리전 14곳을 **정확히** 답한 회차였다.

    모델은 사람이 읽기 좋은 문자를 쓴다. 우리가 ASCII로 쓴 기대 문구와 비교하려면
    여기서 맞춰야 한다. **하네스가 만든 실패는 진짜 실패를 가린다.**
    """
    text = text.translate(_LOOKALIKE)
    # 숫자 사이의 자릿수 구분자만 지운다 (`16 384` · `16,384` → `16384`).
    # 전역으로 쉼표를 지우면 문장이 뭉개진다.
    return re.sub(r"(?<=\d)[, ](?=\d)", "", text)


@dataclass
class Result:
    probe: Probe
    tools: list[str] = field(default_factory=list)
    answer: str = ""
    tool_outputs: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    """답변의 구체값 중 그 턴의 도구 출력에 **근거가 없는** 것들.

    **통과/실패에 넣지 않는다.** 24건 실측에서 오탐이 1건 있었고(단위 환산
    `900초=15분`), 그걸 실패로 만들면 멀쩡한 답변이 빨갛게 뜬다. 대신 세어서
    보여준다 — 이 문제가 나아지는지 나빠지는지를 계속 알 수 있게.
    """

    claims_checked: int = 0
    error: str = ""
    seconds: float = 0.0
    failures: list[str] = field(default_factory=list)
    flaky: bool = False
    """첫 시도에 실패하고 재시도에서 통과했다. 실패는 아니지만 **조용히 넘기지 않는다.**"""

    @property
    def ok(self) -> bool:
        return not self.error and not self.failures

    def to_dict(self) -> dict:
        return {
            "id": self.probe.id, "query": self.probe.query, "why": self.probe.why,
            "tools": self.tools, "answer": self.answer,
            "tool_outputs": self.tool_outputs, "error": self.error,
            "unsupported": self.unsupported, "claims_checked": self.claims_checked,
            "seconds": round(self.seconds, 1), "failures": self.failures,
            "flaky": self.flaky, "ok": self.ok,
        }


def _tool_names(result) -> list[str]:
    return [
        item.raw_item.name
        for item in result.new_items
        if getattr(item, "type", "") == "tool_call_item"
        and getattr(getattr(item, "raw_item", None), "name", None)
    ]


def _tool_outputs(result) -> list[str]:
    """도구가 **실제로 돌려준 문자열**들.

    이름만으로는 "모델이 도구를 불렀다"까지만 안다. 답변이 그 출력에 근거하는지
    보려면 출력이 있어야 한다 — 도구를 부르고도 없는 말을 지어내는 경우가 있고,
    그게 우리가 가장 경계하는 실패다.
    """
    out = []
    for item in result.new_items:
        if getattr(item, "type", "") != "tool_call_output_item":
            continue
        text = getattr(item, "output", None)
        if text is not None:
            out.append(str(text))
    return out


async def _run_once(
    agent, probe: Probe, max_turns: int
) -> tuple[list[str], str, str, list[str]]:
    try:
        # 요청마다 새 상태 — 앞 질의의 계획이 이번 게이트를 열면 안 된다.
        result = await Runner.run(
            agent, [{"role": "user", "content": probe.query}],
            max_turns=max_turns, context=SessionState(),
        )
        return (
            _tool_names(result), str(result.final_output or ""), "",
            _tool_outputs(result),
        )
    except MaxTurnsExceeded:
        return [], "", f"턴 한도({max_turns}) 초과", []
    except Exception as exc:  # noqa: BLE001 — 한 질의 실패가 전체를 막지 않게
        return [], "", f"{type(exc).__name__}: {exc}", []


async def run_probes(
    probes: tuple[Probe, ...], *, max_turns: int, retries: int
) -> list[Result]:
    agent = build_agent()
    out: list[Result] = []
    for probe in probes:
        started = time.monotonic()
        tools, answer, error, outputs = await _run_once(agent, probe, max_turns)
        failures = [] if error else probe.failures(tools, answer)
        flaky = False
        for _ in range(retries if (error or failures) else 0):
            tools2, answer2, error2, outputs2 = await _run_once(agent, probe, max_turns)
            failures2 = [] if error2 else probe.failures(tools2, answer2)
            if not error2 and not failures2:
                # 재시도에서 통과 → 실패가 아니라 불안정으로 기록한다.
                tools, answer, error, failures, flaky = tools2, answer2, "", [], True
                outputs = outputs2
                break
        verdict = claim_check.check(answer, outputs, probe.query)
        record = Result(
            probe=probe, tools=tools, answer=answer, error=error,
            tool_outputs=outputs,
            unsupported=[f"[{f.kind}] {f.token}" for f in verdict.unsupported],
            claims_checked=verdict.checked,
            seconds=time.monotonic() - started, failures=failures, flaky=flaky,
        )
        out.append(record)
        _report(record)
    return out


def _report(r: Result) -> None:
    mark = "✗" if not r.ok else ("~" if r.flaky else "✓")
    print(f"\n{'=' * 74}\n{mark} [{r.probe.id}] {r.probe.query}")
    print(f"  지키는 것: {r.probe.why}")
    print(f"  도구: {' → '.join(r.tools) or '(없음)'}   ({r.seconds:.1f}s)")
    if r.flaky:
        print("  ~ 첫 시도 실패, 재시도 통과 — **불안정**")
    for line in r.failures:
        print(f"  ✗ {line}")
    if r.error:
        print(f"  ✗ {r.error}")
    if r.unsupported:
        # 실패가 아니라 신호다. 답변이 도구가 준 적 없는 구체값을 말하고 있다.
        shown = ", ".join(r.unsupported[:6])
        more = f" 외 {len(r.unsupported) - 6}개" if len(r.unsupported) > 6 else ""
        print(
            f"  ⚑ 주장 대조: 구체값 {r.claims_checked}개 중 "
            f"{len(r.unsupported)}개가 도구 출력에 없음 — {shown}{more}"
        )
    if r.answer:
        cut = r.answer[:400]
        more = f" … (전체 {len(r.answer)}자)" if len(r.answer) > 400 else ""
        print(f"  답변: {cut}{more}")


def main() -> int:
    use_utf8()
    parser = argparse.ArgumentParser(description="에이전트 회귀 하네스")
    parser.add_argument("--only", help="항목 id (쉼표 구분)")
    parser.add_argument("--max-turns", type=int, default=25)
    parser.add_argument("--retries", type=int, default=1,
                        help="실패 시 재시도 횟수 (기본 1 — 비결정성 때문)")
    parser.add_argument("--strict", action="store_true",
                        help="실패가 있으면 종료코드 1")
    parser.add_argument("--out", help="결과 JSON 경로")
    args = parser.parse_args()

    probes = PROBES
    if args.only:
        wanted = {x.strip() for x in args.only.split(",")}
        probes = tuple(p for p in PROBES if p.id in wanted)
        if not probes:
            print(f"해당 항목이 없습니다: {sorted(wanted)}")
            return 1

    results = asyncio.run(
        run_probes(probes, max_turns=args.max_turns, retries=args.retries)
    )
    if args.out:
        Path(args.out).write_text(
            json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(f"\n결과 저장: {args.out}")

    failed = [r for r in results if not r.ok]
    flaky = [r for r in results if r.flaky]
    print(f"\n{'=' * 74}")
    print(f"{len(results)}건 중 통과 {len(results) - len(failed)}, "
          f"실패 {len(failed)}, 불안정 {len(flaky)}")
    for r in failed:
        print(f"  ✗ [{r.probe.id}] {'; '.join(r.failures) or r.error}")
    for r in flaky:
        print(f"  ~ [{r.probe.id}] 재시도로 통과 — 가끔 틀린다는 뜻이다")

    flagged = [r for r in results if r.unsupported]
    if flagged:
        total = sum(len(r.unsupported) for r in flagged)
        print(
            f"\n⚑ 주장 대조: {len(flagged)}건에서 도구 출력에 없는 구체값 {total}개. "
            "**실패가 아니라 신호입니다** — 24건 실측에서 오탐이 1건 있었습니다"
            "(단위 환산). 개수가 많을수록 지어냈을 가능성이 큽니다(최악 사례 16개)."
        )
        for r in flagged:
            print(
                f"  ⚑ [{r.probe.id}] {len(r.unsupported)}개: "
                f"{', '.join(r.unsupported[:4])}"
            )
    print("\n답변이 **잘 쓰였는지**는 판정하지 않습니다 — 그건 사람이 읽으세요.")
    return 1 if (args.strict and failed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
