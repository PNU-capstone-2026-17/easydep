"""LLM 에이전트 시연 — **무엇을 부르고 무엇을 받아 그렇게 답했는지**를 다 보여 준다.

    python -m app.deployment.tools.demo_agent --list          # 질문 목차
    python -m app.deployment.tools.demo_agent --pause         # 발표용(Enter로 한 문항씩)
    python -m app.deployment.tools.demo_agent --only 1,5,7    # 골라서
    python -m app.deployment.tools.demo_agent --full          # 도구 결과를 안 자름
    python -m app.deployment.tools.demo_agent --ask "..."     # 즉석 질문 하나
    python -m app.deployment.tools.demo_agent --save out.json # 기록 저장

`main.py --verbose`와 같은 스트림을 구독하지만 목적이 다르다. 저쪽은 **대화**하며
곁눈질하는 것이고, 이쪽은 **정해진 질문을 순서대로 태우고 근거를 따지는 것**이다.
그래서 셋을 더 얹었다.

1. **LLM이 실제로 넘긴 인자를 통째로** 보여 준다. verbose는 200자에서 자르는데,
   조건부 판정에서 정작 봐야 할 것이 `context={"VolumeType": "gp2"}`가 갔는지다 —
   그게 안 가면 3상태 답이 나오고, 화면만 보면 모델이 틀린 것처럼 읽힌다.
2. **주장 대조기**(`tools/claim_check.py`)를 답변마다 돌린다. 답에 나온 숫자·
   식별자가 **그 턴의 도구 출력에 실재하는지** 기계로 본다. 이 저장소가 반복해서
   겪은 최악의 실패가 "모델이 자기 기억을 우리 KB 이름으로 내보내는 것"이라,
   시연에서 그걸 **화면으로 확인할 수 있어야** 한다.
3. **되돌아볼 수 있게 남긴다**(`--save`). 발표 뒤 "그때 뭘 불렀더라"에 답할 수 있다.

## 대조기 결과를 읽는 법 — 판정이 아니라 신호다

`근거 없음`이 뜬다고 답이 틀린 것은 아니다. 모델이 자기 지식을 **출처를 밝히고**
덧붙이는 것은 정당하고, 실측에서 실제로 잘한 사례가 있다. 이 줄이 말하는 것은
**"이건 도구가 준 값이 아니다"** 하나뿐이다. 그래서 통과/실패에 넣지 않는다 —
헛짚음을 실패로 만들면 멀쩡한 답변이 빨갛게 뜨고 진짜 실패가 노이즈에 묻힌다.

## 회차마다 답이 갈릴 수 있다

같은 질문도 실행마다 도구 순서가 달라진다(실측: 같은 날 두 실행에서 3건이 뒤집힘).
**한 실행의 결과는 신호이지 판정이 아니다.** 발표 중 예상과 다르게 나오면 그것이
곧 이 시스템이 왜 회차를 나눠 통과율로 보는지의 실물이다.

실행에 `.env`의 `API_KEY`/`BASE_URL`/`MODEL`이 필요하다(저장소 최상위 `.env`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from ..kbcommon.console import use_utf8

_RESET = "\x1b[0m"
_C = {
    "q": "\x1b[1;35m",       # 질문
    "call": "\x1b[36m",      # 도구 호출
    "args": "\x1b[2;36m",    # 인자
    "out": "\x1b[2m",        # 도구 결과
    "answer": "\x1b[1m",     # 최종 답변
    "check": "\x1b[33m",     # 대조기
    "bad": "\x1b[31m",       # 근거 없음
}


@dataclass(frozen=True)
class Ask:
    """시연 질문 하나."""

    act: str
    """어느 막에 속하는가 — 화면에 배너로 찍힌다."""

    query: str
    why: str
    """**왜 이걸 묻는가** — 발표자가 청중에게 짚어 줄 한 줄."""

    core: bool = False
    """시간이 없을 때 남길 것."""

    follows: bool = False
    """**앞 문항의 대화를 이어받는가.**

    2막은 "방금 그 계획의 이 줄은 어디서 왔나"를 묻는 자리라, 앞 답변이 문맥에
    없으면 질문이 성립하지 않는다. 실제로 이걸 안 하고 돌렸더니 에이전트가
    *"which cloud provider's VPC are you referring to?"*라고 **정확히 되물었다** —
    모델이 틀린 게 아니라 **가리킬 것이 없는 질문을 우리가 던진 것**이었다.

    3·4막은 독립 질문이라 여기서 문맥을 끊는다. 문맥을 끝까지 끌고 가면 토큰이
    누적되고, "없는 축을 거절하는가" 같은 검사가 앞 대화에 오염된다.
    """


#: 설계 산출물 원문. 프로브와 **같은 것을 쓴다** — 사본을 두면 둘이 갈린다.
#: 파일 경로가 아니라 본문을 붙여 넣는 이유는 프로브 쪽에 적혀 있다(사용자가
#: 실제로 붙여 넣는 상황의 재현).
def _design_query() -> str:
    from .agent_probe import _DESIGN_QUERY

    body = _DESIGN_QUERY.split("\n", 1)[1]
    return "Build a deployment plan from this design artifact.\n" + body


#: 시스템 타겟이 영어이고 도구 출력도 영어라, 질문도 영어로 둔다.
#: (한국어로 물어도 동작하지만 **답이 영어로 온다** — 실측 30칸 중 28칸.)
#:
#: ## 순서가 곧 논증이다
#:
#: 축을 하나씩 자랑하는 순서로 두면 청중의 머릿속에 **"그래서 왜 필요한데?"**가
#: 남는다. 그래서 **산출물을 먼저 내놓고, 그 산출물의 근거를 캐는 순서**로 둔다.
#: 같은 질문이라도 "이 지식베이스는 가격도 압니다"와 "방금 그 다이어그램의
#: `$0.0468/h`은 어디서 나왔습니까"는 **질문의 주인이 다르다.**
#:
#:     1막  설계도 → 배포 계획·다이어그램·요구 부합 판정   (이게 산출물이다)
#:     2막  그 계획의 줄 하나하나는 어디서 왔나            (축들이 답한다)
#:     3막  그 값이 실제로 가능한가                        (제약 판정)
#:     4막  못 하는 것                                     (경계)
ASKS: tuple[Ask, ...] = (
    # ── 1막 ──────────────────────────────────────────────────────────
    Ask("1막 · 산출물", _design_query(),
        "**여기서 시작한다.** 설계 에이전트의 산출물(JSON)을 넣으면 배포 계획과 "
        "다이어그램, 그리고 요구사항 부합 판정이 나온다 — 과제 목표 1·3이 만나는 "
        "지점이다. 다이어그램의 `<<inferred>>` 표시와 legend를 짚을 것: "
        "**모든 상자와 선에 근거가 붙어 있고, 검증된 사실이 아닌 것은 그렇게 "
        "표시된다.** 그리고 예산 판정이 '초과 확정'인지 '판정 불가'인지 보라.",
        core=True),

    # ── 2막 ── 방금 나온 계획의 근거를 캔다 (앞 대화를 이어받는다) ──
    Ask("2막 · 계획의 근거",
        "You put an AWS RDS DBInstance in that plan. Why that type?",
        "계획이 `app::relationalDatabase → AWS::RDS::DBInstance`로 간 근거를 "
        "묻는다. **svcmap 층**이 답한다 — 그리고 그 대응이 '짐작(검수됨)'이라는 "
        "표시가 답변까지 살아남는지 본다.", core=True, follows=True),
    Ask("2막 · 계획의 근거",
        "The plan priced the compute at $0.0468/h. Where does that number come from?",
        "계획에 붙은 값이 **어디서 나왔나.** costkb가 미러의 정가를 그대로 "
        "답한다 — 웹 검색으로 새지 않는 것도 함께 본다.", core=True, follows=True),
    Ask("2막 · 계획의 근거",
        "You warned that t3a.medium is burstable. What does that mean for my service?",
        "계획의 ⚠ 한 줄이 무슨 뜻인지. **perfkb**가 답하고, 이 경고가 "
        "'상시 부하면 이 스펙은 위험하다'는 판정으로 이어진다.", follows=True),
    Ask("2막 · 계획의 근거",
        "The diagram has a VPC, subnet, security group and SSH key that the design "
        "never mentioned. Why are they there?",
        "**bundlekb**가 답한다 — '만들 수 있다'와 '실제로 딸려온다'의 차이. "
        "그리고 '이건 우리 도구가 만드는 것이지 클라우드가 요구하는 것이 "
        "아니다'라는 고지가 붙는지 본다.", core=True, follows=True),
    Ask("2막 · 계획의 근거",
        "You said a /24 subnet has 251 usable addresses. How was that derived?",
        "**sizingkb**가 답한다 — 예약 IP 수가 회사마다 다르고(AWS 5·Azure 5·"
        "GCP 4) 원본 문서에 적혀 있다.", follows=True),
    Ask("2막 · 계획의 근거",
        "If I delete the AWS VPC from that plan, what else is affected?",
        "계획을 지울 때의 파장. **graphkb**가 답하고, 466종을 다 찍지 않고 "
        "**요약하되 근거 꼬리말을 살리는지** 본다.", follows=True),

    # ── 3막 ── 그 값이 실제로 가능한가 ──────────────────────────────
    Ask("3막 · 값이 가능한가", "Can I create a gp2 volume of 30,000 GiB?",
        "계획에 값을 채워 넣을 때 **그 값이 실제로 허용되는지**를 지식베이스가 "
        "판정하는가. 표를 받아 모델이 비교하면 그 비교는 지식베이스가 "
        "보증하지 않는다.", core=True),
    Ask("3막 · 값이 가능한가", "What is the maximum size of an AWS EBS volume?",
        "**조건을 안 준 질문.** 하나의 숫자로 답하면 거짓이다 — 종류마다 "
        "다르다는 것이 답에 나오는지 본다."),

    # ── 4막 ── 경계 ─────────────────────────────────────────────────
    Ask("4막 · 못 하는 것", "List the VMs currently running in my account.",
        "**없는 축을 거절하는가.** 지식베이스로 메우면 없는 배포 상태를 "
        "지어내는 것이다.", core=True),
    Ask("4막 · 못 하는 것",
        "Our data must stay in Korea. Is that satisfied if I deploy to ap-northeast-2?",
        "**법적 판단을 거절해야 한다.** 리전 사실과 대조는 하되 규제 준수는 "
        "판정하지 않는 것이 계약이다(11장). **2026-07-28 실측에서 여기가 "
        "뚫렸다** — 'Yes … so data stored there stays within Korea'라고 답했다. "
        "고치기 전이라면 이 문항은 **결함을 보여주는 자리**다.", core=True),
    Ask("4막 · 못 하는 것", "Are there known patterns for designing retry logic?",
        "수치로 환원되지 않는 지식은 **자문 축**으로 가고 '지침이지 사실 아님' "
        "딱지가 붙는다 — 사실 축과 섞이지 않는다."),
)


@dataclass
class Turn:
    """한 질문의 실행 기록."""

    query: str
    why: str
    act: str = ""
    followed: bool = False
    """앞 대화를 이어받아 물었는가 — 기록을 나중에 읽을 때 필요하다."""
    calls: list[dict] = field(default_factory=list)
    answer: str = ""
    seconds: float = 0.0
    usage: dict = field(default_factory=dict)
    error: str = ""


def _paint(text: str, kind: str, color: bool) -> str:
    return f"{_C[kind]}{text}{_RESET}" if color else text


def _clip(text: str, limit: int | None) -> str:
    flat = str(text)
    if limit is None or len(flat) <= limit:
        return flat
    return f"{flat[:limit]}\n      …(+{len(flat) - limit}자 — --full 로 전체)"


async def _run_one(agent: Any, ask: Ask, *, max_turns: int,
                   history: list | None = None) -> tuple[Turn, list]:
    """질문 하나를 태우고 도구 호출·결과·답변을 모은다.

    `history`를 주면 그 대화를 이어받는다(2막의 "방금 그 계획" 질문). 반환하는
    두 번째 값이 다음 질문에 넘길 히스토리다.
    """
    from agents import Runner

    from ..nim_agent.session import SessionState

    turn = Turn(query=ask.query, why=ask.why, act=ask.act)
    turn.followed = bool(history)
    started = time.time()
    try:
        result = Runner.run_streamed(
            agent, (history or []) + [{"role": "user", "content": ask.query}],
            max_turns=max_turns,
            # 요청마다 새 상태 — 앞 요청의 계획이 이번 게이트를 열면 안 된다
            # (main.py와 같은 규율). 대화 문맥과 도구 게이팅은 다른 것이다.
            context=SessionState(),
        )
        pending: dict | None = None
        async for event in result.stream_events():
            if getattr(event, "type", None) != "run_item_stream_event":
                continue
            item = event.item
            kind = getattr(item, "type", None)
            if kind == "tool_call_item":
                raw = item.raw_item
                pending = {
                    "tool": getattr(raw, "name", None) or type(raw).__name__,
                    # **자르지 않는다** — 인자가 이 시연의 핵심이다.
                    "arguments": getattr(raw, "arguments", "") or "",
                    "output": "",
                }
                turn.calls.append(pending)
            elif kind == "tool_call_output_item":
                if pending is not None:
                    pending["output"] = str(item.output)
                    pending = None
                else:  # 짝을 못 찾은 결과도 버리지 않는다
                    turn.calls.append({"tool": "(호출 미포착)", "arguments": "",
                                       "output": str(item.output)})
        turn.answer = result.final_output or ""
        usage = result.context_wrapper.usage
        turn.usage = {"input": usage.input_tokens, "output": usage.output_tokens,
                      "total": usage.total_tokens, "requests": usage.requests}
        next_history = result.to_input_list()
    except Exception as exc:  # 시연 중 예외는 숨기지 않는다
        turn.error = f"{type(exc).__name__}: {exc}"
        next_history = list(history or [])
    turn.seconds = round(time.time() - started, 1)
    return turn, next_history


def _print_turn(index: int, turn: Turn, *, color: bool, limit: int | None,
                known_tools: frozenset[str], show_act: bool = False) -> None:
    from . import claim_check

    bar = "=" * 78
    if show_act and turn.act:
        print(f"\n\n{'━' * 78}")
        print(_paint(f"  {turn.act}", "q", color))
        print("━" * 78)
    # 설계 산출물처럼 긴 질의는 앞부분만 — 전문은 계획 답변이 되풀이한다.
    shown = turn.query if len(turn.query) <= 300 else (
        turn.query[:300] + f" …(+{len(turn.query) - 300}자의 설계 JSON)")
    print(f"\n{bar}")
    print(_paint(f" [{index}] Q: {shown}", "q", color))
    print(f"      왜 이걸 묻나: {turn.why}")
    print("-" * 78)

    if turn.error:
        print(_paint(f" !! 실행 실패: {turn.error}", "bad", color))
        return

    if not turn.calls:
        print("  (도구를 하나도 부르지 않았습니다 — 거절·되묻기라면 의도된 것입니다)")
    for i, call in enumerate(turn.calls, 1):
        print(_paint(f"  ① 도구 호출 {i}  {call['tool']}", "call", color))
        print(_paint(f"     ↳ LLM이 넘긴 인자: {call['arguments']}", "args", color))
        out = _clip(call["output"], limit)
        head, *rest = out.split("\n")
        print(_paint(f"  ② 도구 결과     {head}", "out", color))
        for line in rest:
            print(_paint(f"                  {line}", "out", color))

    print(_paint("\n  ③ 최종 답변", "answer", color))
    for line in turn.answer.splitlines() or [""]:
        print(f"     {line}")

    verdict = claim_check.check(
        turn.answer, [c["output"] for c in turn.calls], turn.query,
        called_tools=[c["tool"] for c in turn.calls], known_tools=known_tools,
    )
    line = claim_check.report("대조기", verdict).strip()
    print(_paint(f"\n  ④ {line}", "check" if verdict.clean else "bad", color))
    if not verdict.clean:
        print("     ↳ **신호이지 판정이 아닙니다** — 출처를 밝히고 덧붙인 지식일 수 있습니다.")
    u = turn.usage
    print(_paint(
        f"  ⑤ 비용·시간    입력 {u.get('input', 0):,} + 출력 {u.get('output', 0):,}"
        f" = {u.get('total', 0):,} 토큰 · LLM {u.get('requests', 0)}회 · {turn.seconds}s",
        "check", color))


async def _amain(args: argparse.Namespace) -> int:
    from ..nim_agent.agent import build_agent
    from ..nim_agent.tools import LOCAL_TOOLS

    # **번호는 목차(`--list`) 기준으로 고정한다.** 걸러낸 뒤 다시 매기면
    # `--only 7`을 줬는데 화면에 `[1]`이 찍혀, 어느 질문을 본 것인지 알 수 없다.
    if args.ask:
        numbered = [(1, Ask(args.ask, "즉석 질문"))]
    else:
        numbered = list(enumerate(ASKS, 1))
        if args.core:
            numbered = [(i, a) for i, a in numbered if a.core]
        if args.only:
            picked = {int(x) for x in args.only.replace(" ", "").split(",") if x}
            bad = picked - {i for i, _ in numbered}
            if bad:
                print(f"고를 수 있는 번호가 아닙니다: {sorted(bad)} "
                      f"(--list 로 목차를 보세요)", file=sys.stderr)
                return 2
            numbered = [(i, a) for i, a in numbered if i in picked]

    try:
        agent = build_agent()
    except RuntimeError as exc:  # .env 누락 등
        print(f"에이전트를 만들지 못했습니다: {exc}", file=sys.stderr)
        print("최상위 .env에 API_KEY / BASE_URL / MODEL 이 있어야 합니다.", file=sys.stderr)
        return 1

    known = frozenset(t.name for t in LOCAL_TOOLS)
    color = sys.stdout.isatty()
    limit = None if args.full else 700
    turns: list[Turn] = []
    last_act = ""
    history: list = []
    for position, (number, ask) in enumerate(numbered, 1):
        if not ask.follows:
            history = []          # 독립 질문 — 앞 대화를 끊는다
        elif not history:
            # `--only 3`처럼 앞 문항 없이 골라 돌린 경우. 조용히 넘어가면
            # "왜 되묻지?"로 읽히므로 화면에 밝힌다.
            print(f"\n  ※ [{number}]는 앞 문항의 계획을 이어받는 질문입니다 — "
                  "혼자 돌리면 가리킬 것이 없어 에이전트가 되묻습니다.")
        turn, history = await _run_one(agent, ask, max_turns=args.max_turns,
                                       history=history if ask.follows else None)
        turns.append(turn)
        _print_turn(number, turn, color=color, limit=limit, known_tools=known,
                    show_act=(ask.act != last_act))
        last_act = ask.act
        if args.pause and position < len(numbered):
            try:
                input("\n            ── Enter로 다음 ──")
            except EOFError:
                break

    if args.save:
        from pathlib import Path
        Path(args.save).write_text(
            json.dumps([t.__dict__ for t in turns], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n기록을 저장했습니다: {args.save}")
    failed = [t for t in turns if t.error]
    if failed:
        print(f"\n실행 실패 {len(failed)}건 — 위 !! 줄을 보십시오.", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    use_utf8()
    p = argparse.ArgumentParser(
        prog="demo_agent",
        description="LLM 에이전트 시연 — 도구 호출·인자·결과·근거 대조를 다 보여 준다")
    p.add_argument("--list", action="store_true", help="질문 목차만")
    p.add_argument("--only", help="질문 번호 (예: 1,5,7)")
    p.add_argument("--core", action="store_true", help="핵심 질문만")
    p.add_argument("--ask", help="즉석 질문 하나만")
    p.add_argument("--pause", action="store_true", help="문항마다 Enter로 넘김")
    p.add_argument("--full", action="store_true", help="도구 결과를 자르지 않음")
    p.add_argument("--save", help="기록을 JSON으로 저장")
    p.add_argument("--max-turns", type=int, default=20)
    args = p.parse_args(argv)

    if args.list:
        last = ""
        for i, a in enumerate(ASKS, 1):
            if a.act != last:
                print(f"\n── {a.act} " + "─" * max(0, 60 - len(a.act)))
                last = a.act
            shown = a.query if len(a.query) <= 90 else a.query[:90] + " …(설계 JSON)"
            print(f"{i:2d}.{' ★' if a.core else '  '} {shown}")
            print(f"      {a.why}")
        print(f"\n★ = 시간이 없을 때 남길 것 ({sum(1 for a in ASKS if a.core)}문항, --core)")
        return 0
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
