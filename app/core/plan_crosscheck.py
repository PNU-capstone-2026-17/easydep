"""배포 계획 ↔ 3사 실측 대조 — **끊긴 자리를 세는 자**.

## 왜 있나

이 저장소에는 계획을 만드는 경로가 **둘**이고 서로를 모른다(실측 2026-08-01):

    설계 산출물 ──design_tools.compose──▶ 배포 계획(노드·선)
                                              ✂ 끊김
    3사 실측 118주장 ──depkb.closure──▶ 순서·금지·검사·경고·대기

앱의 특성은 이미 앞쪽으로 흐른다 — OpenAPI가 있으면 인바운드 노출, ER 엔티티를
소유하면 영속 저장소, actor가 부르면 공개 노출(`design_tools` 모듈 문서의 추론
규칙 일곱). **그런데 그 계획이 실측을 한 번도 만나지 않는다.**

이 모듈은 그 틈을 **세는 것**이지 메우는 것이 아니다. 나온 목록이 곧 배선의
명세다 — 무엇을 이어야 하는지를 추측이 아니라 관측으로 정하려는 것이다.

## 대조는 어휘 결속 위에서만 한다

계획 노드는 벤더 타입(`aws::AWS::EC2::VPC`)을 달고 있고 주장은 우리 어휘
(`network`)를 쓴다. 그 사이 다리는 **이미 있다** — `depkb.vocabulary.AWS_TYPES`는
CFN 스펙 원문에 결속돼 테스트가 지킨다. 여기서 손으로 표를 만들지 않는 이유가
그것이다.

다만 결속은 9종뿐이고 주장에 나오는 자원은 24종이다. **결속 없는 것은 대조하지
않고 그렇다고 적는다**(`out-of-vocabulary`) — 못 본 것과 문제없는 것을 섞지
않는다.

## 판정하지 않는 것

검사 규칙(`Constraint.rule`)은 산문이다(`"ALB는 서로 다른 AZ의 서브넷 ≥2"`).
기계가 위반을 단정하려면 규칙을 여기 다시 코드로 적어야 하는데, 그러면 사본이
둘이 된다. 그래서 **규칙과 계획의 관측 사실을 나란히 내고 판정은 사람에게
넘긴다** — 이 저장소가 `dataResidency`에 쓰는 것과 같은 규율이다.

표본에 걸어 보는 것은 하네스다: `python -m app.core.cloudkb.tools.crosscheck_sample <표본>`.
**이 모듈은 하네스를 부르지 않는다** — 라이브러리가 하네스를 타면 예외가 구멍이
된다(`tests/test_core_layer.py`).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache

from app.core import input_registry
from app.core.cloudkb.appkb.plan import DeploymentPlan
from app.core.cloudkb.depkb import vocabulary
from app.core.infra_planning import plan_for_anchors

@lru_cache(maxsize=1)
def _bridge() -> dict[str, dict[str, str]]:
    """CSP → (계획의 벤더 타입 → 우리 어휘). **표를 손으로 만들지 않는다.**

    다리를 놓는 데 두 축이 이미 있고, 둘은 따로 지어졌다:

      `depkb.vocabulary.AWS_TYPES`  자원 → CFN 타입. **스키마 원문에 결속**돼
                                    테스트가 지킨다(측정된 사실).
      `graphkb` 동치 그래프          벤더 타입끼리의 대응. cb-spider 드라이버를
                                    읽어 사람이 맞춘 것(짐작·검수됨).

    앞의 것을 **지렛대**로 삼는다: `network → aws::AWS::EC2::VPC`를 그래프에
    넣으면 `core::vNet`이 나오고, 거기서 gcp·azure 타입이 따라 나온다. 그래서
    3사 다리가 **자동으로** 생기고, 우리가 적은 칸은 하나도 없다.

    aws에서만 이름 체계가 겹치는 것이 이 우회의 이유다 — 계획은 gcp에
    `gcp::ComputeNetwork`를 쓰는데 `vocabulary.GCP_TYPES`는 디스커버리 스키마
    이름 `Network`를 쓴다. 둘은 같은 것을 다르게 부르고, 그 사이는 그래프가
    안다.

    그래프를 못 읽으면 aws만 남는다 — **없는 다리를 지어내지 않는다.**
    """
    out: dict[str, dict[str, str]] = {
        "aws": {v: k for k, v in vocabulary.AWS_TYPES.items()}}
    try:
        from app.core.cloudkb.graphkb.agent_api import load_merged
        from app.core.cloudkb.graphkb.query import equivalents

        graph = load_merged()
    except Exception:  # noqa: BLE001 — 그래프가 없으면 aws만 대조한다
        return out
    if graph is None:
        return out
    for resource, aws_type in vocabulary.AWS_TYPES.items():
        node = f"aws::{aws_type}"
        if node not in graph.nodes:
            continue
        for peer in equivalents(graph, node):
            if peer.provider and peer.provider != "aws":
                out.setdefault(peer.provider, {}).setdefault(
                    peer.id.split("::", 1)[1], resource)
    return out

#: 컴퓨트 노드의 실행 방식 → 우리 어휘. **여기만 벤더 타입이 아니라 표시
#: 문자열을 읽는다.**
#:
#: 계획은 컴퓨트 노드에 벤더 타입을 달지 않는다 — `PlanNode.host`에
#: `"VM"`·`"Kubernetes node"`·`"Serverless runtime"` 같은 **사람이 읽는 이름**만
#: 있고(`design_tools._add_computes`), 값이 붙을 때 스펙 이름이 뒤에 덧붙는다
#: (`"VM · t3.small"`). 그래서 접두사로 읽는다.
#:
#: **취약하다.** 저쪽 문구가 바뀌면 조용히 안 읽힌다 — 그래서 이 경로로 읽을
#: 때마다 `WEAK_READING`을 남긴다. 이건 우리 편의가 아니라 **인터페이스 공백의
#: 관측**이고, 대조기가 세려는 것 중 하나다.
_HOST_READING: dict[str, str] = {
    "VM": "vm",
    "Kubernetes node": "k8sCluster",
    # "Serverless runtime"은 없다 — 서버리스는 실측 범위 밖이라 어휘가 없다.
}

#: 대조 결과의 종류. **"어긋났다"와 "못 봤다"를 가른다** — 섞으면 목록이
#: 길어질수록 신뢰가 떨어진다.
DOUBLE_CREATE = "double-create"          # 실측: 서버가 **대신 만든다** / 계획: 또 그림
REDUNDANT_NODE = "redundant-node"        # 실측: 안 정하면 기본값 / 계획: 정했다(정상일 수 있다)
MISSING_REQUIRED = "missing-required"    # 실측: 필수 / 계획: 없음
UNCHECKED_RULE = "unchecked-rule"        # 실측 검사가 계획에 적용된 흔적 없음
ABSENT_ORDER = "absent-order"            # 순서를 담을 자리가 계획에 없음
ABSENT_WARNING = "absent-warning"        # 기능 결속 경고가 계획에 안 실림
ABSENT_WAIT = "absent-wait"              # 완료 대기가 계획에 안 실림
OUT_OF_VOCABULARY = "out-of-vocabulary"  # **대조 불가** — 결속이 없다
WEAK_READING = "weak-reading"            # 표시 문자열로 읽었다 — 저쪽이 바뀌면 끊긴다


@dataclass(frozen=True)
class Finding:
    """대조 결과 하나. `observed`는 계획에서 **본 것**, `measured`는 실측이 아는 것."""

    kind: str
    subject: str
    observed: str
    measured: str

    def line(self) -> str:
        return f"[{self.kind}] {self.subject}\n    계획: {self.observed}\n    실측: {self.measured}"


@dataclass
class Crosscheck:
    csp: str
    #: 계획 노드 → 우리 어휘로 읽힌 것.
    mapped: dict[str, str] = field(default_factory=dict)
    #: 결속이 없어 대조 못 한 노드 → 그 벤더 타입(또는 사유).
    unmapped: dict[str, str] = field(default_factory=dict)
    #: 표시 문자열로 읽은 노드 → 읽은 문자열.
    weak: dict[str, str] = field(default_factory=dict)
    anchors: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()

    def counts(self) -> dict[str, int]:
        return dict(Counter(f.kind for f in self.findings))


def _read_plan(plan: DeploymentPlan, csp: str
               ) -> tuple[dict[str, str], dict[str, str], dict[str, str],
                          dict[str, str]]:
    """계획 노드를 우리 어휘로 읽는다. (읽힘, 못 읽음, 약하게 읽음, 노드별 역할).

    벤더 타입이 있으면 어휘 결속으로 읽고(강함), 컴퓨트 노드는 `host` 표시
    문자열로 읽는다(약함 — `_HOST_READING`).
    """
    table = _bridge().get(csp, {})
    mapped: dict[str, str] = {}
    unmapped: dict[str, str] = {}
    weak: dict[str, str] = {}
    roles: dict[str, str] = {}
    for node in plan.nodes:
        roles[node.id] = node.role
        if node.role == "actor":
            continue  # 사람은 자원이 아니다
        raw = node.type_id or ""
        if raw:
            vendor = raw.split("::", 1)[1] if "::" in raw else raw
            if vendor in table:
                mapped[node.id] = table[vendor]
            else:
                unmapped[node.id] = f"{raw} — 이 타입에 대응하는 실측 어휘가 없다"
            continue
        host = (node.host or "").split(" · ")[0].strip()
        if host in _HOST_READING:
            mapped[node.id] = _HOST_READING[host]
            weak[node.id] = host
        elif host:
            unmapped[node.id] = (
                f"실행 방식 {host!r} — 이 방식에 대응하는 실측 어휘가 없다")
        else:
            unmapped[node.id] = (
                f"벤더 타입도 실행 방식도 없다(role={node.role}) — 계획이 자원 "
                "종류를 정하지 않은 노드다")
    return mapped, unmapped, weak, roles


def crosscheck(plan: DeploymentPlan, csp: str, region: str = "-") -> Crosscheck:
    """계획 하나를 실측에 걸어 본다.

    Args:
        plan: `design_tools.compose`가 낸 배포 계획.
        csp: 계획이 겨눈 프로바이더.
        region: 계획에 실릴 리전(판정에는 안 쓰인다).
    """
    mapped, unmapped, weak, roles = _read_plan(plan, csp)
    result = Crosscheck(csp=csp, mapped=mapped, unmapped=unmapped, weak=weak)
    findings: list[Finding] = []

    for node_id, why in sorted(unmapped.items()):
        findings.append(Finding(
            OUT_OF_VOCABULARY, node_id, why,
            "이 노드에 대해서는 아무 주장도 없다 — **문제없다는 뜻이 아니다**"))
    for node_id, host in sorted(weak.items()):
        findings.append(Finding(
            WEAK_READING, node_id,
            f"실행 방식이 표시 문자열 {host!r}로만 있다 — 벤더 타입이 없다",
            f"{mapped[node_id]}로 읽었다. 저쪽 문구가 바뀌면 이 대조가 조용히 "
            "끊긴다 — 계획이 컴퓨트 노드에 타입을 달면 강한 결속이 된다"))

    drawn = set(mapped.values())
    # **앵커는 "우리가 놓기로 한 워크로드"다.** 계획이 그린 것을 전부 앵커로
    # 삼으면 폐포가 그것들을 "사용자가 고른 것"으로 받아들여, *서버가 대신
    # 만든다*는 주장이 통째로 사라진다(이중 생성 검사가 영영 안 걸린다).
    #
    # 무엇이 워크로드인지는 **계획 자신이 안다** — `PlanNode.role`이 컴퓨트·
    # 인그레스·공유를 이미 가른다. 우리가 새 기준을 만들지 않는다.
    _WORKLOAD_ROLES = ("compute", "ingress")
    anchors = tuple(sorted({
        res for node, res in mapped.items()
        if roles.get(node) in _WORKLOAD_ROLES
        and res in set(input_registry.anchors_for(csp))}))
    result.anchors = anchors
    if not anchors:
        result.findings = tuple(findings)
        return result

    provision = plan_for_anchors(list(anchors), csp, region).provision

    # ① 실측이 필수라는데 계획에 없는 자원. **가장 센 종류다** — 그 계획은
    #    apply가 거부한다(생성 거부 코드가 실측의 오라클이었다).
    for item in provision["createOrder"]:
        if item["required"] and item["id"] not in drawn:
            findings.append(Finding(
                MISSING_REQUIRED, item["id"],
                "이 자원이 계획에 없다",
                f"{csp}에서 {', '.join(anchors)}를 만들려면 필수다(생성 거부로 실측)"))

    # ② 서버가 채우는 자원을 계획이 또 그린 것. **두 부류를 가른다** —
    #    `server-implicit`는 서버가 대신 만드므로 우리가 또 만들면 이중 생성이고,
    #    `server-default`는 "안 정하면 기본값"이라 우리가 정하는 것이 정상일 수
    #    있다. 뭉치면 정상 계획을 결함으로 부른다.
    for item in provision["doNotCreate"]:
        if item["id"] not in drawn:
            continue
        node = next(k for k, v in mapped.items() if v == item["id"])
        implicit = item.get("kind") == "server-implicit"
        findings.append(Finding(
            DOUBLE_CREATE if implicit else REDUNDANT_NODE, item["id"],
            f"노드 `{node}`로 그려져 있다",
            item["why"] + ("" if implicit else
                           " — 정하는 것 자체는 정상일 수 있다. 다만 계획이 "
                           "**정했다는 사실**을 어디에도 안 적는다")))

    # ③ 실측 검사. **판정하지 않는다** — 규칙과 계획의 관측 사실을 나란히 낸다.
    counted = Counter(mapped.values())
    for check in provision["checks"]:
        if check["subject"] not in drawn and check["object"] not in drawn:
            continue
        seen = {r: counted[r] for r in (check["subject"], check["object"])
                if counted.get(r)}
        findings.append(Finding(
            UNCHECKED_RULE, f'{check["subject"]}→{check["object"]}',
            f"계획의 관측: {seen or '해당 자원 없음'} "
            "— 계획에 이 규칙을 적용한 흔적이 없다",
            f'[{check["kind"]}] {check["rule"]}'))

    # ④ 순서. 계획은 노드·선이라 시간축이 없다 — **담을 자리가 없다**는 것이
    #    관측이고, 그래서 하나로 묶어 낸다(자원마다 반복하면 목록만 길어진다).
    order = [c["id"] for c in provision["createOrder"] if c["id"] in drawn]
    if len(order) > 1:
        findings.append(Finding(
            ABSENT_ORDER, "생성 순서",
            "계획은 노드와 선만 담는다 — 순서를 담을 자리가 없다",
            " → ".join(order)))
    deletes = [p for p in provision["deleteBefore"]
               if p[0] in drawn and p[1] in drawn]
    if deletes:
        findings.append(Finding(
            ABSENT_ORDER, "삭제 순서",
            "계획에 삭제 순서가 없다 — 그대로 지우면 거부를 만난다",
            " · ".join(f"{a} 먼저, 그다음 {b}" for a, b in deletes)))

    # ⑤ 기능 결속 경고. 컨트롤 플레인이 막지 않는 지대라 **계획에 안 실리면
    #    아무 데서도 안 나온다** — 검사로는 영영 안 잡힌다.
    for warning in provision["operationalWarnings"]:
        if warning["subject"] in drawn or warning["object"] in drawn:
            findings.append(Finding(
                ABSENT_WARNING, f'{warning["subject"]}→{warning["object"]}',
                "계획에 이 경고가 없다",
                warning["warning"]))

    # ⑥ 완료 대기. 계획대로 순서 없이 실행하면 중간 상태에서 다음을 시도한다.
    for wait in provision["waitFor"]:
        if wait["id"] in drawn:
            findings.append(Finding(
                ABSENT_WAIT, f'{wait["id"]}.{wait["op"]}',
                "계획에 완료 대기가 없다",
                f'{wait["doneSignal"]} ({wait["confidence"]})'))

    result.findings = tuple(findings)
    return result


def render(result: Crosscheck) -> str:
    """사람이 읽는 보고. 세는 것이 목적이므로 **개수를 먼저** 낸다."""
    lines = [
        f"# 계획 ↔ 실측 대조 ({result.csp})",
        "",
        f"어휘로 읽힌 노드 {len(result.mapped)}"
        f"(그중 표시 문자열로 {len(result.weak)}) · 대조 불가 {len(result.unmapped)}",
        f"앵커로 잡힌 것: {', '.join(result.anchors) or '(없음 — 대조 불가)'}",
        "",
        "## 개수",
    ]
    counts = result.counts()
    for kind in (MISSING_REQUIRED, DOUBLE_CREATE, REDUNDANT_NODE,
                 UNCHECKED_RULE, ABSENT_ORDER,
                 ABSENT_WARNING, ABSENT_WAIT, WEAK_READING, OUT_OF_VOCABULARY):
        if counts.get(kind):
            lines.append(f"  {kind:20} {counts[kind]}")
    lines += ["", f"  합계 {len(result.findings)}", "", "## 내용", ""]
    for finding in result.findings:
        lines.append(finding.line())
        lines.append("")
    return "\n".join(lines)
