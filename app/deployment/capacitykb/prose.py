"""description 자유 텍스트에서 수치 제약을 추출한다 (스키마 무지 순수 함수).

**왜 필요한가**: CloudFormation 스키마 1628개를 실측하면 정작 중요한 용량 숫자가
`minimum`/`maximum` 필드가 아니라 `description` 산문에만 있다. 예를 들어
`AWS::EC2::Volume.Size`는 스키마상 아무 제약이 없고 설명문에만
"gp3: ``1 - 65,536`` GiB"가 적혀 있다.

**왜 위험한가**: 산문 추출은 틀리면 지식베이스를 오염시킨다. 실제로
`AWS::RDS::DBInstance.Iops` 설명의 "Must be a multiple between 1 and 50 of the
storage amount"는 범위가 아니라 **비율**이라, 순진한 규칙은 max=50을 만들어
유효한 설정(Iops=3000)을 거부하게 만든다 — 침묵보다 나쁘다.

**방어 원칙 — fail-open만 허용한다**:
- 범위는 넓게 틀리는 방향(envelope)으로만 추출한다.
- 좁히는 방향으로 틀릴 수 있는 것(enum)은 명시적 `Valid Values:` 단일 리스트에서만
  추출한다. 불릿 합집합은 금지 — `Volume.VolumeType`에서 st1/sc1/standard가 누락돼
  유효한 값을 invalid로 오판하게 된다.
- veto 큐(비율/증분/예시)가 있는 줄은 범위 추출에서 제외한다.
- 한 설명 안에서 하한이 상한을 넘으면(자기모순) 그 설명의 범위를 전부 버린다.
  veto와 **독립**이라 정규식이 느슨해져도 살아남는 2차 그물이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# 콤마 그룹은 반드시 `+`. `*`로 쓰면 "1024"가 "102"로 잘린다 (실측 확인).
NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"

# 링크 안의 URL(버전 번호 등)이 숫자로 오인되지 않게 링크 텍스트만 남긴다.
_LINK = re.compile(r"\[([^\]\n]*)\]\(https?://[^)\s]*\)")
# ``3,000``(*default*)``- 80,000`` → ``3,000 - 80,000`` 으로 봉합
_SPLICE = re.compile(r"``\s*\(\*?default\*?\)\s*``")
# 이 단어가 있는 줄의 범위 매칭은 버린다 (비율/증분/예시/버전/백분율)
_VETO = re.compile(r"(?i)\b(multiple|ratio|ratios|increments?|for\s+example|e\.g\.|version|percent)\b")
# 범위 **밖의 특수값**이 따로 허용된다는 신호. 이게 있으면 하한을 기록하지 않는다.
# (실측: SQS.ReceiveMessageWaitTimeSeconds "integer from 1 to 20" + "when you specify 0" → 0도 유효,
#  Redshift.ManualSnapshotRetentionPeriod "must be either -1 or an integer between 1 and 3,653" → -1도 유효.
#  하한을 그대로 기록하면 유효한 값을 거부하는 fail-closed 오류가 된다.)
_SENTINEL = re.compile(
    r"(?i)\beither\s+`*-?\d+`*\s+or\b"
    r"|\bspecify\s+`*-?\d+`*\s+(?:for|to)\b"
    r"|\bif\s+(?:the\s+value\s+is|you\s+specify)\s+`*-?\d+`*\b"
    r"|\bvalue\s+of\s+`*-?\d+`*\s+(?:means|indicates|disables)\b"
)
_UNIT = re.compile(
    r"(?i)\b(GiB|TiB|MiB/s|MiB|KiB|GB|MB|KB|TB|bytes?|seconds?|minutes?|hours?|days?"
    r"|milliseconds?|IOPS|vCPUs?)\b"
)

# (정규식, rule 이름) — 하한/상한 쌍을 만드는 규칙
_PAIR_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            rf"(?i)Valid\s+Range:\s*Minimum\s+value\s+of\s+({NUM})\.\s*"
            rf"Maximum\s+value\s+of\s+({NUM})\."
        ),
        "valid_range",
    ),
    (re.compile(rf"``\s*({NUM})\s*(?:-|–|to)\s*({NUM})\s*``"), "backtick_range"),
    (re.compile(rf"(?i)\bbetween\s+({NUM})\s+and\s+({NUM})\b"), "between"),
    (
        re.compile(rf"(?i)\b(?:integer|number|value)\s+from\s+({NUM})\s+to\s+({NUM})\b"),
        "from_to",
    ),
)

# 단독 상한/하한 규칙
_MAX_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(rf"(?i)\bmaximum\s+allowed\s+value\s+is\s+({NUM})\b"), "max_allowed"),
)
_MIN_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            rf"(?i)\bmust\s+be\s+(?:equal\s+to\s+or\s+greater\s+than|at\s+least)\s+({NUM})\b"
        ),
        "min_at_least",
    ),
)

_DEFAULT_NUM = re.compile(
    rf"(?i)(?:^|[\n.]\s*)(?:The\s+)?default(?:\s+value)?(?:\s+is|:)\s*`*({NUM})`*"
)
_DEFAULT_STR = re.compile(r"(?i)(?:^|\n)\s*Default:\s*``([^`\n]+)``\s*$", re.MULTILINE)
# 반드시 `|`로 구분된 **단일** 리스트만. 불릿 합집합은 fail-closed라 금지.
_ENUM_VALID_VALUES = re.compile(r"(?i)Valid\s+Values:\s*``([^`\n]*\|[^`\n]*)``")

_NOTE_LIMIT = 200


@dataclass(frozen=True, slots=True)
class Extraction:
    """산문에서 뽑아낸 제약 하나."""

    kind: str  # min | max | default | enum
    value: Any
    rule: str
    unit: str | None = None
    conditional: bool = False
    note: str | None = None


def _norm(description: str) -> str:
    """매칭 전 정규화: 링크는 텍스트만 남기고, (*default*) 삽입구를 봉합한다."""
    return _SPLICE.sub(" ", _LINK.sub(r"\1", description))


def _blocks(text: str) -> list[str]:
    """줄/불릿 단위로 나눈다.

    ⚠️ 문장 단위로 나누면 안 된다 — "Valid Range: Minimum value of 125.
    Maximum value of 2000." 이 쪼개져 정상 케이스가 사라진다.
    """
    return [b for b in text.split("\n") if b.strip()]


def _num(raw: str) -> float | int:
    value = float(raw.replace(",", ""))
    return int(value) if value.is_integer() else value


def _unit_of(block: str) -> str | None:
    match = _UNIT.search(block)
    return match.group(1) if match else None


def _shorten(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _NOTE_LIMIT else flat[:_NOTE_LIMIT] + " …"


def extract_ranges(description: str) -> list[Extraction]:
    """설명문에서 하한/상한을 추출한다.

    여러 범위가 있으면(예: 볼륨 타입별로 다른 범위) **envelope**(가장 작은 하한,
    가장 큰 상한)로 합치고 `conditional=True`로 표시한다. envelope의
    명제("이 범위 밖은 어떤 설정으로도 무효")는 참이므로 유효한 반쪽 지식이다.
    """
    text = _norm(description)
    pairs: list[tuple[float | int, float | int, str, str]] = []
    min_cands: list[tuple[float | int, str, str]] = []
    max_cands: list[tuple[float | int, str, str]] = []
    lone_mins: list[float | int] = []
    lone_maxs: list[float | int] = []

    for block in _blocks(text):
        if _VETO.search(block):
            continue  # 비율/증분/예시 문장 — 범위로 오인하면 안 됨
        for pattern, rule in _PAIR_RULES:
            for match in pattern.finditer(block):
                low, high = _num(match.group(1)), _num(match.group(2))
                if low > high:
                    continue  # 역전된 쌍은 애초에 범위가 아니다
                pairs.append((low, high, rule, block))
                min_cands.append((low, rule, block))
                max_cands.append((high, rule, block))
        for pattern, rule in _MIN_RULES:
            for match in pattern.finditer(block):
                value = _num(match.group(1))
                min_cands.append((value, rule, block))
                lone_mins.append(value)
        for pattern, rule in _MAX_RULES:
            for match in pattern.finditer(block):
                value = _num(match.group(1))
                max_cands.append((value, rule, block))
                lone_maxs.append(value)

    if not min_cands and not max_cands:
        return []

    # R3: 자기모순 검사. 단독 하한은 무조건적 주장이므로 어떤 상한보다도 작아야 한다.
    # (조건부로 서로 떨어진 범위들끼리는 비교하지 않는다 — 정상일 수 있으므로)
    highs = [high for _, high, _, _ in pairs] + lone_maxs
    lows = [low for low, _, _, _ in pairs] + lone_mins
    if any(m > h for m in lone_mins for h in highs) or any(
        x < low for x in lone_maxs for low in lows
    ):
        return []

    # 같은 경계에 서로 다른 값이 여러 개면 조건부다 (예: 티어별로 다른 하한).
    conditional = (
        len(pairs) > 1
        or len({value for value, _, _ in min_cands}) > 1
        or len({value for value, _, _ in max_cands}) > 1
    )
    note = None
    if conditional:
        blocks = list(dict.fromkeys(block for _, _, block in min_cands + max_cands))
        note = _shorten(" / ".join(blocks))

    results: list[Extraction] = []
    sentinel = bool(_SENTINEL.search(text))
    if min_cands and not sentinel:
        value, rule, block = min(min_cands, key=lambda item: item[0])
        results.append(
            Extraction(
                kind="min",
                value=value,
                rule=rule,
                unit=_unit_of(block),
                conditional=conditional,
                note=note,
            )
        )
    if max_cands:
        value, rule, block = max(max_cands, key=lambda item: item[0])
        sentinel_note = (
            "범위 밖의 특수값(-1/0 등)이 따로 허용되어 하한은 기록하지 않음"
            if sentinel
            else None
        )
        merged = " | ".join(part for part in (note, sentinel_note) if part)
        results.append(
            Extraction(
                kind="max",
                value=value,
                rule=rule,
                unit=_unit_of(block),
                conditional=conditional,
                note=merged or None,
            )
        )
    return results


def extract_default(description: str, *, numeric: bool = True) -> Extraction | None:
    """기본값을 추출한다.

    Args:
        description: 설명문.
        numeric: True면 숫자 기본값, False면 문자열 기본값(``gp2`` 형태)을 찾는다.
    """
    text = _norm(description)
    if numeric:
        match = _DEFAULT_NUM.search(text)
        if match:
            return Extraction(
                kind="default", value=_num(match.group(1)), rule="default_num"
            )
        return None
    match = _DEFAULT_STR.search(text)
    if match:
        return Extraction(
            kind="default", value=match.group(1).strip(), rule="default_str"
        )
    return None


def extract_enum(description: str) -> Extraction | None:
    """허용값 목록을 추출한다.

    **명시적 `Valid Values: ``a | b | c``` 단일 리스트에서만** 추출한다.
    불릿에 흩어진 값을 합치면 일부를 놓쳐(예: Volume.VolumeType의 st1/sc1/standard)
    유효한 값을 invalid로 오판하게 되므로 절대 하지 않는다.
    """
    match = _ENUM_VALID_VALUES.search(_norm(description))
    if not match:
        return None
    values = [part.strip() for part in match.group(1).split("|")]
    values = [v for v in values if v]
    if len(values) < 2:
        return None
    return Extraction(kind="enum", value=values, rule="enum_valid_values")
