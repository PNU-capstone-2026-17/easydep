"""하류 신호 번역 — `deployment-intent`(k8s 층)에서 클라우드 앵커를 읽는다.

계획 P2(`archive/infra-intent-plan-2026-07-31.md` §3). 앵커를 **발명하지 않는다**:
하류가 `kind: Deployment`라고 적는 순간 그 워크로드는 클러스터 없이 못 사므로
앵커는 `k8sCluster`다. 나머지 신호도 같은 성격의 해석이다.

## 규칙마다 근거가 있고, 애매하면 값이 아니라 질문이다

`RULES`의 각 항목은 (신호 → 클라우드 함의 + 근거 문장)이고 **우리 구성**이다.
모르는 `kind`를 만나면 앵커를 추측하지 않고 `open_questions`에 올린다 — 값을
지어내면 그때부터 계획 전체가 근거를 잃는다.

## 명시적으로 안 쓰는 것

- `replicas` — 대수·스펙은 우리 축이 아니다(계획 §3에서 뺐다).
- `capabilities.networkPolicy` — **k8s 층 오브젝트다.** 클라우드 firewall과
  이름이 비슷해 섞기 쉬운데, 섞으면 없는 의존을 만든다.
- `capabilities.service` — k8s Service가 클라우드 LB를 자동 생성하는 경로는
  **한 번도 재지 않았다**(이중 생성 위험). `unmeasured`로 표시해 내보낸다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: k8s 워크로드 kind → 앵커. 전부 클러스터 위에서만 사는 것들이다.
_WORKLOAD_KINDS: dict[str, str] = {
    "Deployment": "k8sCluster",
    "StatefulSet": "k8sCluster",
    "Job": "k8sCluster",
    "CronJob": "k8sCluster",
    "DaemonSet": "k8sCluster",
}

#: 신호 → (앵커 또는 None, 근거). **우리 구성**이되 하류 신호의 해석이다.
RULES: dict[str, tuple[str | None, str]] = {
    "workload": ("k8sCluster", "k8s 오브젝트는 클러스터 없이 존재할 수 없다"),
    "ingress": ("loadBalancer",
                "외부 노출의 유일한 신호 — 트래픽이 클러스터 밖에서 들어온다"),
    "persistentVolume": ("disk",
                         "PVC는 영속 볼륨을 요구하고 그 실체가 클라우드 디스크다"),
}

#: 우리 축이 아닌 신호 — 왜 안 쓰는지 함께 기록한다(침묵하면 누락처럼 보인다).
OUT_OF_SCOPE: dict[str, str] = {
    "replicas": "대수·스펙 선택은 이 분석의 축이 아니다",
    "networkPolicy": "k8s 층 오브젝트다 — 클라우드 firewall과 다르다",
    "hpa": "대수 축",
    "pdb": "가용성 정책 — 자원 의존이 아니다",
}


@dataclass(frozen=True)
class Translation:
    """번역 결과 — 앵커와, 왜 그 앵커인지, 그리고 못 정한 것."""

    anchors: tuple[str, ...]
    rationale: tuple[tuple[str, str], ...] = ()  #: (앵커, 근거)
    open_questions: tuple[str, ...] = ()
    unmeasured: tuple[str, ...] = ()
    ignored: tuple[tuple[str, str], ...] = ()  #: (신호, 왜 안 쓰나)


def translate(deployment_intent: dict) -> Translation:
    """`deployment-intent` JSON에서 클라우드 앵커를 읽는다.

    Args:
        deployment_intent: `easydep-deployment-intent/v1alpha1` 형태의 사전.
    """
    workloads = deployment_intent.get("workloads") or []
    anchors: dict[str, str] = {}
    questions: list[str] = []
    unmeasured: list[str] = []
    ignored: dict[str, str] = {}

    if not workloads:
        questions.append(
            "배포 의도에 워크로드가 없습니다 — 무엇을 배포할지 정해야 "
            "클라우드 자원을 고를 수 있습니다")

    for w in workloads:
        kind = w.get("kind")
        name = w.get("name", "?")
        if kind in _WORKLOAD_KINDS:
            anchor, why = _WORKLOAD_KINDS[kind], RULES["workload"][1]
            anchors.setdefault(anchor, f"{why} (`{name}`의 kind={kind})")
        else:
            # **추측하지 않는다.** 모르는 kind에 앵커를 붙이면 계획 전체가
            # 근거를 잃는다.
            questions.append(
                f"`{name}`의 kind가 `{kind}`인데 어떤 클라우드 자원 위에서 "
                f"돌아야 하는지 모르겠습니다 — 정해 주세요")

        caps = w.get("capabilities") or {}
        if caps.get("ingress"):
            anchors.setdefault(RULES["ingress"][0],
                               f"{RULES['ingress'][1]} (`{name}`의 ingress)")
        if caps.get("service") and not caps.get("ingress"):
            # 측정 안 한 영역 — 메우지 않고 표시만 한다.
            unmeasured.append(
                f"`{name}`의 Service가 클라우드 로드밸런서를 자동 생성하는지 "
                f"측정하지 않았습니다 — 우리가 LB를 또 만들면 이중 생성이 됩니다")
        for signal, why in OUT_OF_SCOPE.items():
            if signal in caps or signal in w:
                ignored.setdefault(signal, why)
        if w.get("persistentVolume") or caps.get("pvc"):
            anchors.setdefault(RULES["persistentVolume"][0],
                               f"{RULES['persistentVolume'][1]} (`{name}`)")

    return Translation(
        anchors=tuple(sorted(anchors)),
        rationale=tuple(sorted(anchors.items())),
        open_questions=tuple(questions),
        unmeasured=tuple(dict.fromkeys(unmeasured)),
        ignored=tuple(sorted(ignored.items())),
    )
