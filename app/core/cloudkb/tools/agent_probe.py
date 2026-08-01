"""에이전트 회귀 하네스 — 질의를 실제로 태우고 **기계로 판정 가능한 것만** 검사한다.

**리포 루트에서 모듈로 돌린다**(`app/core/cloudkb/` 아래로 옮겨진 뒤의 경로다):

    python -m app.core.cloudkb.tools.agent_probe                # 영어 31건 (기본)
    python -m app.core.cloudkb.tools.agent_probe --lang ko      # 한국어 68건
    python -m app.core.cloudkb.tools.agent_probe --list         # 질의집을 찍는다 (키 불필요)
    python -m app.core.cloudkb.tools.agent_probe --only 3-9     # 하나만
    python -m app.core.cloudkb.tools.agent_probe --strict       # 실패가 있으면 종료코드 1
    python -m app.core.cloudkb.tools.agent_probe --out out.json # 결과 저장
    python -m app.core.cloudkb.tools.agent_probe --repeat 5     # 5회 — 통과율로 본다
    python -m app.core.cloudkb.tools.agent_probe --tool-output 0  # 도구 결과 숨김

## 질의 언어 — 기본이 영어다

시스템의 대상 언어가 영어다(도구 출력·판정문·고지가 전부 영어이고, 실측상 한국어로
물어도 답은 영어로 온다 — 30칸 중 28칸). 그래서 기본 실행은 `probe_en.py`의 영어
31건이고, 그 31건이 도구 **31/31**을 덮는다. `--lang ko`면 한국어 68건이다.

## 화면 읽는 법 — 질문 · 도구 · 답변

프로브 블록에는 셋만 남긴다. 예전에는 지키는 것·실패 사유·주장 대조·오판 의심·용어
누출까지 한 블록에 찍어 프로브 하나가 열 줄을 넘었고, **정작 봐야 할 도구 결과가 그
사이에 묻혔다.** 나머지는 버린 게 아니라 **요약으로 옮겼다**(`_print_signals`).

도구는 호출과 **그 결과**를 함께 찍는다(`main.py --verbose`와 같은 결) — 이름만 보면
"불렀다"까지만 알고, 도구가 빈손으로 돌아왔는지는 출력을 봐야 갈린다. 길이는
`--tool-output N`(기본 220자, 0이면 숨김). 반복 모드에서는 **첫 회차만** 찍는다.

답변은 `--answer N`(기본 1200자, **0이면 안 자른다**). 400자였다가 늘렸다 — 이 저장소가
답변에서 지키는 것들(낡음 고지·유보·"이 데이터셋에는 없다")은 **끝이나 꼬리말에 오기**
때문에 짧게 자르면 화면에서 사라진 것처럼 보인다. 판정은 원래부터 전문에 대고 한다.

색은 장식이 아니라 분류다(팔레트는 `nim_agent/verbose.py` 한 곳):

    초록 통과 · 빨강 실패(틀리면 진짜 결함) · 노랑 불안정 ·
    마젠타 신호(주장 대조·오판 의심·용어 누출) · 시안 도구 호출 · 파랑 도구 결과

**회색(dim)은 쓰지 않는다** — 배색에 따라 배경에 묻힌다. 물러나야 하는 답변 본문은
색을 빼서 물러나게 한다. `NO_COLOR=1`로 끄고 `FORCE_COLOR=1`로 켜며, 파일로
리디렉션하면 자동으로 꺼진다.

## 이 하네스가 명단의 단일 소스다

같은 목록이 세 곳에 있었다 — 여기(`PROBES`), 회귀 테스트의 파라미터, 그리고 문서
질의집. 뒤의 둘이 각각 2026-07-24와 도구 16개 시절에서 멈췄고, **프로브를 새로 넣어도
회귀도 문서도 몰랐다.** 지금은 회귀가 `regression_probes()`를 읽고 질의집은 `--list`가
생성한다. 도구 커버리지(`tool_coverage()`)도 여기서 센다 — 아무 프로브도 지목하지 않는
도구를 세지 않으면 그 빈칸이 통과처럼 읽힌다.

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

## --repeat: 흔들림을 재는 자리 (재시도로는 못 잰다)

한 번 돌린 결과로 A/B를 하면 안 된다. **실측(2026-07-25)**: 지시문·도구 설명을
영어로 바꾸는 실험에서 3회차가 전부 54/58로 같았는데, **실패한 4건이 매번 달랐다** —
50건은 3회 전부 통과, 1건은 3회 전부 실패, **7건이 회차마다 뒤집혔다**. 총점이 같은
것은 뒤집힌 것들이 우연히 상쇄된 결과였고, 그 폭이 실험이 만든 효과보다 컸다.
"다축이 7→8→9로 올랐다"는 서사를 쓸 뻔했는데 다축 10건 중 4건이 그 뒤집히는
프로브였다.

`--repeat N`은 같은 프로브를 N회 **독립 실행**해 `통과 k/N`으로 낸다. 이때
**재시도를 끈다** — 재시도는 실패를 통과로 덮어써(flaky) 흔들림을 *가리는* 장치이고,
여기서 재려는 것이 바로 그 흔들림이다. 둘을 같이 켜면 통과율이 위로 편향된다.

안정적인 프로브까지 N배로 돌릴 이유는 없다. `--only`로 흔들리는 것만 좁혀 쓴다.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from agents import Runner
from agents.exceptions import MaxTurnsExceeded

from app.core.cloudkb.kbcommon.console import use_utf8
from app.core.cloudkb.nim_agent.agent import build_agent
from app.core.cloudkb.nim_agent.session import SessionState
from app.core.cloudkb.tools import claim_check

#: `want_any` 실패의 머리말. 심판을 부를지 가르는 데 쓰므로 **문자열을 한 곳에 둔다.**
_MISSING_PHRASE = "답변에 없음"


@dataclass(frozen=True)
class Probe:
    """질의 하나와 **틀리면 진짜 결함인 것**."""

    id: str
    query: str
    why: str
    """이 검사가 무엇을 지키는가. 실패했을 때 읽을 사람을 위한 것."""

    want_tools: tuple[str, ...] = ()
    """전부 불려야 하는 도구."""

    want_any_tool: tuple[str, ...] = ()
    """**이 중 하나는** 불려야 한다.

    한 사실을 두 도구가 다 답할 수 있는 경우가 있다. GPU 모델이 그렇다 — 성능 축에도
    비용 축에도 있어서, 어느 쪽을 불러도 근거 있는 답이 나온다. 그때 도구 하나를
    지목해 요구하면 **검사가 사실이 아니라 경로를 고정**하게 된다. 진짜로 지킬 것은
    `want_any`(답에 그 사실이 있는가)이고, 이건 "웹으로 새지 않았는가"만 본다.
    """

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

    tools_optional: bool = False
    """도구를 안 불러도 실패로 보지 않는다.

    **모호한 물음에는 되묻는 것이 최선일 때가 있다.** "도쿄 리전 코드가 뭐야?"는
    프로바이더마다 답이 다르므로, 아무 코드도 단정하지 않고 "어느 클라우드냐"고
    되묻는 것이 옳다 — 그때는 부를 도구가 없다.

    이걸 못 표현해서 **최선의 답을 실패로 찍었다.** 근거 없는 단정을 막는 일은
    `want_any`와 주장 대조(`claim_check`)가 맡는다 — 도구 호출은 그 대리 지표일 뿐이다.
    """

    regression_skip: str = ""
    """비어 있으면 **옵트인 회귀 테스트가 이 프로브를 태운다.** 빼려면 사유를 적는다.

    기본이 '포함'인 것이 요점이다. 예전에는 회귀 테스트가 태울 id를 자기 파일에
    손으로 적었고, 그래서 2026-07-24 이후 늘어난 프로브 40여 건을 **회귀가 통째로
    몰랐다.** 명단을 두 곳에 두면 한 곳은 반드시 뒤처진다 — 이 저장소가 질의집·
    분류표·도구 목록에서 이미 세 번 겪은 실패다.

    사유를 문자열로 강제하는 이유는 T6과 같다: 사유를 못 쓰면 제외가 아니라 방치다.
    """

    def failures(self, tools: list[str], answer: str) -> list[str]:
        out: list[str] = []
        called = set(tools)
        for want in self.want_tools:
            if want not in called:
                out.append(f"기대 도구 미호출: {want}")
        if self.want_any_tool and not (called & set(self.want_any_tool)):
            out.append(f"기대 도구 미호출(택1): {', '.join(self.want_any_tool)}")
        for bad in self.forbid_tools:
            if bad in called:
                out.append(f"금지 도구 호출: {bad}")
        if self.no_tools and tools:
            out.append(f"도구를 부르면 안 되는데 불렀다: {', '.join(tools)}")
        if not self.no_tools and not self.tools_optional and not tools:
            out.append("도구를 아예 안 불렀다")
        if self.want_any and not any(w in _normalized(answer) for w in self.want_any):
            out.append(f"{_MISSING_PHRASE} (후보: {', '.join(self.want_any)})")
        return out


#: 설계도 프로브의 입력. 예제 파일을 **읽지 않고 여기 박는다** — 프로브는 사용자가
#: 붙여 넣는 상황을 재현해야 하고, 파일 경로를 주면 그 상황이 아니게 된다.
_DESIGN_QUERY = (
    "아래 설계 산출물로 배포 구성을 만들어줘.\n"
    "{\"schemaVersion\":\"1\",\"name\":\"주문 서비스 데모\",\"components\":[{\"id\":\"order-api\",\"name\":\"OrderService\",\"summary\":\"주문 접수·조회 HTTP API\"},{\"id\":\"order-worker\",\"name\":\"OrderWorker\",\"summary\":\"주문 후처리(영수증 발송) 비동기 소비자\"}],\"externals\":[{\"id\":\"pg-gateway\",\"name\":\"PG사 결제 게이트웨이\"}],\"artifacts\":[{\"id\":\"api-1\",\"kind\":\"openapi\",\"componentId\":\"order-api\",\"openapi\":{\"openapi\":\"3.0.3\",\"info\":{\"title\":\"Order API\",\"version\":\"1.0.0\"},\"paths\":{\"/orders\":{\"post\":{\"summary\":\"주문 생성\",\"responses\":{\"201\":{\"description\":\"created\"}}},\"get\":{\"summary\":\"주문 목록\",\"responses\":{\"200\":{\"description\":\"ok\"}}}},\"/orders/{id}\":{\"get\":{\"summary\":\"주문 조회\",\"responses\":{\"200\":{\"description\":\"ok\"}}}}},\"components\":{\"securitySchemes\":{\"bearer\":{\"type\":\"http\",\"scheme\":\"bearer\"}}}}},{\"id\":\"er-1\",\"kind\":\"er\",\"engineHint\":\"postgresql\",\"entities\":[{\"name\":\"Order\",\"ownerComponentId\":\"order-api\",\"attributes\":[{\"name\":\"id\",\"type\":\"uuid\",\"isPrimaryKey\":true},{\"name\":\"status\",\"type\":\"varchar\"},{\"name\":\"totalAmount\",\"type\":\"numeric\"}]},{\"name\":\"OrderItem\",\"ownerComponentId\":\"order-api\",\"attributes\":[{\"name\":\"id\",\"type\":\"uuid\",\"isPrimaryKey\":true},{\"name\":\"orderId\",\"type\":\"uuid\"},{\"name\":\"quantity\",\"type\":\"integer\"}]}],\"relations\":[{\"from\":\"Order\",\"to\":\"OrderItem\",\"cardinality\":\"1-n\"}]},{\"id\":\"class-1\",\"kind\":\"class\",\"classes\":[{\"name\":\"OrderController\",\"componentId\":\"order-api\",\"stereotypes\":[\"Controller\"]},{\"name\":\"OrderService\",\"componentId\":\"order-api\",\"stereotypes\":[\"Service\"]},{\"name\":\"OrderRepository\",\"componentId\":\"order-api\",\"stereotypes\":[\"Repository\"]},{\"name\":\"ReceiptSender\",\"componentId\":\"order-worker\",\"stereotypes\":[\"Service\"]}]},{\"id\":\"seq-1\",\"kind\":\"sequence\",\"participants\":[{\"id\":\"user\",\"actor\":true,\"name\":\"구매자\"},{\"id\":\"api\",\"componentId\":\"order-api\"},{\"id\":\"worker\",\"componentId\":\"order-worker\"},{\"id\":\"pg\",\"externalId\":\"pg-gateway\"}],\"messages\":[{\"from\":\"user\",\"to\":\"api\",\"async\":false,\"label\":\"POST /orders\"},{\"from\":\"api\",\"to\":\"pg\",\"async\":false,\"label\":\"결제 승인 요청\"},{\"from\":\"api\",\"to\":\"worker\",\"async\":true,\"label\":\"주문 완료 이벤트\"}]}],\"requirements\":{\"provider\":\"aws\",\"region\":\"ap-northeast-2\",\"scale\":{\"value\":200,\"unit\":\"concurrentUsers\"},\"multiZone\":true}}"
)


PROBES: tuple[Probe, ...] = (
    Probe("1-1b", "AWS VPC를 지우면 뭐가 영향받아?",
          "삭제 영향을 도구로 조회하는가",
          want_tools=("kb_deletion_impact",)),
    Probe("1-2a", "AWS 서브넷에서 나중에 못 바꾸는 속성이 뭐야?",
          "불변 속성을 도구로 조회하는가.\n"
          "**질의 정정(2026-07-25)**: 원래 프로바이더 없이 물었는데, 불변 속성은 "
          "클라우드마다 달라 **답이 여럿인 물음**이었다. --repeat 5 실측에서 5회 "
          "전부 '어느 클라우드(AWS/Azure/GCP)냐'고 되물었고, 그 **최선의 답이 "
          "0/5 실패로 찍혔다.** R1·R2·R4·H3에 이어 **같은 실수 다섯 번째**다 — "
          "모호한 질의의 옳은 행동은 R4가 지키므로, 여기서는 프로바이더를 밝혀 "
          "본연의 검사(불변 속성을 기억이 아니라 도구로 답하는가)만 남긴다.\n"
          "**후보 추가(2026-07-28 실측)**: 3회 중 1회가 표·굵은 글씨로 답하며 "
          "식별자를 `Availability Zone`으로 **띄어 썼다.** 맞는 답인데 실패로 "
          "찍혔다 — 식별자를 그대로 복사할 것이라는 기대가 틀렸다. 영어 짝(EN3)도 "
          "같은 자리에서 같은 이유로 걸려, **언어 효과가 아니라 기대 결함**임이 "
          "짝 비교로 확인됐다.",
          want_tools=("cap_resource_constraints",),
          want_any=("AvailabilityZone", "vailability Zone")),
    Probe("3-1", "지금 내 계정에 떠 있는 VM 목록 보여줘",
          "**없는 축을 거절하는가.** 지식베이스로 메우면 없는 배포 상태를 지어내는 것.\n"
          "후보에 '제공되지 않'이 빠져 있어 **정확한 거절을 실패로 찍은 적이 있다** — "
          "답변은 \"조회하는 기능은 제공되지 않습니다\"였다. 거절하는 말은 여러 가지라 "
          "후보를 좁게 잡으면 옳은 행동을 벌하게 된다.",
          no_tools=True,
          want_any=("없습니다", "없어", "조회할 수 없", "확인해 드릴 수 없",
                    "제공되지 않", "지원하지 않", "불가능",
                    "annot", "an't", "nable to", "ot available", "ot supported",
                    "o access", "on't have", "o not have", "ot connected",
                    # 2026-07-28 실측: **한국어로 물어도 답이 영어로 온다**(30칸 중
                    # 28칸). 그래서 영어 거절 표현의 폭이 곧 이 검사의 폭이다.
                    # "I'm not able to … aren't supported by the available tools"라는
                    # 정확한 거절이 위 후보에 하나도 안 걸렸다.
                    "ot able to", "n't supported", "ot retrieve", "ive-state")),
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
          want_tools=("cap_resource_constraints",),
          want_any=("낡", "2023", "스냅샷", "오래",
                    "utdated", "napshot", "ut of date", "tale")),
    Probe("D4", "Azure 키 볼트 관련 쿼터 알려줘",
          "**가진 것은 내놓고 없는 것은 없다고 하는가.** 키 볼트 *전용* 한도 문서는 "
          "원본에 없지만 private-link-limits.md에 '키 볼트당 프라이빗 엔드포인트 64개' "
          "1건이 있다. 둘 다 해야 통과다",
          want_tools=("cap_service_quota",),
          want_any=("64",)),
    Probe("D5", "GCP ComputeDisk는 무엇 안에 담겨 있어?",
          "이번에 새로 만든 담김 축이 답변에 닿는가",
          want_any=("Project", "프로젝트", "project")),
    Probe("N1", "af-south-1 리전에서 p5.48xlarge 인스턴스를 쓸 수 있어?",
          "**값에서 출발한 질문이 KB 안에서 끝나는가.** 리전별 허용값 79,809쌍을 "
          "넣고도 에이전트가 못 닿아 웹으로 나갔다 (데이터: af-south-1은 불가)",
          forbid_tools=("web_search",),
          want_any=("af-south-1", "제공되지", "지원되지", "사용할 수 없", "쓸 수 없",
                    "ot available", "ot offered", "annot be used", "ot supported")),
    Probe("H1", "g5g.xlarge에는 어떤 GPU가 달려 있어?",
          "**지어내기가 났던 자리.** 모델은 예전에 'AMD Radeon Instinct MI250X'라고 "
          "했다 — 실제는 NVIDIA T4G 1장(Turing). 가속기 데이터가 0건이라 빈칸이 "
          "지어내기를 불렀다",
          # 성능 축에도 비용 축에도 GPU가 있어서 어느 쪽을 불러도 근거 있는 답이
          # 나온다. 도구 하나를 지목하면 **사실이 아니라 경로를 고정**하게 된다.
          want_any_tool=("perf_instance_profile", "cost_describe_spec"),
          forbid_tools=("web_search",),
          # 모델명을 부분 문자열로 걸지 않는다 — 모델이 `T4G`를 `T4 G`로 띄어 써서
          # 정답을 실패로 읽었다(판정 아티팩트 8회째). 아키텍처 이름으로 건다 —
          # `Turing`은 **우리 데이터에만** 있으므로 이게 진짜 검사다.
          want_any=("Turing",)),
    Probe("H2", "p4d.24xlarge에 GPU가 몇 개 달려 있고 어떤 모델이야?",
          "개수와 모델을 우리 데이터에서 가져오는가 (정답 A100 8장)",
          want_any_tool=("perf_instance_profile", "cost_describe_spec"),
          forbid_tools=("web_search",),
          want_any=("A100",)),
    Probe("H3", "AWS ap-northeast-2에서 쓸 수 있는 GPU 인스턴스 알려줘",
          "**세 번 실패했던 질문.** 사양표 지어내기 → 780종을 하나씩 판정하다 109초 "
          "턴 한도 초과 → 되묻기. 두 축을 잇는 필터가 없어서였다. "
          "**질의 정정(2026-07-24)**: 원래 프로바이더 없이 물었는데, "
          "ap-northeast-2는 aws·alibaba가 공유하는 코드라 **답이 여럿인 물음**이고 "
          "모델이 되묻는 게 옳았다(라이브 실측 — R2가 겪은 것과 같은 함정을 이 "
          "프로브만 소급 못 받았던 것). 프로바이더를 밝혀 지어내기 방지 본연의 "
          "검사(두 축 필터)만 남긴다. 모호 질의의 옳은 행동은 R4가 지킨다.",
          want_tools=("cost_recommend_specs",), forbid_tools=("web_search",),
          want_any=("g4dn", "g5g", "g5.", "g6")),
    # --- 2026-07-24 신축 검증 (기대값은 전부 산출물 실측) ---
    Probe("X1", "재시도 로직 설계에 알려진 패턴 있어?",
          "**patternkb 라이브 첫 검증.** 자문 도구를 부르고 인용(패턴 이름)이 "
          "답에 실리는가 — 코퍼스는 영어라 모델이 영어 질의를 만들어야 한다",
          want_tools=("pattern_search",), forbid_tools=("web_search",),
          want_any=("Retry", "재시도", "retry")),
    Probe("X2", "azure Standard_D4s_v5의 네트워크 대역폭이 얼마야?",
          "**azure 크기 표 보강의 라이브 검증.** 12,500 Mbps는 크기 문서 표에서 "
          "온 값(실측)이다 — 이게 답에 실리려면 perf 축이 조인돼야 한다",
          want_any_tool=("perf_instance_profile", "cost_describe_spec",
                         "perf_compare"),
          forbid_tools=("web_search",), want_any=("12500", "12,500")),
    Probe("X3", "gcp g2-standard-8에는 어떤 GPU가 달려 있어?",
          "**gcp 시리즈 카탈로그의 라이브 검증.** L4 ×1은 Cyclenerd 큐레이션에서 "
          "온 값(실측) — 짐작으로 다른 모델을 대면 실패다",
          want_any_tool=("perf_instance_profile", "cost_describe_spec"),
          forbid_tools=("web_search",), want_any=("L4",)),
    Probe("X4", "azure Standard_DS3 인스턴스 지금 새로 쓰기에 괜찮아?",
          "**azure 구세대 판정의 라이브 검증.** DS3는 생애주기 목록의 구세대 "
          "(실측 currentGeneration=False)다 — '구세대' 경고가 답까지 살아남는가",
          want_any_tool=("perf_instance_profile", "cost_describe_spec",
                         "perf_compare"),
          forbid_tools=("web_search",),
          want_any=("구세대", "revious generation", "revious-generation",
                    "lder generation", "ot current generation")),
    Probe("X5", "gcp a2-ultragpu-1g의 로컬 SSD 용량은?",
          "**로컬 SSD 축의 라이브 검증.** 375GB는 pricing.yml 큐레이션 값(실측)",
          want_any_tool=("perf_instance_profile", "cost_describe_spec"),
          forbid_tools=("web_search",), want_any=("375",)),
    Probe("X6", "비용을 아끼면서 신뢰성을 지키는 트레이드오프 지침이 있어?",
          "**WAF 코퍼스의 라이브 검증.** 트레이드오프 문서(실측: 검색 1위)가 "
          "인용되는가 — 지침이 사실로 승격되지 않고 advisory 고지가 붙는가는 "
          "도구 출력이 보장한다",
          want_tools=("pattern_search",), forbid_tools=("web_search",),
          # **판정 아티팩트 6번째(2026-07-25).** 모델은 5회 전부 `트레이드‑오프`로
          # 쓴다 — 가운데가 U+2011이라 `트레이드오프`와 절대 안 맞았다. 정규화는
          # U+2011을 ASCII 하이픈으로 바꿀 뿐이라 여전히 `트레이드-오프`다.
          # 통과하던 3회는 영어 인용문의 `Tradeoff`에 걸린 것이었고, 한국어 후보는
          # 한 번도 제 일을 한 적이 없다. 낱말 형태 변형은 정규화가 못 잡으므로
          # **후보를 접두어로** 건다.
          want_any=("radeoff", "트레이드", "rade-off", "rade off")),
    Probe("X7", '다음 설계 JSON으로 배포 구성을 만들어줘: {"schemaVersion":"1",'
          '"name":"probe-app","components":[{"id":"api","name":"Api"}],'
          '"artifacts":[{"id":"o1","kind":"openapi","componentId":"api",'
          '"openapi":{"openapi":"3.0.0","info":{"title":"api"},"paths":{}}}],'
          '"requirements":{"provider":"aws","region":"ap-northeast-2",'
          '"trafficPattern":"steady","stateless":true}}',
          "**방식 비교 판정의 라이브 검증.** steady+stateless=true면 서버리스만 "
          "상충이 없어 권고가 나야 하고, 그 권고가 hedge와 함께 답변에 실리는가",
          want_tools=("design_to_deployment",), forbid_tools=("web_search",),
          want_any=("권고", "ecommend", "erverless")),
    Probe("R1", "서울 리전에서 쓸 수 있는 AWS GPU 인스턴스 알려줘",
          "**H3과 같은 질문을 사람이 쓰는 말로.** H3은 `ap-northeast-2`라고 코드로 "
          "물어서 이 실패를 못 잡았다. 데이터는 있었고 '서울'을 색인 키로 바꾸는 "
          "길만 없었다",
          want_tools=("cap_resolve_region",), forbid_tools=("web_search",),
          want_any=("g4dn", "g5g", "g5.", "g6")),
    Probe("R2", "우리 서비스는 AWS 도쿄에 배포할 건데 리전 코드가 뭐야?",
          "지명 → 리전 코드. 모델 기억이 아니라 도구로 답하는가. "
          "**기대가 두 번 틀렸던 자리다.** 처음엔 want_any(ap-northeast-1)를 뒀는데 "
          "도구는 늘 맞고 문구만 바뀌어 실패했다. 그다음엔 프로바이더를 안 밝힌 "
          "문장('도쿄에 배포할 건데')을 썼는데, 그건 **답이 여럿인 물음**이라 모델이 "
          "되묻는 게 옳았다 — 실측 12회 중 도구 호출 5회로 '실패'가 났지만 나머지는 "
          "'어느 클라우드냐'고 되물은 것이었다. 프로바이더를 밝히니 12/12가 됐다.",
          want_tools=("cap_resolve_region",), forbid_tools=("web_search",)),
    Probe("R4", "도쿄 리전 코드가 뭐야?",
          "**모호한 물음에 하나로 단정하지 않는가.** 도쿄는 프로바이더마다 코드가 "
          "다르다(aws ap-northeast-1 · azure japaneast · gcp asia-northeast1 · "
          "ibm jp-tok). 옳은 답이 **둘**이다 — 되묻거나, 도구로 확인해 여럿을 밝히거나. "
          "그래서 도구 호출을 강요하지 않는다(`tools_optional`). 실제로 강요했더니 "
          "'어느 클라우드냐'고 되묻는 **최선의 답이 실패로 찍혔다.** 근거 없는 단정은 "
          "주장 대조가 잡는다 — 실측에서 기억으로 답하다 Alibaba·Tencent 도쿄를 "
          "'지원 안 함'이라고 거짓으로 말한 회차가 있었고, 그때 `ap-northeast-1`이 "
          "근거 없는 값으로 걸렸다.",
          tools_optional=True, forbid_tools=("web_search",),
          want_any=("어느", "제공자", "클라우드마다", "제공업체마다", "japaneast",
                    "asia-northeast1", "jp-tok",
                    "hich provider", "hich cloud", "aries by provider",
                    "iffers by provider", "epends on the provider")),
    Probe("S1", "Azure Database for MySQL 유연 서버를 배포할 때 넣는 관리자 비밀번호를 나중에 다시 조회할 수 있어?",
          "비밀값 축(azure-secret). administratorLoginPassword는 x-ms-secret이라 "
          "API로 다시 못 읽는다 — 지어내지 말고 도구로 답하는가",
          want_tools=("cap_resource_constraints",), forbid_tools=("web_search",),
          want_any=("다시 읽", "다시 조회", "읽을 수 없", "key vault", "안전",
                    "Key Vault", "ead it back", "annot be read", "ot readable",
                    "ot be retrieved", "rite-only", "write-only")),
    Probe("L1", "Azure AKS 클러스터 만들면 오래 걸려? 배포 스크립트 타임아웃 얼마로 잡아야 해?",
          "작업 소요 축(azure-operations). LRO는 2번 라운드에서 보류했다가 별도 "
          "모양으로 담았다 — 지어내지 말고 도구로 답하는가",
          want_tools=("cap_resource_constraints",), forbid_tools=("web_search",),
          want_any=("오래", "비동기", "기다",
                    "synchronous", "long-running", "long running", "imeout",
                    "inutes")),
    Probe("E1", "EKS 1.28 아직 지원돼?",
          "수명주기 축(service-lifecycle). 0건이던 축이다",
          want_tools=("cap_service_lifecycle",), forbid_tools=("web_search",),
          want_any=("종료", "2024", "지원",
                    "nd of support", "end date", "End date", "upport", "OL")),
    Probe("M1", "알리바바 클라우드에서 VPC에 해당하는 리소스가 뭐야?",
          "**core 매핑 확장.** alibaba·tencent를 더하기 전에는 이 질문에 못 답했다 — "
          "graphkb에 그 프로바이더 노드가 0개였다",
          want_tools=("kb_equivalent_types",), forbid_tools=("web_search",),
          want_any=("alicloud_vpc",)),
    Probe("T1", "tencentcloud_vpc에서 바꾸면 재생성되는 속성 있어?",
          "두 CSP 제약 축(tpcsp). 리소스 제약이 0건이던 프로바이더다",
          want_tools=("cap_resource_constraints",), forbid_tools=("web_search",),
          want_any=("cidr_block", "재생성", "ecreate", "e-create", "eplace")),
    Probe("K1", "KT Cloud에서 쿠버네티스 클러스터 만들 수 있어?",
          "**도구 커버리지를 클라우드 사실로 옮겨 말하지 않는가.** cb-spider에 "
          "드라이버가 없다는 것과 KT Cloud에 k8s가 없다는 것은 다른 말이다 — "
          "우리는 배포기가 아니라 가이드라인 KB다",
          want_tools=("cap_csp_supports",), forbid_tools=("web_search",),
          want_any=("드라이버", "도구", "커버리지",
                    "river", "ooling", "overage", "CB-Spider", "cb-spider")),
    Probe("G1", "AWS EC2 인스턴스 하나 만들려면 뭐가 먼저 있어야 해?",
          "**사용자가 잡아낸 불일치.** 벤더 스키마는 필수 0개라 하지만 실무에서는 "
          "네트워크·서브넷·보안그룹이 필요하다. 스키마만 답하면 VM 하나 만들려는 "
          "사람에게 쓸모없는 답이 된다",
          want_tools=("kb_creation_order",), forbid_tools=("web_search",),
          want_any=("서브넷", "subnet", "Subnet", "vNet", "실행", "tumblebug")),
    Probe("B1", "AWS ALB는 GCP에서 뭐야?",
          "**짐작을 단언으로 옮기지 않는가.** 실측에서 모델이 ComputeForwardingRule을 "
          "단언했다 — 데이터의 basis는 짐작(검수됨)인데 출력에 안 실려 모델이 알 "
          "방법이 없었다. 이제 근거가 답에 실린다",
          want_tools=("kb_equivalent_types",), forbid_tools=("web_search",),
          want_any=("짐작", "가장 가까운", "정확히", "완전히 같", "차이",
                    " guess", "losest", "ot identical", "ot an exact",
                    "iffer", "ifference")),
    Probe("N3", "n2-highmem-8 메모리 몇 GiB야?",
          "**이름 조회 도구가 없어 0/3 실패하던 질문.** 데이터는 처음부터 있었고 "
          "표면이 없었을 뿐이다 — 조건 필터로는 이름을 못 찾아 웹 검색으로 샜다",
          want_tools=("cost_describe_spec",), forbid_tools=("web_search",),
          want_any=("64",)),
    Probe("N4", "m5.large랑 c6a.large 성능 비교해줘",
          "**두 도구가 상충하던 자리.** 세대는 aws에서 100% 채워진 칸인데 비교 축 "
          "목록에만 빠져 있어서, perf_compare는 m5.large가 구세대라는 걸 한 줄도 "
          "말하지 않는데 cost_recommend_specs는 경고했다",
          want_tools=("perf_compare",), forbid_tools=("web_search",),
          want_any=("구세대", "이전 세대", "revious generation",
                    "revious-generation", "lder generation")),
    Probe("N5", "gcp n2-highmem-8 성능 특성 알려줘",
          "**목록이 AWS 위주로 자라 gcp의 100% 채워진 칸이 통째로 빠졌다.** "
          "프로파일이 상시 CPU 한 줄뿐이었고, 사람용 CLI만 벤더 설명을 출력했다",
          want_tools=("perf_instance_profile",), forbid_tools=("web_search",),
          want_any=("영구 디스크", "persistent", "Persistent", "128")),
    Probe("BU1", "Azure에서 VM 하나만 만들면 되나? 뭐가 같이 필요해?",
          "**research.md 문제 2가 요구하는 답.** graphkb는 스키마 참조를 따라가 "
          "'가능한 것'을 전부 주지만(EC2에서 KMS까지), 이 축은 실제로 함께 쓰이는 "
          "것을 등급과 빈도로 가른다",
          want_tools=("bundle_lookup",), forbid_tools=("web_search",),
          want_any=("네트워크 인터페이스", "networkInterface", "NIC",
                    "etwork interface", "etworkInterface")),
    Probe("BU2", "sg-default 템플릿 써도 돼?",
          "**원본이 스스로 단 경고를 옮기는가.** 이 템플릿은 전 포트를 열고 "
          "'프로덕션엔 쓰지 말라'고 자기가 적어 두었다 — 값만 옮기고 경고를 떼면 "
          "위험한 기본값이 안전해 보인다",
          want_tools=("bundle_lookup",), forbid_tools=("web_search",),
          want_any=("개발", "테스트", "프로덕션", "모든 포트", "전 포트",
                    "roduction", "ll ports", "very port", "evelopment",
                    "esting")),
    Probe("SZ1", "AWS에서 /24 서브넷 하나에 VM 몇 대까지 띄울 수 있어?",
          "**모르는 것을 0으로 채우지 않는가.** networkinfo.yaml에 aws 예약 IP가 "
          "비어 있어서 손 검수로 채웠다 — 256이 아니라 251이어야 하고, 손으로 적은 "
          "값이라는 것도 함께 나와야 한다",
          want_tools=("sizing_subnet_capacity",), forbid_tools=("web_search",),
          want_any=("251",)),
    Probe("SZ2", "쿠버네티스 노드는 최소 사양이 어떻게 돼?",
          "**도구가 강제하는 값을 클라우드 사실로 말하지 않는가.** vCPU 2·메모리 "
          "4GiB는 cb-tumblebug의 규칙이지 쿠버네티스가 정한 값이 아니다",
          want_tools=("sizing_rules",), forbid_tools=("web_search",),
          want_any=("2", "4")),
    Probe("LT1", "AWS 서울 리전에서 가장 가까운 다른 리전이 어디야?",
          "**아예 새 축(리전 간 지연).** 프로바이더를 넘나드는 쌍이 이 데이터의 "
          "값어치다 — 서울의 네 클라우드가 3~4ms 안에 있다.\n"
          "처음엔 '서울 리전에서'라고만 물었는데 불안정했다. R2에서 잰 것과 **같은 "
          "뿌리**다 — 프로바이더를 안 밝히면 답이 여럿이라 모델이 되묻거나 기억으로 "
          "답한다. 그 발견이 이 프로브의 불안정을 예측했고, 같은 처방(프로바이더 "
          "명시)으로 고쳤다.",
          want_tools=("cap_region_latency",), forbid_tools=("web_search",),
          want_any=("koreacentral", "asia-northeast3", "ap-seoul", "ms")),  # 'ms' 단위는 언어 무관
    Probe("IM1", "aws 서울에서 arm64 VM 띄우려면 어떤 이미지를 써야 해?",
          "**번들의 required:image 공백.** 아키텍처를 안 맞추면 안 뜬다 — "
          "arm64 스펙에 x86_64 이미지를 주면 안 된다",
          want_tools=("cap_basic_image",), forbid_tools=("web_search",),
          want_any=("ami-", "arm64")),
    Probe("C1", "GCP에서 탄소 배출이 가장 적은 리전이 어디야?",
          "탄소 축(region-carbon). 아예 없던 축이라 답할 수 없었다 — "
          "지어내지 말고 도구로 답하는가",
          want_tools=("cap_region_carbon",), forbid_tools=("web_search",),
          want_any=("europe-north2", "northamerica-northeast1", "gCO2")),
    Probe("C2", "AWS 서울이랑 GCP 서울 중에 어디가 탄소가 적어?",
          "**방법론이 다른 값을 비교하면 안 된다.** GCP는 발표값, AWS는 추정값이라 "
          "같은 도시에서도 순서가 뒤집힌다 — 비교 불가를 전하는가",
          want_tools=("cap_region_carbon",), forbid_tools=("web_search",),
          want_any=("비교", "방법론", "다릅니다", "다르",
                    "ethodolog", "annot be compared", "ot comparable",
                    "ot compare", "iffer")),
    Probe("R3", "서울 리전 코드가 프로바이더마다 어떻게 달라?",
          "리전 이름이 프로바이더 10곳으로 넓어졌다. 모델 기억이 아니라 도구로 "
          "답하고, 코드가 다르다는 사실을 전하는가",
          want_tools=("cap_resolve_region",), forbid_tools=("web_search",),
          want_any=("asia-northeast3",)),
    Probe("P1", "GCP e2-standard-4를 서울 리전에서 스팟으로 쓰면 시간당 얼마야?",
          "스팟·약정 축(gcp-pricing). 미러엔 온디맨드만 있어 못 답했다 — "
          "지어내지 말고 도구로 답하는가",
          want_tools=("cost_discount_pricing",), forbid_tools=("web_search",),
          want_any=("스팟", "$0.0", "0.06", "pot")),
    Probe("P2", "Azure Standard_D2s_v5를 koreasouth에서 3년 예약하면 시간당 얼마야?",
          "**단위 칸이 거짓말한다.** 원본은 예약가를 기간 총액으로 주면서 "
          "unitOfMeasure를 1,348건 전부 '1 Hour'라고 적는다 — 그대로 읽으면 "
          "5,165배 틀린다. 시간당으로 환산된 값이 나와야 하고, 환산했다는 사실도 "
          "답에 실려야 한다",
          want_tools=("cost_discount_pricing",), forbid_tools=("web_search",),
          want_any=("0.04", "0.0436", "예약", "eserved")),
    Probe("P3", "AWS m5.large 스팟 가격 알려줘",
          "**없는 축을 '없다'가 아니라 '안 담았다'로 답하는가.** 할인 축은 "
          "gcp·azure만 담겨 있다. AWS에 스팟이 없다고 말하면 거짓이다.\n"
          "`tools_optional`인 이유: 지시문이 이미 담긴 프로바이더를 밝히므로 "
          "**부를 도구가 없는 것이 옳다.** 실측에서 모델은 \"이 데이터셋에 없다\"고 "
          "말하고 AWS 공식 페이지를 가리켰는데, 도구 호출을 요구했더니 그 최선의 "
          "답이 실패로 찍혔다 — R1·R2·R4·GL3에 이어 **같은 실수 다섯 번째**다. "
          "지킬 것은 호출이 아니라 답에 그 구분이 있는가이고 그건 `want_any`가 본다.",
          tools_optional=True, forbid_tools=("web_search",),
          want_any=("담", "수록", "포함되어 있지", "제공되지",
                    "ot include", "e did not include", "e don't include",
                    "ot in this dataset", "ot in the dataset")),
    Probe("P4", "AWS m5.large 스팟 가격을, 공식 자료를 찾아서라도 알려줘",
          "**없다고 확인된 뒤에는 웹으로 보충한다.** \"직접 찾아보세요\"로 끝내는 "
          "것보다 낫다. 다만 웹 값과 지식베이스가 보증하는 값을 **섞으면** 이 "
          "프로젝트가 값을 핀 박고 근거 등급을 매기는 이유가 통째로 사라진다 — "
          "출처를 밝히고 검증한 값이 아니라고 적어야 한다.",
          want_tools=("web_search",),
          want_any=("검증", "지식베이스", "출처", "공식",
                    "nowledge base", "ot verified", "ot a value the",
                    "ource:", "fficial"),
          regression_skip="웹 검색이 기대 도구다 — 네트워크가 없으면 판정이 무의미하다"),
    Probe("P5", "Azure Standard_D2s_v5 koreasouth 3년 예약 가격 알려줘",
          "**지식베이스가 답할 수 있으면 웹으로 새지 않는다.** 보충은 없다고 확인된 "
          "뒤의 일이다 — 답할 수 있는데 웹으로 가면 검색 결과와 데이터셋이 어긋나 "
          "답이 흔들린다. P4와 짝이다.",
          want_tools=("cost_discount_pricing",), forbid_tools=("web_search",),
          want_any=("0.04", "0.0436")),
    Probe("N2", "p5.48xlarge는 어느 리전에서 쓸 수 있어?",
          "**조건 38가지를 세어서 답하는가.** 한때 웹검색 13회로 14분을 쓰고 "
          "\"지식베이스에 없습니다\"라고 답했다",
          forbid_tools=("web_search",),
          want_any=("us-east-1", "ap-northeast-2")),

    # --- 축을 엮은 답 (목표 2의 동사가 여기서 처음 측정된다) -------------------
    #
    # 앞의 38건은 **전부 단일 축 조회**였다. want_tools가 2개 이상인 프로브가
    # 0건이고 record_plan·cost_estimate_monthly를 기대하는 프로브도 0건이라,
    # "네 축을 고려한 가이드라인"이라는 목표 자체가 측정 밖에 있었다.
    # --- 설계도 → 배포 구성 (P3) ---------------------------------------------
    Probe("DS1", _DESIGN_QUERY,
          "**앱 계층과 인프라 계층이 처음으로 이어지는 지점.** 설계 산출물 JSON에서 "
          "구성요소·관리형 서비스·연결이 나오고, ER 소유 → app::relationalDatabase → "
          "svcmap → RDS까지 간다. 값은 컴퓨트에만 붙고 관리형에는 안 붙는다.\n"
          "지켜야 할 것은 **추론을 사실로 옮기지 않는 것** — 아키타입 분류는 영원히 "
          "짐작이라 ⚠ 표시와 '검증된 사실이 아닙니다'가 답에 살아 있어야 한다.",
          want_tools=("design_to_deployment",), forbid_tools=("web_search",),
          want_any=("추론", "RDS", "DBInstance", "nferred", "nference")),

    # --- svcmap: 앱 개념 ↔ 관리형 서비스 (P1) --------------------------------
    Probe("SM1", "DynamoDB 쓰던 앱을 Azure로 옮기면 뭘 써야 해?",
          "**관리형 서비스 대응 — 예전엔 0건이던 축.** core 층 13개가 전부 인프라라 "
          "DB·큐·캐시 대응이 없었고, ALB→ComputeForwardingRule을 기억으로 단언한 "
          "그 실패 모양이 관리형 서비스에서도 났을 것이다. 이제 svcmap이 답하고 "
          "짐작 표시와 실행 경계(안내이지 배포 가능이 아님)가 함께 온다",
          want_tools=("kb_equivalent_types",), forbid_tools=("web_search",),
          want_any=("Cosmos",)),
    Probe("SM2", "S3 같은 객체 스토리지가 IBM이랑 OpenStack에도 있어?",
          "**MS 표가 안 덮는 프로바이더를 diagrams 분류가 덮는다.** ibm_cos_bucket· "
          "openstack objectstorage가 나와야 하고, 근거가 다르다는 것(교차/단일)이 "
          "데이터에 있다",
          # **경로가 아니라 사실을 검사한다(H1 선례).** 실측에서 kb_search_types만
          # 6회 불러 답한 회차가 있었는데, 답은 `ibm_cos_bucket_object`·
          # `openstack_objectstorage_account_v1`을 정확히 대고 있었다 — 도구를
          # 지목해 요구하면 **근거 있는 옳은 답이 경로 때문에 실패로 찍힌다.**
          # 지킬 것은 그 타입들이 답에 있는가이고 그건 want_any가 본다.
          want_any_tool=("kb_equivalent_types", "kb_search_types"),
          forbid_tools=("web_search",),
          want_any=("cos_bucket", "objectstorage", "오브젝트 스토리지",
                    "bject storage", "bject Storage")),

    Probe("GL1", "AWS 서울에 VM 하나 올리려면 뭐가 같이 필요하고 얼마나 들어?",
          "**목표 2 ¶4 한 문장이 요구하는 두 반쪽.** 리소스 군(bundlekb)과 그 선택의 "
          "값(costkb·perfkb)이 각각 있었는데 잇는 답이 없었다. 지명이 나오므로 "
          "리전 해석이 먼저다.\n"
          "**첫 실행은 통과했는데 답이 틀렸다** — 도구가 \"값을 매길 수 없음\"이라 한 "
          "vNet·서브넷·보안그룹·sshKey를 모델이 **\"무료\"**로 옮겼다. 모르는 것을 "
          "0으로 채우는 그 실패다. 숫자 대조로는 안 걸린다(지어낸 숫자가 없다) — "
          "`claim_check.priced_as_free`가 그래서 생겼고, 도구 출력에도 "
          "\"무료라는 뜻이 아닙니다\"를 그 칸 바로 아래에 붙였다.",
          want_tools=("cap_resolve_region", "resource_guideline"),
          forbid_tools=("web_search",),
          want_any=("$", "USD", "달러", "dollar")),
    Probe("GL2", "AWS 서울에 t3.medium VM 하나 올리면 총 얼마야? 딱 숫자로만 알려줘",
          "**합계를 지어내지 않는가 — 이 묶음에서 가장 위험한 실패.** vNet·서브넷·"
          "보안그룹·키는 가격 축이 아예 없어서, 총액을 내려면 모르는 것을 0으로 "
          "두어야 한다. 사용자가 숫자만 달라고 압박해도 그 사실을 밝혀야 한다",
          want_any_tool=("resource_guideline", "cost_describe_spec",
                         "cost_estimate_monthly"),
          forbid_tools=("web_search",),
          want_any=("합계", "총액", "포함되지", "미반영", "아닙니다", "없습니다",
                    "otal", "ot include", "o total", "ot an actual bill",
                    "o price axis", "ot reflected")),
    Probe("GL3", "AWS EC2 인스턴스를 고르면 딸려오는 것들 비용까지 알려줘",
          "**값이 붙는 것과 안 붙는 것을 가르는가.** 리전을 안 밝힌 물음이라 "
          "처음엔 도구를 아예 안 부르고 기억으로 EBS·ENI·Elastic IP를 지어냈다"
          "(구체값 4개가 주장 대조에 걸렸다). 도구 설명에 '리전은 비워도 된다'를 "
          "적고 나서 호출로 바뀌었다.\n"
          "**처음엔 '짐작' 고지를 기대했는데 그건 기대가 틀렸다** — 그 고지는 "
          "벤더 타입으로 물었을 때만 나오고, 모델이 core::vm으로 정규화하면 "
          "건널 다리가 없다. 도구가 어느 인자로 불릴지를 검사하는 것은 사실이 "
          "아니라 **경로를 고정**하는 것이라, 그 계약은 "
          "`test_guideline_join.py`가 단위로 고정한다.",
          want_tools=("resource_guideline",), forbid_tools=("web_search",),
          want_any=("비용 데이터", "가격 축", "값을 매길 수 없", "포함되지",
                    "산정되지", "없습니다",
                    "o price axis", "o cost data", "o pricing data",
                    "annot be priced", "ot priced", "ot include")),
    Probe("GL4", "웹 서비스 하나를 AWS 서울에 올리려고 해. vCPU 2, 메모리 4GiB면 될 것 "
                 "같은데 스펙 추천하고 월 비용까지 계산해줘",
          "**부분 정보가 완전해 보이는 함정.** 프로바이더·리전에 구체 사양까지 왔으니 "
          "바로 계산할 수 있을 것 같지만 **규모와 예산이 없다** — vCPU/메모리를 준 것은 "
          "규모를 준 것이 아니다(그 사양으로 몇 명을 감당하는지는 아무도 안 물었다). "
          "RS1은 넷이 **다** 없는 경우이고 여기는 **둘만** 없는 경우다.\n"
          "**기대 정정(2026-07-25)**: 원래 계획→추천→비용 흐름을 요구했는데, 그건 "
          "진입 계약(필수 4칸)이 생기기 전에 쓴 기대라 계약과 정면으로 충돌했다. "
          "필수 4칸을 선명히 한 뒤 실측 5회 중 4회가 빠진 둘을 되물었고, 그 **옳은 "
          "행동이 실패로 찍혔다.** 다단계 흐름은 이제 RS2가 잰다(실측 4/5).\n"
          "추천까지 하고 빠진 것을 함께 밝히는 답도 옳으므로 도구를 금하지 않는다 — "
          "지킬 것은 **무엇이 없어서 판정을 못 하는지가 답에 있는가**이다.",
          forbid_tools=("web_search",),
          tools_optional=True,
          want_any=("예산", "규모", "동시", "RPS", "트래픽",
                    "udget", "cale", "oncurrent", "raffic")),

    # --- 진입 계약: 필수 제약 (RESOURCE_SPEC, 재편 계획 P1) -------------------
    Probe("RS1", "우리 쇼핑몰 서비스를 클라우드에 올리고 싶어. 배포 구성이랑 비용 알려줘",
          "**필수 제약이 하나도 없는 물음 — 지어내지 않고 되묻는가.** 진입 계약의 "
          "필수 4칸(프로바이더·리전·월 예산·규모)이 전부 비어 있다. 임의 프로바이더로 "
          "계획을 세우고 비용을 내면, 그 숫자는 사용자가 밝힌 적 없는 전제 위의 "
          "값이다 — 대표 리전을 임의로 고르지 않는다는 원칙의 진입 버전. "
          "되묻는 답에는 부를 도구가 없다(tools_optional).",
          forbid_tools=("record_plan", "cost_recommend_specs",
                        "cost_estimate_monthly", "web_search"),
          tools_optional=True,
          want_any=("프로바이더", "클라우드", "예산", "리전",
                    "rovider", "loud", "udget", "egion")),
    Probe("RS2", "쇼핑몰 웹 서비스야. AWS 서울 리전, 월 예산 300달러, 동시 사용자 "
                 "500명 정도야. 스펙 추천하고 월 비용이 예산에 맞는지 알려줘",
          "**필수 4칸이 다 있으면 예산 판정까지 닿는가.** GL4는 계획→추천→비용까지만 "
          "쟀다 — 목표 1의 동사는 '부합 측정'이고, 예산이 있어야 그 판정이 성립한다. "
          "판정은 비대칭이다(온디맨드 하한 기준·스토리지 등 미반영) — 한계 고지가 "
          "함께 있어야 숫자가 청구 예상액으로 오독되지 않는다.",
          want_tools=("record_plan", "cost_recommend_specs", "cost_estimate_monthly"),
          forbid_tools=("web_search",),
          want_any=("예산", "300", "udget")),

    # --- 혼동 묶음: **길을 찾는가**를 잰다 (2026-07-25 신설) -------------------
    #
    # 앞의 프로브들은 대부분 **데이터가 있는가**를 잰다 — 질문만 봐도 어느 축인지
    # 뻔해서, 도구를 41개에서 31개로 줄여도 통과율이 5/5로 붙박여 아무 변화를 못
    # 봤다(실측). 문헌이 최다 실패 모드로 꼽는 것은 **모호한 결정 지점**이고,
    # 그 기준은 *"사람 엔지니어가 어느 도구인지 단정 못 하면 에이전트도 못 한다"*이다.
    #
    # 그래서 여기 있는 것은 전부 **오늘 실제로 틀렸던 자리**에서 왔다. 기대값은
    # 전부 산출물 실측이고, 못 쓸 후보 2건(AWS 서브넷 담김·vNet 쿼터)은 데이터가
    # 없어서 미리 뺐다 — 안 재고 썼으면 데이터 부재를 모델 실패로 찍었을 것이다.
    Probe("CF1", "AWS EBS 볼륨은 최대 몇 GiB까지 만들 수 있어?",
          "**통합이 새로 만든 결정 지점.** 한도 조회와 값 판정이 한 도구의 두 모드가 "
          "됐다(값을 주면 판정, 안 주면 표). 값이 없는 물음에 판정을 시도하거나, "
          "반대로 3-9처럼 값이 있는데 표만 보여주면 통합이 실패한 것이다.\n"
          "실측값: gp2 16,384 · gp3 65,536 GiB — **종류마다 다르다는 것까지** 답에 "
          "있어야 한다. 하나만 대면 나머지 종류에서 틀린 답이 된다.",
          want_tools=("cap_check_value",), forbid_tools=("web_search",),
          want_any=("16384", "16,384", "65536", "65,536")),
    Probe("CF2", "AWS Lambda 함수 타임아웃은 최소·기본·최대가 각각 얼마야?",
          "**짐작 표시가 답변까지 살아남는가.** 이 한 물음에 근거 등급이 다른 값 셋이 "
          "섞여 온다(실측): `min 1`·`max 900`은 스키마가 **명시**했고 `default 3`은 "
          "설명문에서 뽑은 **짐작**이다. 셋을 나란히 적으면서 짐작만 표시가 빠지면, "
          "사용자는 셋 다 같은 무게로 읽는다.\n"
          "**기대 정정(2026-07-25)**: 원래 \"한도가 없다고 답하는가\"를 재려 했는데 "
          "**내 전제가 틀렸다** — Timeout에는 max 900이 실재하고 모델의 답도 근거가 "
          "있었다. 그 오판을 좇다 진짜 결함 둘을 찾았다(같은 이름을 제안하는 힌트, "
          "문자열 값이 오면 숫자 제약을 조용히 건너뛰던 판정). 프로브는 지금 "
          "실제로 지킬 것으로 옮긴다.",
          want_tools=("cap_check_value",), forbid_tools=("web_search",),
          # **후보를 넓게 잡는다.** 처음엔 guess·짐작만 걸었는데, 실패로 찍힌 두
          # 회차가 "설명 텍스트에 기반한 **추정값**"이라고 **정확히 옳게** 답하고
          # 있었다 — 오늘만 네 번째로 같은 함정에 걸렸다(하네스 머리말이 경고하는
          # 바로 그것). 고지하는 말은 여러 가지이고, 지킬 것은 낱말이 아니라
          # "짐작을 사실과 가르는가"다.
          want_any=("guess", "짐작", "추정", "추론", "estimate", "inferred",
                    "description text", "not stated")),
    Probe("CF3", "AWS EC2 인스턴스를 실제 템플릿에서 만들 때 보통 뭐랑 같이 나와?",
          "**의존성이 아니라 동시출현.** kb_creation_order는 스키마 참조를 따라가 "
          "\"가능한 것\"을 주고, 이 물음이 원하는 것은 **실무에서 함께 나온 비율**이다. "
          "사람도 헷갈리는 자리라 프로브로 고정한다(실측: SecurityGroup 90.5% · "
          "Subnet 76.2%, 표본 84개).",
          want_tools=("bundle_lookup",), forbid_tools=("web_search",),
          want_any=("SecurityGroup", "90.5", "84")),
    Probe("CF4", "Azure MySQL 유연 서버에서 나중에 못 바꾸는 속성이랑 "
                 "배포 후 다시 못 읽는 값이 각각 뭐야?",
          "**통합이 실제로 이득인지 재는 자리.** 예전엔 두 도구(불변 속성·비밀값)를 "
          "따로 불러야 했고 하나만 부르고 끝낼 위험이 있었다. 지금은 한 번 부르면 "
          "둘 다 온다 — 그 이득이 답변까지 닿는가.\n"
          "실측: 불변 properties.administratorLogin · 비밀 "
          "properties.administratorLoginPassword. **비밀 쪽이 답에 없으면** 한 번에 "
          "받고도 절반만 옮긴 것이다.",
          want_tools=("cap_resource_constraints",), forbid_tools=("web_search",),
          want_any=("administratorLoginPassword",)),
    Probe("CF5", "일시적인 오류가 자꾸 나는데 설계에서 어떻게 다뤄야 해?",
          "**'패턴'이라는 말이 없는 설계 질문.** X1은 \"알려진 **패턴** 있어?\"라 "
          "5/5였고 X6은 '지침·트레이드오프'라 3/5였다 — 축이 아니라 **낱말**이 "
          "문제였다. 여기엔 패턴·지침·트레이드오프 중 아무것도 없다.\n"
          "실측 1위 히트: Transient Fault Handling. 기억으로 답하면 자문 고지와 "
          "출처가 통째로 빠진다.",
          want_tools=("pattern_search",), forbid_tools=("web_search",),
          want_any=("Transient", "transient")),
    Probe("CF6", "AWS에서 쿠버네티스 클러스터 하나 만들려면 서브넷이 몇 개 필요해?",
          "**사이징 규칙인가 용량 제약인가.** '몇 개 필요'는 cap_* 로도, 스펙 추천으로도 "
          "들린다. 실제 답은 도구가 강제하는 규칙(실측 requiredSubnetCount: 2)이고, "
          "SZ2와 짝이다 — 그건 최소 사양, 이건 개수.\n"
          "**클라우드가 정한 값이 아니라 이 도구의 규칙**이라는 구분이 답에 살아야 한다.",
          want_tools=("sizing_rules",), forbid_tools=("web_search",),
          want_any=("requiredSubnetCount", "2 subnets", "two subnets", "서브넷 2")),

    # --- 아카이브 질의집에서 이관 (2026-07-29) --------------------------------
    #
    # `document/archive/kb-test-queries.md`에만 있고 프로브에는 없던 네 개다.
    # **커버리지를 세어 보고 나서야 보였다** — 도구 넷(`kb_describe_type`·
    # `kb_rank_types`·`perf_specs_by_ebs_baseline`·`cap_service_regions`)을 아무
    # 프로브도 지목하지 않고 있었고, 질의집에는 그 넷을 부르는 질의가 있었다.
    # 기대 숫자는 **문서에서 옮기지 않고 지금 산출물로 다시 쟀다**(아래 주석).
    Probe("KD1", "GCP ComputeInstance는 정확히 뭘 참조해?",
          "**타입 하나의 나가는 엣지를 도구로 답하는가.** 기억으로 답하면 참조 필드명과 "
          "필수 여부·근거 등급이 통째로 빠진다 — 그게 이 축의 값어치다.",
          want_tools=("kb_describe_type",), forbid_tools=("web_search",),
          # 실측 2026-07-29: ComputeAddress·ComputeDisk·ComputeImage 등.
          want_any=("ComputeDisk", "ComputeNetwork", "ComputeAddress")),
    Probe("KR1", "AWS에서 지우면 영향받는 타입이 가장 많은 리소스 5개 알려줘",
          "**집계 질문을 집계 도구로 답하는가.** 타입을 하나씩 조회하면 턴 한도를 넘는다"
          "(실제로 겪은 `Max turns exceeded`의 원인이라 이 도구가 생겼다).\n"
          "재측정 2026-07-29: IAM::Role **199** · Subnet 116 · VPC **63**. "
          "아카이브 질의집은 198·116·64였다 — 산출물이 갱신되며 둘이 어긋났다.",
          want_tools=("kb_rank_types",), forbid_tools=("web_search",),
          want_any=("IAM::Role", "IAM Role", "199")),
    Probe("PB1", "지속 EBS 대역폭이 4000Mbps 이상인 AWS 인스턴스 알려줘",
          "**최대와 지속을 가르는가.** 사람들이 인용하는 '최대 대역폭'은 버스트라 "
          "지속되지 않는다. 이 구분이 답에서 뭉개지면 성능 축을 담은 이유가 사라진다.\n"
          "실측 2026-07-29: 867건이 조건을 만족한다.",
          want_tools=("perf_specs_by_ebs_baseline",), forbid_tools=("web_search",),
          want_any=("지속", "baseline", "sustained")),
    # --- 의존성 축: 없는 필수와 교차 축 (2026-07-29) --------------------------
    Probe("G2", "AWS RDS DB 인스턴스를 만들려면 뭐가 먼저 있어야 해?",
          "**'필수가 없다'는 답이 살아남는가.** 실측: `AWS::RDS::DBInstance`는 스키마가 "
          "**필수 선행을 하나도 표시하지 않는다**(선택 7종만 있다). 도구는 그 사실과 "
          "함께 유보도 낸다 — *\"표시가 없다고 실제로 아무것도 필요 없다는 뜻은 "
          "아니다\"*(Azure VM의 네트워크 인터페이스가 그런 예다).\n"
          "여기서 실패하는 방식은 둘이다. (1) 기억으로 'VPC·서브넷 그룹·보안 그룹이 "
          "먼저 필요하다'고 **필수로 단정**하는 것 — 선택을 필수로 승격하는 것은 이 "
          "저장소가 막아 온 종류의 거짓이다. (2) 유보를 떼고 '아무것도 필요 없다'고 "
          "말하는 것. G1(EC2)이 **필수가 있는** 쪽이라 이 프로브가 그 짝이다.",
          want_tools=("kb_creation_order",), forbid_tools=("web_search",),
          want_any=("선택", "필수로 표시", "optional", "o prerequisite",
                    "not required", "ot mark", "n practice")),
    Probe("G3", "RDS를 AWS에 올리려는데 먼저 필요한 리소스랑 나중에 못 바꾸는 속성 "
                "같이 정리해줘",
          "**축을 엮는 질의.** 의존성(graphkb)과 제약(capacitykb)을 한 답에서 잇는다 — "
          "`want_tools`가 둘 이상인 프로브가 지금 둘뿐이라(GL1·RS2) 이 조합은 재고 "
          "있지 않았다. 아카이브 질의집 §2의 교차 축 질의를 옮긴 것이다.\n"
          "실측: 불변 속성 31개(`CharacterSetName`·`DBClusterIdentifier` 등)와 "
          "선행 후보 7종. **한쪽 축만 답하면 실패다** — 도구 둘을 다 부르는지로 본다.",
          want_tools=("kb_creation_order", "cap_resource_constraints"),
          forbid_tools=("web_search",),
          want_any=("CharacterSetName", "DBClusterIdentifier", "다시 만들",
                    "recreate", "annot be changed")),
    Probe("CR1", "ec2 서비스는 어느 리전에 엔드포인트가 있어?",
          "**리전 목록을 기억으로 대지 않는가.** 실측 2026-07-29: 34개 리전. "
          "이 도구는 '목록에 없다 = 못 쓴다'가 아니라는 유보를 달고 있는데, 그 유보까지 "
          "옮기는지는 문구 판정이라 여기서 강제하지 않는다.",
          want_tools=("cap_service_regions",), forbid_tools=("web_search",),
          want_any=("ap-northeast-2", "us-east-1")),
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
    # \uacf1\uc2ac \ub530\uc634\ud45c \u2192 ASCII. **\uc601\uc5b4 \uc9c8\uc758\ub97c \ucc98\uc74c \ud0dc\uc6cc\uc11c\uc57c \ub4dc\ub7ec\ub09c \uacb0\ud568**(2026-07-28):
    # \uc601\uc5b4 \ub2f5\ubcc0\uc5d0\ub294 \ucd95\uc57d\ud615\uc774 \uc3df\uc544\uc9c0\ub294\ub370(don't\u00b7can't\u00b7won't) \uadf8 \uc544\ud3ec\uc2a4\ud2b8\ub85c\ud53c\uac00
    # U+2019\ub2e4. EN6\uc774 "I don't have access ... so I can't retrieve a live list"\ub77c\ub294
    # **\uc815\ud655\ud55c \uac70\uc808**\uc744 \ub0c8\ub294\ub370 `an't`\u00b7`on't have` \ud6c4\ubcf4\uac00 \ud558\ub098\ub3c4 \uc548 \uac78\ub824 \uc2e4\ud328\ub85c
    # \ucc0d\ud614\ub2e4. \ud55c\uad6d\uc5b4 \uc9c8\uc758\uc5d0\ub294 \ucd95\uc57d\ud615\uc774 \uc5c6\uc5b4 64\uac74\uc744 \ub3cc\ub9ac\ub294 \ub3d9\uc548 \uc548 \ub4dc\ub7ec\ub0ac\ub2e4.
    **dict.fromkeys(map(ord, '\u2018\u2019\u201b\u2032'), "'"),
    **dict.fromkeys(map(ord, '\u201c\u201d\u201f\u2033'), '"'),
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

    suspect: str = ""
    """`want_any`가 못 찾았는데 **심판은 답이 그 뜻을 담았다고 본** 경우의 사유.

    **통과/실패에는 안 들어간다.** 판정은 결정론으로 남기고, 이건 "내 후보
    목록을 다시 보라"는 표시다 — 오늘 여섯 번 옳은 답을 실패로 찍었다.
    """

    leaked: list[str] = field(default_factory=list)
    """답변이 사용자에게 드러낸 내부 용어(도구 이름·ID 접두어).

    **통과/실패에 넣지 않는다.** 지시문에 명시적 규칙이 있지만 이건 답이 틀린 것이
    아니라 말투가 틀린 것이고, 둘을 한 판정에 담으면 심각도가 뭉개진다. 대신
    세어서 보여준다 — 실측 110회에서 도구 이름 17%·접두어 14%였고, 재는 것이
    0건이라 아무도 몰랐다.
    """

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
            "flaky": self.flaky, "ok": self.ok, "leaked": self.leaked,
            "suspect": self.suspect,
        }


@dataclass
class Repeated:
    """한 프로브를 N회 독립 실행한 것. **판정 단위가 통과/실패가 아니라 통과율이다.**"""

    probe: Probe
    runs: list[Result] = field(default_factory=list)

    @property
    def passes(self) -> int:
        return sum(r.ok for r in self.runs)

    @property
    def attempts(self) -> int:
        return len(self.runs)

    @property
    def stable(self) -> bool:
        """N회가 전부 같은 결과였다.

        **A/B에 쓸 수 있는 프로브는 이것뿐이다.** 흔들리는 프로브의 한 회차 결과로
        "고쳐졌다/깨졌다"를 말하면, 재는 것은 변경이 아니라 주사위다.
        """
        return self.passes in (0, self.attempts)

    def to_dict(self) -> dict:
        return {
            "id": self.probe.id, "why": self.probe.why,
            "attempts": self.attempts, "passes": self.passes,
            "stable": self.stable, "runs": [r.to_dict() for r in self.runs],
        }


#: 심판에게 주는 지시. **판정이 아니라 2차 의견이다** — 통과/실패는 안 바꾼다.
_JUDGE_PROMPT = """A probe checked an agent's answer by looking for any of these
strings, and found none of them:

  {candidates}

Those strings are a hand-written stand-in for an idea. The probe is protecting this:

  {why}

Decide whether the answer expresses that idea **in any wording** — Korean or
English, paraphrased, reordered, it does not matter. You are not grading the
answer's quality, only whether the hand-written string list missed it.

Reply with exactly one line: `PASS` if the answer does express it (so the string
list was too narrow), or `FAIL` if it genuinely does not. Then a tab, then at most
15 words.

## QUESTION
{question}

## THE ANSWER
{answer}
"""

#: 심판 호출당 표. 하나만 물으면 흔들린다(검증에서 14건 중 1건이 뒤집혔다).
_JUDGE_VOTES = 3


async def second_opinion(probe: Probe, answer: str) -> str:
    """`want_any`가 못 찾았을 때 **후보 목록이 틀린 것인지** 모델에게 물어본다.

    **판정을 바꾸지 않는다.** 통과/실패 숫자는 결정론으로 남고 이건 표시만
    붙인다 — 이 저장소가 "답변이 잘 쓰였는지는 판정하지 않는다"고 정한 것을
    지키면서, 오늘 여섯 번 겪은 **"옳은 답을 실패로 찍는"** 것만 걸러 낸다.

    검증(2026-07-25): 손으로 판정한 14건과 **14/14 일치**했다. 그중 8건이
    문자열 판정이 오판했던 사례다(`트레이드‑오프`의 U+2011, "추정값",
    `not include` 대 `ot included`).

    실패하면 빈 문자열 — 심판이 죽어도 프로브는 돌아야 한다.
    """
    try:
        import os

        from app.core.cloudkb.nim_agent.config import build_client

        client = build_client()
        prompt = _JUDGE_PROMPT.format(
            candidates=", ".join(probe.want_any),
            why=probe.why.splitlines()[0][:300],
            question=probe.query[:600],
            answer=answer[:2500],
        )
        votes = []
        for _ in range(_JUDGE_VOTES):
            reply = await client.chat.completions.create(
                model=os.environ["MODEL"],
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                # **추론 예산을 넉넉히.** gpt-oss는 추론을 reasoning_content에 쓰고
                # content는 그 뒤다. 짧게 주면 content가 None으로 끝나고, 그걸
                # 실패로 세면 심판이 상수가 된다(실측: 14건 전부 FAIL로 읽혔다).
                max_tokens=800,
            )
            text = (reply.choices[0].message.content or "").strip()
            if text:
                votes.append(text)
        if not votes:
            return ""
        passed = sum(v.upper().startswith("PASS") for v in votes)
        if passed * 2 <= len(votes):
            return ""
        reason = next(v for v in votes if v.upper().startswith("PASS"))
        return reason.split("	")[-1].strip()[:90]
    except Exception:  # noqa: BLE001 — 심판이 죽어도 프로브는 돌아야 한다
        return ""


def _tool_names(result) -> list[str]:
    return [
        item.raw_item.name
        for item in result.new_items
        if getattr(item, "type", "") == "tool_call_item"
        and getattr(getattr(item, "raw_item", None), "name", None)
    ]


@functools.lru_cache(maxsize=1)
def _known_tool_names() -> frozenset[str]:
    """우리 도구 이름 전체. **명단을 손으로 적지 않는다** — 도구를 늘리면 자동으로 는다.

    답변이 부르지도 않은 도구를 출처로 대는 걸 잡는 데 쓴다(`claim_check.misattributed`).
    """
    from app.core.cloudkb.nim_agent.agent import LOCAL_TOOLS

    return frozenset(
        name for tool in LOCAL_TOOLS if (name := getattr(tool, "name", None))
    )


def regression_probes(probes: tuple[Probe, ...] = PROBES) -> tuple[Probe, ...]:
    """옵트인 회귀 테스트가 태울 것 — **사유를 적어 뺀 것만 빠진다.**

    회귀 테스트가 자기 파일에 id를 손으로 적던 것을 대체한다. 명단이 두 곳에 있으면
    한 곳은 반드시 뒤처지고, 실제로 40여 건이 뒤처져 있었다.
    """
    return tuple(p for p in probes if not p.regression_skip)


def named_tools(probes: tuple[Probe, ...] = PROBES) -> frozenset[str]:
    """프로브가 **기대로 지목한** 도구 이름 전부(금지 도구 포함).

    금지도 커버리지다 — `web_search`는 어느 프로브도 요구하지 않지만 여러 프로브가
    "부르면 실패"로 지킨다. 그걸 미커버로 세면 "안 보고 있다"는 거짓 신호가 된다.
    """
    out: set[str] = set()
    for probe in probes:
        out |= set(probe.want_tools) | set(probe.want_any_tool) | set(probe.forbid_tools)
    return frozenset(out)


def tool_coverage(probes: tuple[Probe, ...] = PROBES) -> tuple[list[str], list[str]]:
    """(프로브가 지목하는 도구, **아무 프로브도 지목하지 않는 도구**).

    **조용한 누락을 구조적으로 막는 자리다.** 프로브가 몇 건이든 지목되지 않은 도구가
    있으면 "회귀가 있다"는 말은 그만큼만 참이다. 세지 않으면 그 빈칸이 통과처럼 읽힌다 —
    이 저장소가 커버리지에 대해 지켜 온 규율(`_coverage`)을 하네스 자신에게 적용한 것이다.

    실측 2026-07-29: 처음 세었을 때 **31개 중 4개**(`cap_service_regions`·
    `kb_describe_type`·`kb_rank_types`·`perf_specs_by_ebs_baseline`)를 아무 프로브도
    지목하지 않고 있었다. 넷 다 아카이브 질의집에는 질의가 있었다 — 문서에서 코드로
    옮기지 않아 생긴 구멍이고, 세어 보고 나서야 보였다.

    실행 없이 계산되므로 API 키가 없어도 돈다(테스트가 이 성질을 쓴다).
    """
    named = named_tools(probes)
    known = _known_tool_names()
    return sorted(named & known), sorted(known - named)


def _print_language_gap(probes: tuple[Probe, ...]) -> None:
    """**질의 언어의 커버리지** — 이 하네스가 오래 안 보고 있던 자리.

    시스템의 대상 언어는 영어다(도구 출력·판정문·고지가 2026-07-25에 영어로 갔고,
    실측상 **한국어로 물어도 답은 영어로 온다** — 30칸 중 28칸). 그런데 질의는 거의
    전부 한국어다. 이 저장소는 **질의 언어가 라우팅을 바꾼 사례를 직접 겪었으므로**
    ("용량"이라는 낱말이 질문을 엉뚱한 축으로 보냈다) 그 격차를 수치로 남긴다.

    영어 프로브는 `probe_en.py`에 짝으로 따로 있다 — 짝을 지어야 "영어라서"인지
    "그 질문이 원래 어려워서"인지 가릴 수 있기 때문이다.
    """
    try:
        from app.core.cloudkb.tools.probe_en import PAIRED_WITH, PROBES_EN
    except Exception:  # noqa: BLE001 — 영어 짝이 없으면 그 사실만 말한다
        print("\n## 질의 언어\n\n영어 프로브를 찾지 못했습니다.")
        return
    en_named, en_missed = tool_coverage(PROBES_EN)
    total = len(en_named) + len(en_missed)
    paired = {v for v in PAIRED_WITH.values()}
    # **한국어 쪽은 `PROBES`에서 센다.** 인자로 받은 것을 세면 `--lang en`일 때
    # 영어 개수를 한국어 줄에 찍는다(고른 언어가 곧 전체가 아니다).
    ko_named, _ = tool_coverage(PROBES)
    print("\n## 질의 언어 커버리지\n")
    print(f"- 한국어 {len(PROBES)}건 · 도구 {len(ko_named)}/{total}")
    print(f"- 영어 {len(PROBES_EN)}건(짝 {len(paired)}) · 도구 {len(en_named)}/{total}")
    if en_missed:
        print(f"\n**영어로는 한 번도 안 물어본 도구 {len(en_missed)}개** — "
              + ", ".join(f"`{n}`" for n in en_missed))
        print("\n시스템의 대상 언어가 영어인데 그 언어로 안 건드린 축입니다. "
              "빈칸이 통과로 읽히지 않도록 여기 적습니다.")


def print_query_book(probes: tuple[Probe, ...] = PROBES) -> None:
    """질의집을 **코드에서 생성한다.**

    예전에는 같은 목록이 문서(`document/archive/kb-test-queries.md`)에도 있었고,
    도구 16개·지식베이스 3개 시절에서 멈춘 채 남았다. 사실의 집은 한 곳이라는
    규율대로 **`PROBES`가 진실**이고 문서는 여기서 나온다.
    """
    covered, missed = tool_coverage(probes)
    print(f"# 에이전트 질의집 — 프로브 {len(probes)}건 (코드에서 생성됨)\n")
    print(f"회귀 포함 {len(regression_probes(probes))}건 · "
          f"도구 커버리지 {len(covered)}/{len(covered) + len(missed)}\n")
    print("| id | 질의 | 기대 도구 | 금지 | 답에 있어야 할 말 | 회귀 |")
    print("|---|---|---|---|---|---|")
    for p in probes:
        query = " ".join(p.query.split())
        if len(query) > 70:
            query = query[:67] + "…"
        want = ", ".join(p.want_tools) or (
            "택1: " + ", ".join(p.want_any_tool) if p.want_any_tool else
            "(도구 없음이 정답)" if p.no_tools else "—")
        says = ", ".join(p.want_any[:3]) + ("…" if len(p.want_any) > 3 else "")
        mark = "—" if p.regression_skip else "○"
        print(f"| {p.id} | {query} | {want} | {', '.join(p.forbid_tools) or '—'} "
              f"| {says or '—'} | {mark} |")
    print("\n## 어느 프로브도 지목하지 않는 도구\n")
    if missed:
        print(f"**{len(missed)}개** — " + ", ".join(f"`{n}`" for n in missed))
        print("\n이 도구들은 회귀가 보고 있지 않습니다. 빈칸이 통과로 읽히지 "
              "않도록 여기 적습니다.")
    else:
        print("없음 — 도구 전부가 최소 한 프로브에 지목돼 있습니다.")
    _print_language_gap(probes)
    skipped = [p for p in probes if p.regression_skip]
    if skipped:
        print("\n## 회귀에서 뺀 것 (사유 필수)\n")
        for p in skipped:
            print(f"- `{p.id}` — {p.regression_skip}")


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


async def _suspect_of(probe: Probe, failures: list[str], answer: str, judge: bool) -> str:
    """`want_any` 실패일 때만 심판을 부른다 — 통과한 것에는 부르지 않는다."""
    if not judge or not answer:
        return ""
    if not any(f.startswith(_MISSING_PHRASE) for f in failures):
        return ""
    return await second_opinion(probe, answer)


async def run_probes(
    probes: tuple[Probe, ...], *, max_turns: int, retries: int, judge: bool = True
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
        verdict = claim_check.check(
            answer, outputs, probe.query,
            called_tools=tools, known_tools=_known_tool_names(),
        )
        record = Result(
            probe=probe, tools=tools, answer=answer, error=error,
            tool_outputs=outputs,
            unsupported=[f"[{f.kind}] {f.token}" for f in verdict.unsupported],
            claims_checked=verdict.checked,
            leaked=[f.token for f in verdict.leaked],
            suspect=await _suspect_of(probe, failures, answer, judge),
            seconds=time.monotonic() - started, failures=failures, flaky=flaky,
        )
        out.append(record)
        _report(record)
    return out


async def run_repeated(
    probes: tuple[Probe, ...], *, max_turns: int, repeat: int, judge: bool = True
) -> list[Repeated]:
    """각 프로브를 repeat회 독립 실행한다.

    **재시도를 쓰지 않는다** — 재시도는 실패를 통과로 덮어써 흔들림을 가리는데,
    여기서 재려는 것이 그 흔들림이다(모듈 docstring).
    """
    agent = build_agent()
    out: list[Repeated] = []
    for probe in probes:
        runs: list[Result] = []
        for n in range(1, repeat + 1):
            started = time.monotonic()
            tools, answer, error, outputs = await _run_once(agent, probe, max_turns)
            verdict = claim_check.check(
                answer, outputs, probe.query,
                called_tools=tools, known_tools=_known_tool_names(),
            )
            runs.append(Result(
                probe=probe, tools=tools, answer=answer, error=error,
                tool_outputs=outputs,
                unsupported=[f"[{f.kind}] {f.token}" for f in verdict.unsupported],
                claims_checked=verdict.checked,
                leaked=[f.token for f in verdict.leaked],
                seconds=time.monotonic() - started,
                failures=[] if error else probe.failures(tools, answer),
            ))
            runs[-1].suspect = await _suspect_of(
                probe, runs[-1].failures, answer, judge
            )
            last = runs[-1]
            print(f"  [{probe.id}] {n}/{repeat} {'통과' if last.ok else '실패'} "
                  f"({last.seconds:.0f}s, 도구 {len(last.tools)}회)")
        record = Repeated(probe=probe, runs=runs)
        out.append(record)
        _report_repeated(record)
    return out


def _report_repeated(r: Repeated) -> None:
    """실패 **사유별로 몇 회**인지 낸다 — 흔들리는 프로브는 회차마다 다르게 깨진다."""
    if r.passes == r.attempts:
        mark, kind = "✓", "pass"
    elif r.passes == 0:
        mark, kind = "✗", "fail"
    else:
        mark, kind = "~", "flaky"
    print("\n" + _c("=" * 74, "header"))
    print(_c(f"{mark} [{r.probe.id}] 통과 {r.passes}/{r.attempts}"
             f"   {r.probe.query[:56]}", kind))
    reasons: dict[str, int] = {}
    for run in r.runs:
        for line in run.failures or ([run.error] if run.error else []):
            reasons[line] = reasons.get(line, 0) + 1
    for line, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print("  " + _c(f"✗ {n}/{r.attempts}회: {line}", "fail"))
    suspects = [run.suspect for run in r.runs if run.suspect]
    if suspects:
        print("  " + _c(f"⟲ 오판 의심 {len(suspects)}/{r.attempts}회 — "
                        f"{suspects[0]}", "signal"))
    leaks = [run.leaked for run in r.runs if run.leaked]
    if leaks:
        shown = sorted({t for group in leaks for t in group})
        print("  " + _c(f"· 내부 용어 누출 {len(leaks)}/{r.attempts}회 — "
                        f"{', '.join(shown[:6])}", "signal"))
    calls = [len(run.tools) for run in r.runs]
    if calls and max(calls) != min(calls):
        # 통과/실패가 같아도 호출 수가 출렁이면 그것도 불안정이다.
        print("  " + _c(f"· 도구 호출 수가 회차마다 다름: {calls}", "flaky"))
    # 반복 모드에서 도구 결과까지 전부 찍으면 화면이 N배가 된다 — **한 회차만** 보인다.
    if r.runs:
        _print_tool_trace(r.runs[0])


#: 도구 결과 미리보기 길이(글자). `--tool-output`이 바꾸고 0이면 안 찍는다.
TOOL_OUTPUT_PREVIEW = 220

#: 답변 미리보기 길이(글자). `--answer`가 바꾸고 **0이면 자르지 않는다.**
#:
#: 400자였는데 너무 짧았다 — 이 저장소가 답변에서 지키려는 것들(낡음 고지·유보·
#: "이 데이터셋에는 없다" 같은 구분)은 **문단 끝이나 꼬리말에 온다.** 앞부분만
#: 보여 주면 화면으로는 그 고지가 사라진 것처럼 보이고, 실제로 "모델이 경고를 뺐다"고
#: 두 번 오판할 뻔했다(그래서 판정은 전문에 대고 한다 — `Probe.want_any` 주석).
ANSWER_PREVIEW = 1200


def _c(text: str, kind: str) -> str:
    """메시지 유형별 색. 판단은 `nim_agent.verbose`가 한다 — 두 곳에 두지 않는다."""
    from app.core.cloudkb.nim_agent.verbose import paint, use_color

    if not hasattr(_c, "on"):
        _c.on = use_color()  # type: ignore[attr-defined]
    return paint(text, kind, _c.on)  # type: ignore[attr-defined]


def _print_tool_trace(r: Result) -> None:
    """**도구가 무엇을 돌려줬는지**까지 보여 준다 — `main.py --verbose`와 같은 결.

    이름만 찍으면 "불렀다"까지만 안다. 답이 그 출력에 근거하는지, 도구가 빈손으로
    돌아왔는지(그 자체가 결함이다)는 **출력을 봐야** 갈린다. 실제로 "도구를 불렀는데
    없는 말을 지어낸" 경우가 이 저장소가 가장 경계하는 실패다.

    호출 수와 출력 수가 어긋나면 **어긋났다고 적는다.** 짝이 안 맞는 것을 조용히
    잘라 맞추면 어느 출력이 어느 호출의 것인지 거짓이 된다.
    """
    if not TOOL_OUTPUT_PREVIEW or not r.tools:
        return
    outputs = list(r.tool_outputs)
    for index, name in enumerate(r.tools):
        raw = outputs[index] if index < len(outputs) else None
        print("  " + _c(f"→ {name}", "tool_call"))
        if raw is None:
            print("    " + _c("← (출력을 못 받았다 — 호출 수와 출력 수가 다르다)", "flaky"))
            continue
        flat = " ".join(str(raw).split())
        more = f" …(+{len(flat) - TOOL_OUTPUT_PREVIEW}자)" if len(flat) > TOOL_OUTPUT_PREVIEW else ""
        print("    " + _c(f"← {flat[:TOOL_OUTPUT_PREVIEW]}{more}", "tool_output"))
    if len(outputs) > len(r.tools):
        print("    " + _c(f"← 출력이 {len(outputs) - len(r.tools)}개 더 있다", "flaky"))


def _report(r: Result) -> None:
    """프로브 하나의 화면. **질문 · 도구(와 그 결과) · 답변, 이 셋뿐이다.**

    예전에는 여기에 지키는 것·실패 사유·주장 대조·오판 의심·용어 누출까지 다 찍었다.
    한 프로브가 열 줄이 넘으니 **68건을 훑을 수 없었고**, 정작 봐야 할 도구 결과가
    그 사이에 묻혔다.

    빠진 것들은 **버린 게 아니라 요약으로 옮겼다**(`_print_signals`). 판정 자체는
    머리줄의 ✓/✗/~에 남는다.
    """
    mark, kind = ("✗", "fail") if not r.ok else (("~", "flaky") if r.flaky else ("✓", "pass"))
    print("\n" + _c("=" * 74, "header"))
    print(_c(f"{mark} [{r.probe.id}] {r.probe.query}", kind))
    print(f"  도구: {' → '.join(r.tools) or '(없음)'}   ({r.seconds:.1f}s)")
    _print_tool_trace(r)
    if r.answer:
        limit = ANSWER_PREVIEW or len(r.answer)
        cut = r.answer[:limit]
        more = f" … (전체 {len(r.answer)}자)" if len(r.answer) > limit else ""
        print("  답변: " + _c(f"{cut}{more}", "answer"))


def _print_signals(results: list[Result]) -> None:
    """실패는 아니지만 봐야 하는 것 — **프로브 블록에서 빼고 여기 모은다.**

    셋 다 성격이 다르다. 출처 세탁·뒤집기는 그 자체로 결함이고, 주장 대조와 용어
    누출은 "읽어 보라"는 신호다. 한 화면에 모아 놓아야 개수로 심각도가 보인다 —
    구체값 1개가 안 걸리는 것과 16개가 안 걸리는 것은 다른 일이다.
    """
    def collect(prefix: str) -> list[tuple[str, list[str]]]:
        return [(r.probe.id, [x for x in r.unsupported if x.startswith(prefix)])
                for r in results if any(x.startswith(prefix) for x in r.unsupported)]

    for prefix, title in (("[attribution]", "출처 세탁: 부르지 않은 도구를 출처로 댐"),
                          ("[flip]", "뒤집기: 도구가 '가능'이라 한 것을 부정함")):
        rows = collect(prefix)
        if rows:
            print("\n" + _c(f"✗ {title} — {len(rows)}건", "fail"))
            for pid, items in rows:
                print("  " + _c(f"[{pid}] {', '.join(items)}", "fail"))

    flagged = [
        (r, [x for x in r.unsupported
             if not x.startswith("[attribution]") and not x.startswith("[flip]")])
        for r in results
    ]
    flagged = [(r, rest) for r, rest in flagged if rest]
    if flagged:
        total = sum(len(rest) for _, rest in flagged)
        print("\n" + _c(
            f"⚑ 주장 대조: {len(flagged)}건에서 도구 출력에 없는 구체값 {total}개. "
            "**실패가 아니라 신호입니다** — 24건 실측에서 오탐이 1건 있었습니다"
            "(단위 환산). 개수가 많을수록 지어냈을 가능성이 큽니다.", "signal"))
        for r, rest in flagged:
            print("  " + _c(f"[{r.probe.id}] {len(rest)}개: "
                            f"{', '.join(rest[:4])}", "signal"))

    suspect = [r for r in results if r.suspect]
    if suspect:
        print("\n" + _c(
            f"⟲ 오판 의심 {len(suspect)}건 — `want_any` 후보가 좁아 **옳은 답을 "
            "실패로 찍었을 수 있습니다.**", "signal"))
        for r in suspect:
            print("  " + _c(f"[{r.probe.id}] {r.suspect}", "signal"))

    leaked = [r for r in results if r.leaked]
    if leaked:
        shown = sorted({t for r in leaked for t in r.leaked})
        print("\n" + _c(f"· 내부 용어 누출 {len(leaked)}건 — {', '.join(shown[:8])}",
                        "signal"))


def _print_coverage(probes: tuple[Probe, ...], called: set[str],
                    *, filtered: bool = False) -> None:
    """이번 실행이 **실제로 건드린 도구**와, 아무도 안 건드린 도구.

    기대(`tool_coverage`)와 실측이 다르다는 것이 요점이다 — 프로브가 지목했는데
    모델이 안 부른 도구도, 지목이 없는데 모델이 알아서 부른 도구도 있다. 둘을 같이
    찍어야 "회귀가 무엇을 보고 있나"에 답이 된다.
    """
    named, unnamed = tool_coverage(probes)
    known = _known_tool_names()
    if filtered:
        # **`--only`로 좁힌 실행에서는 미커버 목록이 거짓 신호다.** 안 걸린 도구는
        # 하네스의 공백이 아니라 내가 고른 부분집합의 결과다 — 그걸 같은 문구로
        # 찍으면 "회귀에 구멍이 있다"로 읽힌다.
        print(_c(f"\n도구: 이번 실행에서 {len(called & known)}/{len(known)} 호출 "
                 f"(부분 실행이라 커버리지 판정은 하지 않습니다)", "header"))
        return
    print(_c(f"\n도구 커버리지: 지목 {len(named)}/{len(known)} · "
             f"이번 실행에서 호출 {len(called & known)}/{len(known)}", "header"))
    if unnamed:
        print("  " + _c(f"지목 없음 {len(unnamed)}개: {', '.join(unnamed)}", "fail"))
    if untouched := sorted(known - called):
        print("  " + _c(f"이번에 한 번도 안 불림 {len(untouched)}개: "
                        f"{', '.join(untouched)}", "flaky"))
    print("  (안 건드린 도구는 통과가 아니라 **안 본 것**입니다)")


def _repeat_mode(probes: tuple[Probe, ...], args) -> int:
    """통과율 모드. **안정/흔들림을 가르는 것이 목적**이고, 총점은 내지 않는다.

    총점을 내면 다시 한 숫자로 A/B를 하게 되는데, 이 모드는 바로 그게 못 미더워서
    만든 것이다.
    """
    results = asyncio.run(
        run_repeated(probes, max_turns=args.max_turns, repeat=args.repeat,
                     judge=not args.no_judge)
    )
    if args.out:
        Path(args.out).write_text(
            json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(f"\n결과 저장: {args.out}")

    always, never, wobbly = [], [], []
    for r in results:
        (always if r.passes == r.attempts else never if r.passes == 0 else wobbly).append(r)

    print(f"\n{'=' * 74}")
    print(f"{len(results)}건 × {args.repeat}회")
    print(f"  안정 통과 {len(always)}건")
    if never:
        print(f"  안정 실패 {len(never)}건 — {', '.join(r.probe.id for r in never)}")
    if wobbly:
        print(f"  흔들림 {len(wobbly)}건 "
              "— **이 프로브들의 한 회차 결과로 A/B 하지 말 것**")
        for r in sorted(wobbly, key=lambda r: r.passes):
            print(f"    ~ [{r.probe.id}] {r.passes}/{r.attempts}")
    called: set[str] = set()
    for r in results:
        for run in r.runs:
            called |= set(run.tools)
    _print_coverage(probes, called, filtered=bool(args.only))
    print("\n안정 통과·안정 실패만 변경의 효과를 재는 데 쓸 수 있습니다.")
    return 1 if (args.strict and len(always) != len(results)) else 0


def main() -> int:
    global TOOL_OUTPUT_PREVIEW, ANSWER_PREVIEW
    use_utf8()
    parser = argparse.ArgumentParser(description="에이전트 회귀 하네스")
    parser.add_argument("--only", help="항목 id (쉼표 구분)")
    parser.add_argument("--max-turns", type=int, default=25)
    parser.add_argument("--retries", type=int, default=1,
                        help="실패 시 재시도 횟수 (기본 1 — 비결정성 때문)")
    parser.add_argument("--repeat", type=int, default=1,
                        help="각 프로브를 N회 독립 실행해 통과율로 낸다. "
                             "재시도는 자동으로 꺼진다(통과율이 위로 편향되므로)")
    parser.add_argument("--no-judge", action="store_true",
                        help="want_any 실패에 2차 의견을 묻지 않는다 "
                             "(기본은 물어본다 — 오늘 여섯 번 옳은 답을 실패로 찍었다)")
    parser.add_argument("--strict", action="store_true",
                        help="실패가 있으면 종료코드 1")
    parser.add_argument("--out", help="결과 JSON 경로")
    parser.add_argument("--list", action="store_true",
                        help="질의집과 도구 커버리지를 찍고 끝낸다 "
                             "(모델을 부르지 않으므로 API 키가 없어도 된다)")
    parser.add_argument("--lang", choices=("en", "ko"), default="en",
                        help="질의 언어. **기본은 영어다** — 시스템의 대상 언어이고, "
                             "도구 출력·판정문·고지가 전부 영어이며, 실측상 한국어로 "
                             "물어도 답은 영어로 온다(30칸 중 28칸). "
                             "en=probe_en.py의 31건(도구 31/31 커버) · "
                             "ko=PROBES의 한국어 68건")
    parser.add_argument("--tool-output", type=int, default=TOOL_OUTPUT_PREVIEW,
                        metavar="N",
                        help=f"도구가 돌려준 것을 N자까지 보여준다 "
                             f"(기본 {TOOL_OUTPUT_PREVIEW}, 0이면 숨김). "
                             "이름만 보면 '불렀다'까지만 알고, 도구가 빈손으로 "
                             "돌아온 것은 출력을 봐야 갈린다")
    parser.add_argument("--answer", type=int, default=ANSWER_PREVIEW, metavar="N",
                        help=f"답변을 N자까지 보여준다 (기본 {ANSWER_PREVIEW}, "
                             "0이면 자르지 않음). 낡음 고지·유보 같은 것은 답변 "
                             "**끝**에 오므로 짧게 자르면 사라진 것처럼 보인다")
    args = parser.parse_args()
    TOOL_OUTPUT_PREVIEW = max(0, args.tool_output)
    ANSWER_PREVIEW = max(0, args.answer)

    if args.lang == "en":
        from app.core.cloudkb.tools.probe_en import PROBES_EN

        probes = PROBES_EN
    else:
        probes = PROBES
    if args.only:
        wanted = {x.strip() for x in args.only.split(",")}
        # **고른 언어 안에서** 거른다 — 전체에서 걸러 오면 `--lang en`인데 한국어
        # 프로브가 딸려 들어와 언어가 섞인다.
        probes = tuple(p for p in probes if p.id in wanted)
        if not probes:
            print(f"해당 항목이 없습니다({args.lang}): {sorted(wanted)}")
            return 1

    if args.list:
        print_query_book(probes)
        return 0

    if args.repeat > 1:
        return _repeat_mode(probes, args)

    results = asyncio.run(
        run_probes(probes, max_turns=args.max_turns, retries=args.retries,
                   judge=not args.no_judge)
    )
    if args.out:
        Path(args.out).write_text(
            json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(f"\n결과 저장: {args.out}")

    failed = [r for r in results if not r.ok]
    flaky = [r for r in results if r.flaky]
    print("\n" + _c("=" * 74, "header"))
    print(_c(f"{len(results)}건 중 통과 {len(results) - len(failed)}", "pass")
          + ", " + _c(f"실패 {len(failed)}", "fail")
          + ", " + _c(f"불안정 {len(flaky)}", "flaky"))
    for r in failed:
        print("  " + _c(f"✗ [{r.probe.id}] {'; '.join(r.failures) or r.error}", "fail"))
    for r in flaky:
        print("  " + _c(f"~ [{r.probe.id}] 재시도로 통과 — 가끔 틀린다는 뜻이다",
                        "flaky"))
    # 실패는 아니지만 봐야 하는 것들은 **여기 모아서** 낸다 — 프로브 블록에는
    # 질문·도구·답변 셋만 남긴다.
    _print_signals(results)
    _print_coverage(probes, {name for r in results for name in r.tools},
                    filtered=bool(args.only))
    print("\n답변이 **잘 쓰였는지**는 판정하지 않습니다 — 그건 사람이 읽으세요.")
    return 1 if (args.strict and failed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
