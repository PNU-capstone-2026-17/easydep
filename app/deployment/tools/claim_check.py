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
#: **반드시 숫자로 끝나야 한다.** 예전 패턴은 `[\d,]{1,}`이라 쉼표로 끝날 수 있었고,
#: "vCPU 8, x86_64"에서 `8,`를 토큰으로 잡아 도구 출력의 `8`과 안 맞았다 —
#: 근거 있는 값이 근거 없다고 나오는 오탐이다(실측 N3).
_NUMBER = re.compile(_LEFT + r"\d[\d,]*\d(?:\.\d+)?" + _RIGHT)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_LOOKALIKE)
    # 자릿수 구분자를 지운다: `16,384` · `16 384` → `16384`
    return re.sub(r"(?<=\d)[, ](?=\d)", "", text)


#: 도구 이름 꼴 — `cost_describe_spec` 처럼 소문자와 밑줄. 답변에서 이걸 찾는다.
_TOOL_NAME = re.compile(_LEFT + r"[a-z]+(?:_[a-z]+){1,3}" + _RIGHT)


@dataclass
class Finding:
    kind: str          # "number" | "identifier" | "attribution" | "flip"
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


def misattributed(
    answer: str,
    called: list[str] | tuple[str, ...],
    known: frozenset[str],
    grounded: str = "",
) -> list[Finding]:
    """답변이 **부르지도 않은 우리 도구**를 출처로 댔는가.

    가장 위험한 형태를 결정론적으로 잡는다. 실측에서 모델이 GPU 사양 표를 지어내고
    *"cap_allowed_values 지식베이스에서 조회한 결과"*라고 적었다 — 그 도구는 그 턴에
    호출되지 않았다. **`basis`·소스 핀·교차 확인이 통째로 무의미해지는 실패**다.

    숫자 대조와 달리 이건 오탐이 거의 없다. 우리 도구 이름은 답변에 우연히 나올 수
    없고, 부른 도구를 언급하는 것은 정당하므로 거른다.

    `grounded`는 도구 출력 전체다. **도구가 스스로 다른 도구를 가리키는 경우**를
    거르는 데 쓴다 — `cost_describe_spec`이 "성능은 `perf_instance_profile`로 보세요"를
    붙이므로, 답변이 그걸 옮긴 것을 세탁으로 세면 우리가 만든 축 연결이 매번 걸린다.
    """
    used = {name.strip() for name in called}
    out, seen = [], set()
    text = normalize(answer)
    for match in _TOOL_NAME.finditer(text):
        token = match.group(0)
        if token in seen or token in used or token not in known:
            continue
        if token in grounded:
            continue  # 도구 출력이 그 이름을 먼저 말했다
        seen.add(token)
        out.append(Finding("attribution", token, _context_of(text, token)))
    return out


#: 우리 도구가 "여기서는 된다"를 적는 줄. **형식을 우리가 통제한다는 게 요점이다.**
#: `capacitykb.agent_api.check`의 조건부 판정이 이 꼴로 쓴다:
#:     조건 38가지 중 **840가지에서 가능**, 98가지에서 불가입니다.
#:       가능: ap-northeast-2; us-east-1; …
#:       불가: ap-east-1; …
_POSSIBLE_LINE = re.compile(r"^\s*가능:\s*(.+)$", re.M)
_DENIED_LINE = re.compile(r"^\s*불가:\s*(.+)$", re.M)

#: 답변이 **못 한다고 단언하는** 표현. 우리 도구가 쓰는 말이 아니라 모델이 쓰는 말이다.
_NEGATION = regex_negation = re.compile(
    r"지원(?:되지|하지)\s*않|미지원|불가능|불가합니다|사용할\s*수\s*없|쓸\s*수\s*없"
    r"|되지\s*않습니다|없습니다|제공되지\s*않"
)

#: 절 경계. 한국어는 한 문장에 긍정과 부정이 같이 온다 — "A는 되지만 B는 안 됩니다".
#: 절로 자르지 않으면 A가 부정 절에 있는 것처럼 보인다.
#:
#: **숫자 사이의 마침표에서는 자르지 않는다.** 그냥 `[.!?]`로 자르면 `16.4`가 `16`과
#: `4`로 쪼개져, 하필 이 검사가 노리는 버전 문자열이 통째로 사라진다(실제로 그랬다).
_CLAUSE = re.compile(
    r"(?<!\d)[.!?](?!\d)|[\n!?]|(?<=지만)|(?<=으나)|(?<=하나)|(?<=며)|(?<=고,)"
)


def _values_of(line: str) -> set[str]:
    """`가능: a; b; c 외 5가지` → {a, b, c}."""
    body = re.sub(r"외\s*\d+가지.*$", "", line)
    return {part.strip() for part in body.split(";") if part.strip()}


def flipped(answer: str, tool_outputs: list[str]) -> list[Finding]:
    """도구가 **가능이라 한 값**을 답변이 못 한다고 단언하는가.

    실측 사례: 도구가 *"938가지 중 840가지에서 가능"*이라 했는데 답변은 *"16.4에서는
    지원되지 않음으로 표시되었습니다"*로 뒤집었다. 숫자 대조로는 안 걸린다 — `16.4`는
    도구 출력에 **있기** 때문이다. 뒤집힌 것은 값이 아니라 **부호**다.

    ## 왜 이건 되고 일반적인 뒤집기는 안 되나

    문장의 뜻을 읽는 일이 아니라 **우리가 쓴 형식을 읽는 일**이기 때문이다. `가능:` /
    `불가:` 줄은 `capacitykb.agent_api.check`가 만든다. 임의의 주장이 뒤집혔는지는
    여전히 못 잡는다 — 이 함수가 보는 것은 그 두 줄에 든 값뿐이다.

    오탐을 줄이는 두 가지:

    - **절 단위로 본다.** "A는 되지만 B는 안 됩니다"에서 A를 부정으로 세면 안 된다.
    - **`불가:`에도 있는 값은 세지 않는다.** 조건이 갈리는 값이라 답변의 부정이
      근거 있는 말일 수 있다.
    """
    joined = "\n".join(tool_outputs)
    possible: set[str] = set()
    denied: set[str] = set()
    for line in _POSSIBLE_LINE.findall(joined):
        possible |= _values_of(line)
    for line in _DENIED_LINE.findall(joined):
        denied |= _values_of(line)
    candidates = {v for v in possible - denied if len(v) >= 2}
    if not candidates:
        return []

    out, seen = [], set()
    text = normalize(answer)
    for clause in _CLAUSE.split(text):
        if not clause or not _NEGATION.search(clause):
            continue
        for value in candidates:
            if value in seen or value not in clause:
                continue
            seen.add(value)
            out.append(Finding("flip", value, clause.strip()[:80]))
    return out


def check(
    answer: str,
    tool_outputs: list[str],
    question: str = "",
    *,
    called_tools: list[str] | tuple[str, ...] = (),
    known_tools: frozenset[str] = frozenset(),
) -> Verdict:
    """답변의 구체값 중 **어디에도 근거가 없는 것**을 찾는다.

    질문에 있던 값은 세지 않는다 — 사용자가 준 값을 되풀이하는 건 주장이 아니다.

    `known_tools`를 주면 **출처 세탁**도 함께 본다(`misattributed`).
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

    if known_tools:
        found = misattributed(answer, called_tools, known_tools, haystack)
        verdict.checked += len(found)
        verdict.unsupported.extend(found)

    turned = flipped(answer, tool_outputs)
    verdict.checked += len(turned)
    verdict.unsupported.extend(turned)
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
