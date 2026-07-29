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


#: 괄호 안의 **환산 표기** — `(72 hours)`, `(20 MB)`처럼 숫자와 단위가 함께 든 것.
#: 이건 앞 숫자를 사람이 읽기 쉽게 바꿔 적은 것이지 **그 값의 단위가 아니다.**
_CONVERSION = re.compile(r"\(\s*[\d,.]+\s*[A-Za-z/]+\s*\)")

#: 단위 토큰 목록. `_UNIT`과 같은 것을 가리키되 **하나씩 따로 쓴다** —
#: 컴파일된 패턴을 문자열로 잘라 재사용하면 조용히 깨진다(실제로 겪었다).
_UNIT_WORDS = (
    "GiB", "TiB", "MiB/s", "MiB", "KiB", "GB", "MB", "KB", "TB",
    "bytes", "byte", "seconds", "second", "minutes", "minute",
    "hours", "hour", "days", "day", "milliseconds", "millisecond",
    "IOPS", "vCPUs", "vCPU",
)

#: `in seconds` / `in GiB` — 원문이 단위를 **선언**하는 형태. 가장 믿을 만하다.
_UNIT_DECL = re.compile(
    r"(?i)\bin\s+(" + "|".join(re.escape(w) for w in _UNIT_WORDS) + r")\b"
)

#: 속성 이름이 단위를 말하는 경우. `TimeoutInMillis`, `IntervalSeconds`, `Iops`.
#: **이름이 가장 강한 근거다** — 산문은 한 문단에 단위를 여럿 섞지만 이름은 하나다.
#: 정규식이 아니라 **소문자 부분 문자열**로 본다. 이름은 낱말 경계가 없는
#: 붙임말(`ReceiveMessageWaitTimeSeconds`)이라 경계 표시가 오히려 방해가 된다.
_NAME_UNITS: tuple[tuple[str, str], ...] = (
    ("millis", "milliseconds"),
    ("milliseconds", "milliseconds"),
    ("seconds", "seconds"),
    ("minutes", "minutes"),
    ("hours", "hours"),
    ("days", "days"),
    ("iops", "IOPS"),
    ("gib", "GiB"),
)


def _unit_from_name(prop: str | None) -> str | None:
    """속성 이름이 스스로 밝히는 단위. 없으면 None."""
    if not prop:
        return None
    tail = prop.rsplit("/", 1)[-1].lower()
    for needle, unit in _NAME_UNITS:
        if needle in tail:
            return unit
    return None
    tail = prop.rsplit("/", 1)[-1]
    for pattern, unit in _NAME_UNITS:
        if pattern.search(tail):
            return unit
    return None


def _unit_of(block: str, prop: str | None = None, full: str | None = None) -> str | None:
    """이 숫자의 단위. **확신이 없으면 None을 돌려준다.**

    예전에는 매칭된 블록의 **첫 번째** 단위 토큰을 그냥 돌려줬다. 그래서 세 가지가
    실제로 틀렸다(실측):

        BacktrackWindow  259200  → `hours`  (원문은 "in seconds … (72 hours)")
        Iops               1000  → `second` (원문은 "operations per second (IOPS)")
        MaximumLength  20971520  → `MB`     (원문은 "number of characters")

    첫 번째가 **3,600배 어긋난 단위**다. `cap_check_value`가
    "500 second는 최소 1000 second를 벗어남" 같은 문장을 만들고 있었다.

    그래서 순서를 세운다:

    1. **속성 이름** — `TimeoutInMillis`·`IntervalSeconds`는 이름이 곧 단위다.
       산문은 한 문단에 단위를 여럿 섞지만 이름은 하나뿐이라 가장 강하다.
    2. **`in X` 선언** — 원문이 "in seconds"라고 못 박은 것.
    3. **블록의 단위** — 위 둘이 없을 때만. 단, 괄호 안 환산 표기는 지운다.

    3번까지 와도 후보가 여럿이면 **담지 않는다.** 틀린 단위는 침묵보다 나쁘다 —
    침묵은 "모른다"지만 틀린 단위는 확신에 찬 오답이다.
    """
    from_name = _unit_from_name(prop)
    if from_name:
        return from_name

    haystack = full or block
    declared = _UNIT_DECL.search(_CONVERSION.sub(" ", haystack))
    if declared:
        return declared.group(1)

    cleaned = _CONVERSION.sub(" ", block)
    found = _UNIT.findall(cleaned)
    if not found:
        return None
    # 서로 다른 단위가 섞여 있으면 무엇이 이 숫자의 것인지 알 수 없다.
    distinct = {u.lower().rstrip("s") for u in found}
    return found[0] if len(distinct) == 1 else None


def _shorten(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _NOTE_LIMIT else flat[:_NOTE_LIMIT] + " …"


def extract_ranges(description: str, prop: str | None = None) -> list[Extraction]:
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
                unit=_unit_of(block, prop, text),
                conditional=conditional,
                note=note,
            )
        )
    if max_cands:
        value, rule, block = max(max_cands, key=lambda item: item[0])
        sentinel_note = (
            "special values outside the range (-1/0, etc.) are separately "
            "allowed, so no lower bound is recorded"
            if sentinel
            else None
        )
        merged = " | ".join(part for part in (note, sentinel_note) if part)
        results.append(
            Extraction(
                kind="max",
                value=value,
                rule=rule,
                unit=_unit_of(block, prop, text),
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
