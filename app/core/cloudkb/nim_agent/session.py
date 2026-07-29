"""요청 단위 실행 상태 — 도구 게이팅의 근거가 된다.

**왜 코드로 강제하나**: 다단계 작업에서 "계획을 먼저 세우라"는 지시는 프롬프트만으로는
지켜지지 않는다. 실측으로 확인한 것:
- gpt-oss-120b / gpt-oss-20b / nemotron-3-super 모두 같은 프롬프트 3회 중 순서가 제각각.
- `ModelSettings(tool_choice="record_plan")`도 NIM에서 무시된다(3회 중 2회가 다른 도구부터).

그래서 순서 보장이 필요한 곳은 모델을 믿지 않고 **도구가 스스로 거부**하게 한다.
계획이 없으면 클라우드 산정 도구가 안내 메시지를 돌려주고, 모델은 그걸 읽고
record_plan을 부른 뒤 다시 시도한다 — 모델 능력과 무관하게 결정적이다.

상태는 **요청 하나마다 새로 만든다**(main.py). 대화 전체가 아니라 한 요청 안에서만
유효해야, 앞 요청의 계획이 뒤 요청의 게이트를 열어주는 일이 없다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SessionState:
    """한 사용자 요청 동안 에이전트가 남기는 실행 상태."""

    plan_steps: list[str] = field(default_factory=list)

    @property
    def has_plan(self) -> bool:
        return bool(self.plan_steps)
