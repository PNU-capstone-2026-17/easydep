"""어휘 판정 — 어느 자원을 어느 **역할**로 들이고, 무엇을 왜 빼는가.

## 왜 문서가 아니라 코드인가

이 판정은 대화와 문서에만 있으면 반드시 뒤처진다(`field_map.py`가 같은 이유로 코드로
왔다). 그리고 **전수성**이 필요하다 — 조사가 찾아낸 자원 중 판정이 안 붙은 것이 있으면
그건 "빼기로 했다"가 아니라 **잊은 것**이고, 둘은 구별되어야 한다. 아래 표에 없는
자원이 산출물에 나타나면 테스트가 죽는다.

## 판정식

계약이 스스로 적어 둔 것을 자원 어휘로 확장한 것이다.

    그 칸이 없으면 뒤 단계 산출물의 요구사항 부합을 잴 수 없는 것만 필수다.
    그리고 모든 칸에는 소비자가 있어야 한다.

자원에 대해 물으면: **어느 단계의 어느 판정과 1:1로 묶이는가.** 묶이지 않으면 뺀다.

## 역할이 셋인 이유

*"성능·비용으로 추천 가능한가"*를 **포함 자격**으로 쓰면 vNet·subnet·securityGroup까지
탈락한다(실제로 낸 오류다). 추천 가능성은 포함 자격이 아니라 **선택 역할 자원에 붙는
서비스 수준**이다. 역할은 산출물에서 어떻게 등장하는가와 같은 말이다.

근거는 `docs/cloud-native-extension.md` §4~5.
"""

from __future__ import annotations

from dataclasses import dataclass

#: IaC가 **만드는 자원** · 다이어그램의 노드·아티팩트로 등장한다.
COMPOSE = "compose"
#: 다른 자원의 **속성**으로 등장한다. 만드는 것이 아니라 고르는 것.
#:
#: 근거가 둘이다. (가) `core/infra/control.go:1302` — 노드 삭제가 다른 자원에는 참조
#: 카운트를 되돌리는데 `spec`만 주석으로 꺼져 있다. (나) TOSCA에서 `num_cpus`·
#: `mem_size` 같은 것은 관계가 아니라 노드의 `host` **capability 속성**이다.
SELECT = "select"

#: 역할은 **분류하는 자원이 있을 때만** 존재한다.
#:
#: 이전 판에 `BOUND`("만들지 않지만 판정에 쓰인다 — 리전·존·쿼터")를 뒀는데 **해당하는
#: 자원이 하나도 없었다.** 쓰이지 않는 칸은 분류가 아니라 우리가 만든 자리이고, 그런
#: 것이 남아 있으면 나중에 사실처럼 인용된다. 실제로 그 일이 반복됐다. 필요해지면
#: **그때 해당 자원과 함께** 추가한다 — `test_scope.py`가 빈 역할을 막는다.
ROLES = (COMPOSE, SELECT)


@dataclass(frozen=True)
class Decision:
    """자원 하나에 대한 판정."""

    role: str | None          #: 들이는 경우의 역할. 빼면 None.
    why: str                  #: 들이는 이유 = 어느 판정과 묶이나 / 빼는 이유
    conditional: bool = False #: 구성에 따라 등장한다(항상은 아니다)
    reversible: str = ""      #: 빼는 판정이 뒤집힐 조건. 비면 구조적 제외다.


def _in(role: str, why: str, *, conditional: bool = False) -> Decision:
    return Decision(role=role, why=why, conditional=conditional)


def _out(why: str, *, reversible: str = "") -> Decision:
    return Decision(role=None, why=why, reversible=reversible)


#: 자원 → 판정. **전수여야 한다** — 조사 산출물의 모든 자원이 여기 있어야 한다.
SCOPE: dict[str, Decision] = {
    # ── 들인다: 구성 ────────────────────────────────────────────────────
    "vNet": _in(COMPOSE, "네트워크의 뿌리. 없으면 어떤 IaC도 성립하지 않는다"),
    "subnet": _in(COMPOSE, "노드의 주소가 여기서 나온다. 배치(AZ) 판정이 걸리는 자리"),
    "securityGroup": _in(
        COMPOSE, "하류 intent의 `networkPolicy`가 이것을 요구한다 — 계획의 엣지가 곧 허용 흐름"),
    "sshKey": _in(COMPOSE, "노드 생성의 필수 참조이고 실제로 만들어지는 자원이다"),
    "infra": _in(COMPOSE, "노드를 담는 그룹. 다이어그램의 경계 상자가 이것이다"),
    "node": _in(COMPOSE, "컴퓨트 그 자체. 비용·성능 판정이 전부 여기 붙는다"),
    "nodeGroup": _in(
        COMPOSE, "대수가 붙는 단위. **대수는 노드가 아니라 실행 환경의 성질**이라 이 층이 필요하다"),
    "k8sCluster": _in(
        COMPOSE,
        "`infra`와 평행한 최상위 그룹 추상이다(cb-tumblebug 자원 모델의 진술). "
        "추천 대상이 아닌 것은 `infra`와 같고, 스펙 축은 `k8sNodeGroup`이 받는다",
        conditional=True),
    "k8sNodeGroup": _in(
        COMPOSE, "컨테이너 배포에서 스펙·이미지 판정이 붙는 자리", conditional=True),
    "nlb": _in(COMPOSE, "하류 intent의 `ingress`에 대응한다 — 외부 노출 시", conditional=True),
    "dataDisk": _in(COMPOSE, "하류 intent의 `pvc`에 대응한다 — 영속이 필요할 때", conditional=True),
    "vpn": _in(
        COMPOSE,
        "멀티클라우드·온프렘 연결이 요구사항에 있을 때. azure는 전용 게이트웨이 서브넷을 "
        "요구해 간선이 하나 더 생긴다",
        conditional=True),

    # ── 들인다: 선택 ────────────────────────────────────────────────────
    "spec": _in(SELECT, "노드의 속성으로 들어간다(`instance_type`). 비용·성능 추천의 대상"),
    "image": _in(SELECT, "노드의 속성으로 들어간다(`ami`). 스펙과 짝으로 판정된다"),

    # ── 뺀다 ────────────────────────────────────────────────────────────
    "sqlDb": _out(
        "**판정 1:1이 안 선다.** 미러 스펙 138,115건 중 `db.*` 클래스가 0건이라 비용·성능을 "
        "못 재고 제약만 남는다. 그리고 cb-tumblebug이 이것을 넣은 이유는 '프로비저닝하고 "
        "생명주기를 추적한다'인데 **우리는 계획을 만들지 운영하지 않는다** — 그들의 포함 "
        "근거가 우리에게 성립하지 않는다",
        reversible="DB 인스턴스 클래스 카탈로그(스펙+단가)를 인용 가능한 형태로 확보하면. "
                   "지금은 관리형 가격 API에만 있고 그건 재배포 금지라 지웠다"),
    "objectStorage": _out(
        "**구조적으로 추천 축이 없다** — 버킷은 고를 스펙이 없다. 데이터 부재가 아니라 "
        "선택지가 존재하지 않는 것이라 재검토 대상이 아니다"),
    "customImage": _out(
        "빌드 산출물이지 배포 대상이 아니다. 다이어그램에서 아티팩트로 그려질 수는 있으나 "
        "IaC가 만드는 자원의 자리가 아니다"),
    "publicIp": _out("대개 노드 생성에 암묵적으로 딸려온다 — 하류가 별도로 요구하지 않는다"),
    "vNic": _out("같음. 노드에 암묵적으로 붙는다"),
    "globalDns": _out(
        "지금 하류(deployment intent 19칸)에 DNS 필드가 없다",
        reversible="**과적합 위험 구간이다.** DNS를 다루는 소비자가 생기면 즉시 재검토한다 "
                   "— 지금 빠진 이유가 '필요 없어서'가 아니라 '그 하류가 안 다뤄서'다"),
    "fileSystem": _out(
        "cb-spider 드라이버 계약에는 있으나 cb-tumblebug이 노출하지 않는다 — 배포 경로가 없다",
        reversible="cb-tumblebug이 노출하면"),
}


def decision_of(resource: str) -> Decision:
    try:
        return SCOPE[resource]
    except KeyError:
        raise KeyError(
            f"판정이 없는 자원이다: {resource}. 빼기로 한 것과 잊은 것은 다르므로 "
            f"`SCOPE`에 사유와 함께 넣어야 한다."
        ) from None


def in_scope() -> tuple[str, ...]:
    return tuple(name for name, d in SCOPE.items() if d.role is not None)


def by_role(role: str) -> tuple[str, ...]:
    return tuple(name for name, d in SCOPE.items() if d.role == role)
