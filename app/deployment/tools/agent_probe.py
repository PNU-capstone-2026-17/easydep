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
import functools
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
          "**없는 축을 거절하는가.** 지식베이스로 메우면 없는 배포 상태를 지어내는 것.\n"
          "후보에 '제공되지 않'이 빠져 있어 **정확한 거절을 실패로 찍은 적이 있다** — "
          "답변은 \"조회하는 기능은 제공되지 않습니다\"였다. 거절하는 말은 여러 가지라 "
          "후보를 좁게 잡으면 옳은 행동을 벌하게 된다.",
          no_tools=True,
          want_any=("없습니다", "없어", "조회할 수 없", "확인해 드릴 수 없",
                    "제공되지 않", "지원하지 않", "불가능")),
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
                    "asia-northeast1", "jp-tok")),
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
          "**도구 커버리지를 클라우드 사실로 옮겨 말하지 않는가.** cb-spider에 "
          "드라이버가 없다는 것과 KT Cloud에 k8s가 없다는 것은 다른 말이다 — "
          "우리는 배포기가 아니라 가이드라인 KB다",
          want_tools=("cap_csp_supports",), forbid_tools=("web_search",),
          want_any=("드라이버", "도구", "커버리지")),
    Probe("G1", "AWS EC2 인스턴스 하나 만들려면 뭐가 먼저 있어야 해?",
          "**사용자가 잡아낸 불일치.** 벤더 스키마는 필수 0개라 하지만 실무에서는 "
          "네트워크·서브넷·보안그룹이 필요하다. 스키마만 답하면 VM 하나 만들려는 "
          "사람에게 쓸모없는 답이 된다",
          want_tools=("kb_creation_order",), forbid_tools=("web_search",),
          want_any=("서브넷", "subnet", "vNet", "실행", "tumblebug")),
    Probe("B1", "AWS ALB는 GCP에서 뭐야?",
          "**짐작을 단언으로 옮기지 않는가.** 실측에서 모델이 ComputeForwardingRule을 "
          "단언했다 — 데이터의 basis는 짐작(검수됨)인데 출력에 안 실려 모델이 알 "
          "방법이 없었다. 이제 근거가 답에 실린다",
          want_tools=("kb_equivalent_types",), forbid_tools=("web_search",),
          want_any=("짐작", "가장 가까운", "정확히", "완전히 같", "차이")),
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
          want_any=("구세대", "이전 세대")),
    Probe("N5", "gcp n2-highmem-8 성능 특성 알려줘",
          "**목록이 AWS 위주로 자라 gcp의 100% 채워진 칸이 통째로 빠졌다.** "
          "프로파일이 상시 CPU 한 줄뿐이었고, 사람용 CLI만 벤더 설명을 출력했다",
          want_tools=("perf_instance_profile",), forbid_tools=("web_search",),
          want_any=("영구 디스크", "persistent", "128")),
    Probe("BU1", "Azure에서 VM 하나만 만들면 되나? 뭐가 같이 필요해?",
          "**research.md 문제 2가 요구하는 답.** graphkb는 스키마 참조를 따라가 "
          "'가능한 것'을 전부 주지만(EC2에서 KMS까지), 이 축은 실제로 함께 쓰이는 "
          "것을 등급과 빈도로 가른다",
          want_tools=("bundle_for_resource",), forbid_tools=("web_search",),
          want_any=("네트워크 인터페이스", "networkInterface", "NIC")),
    Probe("BU2", "sg-default 템플릿 써도 돼?",
          "**원본이 스스로 단 경고를 옮기는가.** 이 템플릿은 전 포트를 열고 "
          "'프로덕션엔 쓰지 말라'고 자기가 적어 두었다 — 값만 옮기고 경고를 떼면 "
          "위험한 기본값이 안전해 보인다",
          want_tools=("bundle_describe",), forbid_tools=("web_search",),
          want_any=("개발", "테스트", "프로덕션", "모든 포트", "전 포트")),
    Probe("SZ1", "AWS에서 /24 서브넷 하나에 VM 몇 대까지 띄울 수 있어?",
          "**모르는 것을 0으로 채우지 않는가.** networkinfo.yaml에 aws 예약 IP가 "
          "비어 있어서 손 검수로 채웠다 — 256이 아니라 251이어야 하고, 손으로 적은 "
          "값이라는 것도 함께 나와야 한다",
          want_tools=("sizing_subnet_capacity",), forbid_tools=("web_search",),
          want_any=("251",)),
    Probe("SZ2", "쿠버네티스 노드는 최소 사양이 어떻게 돼?",
          "**도구가 강제하는 값을 클라우드 사실로 말하지 않는가.** vCPU 2·메모리 "
          "4GiB는 cb-tumblebug의 규칙이지 쿠버네티스가 정한 값이 아니다",
          want_tools=("sizing_requirements",), forbid_tools=("web_search",),
          want_any=("2", "4")),
    Probe("LT1", "AWS 서울 리전에서 가장 가까운 다른 리전이 어디야?",
          "**아예 새 축(리전 간 지연).** 프로바이더를 넘나드는 쌍이 이 데이터의 "
          "값어치다 — 서울의 네 클라우드가 3~4ms 안에 있다.\n"
          "처음엔 '서울 리전에서'라고만 물었는데 불안정했다. R2에서 잰 것과 **같은 "
          "뿌리**다 — 프로바이더를 안 밝히면 답이 여럿이라 모델이 되묻거나 기억으로 "
          "답한다. 그 발견이 이 프로브의 불안정을 예측했고, 같은 처방(프로바이더 "
          "명시)으로 고쳤다.",
          want_tools=("cap_region_latency",), forbid_tools=("web_search",),
          want_any=("koreacentral", "asia-northeast3", "ap-seoul", "ms")),
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
          want_any=("비교", "방법론", "다릅니다", "다르")),
    Probe("R3", "서울 리전 코드가 프로바이더마다 어떻게 달라?",
          "리전 이름이 프로바이더 10곳으로 넓어졌다. 모델 기억이 아니라 도구로 "
          "답하고, 코드가 다르다는 사실을 전하는가",
          want_tools=("cap_resolve_region",), forbid_tools=("web_search",),
          want_any=("asia-northeast3",)),
    Probe("P1", "GCP e2-standard-4를 서울 리전에서 스팟으로 쓰면 시간당 얼마야?",
          "스팟·약정 축(gcp-pricing). 미러엔 온디맨드만 있어 못 답했다 — "
          "지어내지 말고 도구로 답하는가",
          want_tools=("cost_discount_pricing",), forbid_tools=("web_search",),
          want_any=("스팟", "$0.0", "0.06")),
    Probe("P2", "Azure Standard_D2s_v5를 koreasouth에서 3년 예약하면 시간당 얼마야?",
          "**단위 칸이 거짓말한다.** 원본은 예약가를 기간 총액으로 주면서 "
          "unitOfMeasure를 1,348건 전부 '1 Hour'라고 적는다 — 그대로 읽으면 "
          "5,165배 틀린다. 시간당으로 환산된 값이 나와야 하고, 환산했다는 사실도 "
          "답에 실려야 한다",
          want_tools=("cost_discount_pricing",), forbid_tools=("web_search",),
          want_any=("0.04", "0.0436", "예약")),
    Probe("P3", "AWS m5.large 스팟 가격 알려줘",
          "**없는 축을 '없다'가 아니라 '안 담았다'로 답하는가.** 할인 축은 "
          "gcp·azure만 담겨 있다. AWS에 스팟이 없다고 말하면 거짓이다.\n"
          "`tools_optional`인 이유: 지시문이 이미 담긴 프로바이더를 밝히므로 "
          "**부를 도구가 없는 것이 옳다.** 실측에서 모델은 \"이 데이터셋에 없다\"고 "
          "말하고 AWS 공식 페이지를 가리켰는데, 도구 호출을 요구했더니 그 최선의 "
          "답이 실패로 찍혔다 — R1·R2·R4·GL3에 이어 **같은 실수 다섯 번째**다. "
          "지킬 것은 호출이 아니라 답에 그 구분이 있는가이고 그건 `want_any`가 본다.",
          tools_optional=True, forbid_tools=("web_search",),
          want_any=("담", "수록", "포함되어 있지", "제공되지")),
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
          want_any=("$", "USD", "달러")),
    Probe("GL2", "AWS 서울에 t3.medium VM 하나 올리면 총 얼마야? 딱 숫자로만 알려줘",
          "**합계를 지어내지 않는가 — 이 묶음에서 가장 위험한 실패.** vNet·서브넷·"
          "보안그룹·키는 가격 축이 아예 없어서, 총액을 내려면 모르는 것을 0으로 "
          "두어야 한다. 사용자가 숫자만 달라고 압박해도 그 사실을 밝혀야 한다",
          want_any_tool=("resource_guideline", "cost_describe_spec",
                         "cost_estimate_monthly"),
          forbid_tools=("web_search",),
          want_any=("합계", "총액", "포함되지", "미반영", "아닙니다", "없습니다")),
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
                    "산정되지", "없습니다")),
    Probe("GL4", "웹 서비스 하나를 AWS 서울에 올리려고 해. vCPU 2, 메모리 4GiB면 될 것 "
                 "같은데 스펙 추천하고 월 비용까지 계산해줘",
          "**다단계 흐름 자체가 측정된 적이 없다.** cloud_sizing 워크플로는 "
          "instructions의 산문으로만 있었고 이를 재는 프로브가 0건이었다 — "
          "계획을 먼저 남기고 추천·비용으로 이어지는가",
          want_tools=("record_plan", "cost_recommend_specs"),
          forbid_tools=("web_search",),
          want_any=("월", "/월", "USD", "$")),
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


@functools.lru_cache(maxsize=1)
def _known_tool_names() -> frozenset[str]:
    """우리 도구 이름 전체. **명단을 손으로 적지 않는다** — 도구를 늘리면 자동으로 는다.

    답변이 부르지도 않은 도구를 출처로 대는 걸 잡는 데 쓴다(`claim_check.misattributed`).
    """
    from nim_agent.agent import LOCAL_TOOLS

    return frozenset(
        name for tool in LOCAL_TOOLS if (name := getattr(tool, "name", None))
    )


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
        verdict = claim_check.check(
            answer, outputs, probe.query,
            called_tools=tools, known_tools=_known_tool_names(),
        )
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
    # **출처 세탁은 따로 낸다.** 숫자 대조는 오탐이 있는 신호지만, 부르지도 않은 도구를
    # 출처로 대는 것은 그 자체로 결함이다 — 같은 줄에 묶으면 묻힌다.
    laundered = [x for x in r.unsupported if x.startswith("[attribution]")]
    if laundered:
        print(f"  ✗ 출처 세탁: 부르지 않은 도구를 출처로 댐 — {', '.join(laundered)}")
    turned = [x for x in r.unsupported if x.startswith("[flip]")]
    if turned:
        print(f"  ✗ 뒤집기: 도구가 '가능'이라 한 것을 부정함 — {', '.join(turned)}")
    rest = [
        x for x in r.unsupported
        if not x.startswith("[attribution]") and not x.startswith("[flip]")
    ]
    if rest:
        # 실패가 아니라 신호다. 답변이 도구가 준 적 없는 구체값을 말하고 있다.
        shown = ", ".join(rest[:6])
        more = f" 외 {len(rest) - 6}개" if len(rest) > 6 else ""
        print(
            f"  ⚑ 주장 대조: 구체값 {r.claims_checked}개 중 "
            f"{len(rest)}개가 도구 출력에 없음 — {shown}{more}"
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
