"""의미 검증자 — 산출물만 보고 규칙 위반을 판정한다.

## 왜 생성기와 떼어 놓는가

검증은 **컨텍스트 이전이 거의 필요 없다.** 산출물 하나와 규칙 목록만 있으면 판정할 수
있고, 생성 프롬프트·사용자 피드백·재생성 이력은 몰라도 된다. 그래서 이 층을 떼어 내는
비용이 낮고, 떼어 내면 얻는 것이 둘이다.

  - **생성기의 자기 채점이 아니다.** 같은 프롬프트 계보에서 나온 판정은 자기가 방금 쓴
    문장을 옹호하는 쪽으로 기운다. 검증자에게는 결과물만 준다(black-box).
  - **규칙이 한 곳에서 온다.** 무엇을 볼지는 `knowledge/rules.py`가 정하고, 단계는 어느
    단계인지만 말한다. 예전에는 step3·step4가 거의 같은 25줄을 각자 들고 있었다.

## 두 가지 실패 모드를 여기서 막는다

  1. **근거 없는 지적**(검증자의 환각). 지식베이스에 없는 규칙을 인용한 판정은 버린다
     (`steps/grounding.py`). 전부 버려지면 "결함 없음"이 아니라 "판정을 얻지 못함"이다.
  2. **early victory**(대충 보고 통과). 규칙마다 판정을 요구하므로(`schemas.RuleVerdict`)
     빠진 규칙이 응답에서 드러난다. 빠진 것은 저하로 남긴다 — 그 실행의 `issues`는
     결함의 전부가 아니라 하한이다.

`enable_semantic_validator=False`면 부르지 않는다. 그때 상태는 `disabled`이고, 이는
"깨끗하다"가 아니다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage, SystemMessage

from app.requirements import prompts
from app.requirements.agent import grounding
from app.requirements.agent.llm import invoke_structured
from app.requirements.common import telemetry
from app.requirements.config import settings
from app.requirements.knowledge import rules
from app.requirements.schemas import Critique

#: 검증을 실제로 거쳤는가. **"결함 없음"과 "확인 못 함"을 같은 값으로 두지 않기 위해 있다.**
OK = "ok"                    # 판정을 받았다(결함이 있든 없든)
DISABLED = "disabled"        # 설정으로 껐다
FAILED = "failed"            # 호출이 실패했다
UNGROUNDED = "ungrounded"    # 불렀는데 근거 있는 판정이 하나도 없었다
PENDING = "pending"          # 아직 묻지 않았다(산출물 조립 시점의 초기값)

#: 리포트에서 "이 산출물은 의미 검증을 거치지 못했다"로 묶어야 하는 상태들.
#: 원인은 다르지만 결과는 같다 — 남아 있는 issues가 결함의 전부라고 말할 수 없다.
UNVALIDATED = (FAILED, UNGROUNDED)


@dataclass(frozen=True)
class Review:
    """한 산출물에 대한 판정 결과."""

    findings: list[str] = field(default_factory=list)
    status: str = OK
    #: 검증자가 판정하지 않고 넘어간 규칙 id들(early victory의 흔적).
    unexamined: tuple[str, ...] = ()


def review(
    stage: str,
    artifact: dict,
    *,
    prefix: str,
    source: str,
    subject: str | None = None,
) -> Review:
    """`stage`의 규칙으로 `artifact`를 판정한다.

    artifact는 **산출물뿐이어야 한다** — 생성 프롬프트나 사용자 피드백을 섞으면 검증자가
    "무엇을 지시받았는지"를 알게 되고, 그러면 지시를 따랐는지를 보게 된다. 우리가 물어야
    하는 것은 결과물이 규칙을 지켰는지다.

    prefix는 지적 문구의 머리표(`[semantic]`·`[rel]`), source는 저하 기록의 이름이다.
    """
    if not settings.enable_semantic_validator:
        return Review(status=DISABLED)

    expected = [r.id for r in rules.judged_by(stage, rules.JUDGED_VALIDATOR)]
    if not expected:
        # 이 단계에 의미 검증자가 볼 규칙이 없다. 호출은 낭비이고, 통과라고 말하면 거짓이다.
        return Review(status=DISABLED)

    system = prompts.validator_system_for(stage)
    try:
        critique: Critique = invoke_structured(
            Critique,
            [
                SystemMessage(content=system),
                HumanMessage(content=f"[ARTIFACT UNDER REVIEW]\n{json.dumps(artifact, ensure_ascii=False)}"),
            ],
        )
    except Exception as exc:  # noqa: BLE001 - 검증 실패는 치명적이지 않다
        telemetry.record_degradation(source, f"{type(exc).__name__}: {exc}", subject=subject)
        return Review(status=FAILED)

    violated = [v for v in critique.verdicts if v.violated]
    findings, dropped = grounding.grounded_findings(
        violated, prefix=prefix, source=source, subject=subject
    )

    # 판정을 받지 못한 규칙. 규칙 id를 안 세고 개수만 보면, 엉뚱한 규칙 하나를 대신 판정한
    # 응답이 "다 봤다"로 통과한다.
    examined = {v.rule_id for v in critique.verdicts}
    unexamined = tuple(rule_id for rule_id in expected if rule_id not in examined)
    if unexamined:
        telemetry.record_degradation(
            f"{source}.unexamined_rules",
            f"검증자가 판정하지 않은 규칙 {len(unexamined)}건: {list(unexamined)}",
            subject=subject,
        )

    # 아무 규칙도 판정하지 않았거나, 낸 지적이 전부 근거 없어 버려졌다 → 판정을 못 얻었다.
    if not examined or (dropped and not findings):
        return Review(status=UNGROUNDED, unexamined=unexamined)
    return Review(findings=findings, status=OK, unexamined=unexamined)
