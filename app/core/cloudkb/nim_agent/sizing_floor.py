"""스펙 하한을 정하는 **층 셋** — 근거 없는 계수를 코드에서 몰아낸 자리.

## 왜 이 모듈이 생겼나

`design_tools`에 이런 줄이 있었다.

    users = requirements.get("expectedConcurrentUsers")
    vcpu, mem = (2, 4) if not users or users <= 500 else (4, 8)

`500`·`(2,4)`·`(4,8)` 어느 것에도 출처가 없다. 그리고 이 저장소는 바로 그 형태를
**KB에서 명시적으로 배제**해 뒀다 — `sizingkb/__init__.py`가 *"동시 사용자 N명 →
vCPU M 형태의 어떤 것도. 소스가 없다"*라고 적고, 조사 결론도 같다
(`archive/bundle-sizing-research-2026-07-22.md`). 배제한 계수가 코드에 하드코딩으로
살아 있었던 것이다.

재조사(2026-07-29)에서도 그 결론은 그대로였다. 사이징 후보 소스 목록에 **동시
사용자를 스펙으로 바꾸는 규칙을 적어 둔 1차 소스가 없다.** 남아 있던 후보 하나
(`karpenter-provider-aws`, 인스턴스 선택 로직)는 오히려 이 설계를 뒷받침한다 —
카펜터도 **파드가 요청한 CPU·메모리에서 출발**하지, 사용자 수에서 출발하지 않는다.

## 층을 나눈다 — 셋 중 하나를 고르는 문제가 아니다

  층 1  하드 하한   sizingkb가 **출처와 함께** 들고 있는 하한. 협상 대상이 아니다.
                    실측: `k8s-node`만 있다(vCPU 2 · 4 GiB, tumblebug 원본).
                    일반 웹앱 VM에는 **해당하는 공식 하한이 없다.**
  층 2  워크로드     사용자·설계자가 준 `minVCpu`·`minMemoryGiB`. 검증된 사실이
                    아니라 **진술로 기록된다**(`stateless`가 이미 그런 칸이다).
  층 3  둘 다 없음   **하나를 고르지 않는다.** 최저가를 뽑으면 그건 판단을 안 한 것이
                    아니라 "제일 작은 걸로 충분하다"는 **더 센 주장**을 근거 없이
                    하는 것이다. 대신 카탈로그 대역을 내고, 무엇을 주면 좁혀지는지
                    말한다(`verify._what_would_close_the_gaps`와 같은 꼴).

## 규모 신호는 여기서 값을 한다

`expectedConcurrentUsers`는 **하한을 정하지 않는다.** 층 3에서 *되묻는 근거*가 된다 —
"동시 사용자 3,000인데 하한이 없다"가 "하한을 알려 달라"보다 강한 요청이다.
"""

from __future__ import annotations

from dataclasses import dataclass

#: 층 이름. 노트·판정문에 그대로 실리므로 문자열을 한 곳에 둔다.
LAYER_KB = "kb"
LAYER_STATED = "stated"
LAYER_NONE = "none"

#: 계약에서 워크로드 하한을 담는 칸.
VCPU_FIELD = "minVCpu"
MEM_FIELD = "minMemoryGiB"


@dataclass(frozen=True)
class Floor:
    """이번 계획에 걸리는 하한과 **그것이 어디서 왔는가.**"""

    vcpu: float = 0.0
    mem_gib: float = 0.0
    layers: tuple[str, ...] = ()
    why: str = ""
    """노트에 그대로 실릴 한 줄. 근거 없이 붙는 말은 없다는 규율 그대로."""

    @property
    def decided(self) -> bool:
        """하한이 하나라도 정해졌는가 — 층 3인지 가르는 유일한 질문."""
        return bool(self.layers)

    @property
    def layer(self) -> str:
        return "+".join(self.layers) if self.layers else LAYER_NONE


def _kb_minimums(scope: str) -> tuple[float, float, str]:
    """층 1 — sizingkb의 하드 하한. 스코프가 없으면 **하한도 없다.**

    스코프를 무시하고 끌어 쓰면 안 된다. `k8s-node`의 하한을 일반 VM에 적용하면
    출처가 있는 값을 출처가 없는 자리에 붙이는 것이고, 그게 이 모듈이 없애려는
    바로 그 오류다.
    """
    if not scope:
        return 0.0, 0.0, ""
    try:
        from app.core.cloudkb.sizingkb.dataset import rules_of
        from app.core.cloudkb.sizingkb.model import MINIMUM

        rules = rules_of(kind=MINIMUM, scope=scope)
    except Exception:  # noqa: BLE001 — KB가 없으면 하한이 없는 것이지 실패가 아니다
        return 0.0, 0.0, ""

    vcpu = mem = 0.0
    sources: set[str] = set()
    for rule in rules:
        try:
            value = float(rule.value)
        except (TypeError, ValueError):
            continue
        metric = (rule.metric or "").lower()
        if "vcpu" in metric:
            vcpu = max(vcpu, value)
            sources.add(rule.evidence)
        elif "mem" in metric:
            mem = max(mem, value)
            sources.add(rule.evidence)
    if not (vcpu or mem):
        return 0.0, 0.0, ""
    return vcpu, mem, (
        f"{scope} has a stated minimum of {vcpu:g} vCPU and {mem:g} GiB "
        f"(source {', '.join(sorted(sources))}) — this is a tooling rule, not a "
        "cloud fact, but it is written down"
    )


def _stated(requirements: dict) -> tuple[float, float]:
    """층 2 — 사용자·설계자가 준 하한. 없으면 0."""
    def number(name: str) -> float:
        raw = (requirements or {}).get(name)
        try:
            value = float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0
        return value if value > 0 else 0.0

    return number(VCPU_FIELD), number(MEM_FIELD)


def resolve(requirements: dict, *, scope: str = "") -> Floor:
    """이 워크로드의 하한 — 층 1과 층 2 중 **더 큰 쪽**이 각 축을 잡는다.

    둘 다 있으면 둘 다 만족해야 하므로 축마다 최대를 취한다. 어느 층이 잡았는지는
    `layers`에 남는다 — 사용자가 준 값이 KB 하한에 밀렸다면 그 사실이 보여야 한다.
    """
    kb_vcpu, kb_mem, kb_why = _kb_minimums(scope)
    st_vcpu, st_mem = _stated(requirements)

    layers: list[str] = []
    parts: list[str] = []
    if kb_vcpu or kb_mem:
        layers.append(LAYER_KB)
        parts.append(kb_why)
    if st_vcpu or st_mem:
        layers.append(LAYER_STATED)
        parts.append(
            f"the requirement states a floor of "
            f"{st_vcpu:g} vCPU and {st_mem:g} GiB "
            "(recorded as the user's or designer's claim, not a verified fact)"
        )
    return Floor(
        vcpu=max(kb_vcpu, st_vcpu),
        mem_gib=max(kb_mem, st_mem),
        layers=tuple(layers),
        why="; ".join(p for p in parts if p),
    )


def band(provider: str, region: str | None, *, sample: int = 3) -> tuple[list[dict], list[dict]]:
    """층 3의 산출 — **하나를 고르지 않고** 카탈로그의 양끝을 보여 준다.

    (가장 싼 것들, 가장 큰 것들). 값이 붙은 스펙만 본다(`filter_specs`의 기본).
    """
    from app.core.cloudkb.costkb import dataset as cost_dataset

    cheapest = cost_dataset.filter_specs(
        provider=provider, region=region, sort_by="cost", limit=sample,
    )
    biggest = cost_dataset.filter_specs(
        provider=provider, region=region, sort_by="vcpu", limit=sample,
    )
    return cheapest, biggest


def undecided_note(requirements: dict, cheapest: list[dict], biggest: list[dict]) -> str:
    """층 3에서 계획에 실리는 문장. **침묵 대신 무엇이 없는지 말한다.**

    규모 신호가 있으면 되묻기의 근거로 쓴다 — 값을 정하는 데는 못 쓰지만, 하한이
    없다는 사실을 사용자에게 더 급하게 만들어 주는 데는 쓴다.
    """
    users = (requirements or {}).get("expectedConcurrentUsers")
    rps = (requirements or {}).get("approxRequestsPerSecond")
    scale = (f"{users} concurrent users" if users is not None else
             f"about {rps} requests per second" if rps is not None else "")

    span = ""
    if cheapest and biggest:
        low, high = cheapest[0], biggest[0]
        span = (
            f" The priced catalogue here runs from {low.get('specName')} "
            f"({low.get('vcpu')} vCPU / {low.get('memGiB')} GiB"
            + (f", ${low['hourlyUSD']:.4f}/h" if low.get("hourlyUSD") else "")
            + f") up to {high.get('specName')} ({high.get('vcpu')} vCPU / "
            f"{high.get('memGiB')} GiB"
            + (f", ${high['hourlyUSD']:.4f}/h" if high.get("hourlyUSD") else "")
            + ")."
        )

    head = (
        "No spec was chosen: nothing states a floor for this workload. "
        "Picking the cheapest one would not be a neutral choice — it would assert "
        "that the smallest machine is enough, and no source says that."
    )
    ask = (
        f" Give {VCPU_FIELD} and {MEM_FIELD} and this narrows to a single "
        "recommendation."
    )
    urgency = (
        f" The requirement says {scale}, which is why this matters here — but a "
        "scale figure cannot be converted into a spec: no source states that "
        "conversion, and this knowledge base deliberately does not carry one."
        if scale else ""
    )
    return head + span + urgency + ask
