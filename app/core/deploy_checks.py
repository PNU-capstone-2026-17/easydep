"""배포 후 검증 스위트 — **컨트롤 플레인이 안 막는 것을 잡는 유일한 자리.**

## 왜 이 단계가 있나

과제 문제 ③이 *"요구사항 분석, 설계, 구현, **테스트**로 이어지는"* 사슬을
요구하는데 테스트 단계 산출물이 없었다. 그런데 재료는 이미 있다 — **기능 결속
14건**이다.

기능 축은 다른 두 축과 오라클이 다르다. 존재·생명주기는 컨트롤 플레인이
거부해 주지만, 기능 결속은 **막지 않는다**(우리가 `무방비:`로 적어 온 지대).
`vm`에서 `publicIp`를 떼면 API는 아무 말도 없고 서비스만 죽는다.

그래서 그 결속은 **apply 전 검사로는 영영 안 잡히고, 배포 후 검증 말고는 잡을
방법이 없다.** 이 모듈이 그 검증을 계획에서 뽑아낸다.

## 무엇을 내는가 — **실행기가 아니라 점검표**

검사의 대상 주소(공인 IP·클러스터 이름 …)는 apply **뒤에야** 정해진다. 그래서
여기서는 돌아가는 코드를 내지 않고, **무엇을 · 어디서 · 무엇으로 확인하는가**를
낸다. 실행은 하류(또는 사람)의 몫이고, 그것이 이 저장소가 계획층에서 지켜 온
경계이기도 하다.

각 점검은 자기를 낳은 **주장의 좌표를 들고 다닌다** — 왜 이걸 확인해야 하는지가
"우리가 그렇게 생각해서"가 아니라 실측이라야 한다.

## 신호는 주장이 나른다

무엇으로 재는지는 `claims.json`의 `signal`이 말한다(실측을 기록한 자리에서
선언된다). 여기서 술어 산문을 파싱하지 않는다 — 그러면 규칙 사본이 둘이 된다.

**신호를 늘리려면 `build_claims.SIGNALS`와 여기 `_HOW`를 함께 고쳐야 한다.**
한쪽만 늘면 주장은 있는데 점검이 안 나오고, 그 침묵은 "확인할 것이 없다"로
읽힌다. 테스트가 두 목록의 일치를 지킨다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_CLAIMS = (Path(__file__).resolve().parent / "cloudkb" / "depkb" / "claims.json")


@dataclass(frozen=True)
class Check:
    """배포 후 확인할 것 하나. **근거를 들고 다닌다.**"""

    #: 신호 id(`claims.json`의 `signal`).
    signal: str
    #: 어디서 실행하나 — `outside`(우리 쪽) · `guest`(그 VM 안) · `cluster`(클러스터 안).
    where: str
    #: 무엇을 확인하나 — 사람이 읽는 한 줄.
    what: str
    #: 어떻게 — 실제로 돌릴 것에 가장 가까운 형태. **대상 주소는 apply 뒤에 채운다.**
    how: str
    #: 통과 조건.
    passes: str
    #: 이 점검을 낳은 결속 — `(csp, subject, object)`.
    because: tuple[str, str, str]
    #: 그 주장의 실험 좌표. **왜 이걸 확인하나**에 답하는 자리다.
    evidence: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"signal": self.signal, "where": self.where, "what": self.what,
                "how": self.how, "passes": self.passes,
                "because": {"csp": self.because[0], "subject": self.because[1],
                            "object": self.because[2]},
                "evidence": list(self.evidence)}


#: 신호 → (어디서, 무엇을, 어떻게, 통과 조건). **실험이 실제로 쓴 방법을 옮긴다.**
#:
#: `<대상>` 자리는 apply 뒤에 채운다 — 계획층은 주소를 모른다. 그 사실을 숨기지
#: 않으려고 자리표시자를 그대로 남긴다.
_HOW: dict[str, tuple[str, str, str, str]] = {
    "inbound-tcp": (
        "outside",
        "밖에서 이 서버로 들어갈 수 있는가",
        "TCP 연결: <공인 주소>:22 (실험은 SSH 포트로 쟀다)",
        "연결이 선다. **연속 확인**이 필요하다 — 실험에서 한 번의 성공/실패가 "
        "전파 지연과 구별되지 않았다"),
    "egress-https": (
        "guest",
        "이 서버가 밖으로 나갈 수 있는가",
        "게스트에서 외부 HTTPS 요청(예: `curl -sS https://…`)",
        "응답이 온다. 인바운드와 **다른 방향**이라 따로 확인한다"),
    "lb-serving": (
        "outside",
        "로드밸런서를 통해 백엔드가 응답하는가",
        "LB 프런트엔드 주소로 HTTP GET",
        "200이 온다. **백엔드 접근 로그로 교차 확인**할 것 — 실험에서 프런트엔드 "
        "응답만으로는 어느 백엔드가 받았는지 못 갈랐다"),
    "dns-resolution": (
        "guest",
        "이름이 풀리는가",
        "게스트에서 `getent hosts <이름>`",
        "주소가 나온다. **캐시를 비우고 확인할 것** — 실험에서 TTL 3600과 "
        "게스트 캐시가 상실을 3시간 가렸다(TTL을 낮추고 `resolvectl "
        "flush-caches`를 썼다)"),
    "service-discovery": (
        "cluster",
        "클러스터 안에서 서비스 이름으로 서로를 찾는가",
        "클러스터 안 파드에서 `http://<서비스 이름>` 요청",
        "응답이 온다. **파드가 Running인 것과 다른 사실이다** — 실험에서 "
        "워크로드는 살아 있는데 이름만 죽었다"),
    "volume-write": (
        "guest",
        "붙인 디스크에 실제로 써지는가",
        "게스트에서 `dd oflag=direct`로 쓰기",
        "쓰기가 성공한다. **`oflag=direct`가 필요하다** — 페이지 캐시가 상실을 "
        "가린다. 그리고 **파이프를 쓰지 말 것**(실험에서 `| tail`이 종료 코드를 "
        "삼켜 I/O 오류가 성공으로 보였다)"),
    "imds-credentials": (
        "guest",
        "이 서버가 클라우드 API 자격증명을 얻는가",
        "게스트에서 인스턴스 메타데이터 질의(aws는 IMDSv2 토큰 → 역할 조회)",
        "역할 이름과 자격증명이 온다"),
}


@lru_cache(maxsize=1)
def _function_claims() -> tuple[dict, ...]:
    doc = json.loads(_CLAIMS.read_text(encoding="utf-8"))
    return tuple(c for c in doc["claims"] if c["question"] == "function")


def signals() -> frozenset[str]:
    """주장에 실제로 실린 신호들 — 두 목록의 일치를 테스트가 이걸로 본다."""
    return frozenset(c["signal"] for c in _function_claims() if c.get("signal"))


def checks_for(csp: str, resources: set[str] | frozenset[str]) -> tuple[Check, ...]:
    """이 구성에 대해 배포 후 확인할 것.

    **계획에 그려진 자원에 걸리는 결속만** 낸다. 안 놓는 자원의 점검을 내면
    실행하는 사람이 그것을 찾다가 시간을 버리고, 목록이 길어질수록 신뢰가
    떨어진다.

    Args:
        csp: 주장이 CSP로 색인돼 있어 필수다.
        resources: 계획이 놓는 자원의 우리 어휘 이름들.
    """
    out: list[Check] = []
    seen: set[tuple] = set()
    for claim in _function_claims():
        if claim["csp"] != csp:
            continue
        if claim["subject"] not in resources and claim["object"] not in resources:
            continue
        signal = claim.get("signal") or ""
        recipe = _HOW.get(signal)
        if recipe is None:
            # **모르는 신호를 조용히 버리지 않는다.** 주장은 있는데 점검이
            # 없으면 그 침묵이 "확인할 것 없음"으로 읽힌다.
            out.append(Check(
                signal=signal or "(없음)", where="unknown",
                what=f'{claim["subject"]}→{claim["object"]}의 결속을 확인해야 하나 '
                     "이 신호에 대한 점검 방법이 없다",
                how="(미정 — deploy_checks._HOW에 추가해야 한다)",
                passes="(미정)",
                because=(claim["csp"], claim["subject"], claim["object"])))
            continue
        key = (signal, claim["subject"], claim["object"])
        if key in seen:
            continue   # 같은 쌍을 두 신호로 쟀어도 사람에게 할 말은 하나다
        seen.add(key)
        where, what, how, passes = recipe
        out.append(Check(
            signal=signal, where=where,
            what=f'{what} — {claim["object"]}를 떼면 {claim["subject"]}가 깨진다',
            how=how, passes=passes,
            because=(claim["csp"], claim["subject"], claim["object"]),
            evidence=tuple(
                f'{e.get("experiment", "")}/{e.get("step", "")}'
                for e in claim["evidence"] if e.get("experiment"))))
    return tuple(out)


def build(csp: str, resources: set[str] | frozenset[str]) -> dict:
    """검증 산출물 한 벌."""
    found = checks_for(csp, resources)
    return {
        "schemaVersion": "easydep-deploy-checks/v1alpha1",
        "csp": csp,
        "checks": [c.as_dict() for c in found],
        "_why": ("기능 결속은 **컨트롤 플레인이 막지 않는다** — apply는 성공하는데 "
                 "서비스가 죽는 지대다. 생성·삭제 검사로는 안 잡히므로 배포 후 "
                 "확인 말고는 방법이 없다"),
        "_scope": ("계획에 그려진 자원에 걸리는 결속만 낸다. **여기 없는 것이 "
                   "'문제없다'는 뜻은 아니다** — 기능 축의 커버리지가 축마다 "
                   "고르지 않다(신호 7종 × 3사 18칸 중 9칸이 빔)"),
        "_provenance": ("app/core/deploy_checks.build — 신호는 claims.json이 "
                        "나르고 점검 방법은 실험이 실제로 쓴 것이다"),
    }
