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
import pathlib
import sys
import uuid
from collections.abc import Callable

from app.requirements.agent import resume_analysis, start_analysis
from app.requirements.common import telemetry
from app.requirements.common.console import use_utf8_stdout
from app.requirements.config import settings


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
    constraints_text: str = "",
) -> dict:
    """분석을 시작하고, 구체화가 필요하면 ask()로 답변을 받아 완료까지 진행한다.

    ask/out을 주입받으므로 테스트에서 입출력을 대체할 수 있다.

    `constraints_text`는 사용자가 **요구사항과 따로** 쓴 클라우드 제약 원문이다.
    그래프는 처음부터 이 인자를 받고 있었는데(`start_analysis`) **CLI만 안 넘기고
    있었다**(2026-07-29에 발견). 그래서 터미널로 돌리면 `RESOURCE_SPEC`이 영영
    안 나왔고, 뒤 단계(배포 계획)는 provider·region 없이 조인이 전부 닫힌 채로
    돌아갔다 — 배선 하나가 빠져 축이 통째로 안 열린 자리다.
    """
    payload = start_analysis(requirements, thread_id, constraints_text=constraints_text)
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


def _collect_constraints(args: argparse.Namespace) -> str:
    """클라우드 제약 원문을 인자/파일에서 모은다. 없으면 빈 문자열.

    요구사항과 **따로** 받는 이유는 `analyze_interactive`의 docstring에 있다.
    """
    if args.constraints:
        return args.constraints.strip()
    if args.constraints_file:
        return pathlib.Path(args.constraints_file).read_text(encoding="utf-8").strip()
    return ""


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
    use_utf8_stdout()
    telemetry.configure_logging()
    parser = argparse.ArgumentParser(
        prog="python -m app.requirements.cli",
        description="클라우드 네이티브 요구사항 분석 에이전트 (FR/NFR 분류)",
    )
    parser.add_argument("requirements", nargs="*", help="요구사항 문장 (여러 개 가능)")
    parser.add_argument("--file", "-f", help="요구사항을 한 줄에 하나씩 담은 파일")
    # **요구사항과 따로 받는다.** 실측상 provider·region·예산은 요구사항 산문에
    # 0건이고(`steps/step_resource.py`), 섞으면 분류기가 그것들을 FR/NFR로 판정해야
    # 한다. 그래프는 처음부터 이 값을 받고 있었는데 CLI만 안 넘기고 있었다.
    parser.add_argument(
        "--constraints", "-c",
        help="클라우드 제약 원문 (예: 'Deploy on AWS in the Seoul region. …')",
    )
    parser.add_argument(
        "--constraints-file", help="클라우드 제약 원문을 담은 파일",
    )
    parser.add_argument(
        "--no-bert",
        action="store_true",
        help="Reject raw analysis and point to the preclassified batch runner.",
    )
    parser.add_argument(
        "--interactive", "-i", action="store_true",
        help="대화형 모드: step1 clarify + 각 스텝 말미 피드백 게이트 + 전체 파이프라인(step2~4)",
    )
    args = parser.parse_args(argv)

    if args.no_bert:
        parser.error(
            "--no-bert cannot classify raw requirements. Provide an input JSON with "
            "classified [{id, text, type}] items to "
            "`python -m app.requirements.run_pipeline --input <file>` instead."
        )
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

    constraints = _collect_constraints(args)
    if constraints:
        shown = constraints[:100] + ("…" if len(constraints) > 100 else "")
        print(f"클라우드 제약(요구사항과 별도): {shown}")
    else:
        # **없으면 없다고 말한다.** 조용히 넘어가면 `RESOURCE_SPEC`이 안 나온 이유를
        # 나중에 못 찾는다 — 배선이 빠져 있던 동안 정확히 그랬다.
        print("클라우드 제약 없음 — RESOURCE_SPEC은 나오지 않습니다 "
              "(--constraints / --constraints-file)")

    print(f"\n{len(requirements)}개 요구사항 분석 중... (모델: {settings.model})")
    thread_id = str(uuid.uuid4())

    def ask(prompt: str) -> str:
        if not sys.stdin.isatty():
            # 비대화형인데 구체화 질문이 필요한 상황 → 답변 불가.
            raise EOFError
        return input(prompt)

    try:
        payload = analyze_interactive(
            requirements, thread_id, ask, print, constraints_text=constraints,
        )
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
