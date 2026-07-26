"""터미널에서 요구사항 분석 에이전트를 실행하는 CLI.

사용 예:
  python -m app.requirements.cli "Users can log in with email and password." "Respond within 2s."
  python -m app.requirements.cli --file reqs.txt
  echo "I want to build a shop" | python -m app.requirements.cli        # stdin(파이프)
  python -m app.requirements.cli                                        # 대화형 입력

추상 요구사항이면 에이전트가 되물으며, 대화형(tty)일 때 답변을 입력받아 이어간다.
파이프/비대화형에서 구체화가 필요하면 질문만 출력하고 종료(코드 2)한다.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Callable

from app.requirements.agent import resume_analysis, start_analysis
from app.requirements.common import telemetry
from app.requirements.config import settings


def _reconfigure_utf8() -> None:
    """Windows 콘솔(cp949)에서 한글/기호 출력이 깨지지 않도록 UTF-8로 전환."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass


def format_results(items: list[dict]) -> str:
    """분류 결과(FR/NFR 목록)를 터미널용 텍스트로 렌더링한다."""
    if not items:
        return "(분류된 요구사항이 없습니다)"

    lines = [f"\n=== FR/NFR 분류 결과 ({len(items)}개) ==="]
    for it in items:
        lines.append(f"[{it['id']}] {it['type']:<3}")
        lines.append(f"    {it['text']}")
    return "\n".join(lines)


def analyze_interactive(
    requirements: list[str],
    thread_id: str,
    ask: Callable[[str], str],
    out: Callable[[str], None],
) -> dict:
    """분석을 시작하고, 구체화가 필요하면 ask()로 답변을 받아 완료까지 진행한다.

    ask/out을 주입받으므로 테스트에서 입출력을 대체할 수 있다.
    """
    payload = start_analysis(requirements, thread_id)
    while payload["status"] in ("need_clarification", "need_feedback"):
        if payload["status"] == "need_clarification":
            out("\n[더 구체화가 필요합니다 — 아래 질문에 답해주세요]")
            for i, q in enumerate(payload.get("questions") or [], start=1):
                out(f"  {i}. {q}")
            answer = ask("답변> ")
        else:  # need_feedback — 각 스텝 말미 피드백 게이트 (빈 입력이면 다음 단계로)
            out(f"\n[{payload.get('phase')} 결과에 대한 피드백 — 없으면 그냥 Enter]")
            out(f"  {payload.get('feedback_summary')}")
            try:
                answer = ask("피드백> ")
            except EOFError:
                answer = ""  # 비대화형/입력종료 → 피드백 없이 진행
        payload = resume_analysis(answer, thread_id)
    return payload


def _collect_requirements(args: argparse.Namespace, out: Callable[[str], None]) -> list[str]:
    """인자 / 파일 / stdin / 대화형 입력에서 요구사항 문장 리스트를 모은다."""
    if args.requirements:
        return args.requirements
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    if not sys.stdin.isatty():
        # 파이프로 들어온 입력
        return [line.strip() for line in sys.stdin if line.strip()]
    # 대화형: 빈 줄까지 요구사항을 입력받는다.
    out("요구사항을 한 줄에 하나씩 입력하세요. (빈 줄 입력 시 종료)")
    reqs: list[str] = []
    while True:
        try:
            line = input("요구사항> ").strip()
        except EOFError:
            break
        if not line:
            break
        reqs.append(line)
    return reqs


def main(argv: list[str] | None = None) -> int:
    _reconfigure_utf8()
    telemetry.configure_logging()
    parser = argparse.ArgumentParser(
        prog="python -m app.requirements.cli",
        description="클라우드 네이티브 요구사항 분석 에이전트 (FR/NFR 분류)",
    )
    parser.add_argument("requirements", nargs="*", help="요구사항 문장 (여러 개 가능)")
    parser.add_argument("--file", "-f", help="요구사항을 한 줄에 하나씩 담은 파일")
    parser.add_argument(
        "--no-bert", action="store_true", help="BERT 검증 비활성화(빠르게 실행)"
    )
    parser.add_argument(
        "--interactive", "-i", action="store_true",
        help="대화형 모드: step1 clarify + 각 스텝 말미 피드백 게이트 + 전체 파이프라인(step2~4)",
    )
    args = parser.parse_args(argv)

    if args.no_bert:
        settings.enable_bert_verify = False
    if args.interactive:
        # 대화형: 모든 interrupt 게이트를 켜고 그래프를 재컴파일한다.
        # (step2~4 파이프라인은 모드와 무관하게 항상 실행된다.)
        settings.enable_feedback_gates = True
        from app.requirements.agent.graph import rebuild_graph
        rebuild_graph()

    requirements = _collect_requirements(args, print)
    if not requirements:
        print("요구사항이 없습니다. 인자/파일/입력으로 하나 이상 제공하세요.", file=sys.stderr)
        return 1

    print(f"\n{len(requirements)}개 요구사항 분석 중... (모델: {settings.model})")
    thread_id = str(uuid.uuid4())

    def ask(prompt: str) -> str:
        if not sys.stdin.isatty():
            # 비대화형인데 구체화 질문이 필요한 상황 → 답변 불가.
            raise EOFError
        return input(prompt)

    try:
        payload = analyze_interactive(requirements, thread_id, ask, print)
    except EOFError:
        print(
            "\n[구체화 질문이 필요하지만 비대화형 입력이라 답변할 수 없습니다]\n"
            "대화형 터미널에서 실행하거나, 더 구체적인 요구사항을 입력하세요.",
            file=sys.stderr,
        )
        return 2

    print(format_results(payload.get("requirements") or []))
    # 파이프라인(대화형)까지 돌았으면 유스케이스·다이어그램도 보여준다.
    use_cases = payload.get("use_cases")
    if use_cases:
        print(f"\n=== 유스케이스 ({len(use_cases)}개) ===")
        for uc in use_cases:
            print(f"  [{uc['id']}] {uc['name']}  (주액터: {uc.get('primary_actor', '?')})")
    diagram = payload.get("diagram")
    if diagram:
        print("\n=== PlantUML 다이어그램 ===")
        print(diagram)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
