"""시연 — 지식베이스가 **어떻게 답하는지**를 실물로 보여 준다.

    python -m app.deployment.tools.demo            # 전부, 한 번에
    python -m app.deployment.tools.demo --pause    # 발표용: 한 단계씩 Enter로 넘김
    python -m app.deployment.tools.demo --step 4   # 하나만
    python -m app.deployment.tools.demo --list     # 목차

**모델을 부르지 않습니다.** 네트워크도 API 키도 필요 없고, 몇 번을 돌려도 **같은
글자**가 나옵니다. 이게 라이브 에이전트 시연과 나누어 둔 이유입니다 — 라이브는
실측에서 회차마다 결과가 갈렸고(같은 날 두 실행에서 3건이 뒤집힘), 발표장에서
그 확률에 기댈 이유가 없습니다.

라이브가 필요한 것은 딱 하나, **"모델이 실제로 이 축으로 라우팅하는가"**입니다.
그건 `tools/probe_en.py`가 재고, 기록은 문서에 있습니다.

## 이 시연이 주장하는 것

지식베이스의 값어치는 "데이터가 많다"가 아니라 **"모르는 것을 모른다고 말한다"**는
데 있습니다. 그래서 각 단계는 데이터가 아니라 **답하는 방식**을 보여 줍니다 —
조건을 붙이는가, 모름을 접지 않는가, 짐작을 짐작이라 하는가, 합계를 거절하는가.

각 단계는 **부르는 코드를 먼저 찍고** 그 결과를 그대로 붙입니다. 지어낸 것이
없다는 것을 화면에서 확인할 수 있어야 하기 때문입니다.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from dataclasses import dataclass, field
from typing import Callable

from ..kbcommon.console import use_utf8

BAR = "=" * 78


@dataclass(frozen=True)
class Step:
    """시연 한 단계."""

    title: str
    """발표자가 읽을 제목."""

    look_at: str
    """**이 답의 어디를 보라** — 청중에게 짚어 줄 한 줄."""

    calls: tuple[tuple[str, Callable[[], str]], ...] = field(default_factory=tuple)
    """(화면에 보일 코드, 실제로 부를 것). 코드 문자열과 호출이 어긋나면 시연이
    거짓말이 되므로, 둘을 나란히 두고 **손으로 맞춘다.**"""


def _steps() -> tuple[Step, ...]:
    from ..capacitykb import agent_api as cap
    from ..costkb import agent_api as cost
    from ..graphkb import agent_api as graph
    from ..patternkb import agent_api as pat
    from ..perfkb import agent_api as perf

    return (
        Step(
            "조건이 답을 가른다",
            "같은 값·같은 속성인데 **디스크 종류 하나로 답이 뒤집힙니다.** "
            "그리고 판정문이 '어느 조건에서의 한도인지'를 함께 말합니다 — "
            "그게 해결책(gp3로 바꾸면 된다)을 가리키기 때문입니다.",
            (
                ('cap.check("AWS::EC2::Volume", "Size", "30000", context={"VolumeType": "gp2"})',
                 lambda: cap.check("AWS::EC2::Volume", "Size", "30000",
                                   context={"VolumeType": "gp2"})),
                ('cap.check("AWS::EC2::Volume", "Size", "30000", context={"VolumeType": "gp3"})',
                 lambda: cap.check("AWS::EC2::Volume", "Size", "30000",
                                   context={"VolumeType": "gp3"})),
            ),
        ),
        Step(
            "모르면 한쪽으로 찍지 않는다 — 3상태",
            "조건을 안 주면 **'된다'도 '안 된다'도 아닌 셋째 상태**로 답합니다. "
            "찍었으면 gp2 디스크를 망가뜨리거나, gp3로 가는 길을 막았을 겁니다.",
            (
                ('cap.check("AWS::EC2::Volume", "Size", "30000")',
                 lambda: cap.check("AWS::EC2::Volume", "Size", "30000")),
            ),
        ),
        Step(
            "'없다'와 '못 찾겠다'를 다른 문장으로",
            "첫 줄은 **'수집 범위 안이라 없음이 답'**이고, 둘째 줄은 "
            "**'그런데 그 속성 자체가 없다'**입니다. 두 문장이 붙어 있어야 "
            "사용자가 오타를 의심합니다(RDS는 `DBInstanceClass`입니다).",
            (
                ('cap.property_limits("AWS::RDS::DBInstance", "InstanceType")',
                 lambda: cap.property_limits("AWS::RDS::DBInstance", "InstanceType")),
            ),
        ),
        Step(
            "막다른 길을 답으로 바꾼다",
            "값을 타입 이름인 줄 알고 물은 경우입니다. 예전엔 '없습니다' 한 줄이라 "
            "모델이 **웹검색 13회·838초**를 쓰고 포기했습니다 — 그때 우리는 그 값의 "
            "데이터를 쥐고 있었습니다. 지금은 **어디로 가면 되는지**까지 답합니다.",
            (
                ('cap.value_lookup("p5.48xlarge")',
                 lambda: cap.value_lookup("p5.48xlarge")),
            ),
        ),
        Step(
            "짐작한 자리를 짐작이라고 말한다",
            "같은 함수, 두 인스턴스. **뒤쪽에만 괄호가 붙습니다** — AWS가 직접 "
            "말한 것과, 필드가 말하지 않은 것에서 우리가 뒤집어 얻은 것의 차이입니다. "
            "이 구분을 안 했다면 t1 계열에 '성능 보장'이라는 **정반대 답**이 나갑니다.",
            (
                ('perf.instance_profile("aws", "t3.micro")',
                 lambda: perf.instance_profile("aws", "t3.micro")),
                ('perf.instance_profile("aws", "m5.large")',
                 lambda: perf.instance_profile("aws", "m5.large")),
            ),
        ),
        Step(
            "승자를 선언하지 않는다",
            "상시 대역폭은 t3가 높고 버스트 최대는 m5가 높습니다. "
            "**한 줄로 접으면 어느 쪽으로 접든 거짓**이라 접지 않습니다. "
            "마지막 줄은 프로바이더 간 비교를 **구조적으로** 거절합니다.",
            (
                ('perf.compare("aws", ["t3.large", "m5.large"])',
                 lambda: perf.compare("aws", ["t3.large", "m5.large"])),
            ),
        ),
        Step(
            "합계를 내지 않는다 · 모르는 것을 센다",
            "값이 없는 후보를 **숫자로 셉니다** — 목록이 짧아진 이유를 침묵으로 "
            "두지 않습니다. 그리고 월 비용이 **일부러 빠져 있습니다**: 같이 주면 "
            "모델이 계산 도구를 건너뛰고 암산했습니다(실측 5회 중 5회).",
            (
                ('cost.recommend_specs(4, 16, "aws", limit=3)',
                 lambda: cost.recommend_specs(4, 16, "aws", limit=3)),
            ),
        ),
        Step(
            "'가장 가까운 것'이지 '같은 것'이 아니다",
            "꼬리말 둘을 보십시오 — **짐작 몇 건인지**, 그리고 **대응이 있다는 것과 "
            "우리가 만들 수 있다는 것은 다른 말**이라는 고지입니다. 클라우드마다 "
            "리소스를 가르는 선이 달라서 정확한 대응이 없을 수 있습니다.",
            (
                ('graph.equivalent_types("AWS::SQS::Queue")',
                 lambda: graph.equivalent_types("AWS::SQS::Queue")),
            ),
        ),
        Step(
            "긴 목록을 줄이되, 근거와 버린 수를 남긴다",
            "466종을 다 찍으면 정작 읽어야 할 **근거 꼬리말이 467번째 줄**에 갑니다. "
            "요약하되 ① 총계와 그룹별 개수는 **완전**하고 ② 버린 것을 **세고** "
            "③ 비율은 **전수 기준**입니다.",
            (
                ('graph.deletion_impact("AWS::EC2::VPC")',
                 lambda: graph.deletion_impact("AWS::EC2::VPC")),
            ),
        ),
        Step(
            "지침은 사실이 아니다 — 자문 축은 딱지를 붙인다",
            "첫 줄에 **advisory(사실 아님)** 딱지가 붙습니다. 수치로 환원되지 않는 "
            "설계 지침을 사실 축에 섞으면 지침이 사실 행세를 하게 되므로, "
            "축을 아예 나눴습니다.",
            (
                ('pat.search_patterns("retry backoff transient failure")',
                 lambda: pat.search_patterns("retry backoff transient failure", top=2)),
            ),
        ),
        Step(
            "무엇까지 훑었는지 스스로 말한다",
            "각 축이 **자기 수집 범위**를 답할 수 있습니다. "
            "'지원 클라우드 목록에 이름이 있다'와 '그 질문에 답할 수 있다'는 "
            "다른 말이고, 그 차이를 데이터가 직접 말하게 한 것입니다.",
            (
                ("cost.coverage_text()", lambda: cost.coverage_text()),
                ("pat.coverage_text()", lambda: pat.coverage_text()),
            ),
        ),
    )


def _run_step(index: int, step: Step) -> None:
    print(f"\n{BAR}")
    print(f" {index}. {step.title}")
    print(BAR)
    for code, call in step.calls:
        print(f"\n>>> {code}")
        try:
            out = call()
        except Exception as exc:  # 시연 중 예외는 숨기지 않는다
            out = f"[호출 실패] {type(exc).__name__}: {exc}"
        print(out.rstrip() if isinstance(out, str) else repr(out))
    print("\n" + textwrap.fill(f"[여기를 보라] {step.look_at}", width=76,
                               subsequent_indent="             "))


def main(argv: list[str] | None = None) -> int:
    use_utf8()
    parser = argparse.ArgumentParser(
        prog="demo", description="지식베이스 시연 (모델 없음 · 매번 같은 출력)")
    parser.add_argument("--step", type=int, help="이 단계 하나만")
    parser.add_argument("--pause", action="store_true", help="단계마다 Enter로 넘김")
    parser.add_argument("--list", action="store_true", help="목차만")
    args = parser.parse_args(argv)

    steps = _steps()
    if args.list:
        for i, s in enumerate(steps, 1):
            print(f"{i:2d}. {s.title}")
        return 0

    if args.step is not None:
        if not 1 <= args.step <= len(steps):
            print(f"단계는 1~{len(steps)}입니다.", file=sys.stderr)
            return 2
        _run_step(args.step, steps[args.step - 1])
        return 0

    for i, step in enumerate(steps, 1):
        _run_step(i, step)
        if args.pause and i < len(steps):
            try:
                input("\n            ── Enter로 다음 ──")
            except EOFError:
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
