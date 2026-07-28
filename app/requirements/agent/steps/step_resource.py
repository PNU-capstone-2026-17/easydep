"""제약 구조화 — 산문에 흩어진 클라우드 제약을 `RESOURCE_SPEC` 계약으로 옮긴다(A 트랙).

## 이 단계가 있는 이유

`docs/cloud-native-requirements.md` §1이 진단한 공백이 이것이다: **`RESOURCE_SPEC`을
아무도 만들지 않는다.** 스키마도, 필수 판정식도, 되묻기 문구(`REQUIRED_WHY`)도 이미
있는데 생산자만 없어서 `app/requirements/api.py`가 "이 에이전트가 만들지 않으므로
비운다"고 적고 있었다. 이 단계가 그 생산자다.

## 무엇을 안 하는가 — 여기가 이 단계의 성격이다

**값을 지어내지 않는다.** 못 알아들은 것은 빈 칸으로 두고 **되묻는다**. 그래서 이
단계의 산출물은 두 갈래다.

  - `resource_spec` — **계약을 만족할 때만 존재한다.** 필수 칸이 비면 아예 내지 않는다.
    반쯤 채운 것을 내보내면 뒤 단계가 그걸 사양으로 알고 조인을 돌린다.
  - `resource_intake` — 초안·질문·근거(provenance)·**버린 후보**. 사람이 읽고 되짚는 자리다.

버린 후보를 남기는 이유는 감사 추적이다. "왜 이 값이 안 들어갔나"에 답할 수 없으면
빈 칸이 **정보 부재**인지 **추출 실패**인지 구별되지 않는다.

## 패턴을 어디서 얻었나

지어내지 않고 코퍼스에서 얻었다(2026-07-28 실측, 내부 입력 11종 270문장 + PURE 18편
7,659문장). 그 실측이 말한 것:

  - **`provider`·`region`·`monthlyBudgetUSD`는 요구사항 문장에 아예 없다**(0건). 이건
    추출기의 실패가 아니라 **체크리스트가 작동한 결과**다 — 그래서 이 세 칸은 되묻기가
    기본 경로이고, 산문 채굴은 제약 원문(`resource_constraints_text`)에서만 시도한다.
  - **규모는 요구사항 문장에 있다**(4종 4건: `10 000 concurrent shoppers` ·
    `100 000 concurrent ... sessions` · `at least 100 users` · `one million concurrent
    video streams`). 그래서 규모만 요구사항 문장에서도 캔다.
  - **단가를 예산으로 오인하는 함정이 실재한다** — `iot_telemetry/NFR-08`의
    "storage cost does not exceed **$0.02 per GB-month**"는 월 예산이 아니다. 통화 기호와
    'month'만 보면 걸린다. 그래서 `per <단위>`를 먼저 보고 **월 예산이 아닌 단가는 버린다.**
  - **숫자+동시성만으로는 부족하다** — PURE에 `100 simultaneous icons` ·
    `six active control nodes`가 있다. 주체가 사용자·세션 계열일 때만 규모로 센다.

## 모호하면 채우지 않고 묻는다

값이 둘 이상 서로 다르게 나오면(프로바이더 둘, 리전 후보 여럿, 동시 사용자 수 둘) 그건
정보가 아니라 **질문**이다. `core.regions`가 후보를 좁히지 않고 돌려주는 것과 같은 규율 —
우리가 고른 것이 사용자가 뜻한 것인 양 보이면 안 된다.

환율은 환산하지 않는다. 계약이 그렇게 정해 두었고(환율 소스가 없다), 그래서 USD가 아닌
금액은 **버리고 묻는다.**
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dc_field

from app.core import cloud_contract, regions
from app.requirements.agent.state import AgentState
from app.requirements.common.state_contract import contract

#: 계약 판. 스키마가 `const`로 못 박아 둔 값이라 여기서 정하는 것이 아니라 옮겨 적는다.
SCHEMA_VERSION = "1"

#: 질문의 종류. 빈 칸의 **이유**가 다르면 사용자가 할 일도 다르다.
MISSING = "missing"        # 아무것도 못 찾았다 → 값을 달라
AMBIGUOUS = "ambiguous"    # 여럿을 찾았다 → 어느 것인지 골라 달라
UNCONVERTIBLE = "currency"  # USD가 아니다 → 계약이 환산을 거부한다
CONFLICT = "conflict"      # 제약 원문과 요구사항이 다른 값을 말한다

#: 규모로 세는 주체. **닫힌 집합이다** — `100 simultaneous icons`(PURE)를 동시 사용자로
#: 세지 않기 위한 것이고, 늘리려면 코퍼스에 그 표현이 실재한다는 근거가 있어야 한다.
_SCALE_SUBJECTS = (
    "user", "users", "shopper", "shoppers", "customer", "customers", "visitor",
    "visitors", "session", "sessions", "stream", "streams", "viewer", "viewers",
    "client", "clients", "connection", "connections", "사용자", "동시접속자", "세션",
)

#: 자릿수 구분자로 실제로 쓰이는 것들. 얇은 공백(U+2009)·비분할 공백(U+00A0)이 실측
#: 코퍼스에 있다(`10 000 concurrent shoppers`) — 보통 공백만 보면 통째로 놓친다.
_SEPARATORS = "    ,"

#: 배수 낱말. 닫힌 집합이고, 실측에서 `one million`이 실재해서 넣었다.
_MULTIPLIERS = {"thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
_UNITS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

_NUMBER = rf"\d[\d{re.escape(_SEPARATORS)}]*(?:\.\d+)?"
_WORD_NUMBER = rf"(?:{'|'.join(_UNITS)})\s+(?:{'|'.join(_MULTIPLIERS)})"

#: 동시성을 말하는 낱말. 숫자와 주체 사이 어디에 있어도 된다.
_CONCURRENCY = r"(?:concurrent(?:ly)?|simultaneous(?:ly)?|at the same time|동시)"

#: 월 예산임을 확정하는 꼬리표. **이것이 없으면 예산으로 세지 않는다.**
_PER_MONTH = r"(?:per\s+month|a\s+month|/\s*month|monthly|매달|월\s*간?|월당)"

#: 금액에 붙은 `per <단위>` — 단가의 표지다. `per month`만 예외다.
_PER_UNIT = re.compile(r"(?:per|/)\s*(?:gb|tb|mb|gib|tib|hour|hr|user|request|call|seat|일|시간)",
                       re.IGNORECASE)

#: USD가 아닌 통화. 계약이 환산을 거부하므로 **버리고 묻는** 대상이다.
#:
#: **금액에 붙어 있을 때만 통화다.** 낱말만 보면 안 된다 — `원`은 원본·지원·복원에 들어
#: 있고, 낱말 뒤를 막는 방식(`원(?![가-힣])`)으로는 "300만원이다"를 놓친다(조사가 붙는다).
#: 그래서 **숫자와의 인접**을 조건으로 둔다. 방향이 둘이라 식도 둘이다(기호는 앞, 한국어
#: 단위는 뒤).
_OTHER_CURRENCY = re.compile(
    r"(?:₩|krw|€|eur|£|gbp|¥|jpy|cny|rmb)\s*\d[\d,.\s]*"
    r"|\d[\d,.\s]*\s*(?:만|억|천)?\s*(?:원|엔|위안|유로|krw|eur|gbp|jpy|cny|rmb)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Candidate:
    """추출한 값 하나. **원문과 출처를 늘 들고 다닌다** — 틀렸을 때 되짚을 근거다."""

    field: str
    value: object
    as_written: str
    source: str   # "constraints" 또는 요구사항 id
    how: str      # 어느 규칙이 뽑았는가

    def as_dict(self) -> dict:
        return {"field": self.field, "value": self.value, "as_written": self.as_written,
                "source": self.source, "how": self.how}


@dataclass
class Extraction:
    """한 번의 훑기 결과. 버린 것도 함께 담는다."""

    found: list[Candidate] = dc_field(default_factory=list)
    rejected: list[dict] = dc_field(default_factory=list)

    def reject(self, field_name: str, as_written: str, source: str, why: str) -> None:
        self.rejected.append({"field": field_name, "as_written": as_written,
                              "source": source, "why": why})


def _to_number(raw: str) -> float | None:
    """`10 000` · `10,000` · `one million` → 수. 못 읽으면 None."""
    text = raw.strip().lower()
    words = text.split()
    if len(words) == 2 and words[0] in _UNITS and words[1] in _MULTIPLIERS:
        return float(_UNITS[words[0]] * _MULTIPLIERS[words[1]])
    if len(words) == 2 and words[1] in _MULTIPLIERS:
        head = _to_number(words[0])
        return None if head is None else head * _MULTIPLIERS[words[1]]
    stripped = text
    for separator in _SEPARATORS:
        stripped = stripped.replace(separator, "")
    try:
        return float(stripped)
    except ValueError:
        return None


def _sentences(state: AgentState) -> list[tuple[str, str]]:
    """(출처 이름, 문장). 제약 원문이 먼저 온다 — 충돌 시 우선순위의 근거다."""
    out: list[tuple[str, str]] = []
    constraints = (state.get("resource_constraints_text") or "").strip()
    if constraints:
        out.append(("constraints", constraints))
    for item in state.get("classified") or []:
        text = (item.get("text") or "").strip()
        if text:
            out.append((item.get("id") or "?", text))
    return out


# --- 칸마다 하나씩 -----------------------------------------------------------
def _extract_provider(text: str, source: str, out: Extraction) -> None:
    """리전 지식베이스가 **실제로 아는** 프로바이더 id만 센다.

    별칭표('Amazon Web Services' → aws)를 두지 않는다. 실측 코퍼스에 프로바이더 언급이
    0건이라 별칭을 지을 근거가 없고, 근거 없는 사전이 바로 이 저장소가 금지하는 것이다.
    못 알아들으면 되묻는 편이 지어내는 것보다 낫다.
    """
    for name in regions.providers():
        if re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            out.found.append(Candidate("provider", name, name, source, "provider-id"))


def _extract_region(text: str, source: str, out: Extraction, provider: str | None) -> None:
    """리전 **코드**를 채운다. 지명은 해석기를 거치고, 여럿이면 채우지 않는다.

    요구사항 문장에서는 찾지 않는다(부르는 쪽이 제약 원문만 넘긴다). 실측에서 요구사항
    쪽 리전 언급은 전부 "across multiple cloud regions" 같은 **성질 서술**이었고, 거기서
    지명을 캐면 확신에 찬 오답이 된다.
    """
    for match in re.finditer(r"[A-Za-z][A-Za-z0-9-]{3,}", text):
        token = match.group(0)
        candidates = regions.resolve(token, provider=provider)
        if not candidates:
            continue
        codes = {c.code for c in candidates}
        if len(codes) == 1:
            found = candidates[0]
            out.found.append(Candidate("region", found.code, token, source, "region-code"))
            if found.code.lower() != token.lower():
                out.found.append(
                    Candidate("regionAsWritten", token, token, source, "region-name")
                )
        else:
            out.reject("region", token, source,
                       f"후보가 {len(codes)}개다({', '.join(sorted(codes)[:4])}…) — 좁히지 않는다")

    # 한국어 지명은 조사가 붙으므로 문장을 통째로 넘긴다(해석기가 부분 문자열로 찾는다).
    if any("가" <= ch <= "힣" for ch in text):
        candidates = regions.resolve(text, provider=provider)
        codes = {c.code for c in candidates}
        if len(codes) == 1:
            written = text[:40]
            out.found.append(
                Candidate("region", candidates[0].code, written, source, "region-alias-ko")
            )
            # **여기야말로 원문을 남겨야 하는 자리다.** 별칭 해석("서울" → ap-northeast-2)은
            # 코드만 보면 어디서 왔는지 알 수 없다. 계약이 `regionAsWritten`을 둔 이유가
            # 정확히 이것이고, 영어 경로에만 남기면 해석이 가장 불투명한 쪽이 빈다.
            out.found.append(
                Candidate("regionAsWritten", written, written, source, "region-alias-ko")
            )
        elif codes:
            out.reject("region", text[:40], source,
                       f"한국어 지명 후보가 {len(codes)}개다 — 좁히지 않는다")


def _extract_budget(text: str, source: str, out: Extraction) -> None:
    """월 예산(USD)만 채운다. **단가와 타 통화는 버린다.**"""
    for match in re.finditer(rf"(?:\$|\busd\b)\s*({_NUMBER})", text, re.IGNORECASE):
        tail = text[match.end():match.end() + 40]
        head = text[max(0, match.start() - 40):match.start()]
        if _PER_UNIT.search(tail):
            # 실측 함정: "$0.02 per GB-month"는 예산이 아니라 단가다.
            out.reject("monthlyBudgetUSD", match.group(0), source, "단가다(per <단위>)")
            continue
        if not re.search(_PER_MONTH, tail, re.IGNORECASE) and not re.search(_PER_MONTH, head, re.IGNORECASE):
            out.reject("monthlyBudgetUSD", match.group(0), source, "월 단위 표시가 없다")
            continue
        value = _to_number(match.group(1))
        if value is None or value <= 0:
            out.reject("monthlyBudgetUSD", match.group(0), source, "수로 못 읽었다")
            continue
        out.found.append(
            Candidate("monthlyBudgetUSD", value, match.group(0), source, "usd-per-month")
        )

    for match in _OTHER_CURRENCY.finditer(text):
        out.reject("monthlyBudgetUSD", match.group(0).strip(), source,
                   "USD가 아니다 — 계약이 환율 환산을 거부한다")


def _extract_scale(text: str, source: str, out: Extraction) -> None:
    """동시 사용자와 초당 요청. **주체를 본다** — 숫자+동시성만으로는 세지 않는다."""
    number = rf"(?:{_WORD_NUMBER}|{_NUMBER})"
    subjects = "|".join(re.escape(s) for s in _SCALE_SUBJECTS)

    # 숫자 … 동시성 … 주체 (그리고 동시성과 주체가 뒤바뀐 순서도)
    patterns = (
        rf"({number})\s+(?:[\w-]+\s+){{0,3}}?{_CONCURRENCY}\s+(?:[\w-]+\s+){{0,3}}?({subjects})\b",
        rf"({number})\s+(?:[\w-]+\s+){{0,3}}?({subjects})\b[^.]{{0,40}}?{_CONCURRENCY}",
        rf"{_CONCURRENCY}[^.]{{0,20}}?({number})\s+(?:[\w-]+\s+){{0,3}}?({subjects})\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            value = _to_number(match.group(1))
            if value is None or value < 1:
                continue
            out.found.append(Candidate(
                "expectedConcurrentUsers", int(value), match.group(0).strip(), source,
                "concurrent-subject",
            ))

    for match in re.finditer(
        rf"({number})\s*(?:requests?\s*(?:per|/)\s*(?:second|sec|s)\b|\brps\b|\btps\b|\bqps\b)",
        text, re.IGNORECASE,
    ):
        value = _to_number(match.group(1))
        if value is not None and value > 0:
            out.found.append(Candidate(
                "approxRequestsPerSecond", value, match.group(0).strip(), source, "rps",
            ))


def _extract_shape(text: str, source: str, out: Extraction) -> None:
    """부하 모양과 가용영역 — 계약의 선택 칸. 명시적일 때만 채운다.

    `trafficPattern`은 `steady|spiky` 둘뿐이다. "peak demand spikes"(실측)는 spiky이고,
    상시 부하라고 **적혀 있을 때만** steady다. 안 적힌 것을 steady로 두면 그건 기본값이
    아니라 우리가 지어낸 주장이 된다.
    """
    # **걸린 자리를 그대로 남긴다.** 문장 앞부분을 근거로 적으면 값이 왜 들어갔는지 보이지
    # 않는다 — 실측에서 `multiZone=True`의 근거가 "ninety-nine point nine-five percent"로
    # 찍혀 멀쩡한 추출이 오탐처럼 읽혔다(진짜 근거는 문장 뒤쪽의 "availability zones"였다).
    #
    # **낱말만으로는 안 된다 — 부하 맥락을 함께 요구한다.** `burst` 하나만 보던 판을
    # PURE에 돌리니 "TCS data **burst** messages"(메시지 형식이지 부하 모양이 아니다)가
    # 유일한 후보로 나왔다. 맥락을 붙인 뒤 PURE 7,659문장에서 후보 0건이 됐고, 내부
    # 코퍼스의 참 사례("handle peak demand spikes")는 그대로 걸린다.
    spiky = re.search(
        r"(?:peak|burst[sy]?|spike[sd]?)\s+(?:demand|load|traffic|usage|hour|period)"
        r"|(?:demand|load|traffic|usage|request)\s+(?:peaks?|bursts?|spikes?)"
        r"|(?:peaks?|bursts?|spikes?)\s+in\s+(?:demand|load|traffic|usage)"
        r"|bursty|폭증|트래픽\s*피크",
        text, re.IGNORECASE,
    )
    steady = re.search(r"(?:steady|constant|uniform)\s+(?:load|traffic|demand)|상시\s*부하",
                       text, re.IGNORECASE)
    if spiky:
        out.found.append(
            Candidate("trafficPattern", "spiky", spiky.group(0), source, "spiky-word")
        )
    elif steady:
        out.found.append(
            Candidate("trafficPattern", "steady", steady.group(0), source, "steady-word")
        )

    zones = re.search(r"(?:multi-?az|multiple availability zones?|availability zones?|가용\s*영역)",
                      text, re.IGNORECASE)
    if zones:
        out.found.append(Candidate("multiZone", True, zones.group(0), source, "az-word"))


# --- 되묻기의 답 -------------------------------------------------------------
#: 답을 그대로 받는 칸 — 계약이 "사용자 표현 그대로"를 요구한다.
_AS_WRITTEN_FIELDS = ("dataResidency",)

#: 예/아니오 칸. 답이 이 목록에 없으면 **채우지 않고 다시 묻는다.**
_YES = ("yes", "y", "true", "예", "네", "응", "그렇다")
_NO = ("no", "n", "false", "아니오", "아니요", "아뇨", "아니다")

ANSWER = "answer"


def answer_field(field_name: str, text: str, provider: str | None = None) -> Extraction:
    """되묻기의 답 하나를 **산문과 같은 규율로** 정규화한다.

    답이라고 해서 값으로 바로 받지 않는다. 사용자가 "서울"이라고 답해도 계약이 요구하는
    것은 리전 **코드**이고, 후보가 여럿이면 여전히 모호하다 — 질문에 답했다는 사실이
    모호함을 없애 주지는 않는다. 그래서 같은 해석기·같은 거절 사유를 그대로 쓴다.

    산문과 **다른 점은 하나**다: 무엇에 대한 답인지 이미 알기 때문에 문맥 단서를 요구하지
    않는다. `monthlyBudgetUSD` 질문에 "3000"이라고 답한 것은 월 예산 3000 USD이지, 월
    단위 표시가 없다고 버릴 것이 아니다. 통화·단가 판정은 그대로 남는다 — 그건 문맥이
    아니라 계약이 거부하는 것이라서다.
    """
    out = Extraction()
    body = (text or "").strip()
    if not body:
        return out

    def keep(value: object, how: str) -> None:
        out.found.append(Candidate(field_name, value, body, ANSWER, how))

    if field_name in _AS_WRITTEN_FIELDS:
        keep(body, "answer-verbatim")
    elif field_name == "provider":
        _extract_provider(body, ANSWER, out)
        if not out.found:
            out.reject(field_name, body, ANSWER,
                       f"아는 프로바이더가 아니다 — {', '.join(regions.providers())} 중 하나여야 한다")
    elif field_name == "region":
        _extract_region(body, ANSWER, out, provider)
        if not out.found and not out.rejected:
            out.reject(field_name, body, ANSWER, "리전으로 못 알아들었다")
    elif field_name == "monthlyBudgetUSD":
        if _OTHER_CURRENCY.search(body):
            out.reject(field_name, body, ANSWER,
                       "USD가 아니다 — 계약이 환율 환산을 거부한다")
        elif _PER_UNIT.search(body):
            out.reject(field_name, body, ANSWER, "단가다(per <단위>) — 월 예산이 필요하다")
        else:
            match = re.search(rf"{_NUMBER}", body)
            value = _to_number(match.group(0)) if match else None
            if value is None or value <= 0:
                out.reject(field_name, body, ANSWER, "양수인 금액으로 못 읽었다")
            else:
                keep(value, "answer-number")
    elif field_name in ("expectedConcurrentUsers", "approxRequestsPerSecond"):
        match = re.search(rf"(?:{_WORD_NUMBER}|{_NUMBER})", body, re.IGNORECASE)
        value = _to_number(match.group(0)) if match else None
        if value is None or value < 1:
            out.reject(field_name, body, ANSWER, "1 이상의 수로 못 읽었다")
        else:
            keep(int(value) if field_name == "expectedConcurrentUsers" else value,
                 "answer-number")
    elif field_name == "trafficPattern":
        lowered = body.lower()
        if lowered in ("steady", "spiky"):
            keep(lowered, "answer-enum")
        else:
            out.reject(field_name, body, ANSWER, "steady 또는 spiky여야 한다")
    elif field_name in ("multiZone", "stateless"):
        lowered = body.lower()
        if lowered in _YES:
            keep(True, "answer-boolean")
        elif lowered in _NO:
            keep(False, "answer-boolean")
        else:
            out.reject(field_name, body, ANSWER, "예/아니오로 못 읽었다")
    else:
        # 계약에 없는 칸에 답이 오면 **버린다.** 받아 두면 스키마 검증에서 스펙 전체가
        # 무효가 되고, 한 칸의 오타가 나머지를 다 죽인다.
        out.reject(field_name, body, ANSWER, "계약에 없는 칸이다")
    return out


# --- 조립 -------------------------------------------------------------------
def _collect(state: AgentState) -> Extraction:
    out = Extraction()
    sources = _sentences(state)
    answers = {k: str(v) for k, v in (state.get("resource_answers") or {}).items()}

    # 프로바이더를 먼저 정한다 — 리전 후보를 좁히는 데 쓴다(같은 지명이 프로바이더
    # 10곳에 걸린다). 좁히는 근거가 사용자 자신의 말일 때만 좁힌다.
    #
    # **되묻기의 답도 여기서 함께 본다.** 답을 맨 나중에 접으면 "aws"라고 답한 사용자에게
    # 리전이 여전히 모호하다고 다시 묻게 된다 — 방금 준 답을 안 쓰고 물어보는 꼴이다.
    for source, text in sources:
        _extract_provider(text, source, out)
    if "provider" in answers:
        out.found.extend(answer_field("provider", answers["provider"]).found)
    providers = {c.value for c in out.found if c.field == "provider"}
    provider = str(next(iter(providers))) if len(providers) == 1 else None

    for source, text in sources:
        _extract_scale(text, source, out)
        _extract_shape(text, source, out)
        # 예산·리전은 **제약 원문에서만** 캔다. 요구사항 산문에는 0건이라는 실측이 있고,
        # 없는 곳을 뒤지면 오탐만 남는다(§6.12).
        if source == "constraints":
            _extract_budget(text, source, out)
            _extract_region(text, source, out, provider)

    # 나머지 답. 사용자가 그 칸을 두고 답한 것이라 산문에서 캔 것보다 늦고 명시적이다
    # (`_resolve_field`가 그 순서를 우선순위로 쓴다). `provider`는 위에서 이미 접었다 —
    # 두 번 접으면 같은 값이 두 번 세어져 근거 목록이 부풀고, 거절 사유도 두 벌이 된다.
    for name, text in answers.items():
        if name == "provider":
            answered = answer_field(name, text)
            out.rejected.extend(answered.rejected)   # 값은 위에서 이미 넣었다
            continue
        answered = answer_field(name, text, provider)
        out.found.extend(answered.found)
        out.rejected.extend(answered.rejected)
    return out


def _resolve_field(name: str, found: list[Candidate]) -> tuple[object | None, dict | None]:
    """한 칸의 값을 정한다. 돌려주는 둘째 값은 **질문**(없으면 None).

    규칙 셋:
      - 값이 하나로 모이면 채운다(같은 값이 여러 번 나온 것은 충돌이 아니다).
      - 출처가 다르게 말하면 **늦고 명시적인 쪽**을 쓰되 충돌을 질문으로 남긴다.
        순서는 되묻기의 답 > 제약 원문 > 요구사항 산문이다 — 답은 사용자가 **그 칸을
        두고** 한 말이라 가장 세다. 조용히 덮으면 밀려난 쪽이 사라지므로 충돌은 남긴다.
      - 그 외 서로 다른 값이 여럿이면 **채우지 않고** 묻는다.
    """
    mine = [c for c in found if c.field == name]
    if not mine:
        return None, None
    values = {c.value for c in mine}
    if len(values) == 1:
        return mine[0].value, None

    for source, note in ((ANSWER, "되묻기의 답"), ("constraints", "제약 원문")):
        winning = {c.value for c in mine if c.source == source}
        if len(winning) != 1:
            continue
        return next(iter(winning)), {
            "field": name, "kind": CONFLICT,
            "why": cloud_contract.why(name),
            "seen": [c.as_dict() for c in mine],
            "question": (f"{name}에 대해 출처마다 다른 값을 말한다 — {note}을 썼다. "
                         f"맞는가?"),
        }
    return None, {
        "field": name, "kind": AMBIGUOUS,
        "why": cloud_contract.why(name),
        "seen": [c.as_dict() for c in mine],
        "question": f"{name}에 해당하는 값이 여럿이다 — 어느 것인가?",
    }


@contract("build_resource_spec", requires=("classified",),
          produces=("resource_intake",))
def build_resource_spec(state: AgentState) -> dict:
    """제약 산문을 `RESOURCE_SPEC`으로 옮기고, 못 채운 칸은 계약의 이유를 들어 되묻는다."""
    extraction = _collect(state)

    spec: dict = {"schemaVersion": SCHEMA_VERSION}
    questions: list[dict] = []
    fields = sorted({c.field for c in extraction.found})
    for name in fields:
        value, question = _resolve_field(name, extraction.found)
        if value is not None:
            spec[name] = value
        if question:
            questions.append(question)

    # 계약이 아는 칸만 남긴다. 모르는 칸을 넣으면 `additionalProperties: false`에 걸려
    # 스펙 전체가 무효가 된다 — 한 칸의 추출 실수가 나머지를 다 죽이면 안 된다.
    unknown = sorted(set(spec) - cloud_contract.schema_fields())
    for name in unknown:
        extraction.reject(name, str(spec.pop(name)), "spec", "계약에 없는 칸이다")

    # 못 채운 필수 칸 → **계약이 적어 둔 이유를 그대로** 되묻는다. 이유 없이 물으면
    # 사용자가 아무 값이나 채운다는 것이 `REQUIRED_WHY`의 존재 이유다.
    asked = {q["field"] for q in questions}
    for name in cloud_contract.missing_fields(spec):
        if name in asked:
            continue
        rejected = [r for r in extraction.rejected if r["field"] == name]
        questions.append({
            "field": name,
            "kind": UNCONVERTIBLE if any(
                "USD" in r["why"] for r in rejected
            ) else MISSING,
            "why": cloud_contract.why(name),
            "seen": rejected,
            "question": f"{name} 값이 필요하다 — {cloud_contract.why(name)}",
        })

    errors = cloud_contract.validate(spec)
    intake = {
        "draft": spec,
        "valid": not errors,
        "errors": errors,
        "questions": questions,
        "provenance": [c.as_dict() for c in extraction.found],
        # 버린 후보를 남긴다 — 빈 칸이 "정보가 없어서"인지 "우리가 버려서"인지 구별된다.
        "rejected": extraction.rejected,
        "sources": sorted({c.source for c in extraction.found}),
    }

    result: dict = {"resource_intake": intake, "phase": "resource_spec"}
    # **계약을 만족할 때만 산출물이 존재한다.** 반쯤 채운 사양을 내보내면 뒤 단계가 그것을
    # 사양으로 알고 조인을 돌린다 — 값이 없는 것보다 나쁘다.
    if not errors:
        result["resource_spec"] = spec
    return result
