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
    #: 이 측정을 그르칠 수 있는 것들 — **실험이 실제로 물린 함정**(`_PITFALLS`).
    pitfalls: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"signal": self.signal, "where": self.where, "what": self.what,
                "how": self.how, "passes": self.passes,
                "because": {"csp": self.because[0], "subject": self.because[1],
                            "object": self.because[2]},
                "evidence": list(self.evidence),
                "pitfalls": list(self.pitfalls)}


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
    "node-join": (
        "outside",
        "새 노드가 클러스터에 합류하는가",
        "노드그룹 생성 후 describe-nodegroup을 **터미널 상태까지** 폴링",
        "ACTIVE가 된다. **API 수락(CREATING)은 증거가 아니다** — 실험 1차가 "
        "수락을 보고 판정하려다 원인을 못 갈랐고, join 실패는 CREATE_FAILED의 "
        "health(NodeCreationFailure)로만 드러난다"),
}

#: 신호별 함정 — **실험이 실제로 물린 것들의 사영**(클린룸 배터리가 note에서
#: 재도출·검증, 2026-08-02). 일반 지식 추정은 여기 없다 — 실측 좌표가 note에
#: 있는 것만 싣는다. 함정 없는 신호는 함정이 없다는 뜻이 아니라 **안 물렸다**는
#: 뜻이다.
_PITFALLS: dict[str, tuple[str, ...]] = {
    "inbound-tcp": (
        "대상의 공인 주소를 매번 API로 재조회할 것 — gcp 임시 IP는 재부여 시 "
        "새 주소가 온다(34.64.142.22→34.22.74.114 실측). 옛 주소로 찍으면 회복을 "
        "영영 못 본다",
        "azure Standard PIP는 NSG가 없으면 인바운드가 기본 차단 — '안 닿는다'가 "
        "결속 상실이 아니라 secure-by-default일 수 있다(NSG 부착 전 7회 실패 실측)",
        "확인 대상 자원 식별자를 매번 검증할 것 — 실험에서 결과 파일 경합 여파로 "
        "다른 VPC의 라우트를 지워 측정이 통째로 무효가 됐다(Z0b)",
        "기준선은 직전 회복 확인 위에 세울 것 — 앞 셀의 여파가 원인에 섞인다",
        "상실 판정 전에 대상이 여전히 실행 중인지 교차 확인할 것(VM이 죽어 안 "
        "닿는 것과 결속 상실은 다르다 — 실험은 매번 vm-still-running을 뒀다)",
        "합성 신호다 — IP·방화벽·라우트 중 무엇이 죽었는지는 단독으로 못 가른다. "
        "실험은 배치로 격리했고(자동 공인 IP 없이 기동), 점검은 실패 시 계층별 "
        "API 상태 조회를 곁들여야 한다",
    ),
    "egress-https": (
        "인바운드 통과는 아웃바운드를 보증하지 않는다 — 같은 간선을 방향 다른 "
        "신호로 따로 재확인한 것이 실측이다",
    ),
    "imds-credentials": (
        "'VM이 잘 떠 있다'는 어떤 점검도 이 상실을 못 본다 — 존재는 optional인데 "
        "기능은 결속이다(프로필 없이도 VM은 선다, rc=22 실측)",
        "상실은 상위 계층 장애로 증폭되어 나타난다(EKS CSI 'no EC2 IMDS role "
        "found') — 상위 증상만 보면 원인 계층을 놓친다. VM 층에서 격리해 잰다",
    ),
    "dns-resolution": (
        "레코드 존재와 영역-네트워크 연결은 다른 간선이다 — 레코드가 있는데 안 "
        "풀리면 link 쪽, 레코드가 없으면 다른 간선의 문제로 갈라 읽는다",
    ),
    "lb-serving": (
        "azure는 서브넷 NSG와 NIC NSG를 **둘 다** 통과해야 한다 — VM 생성 도구가 "
        "NIC에 몰래 붙인 NSG(ssh만 허용)가 서브넷에서 연 80을 막아 기준선 자체가 "
        "안 선 것이 1차 미판정의 원인이었다",
        "판정 경로와 관리(진단) 경로를 분리할 것 — LB 인바운드 NAT로 관리 접근을 "
        "빼서 게스트 진단 경로를 확보한 것이 원인 규명의 열쇠였다(Z1)",
        "존재 축 점검(자원이 다 있는가)으로는 이 결속이 아예 안 보인다 — LB는 "
        "백엔드 없이도 만들어진다",
    ),
    "volume-write": (
        "재부착 후 회복에 게스트 재마운트가 필요한지는 실측이 말하지 않는다 — "
        "회복 실패와 게스트 조치 미수행을 가르지 말고 관측 그대로 적을 것",
    ),
    "service-discovery": (
        "Pod 상태 점검은 이 상실을 못 본다 — Pod는 Running인 채로 이름만 죽는다",
        "관측자는 도구여야 한다(agnhost 수준) — 앱을 판정 기준으로 삼지 않는다",
        "gcp에서만 실측됐다 — 다른 CSP 관리형 k8s에서의 통용은 예상이지 판정이 "
        "아니다",
    ),
    "node-join": (
        "기존 노드그룹은 정책이 떨어져도 ACTIVE·이슈 0으로 계속 돈다(O1) — "
        "**카나리아(새 노드그룹 생성) 없이는 이 상실이 잠복한다.** 수동 점검은 "
        "전부 통과한다",
        "인스턴스 기동 성공을 join 성공으로 오판하지 말 것 — 상실 지점은 EC2 "
        "기동이 아니라 컨트롤 플레인의 노드 수용이다",
        "판정 사이 정책이 되돌아갔을 수 있다 — 변이 지속을 list-attached로 "
        "실증해야 인과가 닫힌다(F1c)",
        "클러스터 역할과 노드 역할을 혼동하지 말 것 — 이 신호가 재는 것은 "
        "클러스터 역할이다(EC2 기동은 노드 역할·서비스 연결 역할의 몫)",
    ),
}

#: 점검 스위트가 **못 덮는 지점** — 침묵이 "다 덮었다"로 읽히지 않게 명시한다
#: (클린룸 배터리의 gaps, 실측 근거 있는 것만).
GAPS: tuple[str, ...] = (
    "inbound-tcp는 합성 신호라 실패의 원인 계층(IP·방화벽·라우트, azure는 "
    "서브넷/NIC 이중 NSG)을 단독으로 못 가른다 — 실패 시 계층별 API 상태 조회의 "
    "2차 진단이 필요하다",
    "node-join 상실은 카나리아 없이는 관측 불가능한 잠복 상실이다 — 스케일아웃· "
    "노드 교체가 일어나는 순간까지 어떤 수동 점검도 통과한다(O1 실측)",
    "egress 방향은 aws subnet→IGW에서만 실측됐다 — 인바운드 통과를 아웃바운드 "
    "보증으로 확장할 수 없고, azure는 대응 자원이 시스템 라우트라 신호 설계가 "
    "별도로 필요하다",
    "dns-resolution·service-discovery의 통과는 캐시 잔존일 수 있다 — TTL을 "
    "통제할 수 없는 환경에서는 통과의 신뢰가 TTL만큼 깎인다(TTL 3600이 상실을 "
    "3시간 가린 실측)",
    "게스트 안 신호들(egress·imds·volume-write·dns)은 게스트 진입 경로가 살아 "
    "있어야 잴 수 있다 — 진입 경로가 판정 대상 결속과 겹치면 점검이 마비된다. "
    "판정 경로와 진단 경로의 분리(Z1)가 스위트 전체의 전제다",
)


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
                for e in claim["evidence"] if e.get("experiment")),
            pitfalls=_PITFALLS.get(signal, ())))
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
