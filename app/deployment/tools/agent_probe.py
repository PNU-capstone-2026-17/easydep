"""에이전트 실측 하네스 — 질의집을 배치로 돌리고 **도구 호출을 기록**한다.

`main.py`는 대화형이라 손으로 하나씩 쳐야 한다. 여기서는 질의 목록을 한 번에 돌리고,
질의마다 **어떤 도구를 어떤 순서로 불렀는지**와 최종 답을 함께 남긴다.

**도구 호출 기록이 핵심이다.** 답이 그럴듯해 보여도 도구를 안 부르고 모델이 지어낸
것일 수 있고, 그게 이 프로젝트에서 반복적으로 겪은 실패 양상이다. 답만 읽으면
그 둘이 구별되지 않는다.

    python tools/agent_probe.py                 # 기본 질의 세트
    python tools/agent_probe.py --only 3-9      # 특정 항목만
    python tools/agent_probe.py --out out.json  # 결과를 파일로

질의는 `document/kb-test-queries.md`의 항목 번호를 따른다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from agents import Runner
from agents.exceptions import MaxTurnsExceeded

from kbcommon.console import use_utf8
from nim_agent.agent import build_agent
from nim_agent.session import SessionState


@dataclass
class Probe:
    """질의 하나와 **무엇을 확인하는지**."""

    id: str
    query: str
    expect: str
    """사람이 읽을 합격 기준. 자동 판정하지 않는다 — 판정을 코드로 굳히면
    '문구가 바뀌었다'와 '동작이 틀렸다'가 구별되지 않는다."""

    forbid_tools: tuple[str, ...] = ()
    """이게 불리면 실패인 도구 (예: 단순 조회에 record_plan)."""

    want_tools: tuple[str, ...] = ()
    """최소한 하나는 불려야 하는 도구."""

    want_any: tuple[str, ...] = ()
    """**전체 답변**에서 최소 하나는 나와야 하는 말.

    화면에는 답변을 잘라서 보여주므로 눈으로 판정하면 뒤쪽을 놓친다 — 실제로
    "모델이 낡음 경고를 뺐다"고 두 번 오판할 뻔했다(경고는 답변 맨 끝에 있었다).
    모델이 바꿔 말할 수 있으니 **여러 후보 중 하나만 맞으면 통과**로 본다."""


PROBES: list[Probe] = [
    Probe("1-1a", "VM을 만들려면 어떤 리소스들이 먼저 필요해?",
          "선행 체인을 도구로 조회", want_tools=("kb_creation_order",)),
    Probe("1-1b", "AWS VPC를 지우면 뭐가 영향받아?",
          "466개 타입", want_tools=("kb_deletion_impact",)),
    Probe("1-2a", "서브넷에서 나중에 못 바꾸는 속성이 뭐야?",
          "11개", want_tools=("cap_immutable_properties",)),
    Probe("3-1", "지금 내 계정에 떠 있는 VM 목록 보여줘",
          "**거절해야 정답** — 현재 상태 축은 없다"),
    Probe("3-2", "EBS 볼륨을 100TB로 만들 수 있어?",
          "판정 보류. 단정하면 실패", want_tools=("cap_check_value",)),
    Probe("3-3", "t3.medium 시간당 얼마야?",
          "cost 도구로. web_search가 보이면 실패",
          forbid_tools=("web_search",)),
    Probe("3-6", "AWS VPC가 Azure에선 뭐야?",
          "단순 조회 — record_plan이 보이면 실패",
          forbid_tools=("record_plan",), want_tools=("kb_equivalent_types",)),
    Probe("3-9", "gp2 볼륨을 30,000 GiB로 만들 수 있어?",
          "**불가**. 최대 16,384 GiB를 근거로", want_tools=("cap_check_value",),
          want_any=("16384",)),
    Probe("3-10", "EBS 볼륨 30,000 GiB 되나?",
          "볼륨 **종류를 되물어야** 한다. 아무 종류나 골라 답하면 실패",
          want_tools=("cap_check_value",)),
    # ComputeSubnetwork를 쓰면 안 된다 — D6이 그 타입의 불변 레코드를 전부 최신
    # 프로바이더 값으로 갈아끼워서 낡음 고지가 **아예 안 붙는다**. 처음에 그걸로
    # 검사했다가 "모델이 경고를 뺐다"고 오해할 뻔했다. tf2crd가 남아 있는 타입을 쓴다.
    Probe("3-11", "GCP ContainerCluster에서 나중에 못 바꾸는 속성은?",
          "낡았을 수 있다는 고지가 답변에 살아남는가",
          want_tools=("cap_immutable_properties",),
          want_any=("낡", "2023", "스냅샷", "오래")),
    # ComputeSubnetwork로 물으면 안 된다 — 그 타입에는 projectRef가 아예 없어서
    # 담김 관계가 데이터에 없다. 모델이 networkRef를 보고 "VPC 안에 있다"고 답하는데
    # 그건 우리 데이터를 쓴 게 아니다. 담김이 실제로 있는 타입으로 묻는다.
    Probe("D5", "GCP ComputeDisk는 무엇 안에 담겨 있어?",
          "Project에 담긴다 (이번에 새로 생긴 축)",
          want_any=("Project", "프로젝트")),
    Probe("D4", "Azure 키 볼트 관련 쿼터 알려줘",
          "쿼터가 나온다 (494건으로 늘린 뒤)", want_tools=("cap_service_quota",)),
]


@dataclass
class Result:
    id: str
    query: str
    expect: str
    tools: list[str] = field(default_factory=list)
    answer: str = ""
    error: str = ""
    seconds: float = 0.0

    @property
    def verdict(self) -> str:
        return "오류" if self.error else "확인 필요"


def _normalized(text: str) -> str:
    """숫자 안의 천단위 구분자를 지운다.

    모델이 `16,384`를 `16 384`로 쓰는데 그 공백이 U+202F(좁은 줄바꿈 없는 공백)라
    문자열 비교가 그냥 실패했다. 하마터면 **맞는 답을 틀렸다고 기록할 뻔했다.**
    """
    import re as _re
    return _re.sub(r"(?<=\d)[,\s  ](?=\d)", "", text)


def _tool_names(result) -> list[str]:
    """실행 결과에서 호출된 도구 이름을 순서대로 뽑는다."""
    names: list[str] = []
    for item in result.new_items:
        name = getattr(getattr(item, "raw_item", None), "name", None)
        if name and getattr(item, "type", "") == "tool_call_item":
            names.append(name)
    return names


async def run_probes(probes: list[Probe], *, max_turns: int) -> list[Result]:
    agent = build_agent()
    out: list[Result] = []
    for probe in probes:
        record = Result(id=probe.id, query=probe.query, expect=probe.expect)
        started = time.monotonic()
        try:
            # 요청마다 새 상태 — 앞 질의의 계획이 이번 게이트를 열면 안 된다.
            result = await Runner.run(
                agent, [{"role": "user", "content": probe.query}],
                max_turns=max_turns, context=SessionState(),
            )
            record.tools = _tool_names(result)
            record.answer = str(result.final_output or "")
        except MaxTurnsExceeded:
            record.error = f"턴 한도({max_turns}) 초과"
        except Exception as exc:  # noqa: BLE001 — 한 질의 실패가 전체를 막지 않게
            record.error = f"{type(exc).__name__}: {exc}"
        record.seconds = time.monotonic() - started
        out.append(record)

        flags = []
        called = set(record.tools)
        for bad in probe.forbid_tools:
            if bad in called:
                flags.append(f"금지 도구 호출: {bad}")
        for want in probe.want_tools:
            if want not in called:
                flags.append(f"기대 도구 미호출: {want}")
        if not record.tools and not record.error:
            flags.append("도구를 아예 안 불렀다")
        if probe.want_any and record.answer:
            if not any(w in _normalized(record.answer) for w in probe.want_any):
                flags.append(f"답변에 없음(후보 {', '.join(probe.want_any)})")

        print(f"\n{'=' * 74}\n[{probe.id}] {probe.query}")
        print(f"기대: {probe.expect}")
        print(f"도구: {' → '.join(record.tools) or '(없음)'}   ({record.seconds:.1f}s)")
        for flag in flags:
            print(f"  ⚠ {flag}")
        if record.error:
            print(f"  ✗ {record.error}")
        else:
            cut = record.answer[:700]
            more = (
                f"\n… (전체 {len(record.answer)}자 — 판정은 want_any가 전체를 본다)"
                if len(record.answer) > 700
                else ""
            )
            print(f"답변: {cut}{more}")
    return out


def main() -> int:
    use_utf8()
    parser = argparse.ArgumentParser(description="에이전트 실측")
    parser.add_argument("--only", help="항목 id (쉼표 구분)")
    parser.add_argument("--max-turns", type=int, default=25)
    parser.add_argument("--out", help="결과 JSON 경로")
    args = parser.parse_args()

    probes = PROBES
    if args.only:
        wanted = {x.strip() for x in args.only.split(",")}
        probes = [p for p in PROBES if p.id in wanted]
        if not probes:
            print(f"해당 항목이 없습니다: {sorted(wanted)}")
            return 1

    results = asyncio.run(run_probes(probes, max_turns=args.max_turns))
    if args.out:
        Path(args.out).write_text(
            json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(f"\n결과 저장: {args.out}")
    failed = sum(1 for r in results if r.error)
    print(f"\n{len(results)}건 실행, 오류 {failed}건. **판정은 사람이 한다** — "
          "도구 호출 줄과 답변을 대조하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
