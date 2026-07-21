# -*- coding: utf-8 -*-
"""답변의 **구체적 주장**이 도구 출력에 근거하는지 기계적으로 대조한다.

**왜 프롬프트가 아니라 이것인가.** 실측에서 반복되는 최악의 실패는 모델이 자기
기억을 우리 KB 이름으로 내보내는 것이다:

- GPU 표의 vCPU·메모리를 지어내고 *"cap_allowed_values 지식베이스에서 조회한 결과"*
  라고 적었다(`g5g`를 AMD라고 했다 — NVIDIA다).
- 도구가 *"938가지 중 840가지에서 가능"*이라 했는데 답변은 *"16.4에서는 지원되지
  않음으로 표시되었습니다"*로 뒤집었다.

지시문으로 막는 방식은 이 프로젝트가 안 통한다고 이미 쟀다. 여기서는 **설득하지 않고
대조한다** — 답변에 나온 숫자·식별자가 그 턴의 도구 출력에 실재하는지 본다.

**이건 판정이 아니라 신호다.** 근거 없는 구체값이 있다는 것이지 답이 틀렸다는 뜻은
아니다. 모델이 자기 지식을 **출처를 밝히고** 덧붙이는 것은 정당하다(실측에서 실제로
잘한 사례가 있다). 그래서 오탐률을 함께 재고, 쓸지 말지는 그 숫자를 보고 정한다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

#: 유사 문자(대시·공백)를 ASCII로. 하네스와 같은 이유다 — 모델은 예쁜 문자를 쓴다.
_LOOKALIKE = {
    **dict.fromkeys(map(ord, "‐‑‒–—―−"), "-"),
    **dict.fromkeys(map(ord, "        "
                              "    　"), " "),
}

# **파이썬의 `\b`·`\w`는 한글을 단어 문자로 본다.** 그래서 한국어 답변에서
# `64개`는 경계가 안 잡혀 아예 안 걸리고, `db.t3.medium을`은 조사까지 토큰에
# 들어온다(둘 다 실측에서 났다). ASCII 기준 경계를 직접 만든다.
_LEFT = r"(?<![A-Za-z0-9._:/-])"
_RIGHT = r"(?![A-Za-z0-9._:/-])"

#: 클라우드 식별자 꼴. 이름 규칙이 뚜렷해서 자연어와 잘 안 섞인다.
_IDENTIFIER = re.compile(
    _LEFT
    + r"""(?x:
        (?:[a-z]+\.)?[a-z][0-9]?[a-z]*\d+[a-z]*\.[a-z0-9]+   # db.t3.medium · p5.48xlarge
      | [a-z]{2}(?:-[a-z]+)+-\d                     # us-east-1 · ap-northeast-2
      | AWS::[A-Za-z0-9]+::[A-Za-z0-9]+             # AWS::EC2::Instance
      | Microsoft\.[A-Za-z0-9]+(?:/[A-Za-z0-9]+)+   # Microsoft.DBforMySQL/flexibleServers
      | (?:gp|io|st|sc)\d                           # gp3 · io2 · st1
    )"""
    + _RIGHT
)

#: 두 자리 이상의 수만 본다. 1·2·3은 목록 번호로 흔해서 신호가 안 된다.
_NUMBER = re.compile(_LEFT + r"\d[\d,]{1,}(?:\.\d+)?" + _RIGHT)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_LOOKALIKE)
    # 자릿수 구분자를 지운다: `16,384` · `16 384` → `16384`
    return re.sub(r"(?<=\d)[, ](?=\d)", "", text)


@dataclass
class Finding:
    kind: str          # "number" | "identifier"
    token: str
    context: str


@dataclass
class Verdict:
    unsupported: list[Finding] = field(default_factory=list)
    checked: int = 0

    @property
    def clean(self) -> bool:
        return not self.unsupported


def _context_of(text: str, token: str, width: int = 34) -> str:
    index = text.find(token)
    if index < 0:
        return ""
    start = max(0, index - width)
    return text[start : index + len(token) + width].replace("\n", " ").strip()


def check(answer: str, tool_outputs: list[str], question: str = "") -> Verdict:
    """답변의 구체값 중 **어디에도 근거가 없는 것**을 찾는다.

    질문에 있던 값은 세지 않는다 — 사용자가 준 값을 되풀이하는 건 주장이 아니다.
    """
    haystack = normalize("\n".join(tool_outputs))
    asked = normalize(question)
    text = normalize(answer)

    verdict = Verdict()
    seen: set[str] = set()

    for pattern, kind in ((_NUMBER, "number"), (_IDENTIFIER, "identifier")):
        for match in pattern.finditer(text):
            token = match.group(0)
            if token in seen:
                continue
            seen.add(token)
            if token in asked:
                continue
            verdict.checked += 1
            if token in haystack:
                continue
            verdict.unsupported.append(Finding(kind, token, _context_of(text, token)))
    return verdict


def report(name: str, verdict: Verdict, *, limit: int = 6) -> str:
    if verdict.clean:
        return f"  {name}: 구체값 {verdict.checked}개 전부 도구 출력에 있음"
    lines = [
        f"  {name}: 구체값 {verdict.checked}개 중 "
        f"**{len(verdict.unsupported)}개가 근거 없음**"
    ]
    for finding in verdict.unsupported[:limit]:
        lines.append(f"      [{finding.kind}] {finding.token}  …{finding.context}…")
    if len(verdict.unsupported) > limit:
        lines.append(f"      외 {len(verdict.unsupported) - limit}개")
    return "\n".join(lines)
