"""**구현 이후 테스팅** — 요구사항에서 도출한 중립 수용 스위트.

## 왜 앱 자신의 테스트로는 부족한가

구현 에이전트가 생성한 단위·E2E 테스트는 **앱이 자기를 채점**하는 것이다 —
스텁을 통과시키는 테스트를 쓰면 자기심판이 된다. 그리고 계열 비교(단순 LLM·
MetaGPT)에서 각 시스템의 자기 테스트로 자기를 재면 공정하지 않다. 그래서 수용
검증은 **요구사항에서 직접** 도출하고, 배포된 앱에 **블랙박스로** 건다 — 누가
만든 앱이든 같은 잣대다.

## 두 입력, 둘 다 근거

    요구사항   무엇이 성립해야 하나 — classified.json의 FR·NFR (씨앗의 것)
    OpenAPI    그 행위에 어떻게 닿나 — 각 시스템이 낸 api_spec의 연산

FR은 연산에 매핑해 "이 엔드포인트가 이 요구를 만족하는가"를 만든다. 못 매핑하면
**지어내지 않고 unmapped로 남긴다**(관심사 축의 unjudged와 같은 규율 — 없는
것을 '통과'로 치지 않는다). NFR은 검증 패턴(지연·생존·격리)에 매칭한다.

## 이것은 실행기가 아니라 점검표다(+ 얇은 HTTP 실행)

`deploy_checks`와 같은 결. 무엇을·어디서·어떻게·통과 조건을 낸다. 기능 검사는
살아 있는 base_url에 HTTP로 바로 돌릴 수 있고(`run_functional`), 생존·격리 같은
NFR은 재배포·프로세스 종료 같은 오케스트레이션이 필요해 절차로 기술한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 매핑 점수에서 버리는 일반어 — 요구문의 뼈대라 변별력이 없다.
_STOP = {
    "the", "a", "an", "of", "to", "and", "or", "for", "in", "on", "with",
    "system", "shall", "must", "be", "is", "are", "by", "that", "this", "it",
    "their", "them", "they", "as", "at", "from", "into", "using", "able",
    "allow", "enable", "provide", "present", "given", "when", "then",
}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z]+", (text or "").lower())
    return {w for w in words if len(w) > 2 and w not in _STOP}


@dataclass(frozen=True)
class AcceptanceCheck:
    """요구 하나에 대한 수용 검사. **요구가 잣대이고, 앱의 자기 테스트가 아니다.**"""

    requirement_id: str
    requirement_text: str
    kind: str                       #: "functional" | "nfr"
    #: 기능: (METHOD, path). 못 매핑했으면 None(unmapped).
    endpoint: tuple[str, str] | None = None
    operation: str = ""             #: 매핑된 연산 요약(있으면)
    how: str = ""                   #: 어떻게 관측하나
    passes: str = ""                #: 통과 조건
    unmapped: str = ""              #: 매핑 실패 사유(있으면 — 이것이 정직의 자리)

    def as_dict(self) -> dict:
        return {
            "requirementId": self.requirement_id,
            "requirement": self.requirement_text,
            "kind": self.kind,
            "endpoint": (None if self.endpoint is None
                         else {"method": self.endpoint[0], "path": self.endpoint[1]}),
            "operation": self.operation,
            "how": self.how,
            "passes": self.passes,
            "unmapped": self.unmapped,
        }


#: NFR 검증 패턴 — 되풀이되는 종류에 관측법을 붙인다. 열쇠말로 매칭하고, 어느
#: 패턴에도 안 걸리면 unmapped로 남긴다(deploy_checks._HOW와 같은 결).
_NFR_PATTERNS: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    ("latency", ("second", "within", "respond", "response time", "ms", "latency"),
     "재현 부하 없이 대표 엔드포인트로 요청해 응답 시간을 잰다",
     "명시된 시한 안에 응답한다(반복 측정, 캐시 웜업 후)"),
    ("survival", ("year", "retain", "kept", "survive", "redeploy", "persist",
                  "durable", "backup"),
     "데이터를 쓰고 → 재배포(또는 파드 재생성) → 다시 읽는다",
     "재배포 뒤에도 앞서 쓴 데이터가 남아 있다 — 안 남으면 이 요구가 깨진다"),
    ("isolation", ("must not prevent", "keep working", "without", "fail",
                   "isolat", "degrad", "continue"),
     "지목된 부분(예: 사진 처리 워커)을 죽이고 → 나머지 흐름을 시도한다",
     "일부가 죽어도 핵심 흐름(예: 제출)이 계속된다"),
    ("reachability", ("network", "mobile", "reach", "public", "internet"),
     "밖에서 배포된 진입점으로 도달을 시도한다(deploy_checks inbound-tcp와 짝)",
     "명시된 경로에서 도달된다"),
)


def _match_operation(req_text: str, operations: list[dict]) -> dict | None:
    """요구문과 가장 많이 겹치는 연산. 겹침이 얕으면(<2) 매핑 안 한다."""
    rt = _tokens(req_text)
    best, best_score = None, 0
    for op in operations:
        score = len(rt & op["tokens"])
        if score > best_score:
            best, best_score = op, score
    return best if best_score >= 2 else None


def _operations(openapi: dict) -> list[dict]:
    out: list[dict] = []
    for path, methods in (openapi.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if not isinstance(op, dict):
                continue
            summary = op.get("summary") or op.get("operationId") or ""
            out.append({
                "method": method.upper(), "path": path, "summary": summary,
                "tokens": _tokens(f"{summary} {op.get('operationId','')} {path}"),
            })
    return out


def _nfr_check(rid: str, text: str) -> AcceptanceCheck | None:
    """요구문을 NFR 검증 패턴에 매칭. 안 걸리면 None."""
    rt_low = text.lower()
    for name, keys, how, passes in _NFR_PATTERNS:
        if any(k in rt_low for k in keys):
            return AcceptanceCheck(rid, text, "nfr",
                                   how=f"[{name}] {how}", passes=passes)
    return None


def derive(requirements: list[dict], openapi: dict) -> list[AcceptanceCheck]:
    """요구사항(FR·NFR) + OpenAPI → 수용 검사 목록. **요구가 잣대다.**

    분류(FR/NFR)를 신뢰하지 않고 **내용으로** 판정한다 — 요구사항 에이전트가
    NFR을 'shall' FR로 바꿔 라벨을 FR로 붙이는 일이 실측됐다(field-report는
    20개가 전부 FR로 분류됨). 그래서 순서는: ① 엔드포인트에 매핑되면 기능 검사,
    ② 아니면 NFR 패턴(생존·격리·지연) 시도, ③ 그것도 아니면 unmapped.

    Args:
        requirements: `classified.json` — {id, type, text}의 목록.
        openapi: 그 시스템이 낸 `api_spec.json`(엔드포인트 발견용).
    """
    operations = _operations(openapi)
    checks: list[AcceptanceCheck] = []
    for req in requirements:
        rid = str(req.get("id", ""))
        text = req.get("text") or req.get("description") or ""
        rtype = (req.get("type") or req.get("category") or "").upper()

        # NFR로 명시된 것은 엔드포인트가 아니라 검증 패턴부터.
        if rtype.startswith("NFR"):
            nfr = _nfr_check(rid, text)
            checks.append(nfr or AcceptanceCheck(
                rid, text, "nfr",
                unmapped="이 NFR에 맞는 검증 패턴이 없다 — 사람이 정해야 한다"))
            continue

        # ① 엔드포인트 매핑
        op = _match_operation(text, operations)
        if op is not None:
            checks.append(AcceptanceCheck(
                rid, text, "functional",
                endpoint=(op["method"], op["path"]), operation=op["summary"],
                how=f"{op['method']} {op['path']}로 이 행위를 시도한다",
                passes="2xx로 응답하고 요구된 결과를 낸다(스텁·미구현이면 여기서 "
                       "드러난다)"))
            continue
        # ② 엔드포인트가 없으면 NFR 패턴(오분류된 NFR 잡기)
        nfr = _nfr_check(rid, text)
        if nfr is not None:
            checks.append(nfr)
            continue
        # ③ 아무데도 안 걸리면 지어내지 않고 남긴다
        checks.append(AcceptanceCheck(
            rid, text, "functional",
            unmapped="엔드포인트에도 NFR 패턴에도 안 걸린다 — 구현이 이 행위를 "
                     "안 냈거나(스텁) 검증 방법을 사람이 정해야 한다"))
    return checks


def coverage(checks: list[AcceptanceCheck]) -> dict[str, int]:
    """매핑 커버리지 — 침묵을 수치로. unmapped가 곧 '구현이 못 닿은 요구'다."""
    return {
        "total": len(checks),
        "mapped": sum(1 for c in checks if not c.unmapped),
        "unmapped": sum(1 for c in checks if c.unmapped),
        "functional": sum(1 for c in checks if c.kind == "functional"),
        "nfr": sum(1 for c in checks if c.kind == "nfr"),
    }


def run_functional(base_url: str, checks: list[AcceptanceCheck],
                   *, timeout: float = 5.0) -> list[dict]:
    """기능 검사를 살아 있는 앱에 **블랙박스 HTTP**로 돌린다.

    2xx/4xx/5xx와 응답 시간만 본다 — 어느 시스템이 만든 앱이든 같은 잣대다.
    NFR(생존·격리)은 오케스트레이션이 필요해 여기서 안 돈다(점검표로 남는다).
    바디·인증이 필요한 엔드포인트는 스텁 호출이라 4xx가 날 수 있고, 그 사실을
    그대로 적는다 — '동작 검증'이 아니라 '엔드포인트가 서 있고 응답하는가'다.
    """
    import time
    import urllib.error
    import urllib.request

    results: list[dict] = []
    for c in checks:
        if c.kind != "functional" or c.endpoint is None:
            continue
        method, path = c.endpoint
        url = base_url.rstrip("/") + path
        started = time.monotonic()
        status: int | str
        try:
            req = urllib.request.Request(url, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        except Exception as exc:  # noqa: BLE001 — 못 닿음도 결과다
            status = f"{type(exc).__name__}"
        results.append({
            "requirementId": c.requirement_id,
            "endpoint": f"{method} {path}",
            "status": status,
            "ms": round((time.monotonic() - started) * 1000, 1),
        })
    return results
