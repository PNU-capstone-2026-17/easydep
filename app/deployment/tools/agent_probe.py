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
          "**없는 걸 지어내지 않는가.** 키 볼트 한도 문서는 원본에 아예 없다",
          want_tools=("cap_service_quota",),
          want_any=("없", "포함되어 있지", "포털", "지원")),
    Probe("D5", "GCP ComputeDisk는 무엇 안에 담겨 있어?",
          "이번에 새로 만든 담김 축이 답변에 닿는가",
          want_any=("Project", "프로젝트")),
    Probe("N1", "af-south-1 리전에서 p5.48xlarge 인스턴스를 쓸 수 있어?",
          "**값에서 출발한 질문이 KB 안에서 끝나는가.** 리전별 허용값 79,809쌍을 "
          "넣고도 에이전트가 못 닿아 웹으로 나갔다 (데이터: af-south-1은 불가)",
          forbid_tools=("web_search",),
          want_any=("af-south-1", "제공되지", "지원되지", "사용할 수 없", "쓸 수 없")),
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
# 3-3의 도구 **순서**
#   계획 게이트에 몇 번 부딪힌 뒤 계획을 세우는지가 실행마다 다르다. 게이트가 막는지는
#   `forbid_tools`가 아니라 게이트 자체의 단위 테스트가 지킨다.


def _normalized(text: str) -> str:
    """숫자 안의 천단위 구분자를 지운다.

    모델이 `16,384`를 `16 384`로 쓰는데 그 공백이 U+202F(좁은 줄바꿈 없는 공백)라
    문자열 비교가 그냥 실패했다. 하마터면 **맞는 답을 틀렸다고 기록할 뻔했다.**
    """
    return re.sub(r"(?<=\d)[,\s  ](?=\d)", "", text)


@dataclass
class Result:
    probe: Probe
    tools: list[str] = field(default_factory=list)
    answer: str = ""
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
            "tools": self.tools, "answer": self.answer, "error": self.error,
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


async def _run_once(agent, probe: Probe, max_turns: int) -> tuple[list[str], str, str]:
    try:
        # 요청마다 새 상태 — 앞 질의의 계획이 이번 게이트를 열면 안 된다.
        result = await Runner.run(
            agent, [{"role": "user", "content": probe.query}],
            max_turns=max_turns, context=SessionState(),
        )
        return _tool_names(result), str(result.final_output or ""), ""
    except MaxTurnsExceeded:
        return [], "", f"턴 한도({max_turns}) 초과"
    except Exception as exc:  # noqa: BLE001 — 한 질의 실패가 전체를 막지 않게
        return [], "", f"{type(exc).__name__}: {exc}"


async def run_probes(
    probes: tuple[Probe, ...], *, max_turns: int, retries: int
) -> list[Result]:
    agent = build_agent()
    out: list[Result] = []
    for probe in probes:
        started = time.monotonic()
        tools, answer, error = await _run_once(agent, probe, max_turns)
        failures = [] if error else probe.failures(tools, answer)
        flaky = False
        for _ in range(retries if (error or failures) else 0):
            tools2, answer2, error2 = await _run_once(agent, probe, max_turns)
            failures2 = [] if error2 else probe.failures(tools2, answer2)
            if not error2 and not failures2:
                # 재시도에서 통과 → 실패가 아니라 불안정으로 기록한다.
                tools, answer, error, failures, flaky = tools2, answer2, "", [], True
                break
        record = Result(
            probe=probe, tools=tools, answer=answer, error=error,
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
    print("\n답변이 **잘 쓰였는지**는 판정하지 않습니다 — 그건 사람이 읽으세요.")
    return 1 if (args.strict and failed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
