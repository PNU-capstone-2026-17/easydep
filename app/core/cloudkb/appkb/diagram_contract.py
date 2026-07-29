"""배포 다이어그램이 **반드시 담아야 하는 사실** — 그림의 정보 계약.

## 왜 목록이 필요한가

`diagram.py`는 어떻게 그리는지를 안다(도형·중첩·스테레오타입). 그런데 **무엇이 그림에
있어야 하는가**의 목록은 어디에도 없었다. 규칙이 코드에 흩어져 있으면 하나가 빠져도
아무 검사가 실패하지 않는다 — 실제로 큐가 원통으로 그려지던 것도, 상자 하나가 1대처럼
읽히던 것도 **사람이 보고 나서야** 발견됐다.

여기 있는 것은 "그림이 예쁜가"가 아니라 **"그림만 떼어 봐도 사실이 살아남는가"**다.
그림은 잘려서 돌아다니고, 노트는 그림과 같이 안 다닌다.

## 각 사실에 셋이 붙는다

    why      왜 이 사실이 그림에 있어야 하나 (없으면 무엇을 오해하나)
    grammar  어떤 시각 문법으로 나타나나
    probe    **되파싱·문자열로 어떻게 확인하나** — 테스트가 이걸 쓴다

`probe`가 있는 것이 요점이다. 확인할 수 없는 요구는 요구가 아니라 바람이다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fact:
    key: str
    why: str
    grammar: str
    probe: str


REQUIRED_FACTS: tuple[Fact, ...] = (
    Fact(
        "identity",
        "그림의 상자를 계획·산출물과 **기계로 이을 수 있어야** 한다. 이름이 사람 눈에만 "
        "맞으면 하류(구현 에이전트의 `diagramAlias`)가 워크로드를 못 잇고, 그러면 외부 "
        "노출 검증이 통째로 빠진다",
        "노드 별칭이 계획 id와 **같다**. 자유 서식을 쓰지 않는다",
        "`parse_back(uml)`이 돌려주는 별칭 집합 = 계획의 노드 id 집합",
    ),
    Fact(
        "archetype-shape",
        "무엇인지가 도형으로 보여야 한다. 관리형을 전부 원통으로 그렸더니 **SQS 큐도 "
        "Secrets Manager도 데이터베이스로 읽혔다**(2026-07-28)",
        "아키타입 → PlantUML 도형(queue·storage·database·folder·component·node·"
        "hexagon·cloud). 아키타입이 없으면 역할 도형으로 떨어진다",
        "아키타입이 붙은 노드 줄이 해당 도형 키워드로 시작한다",
    ),
    Fact(
        "host-nesting",
        "UML 배포 다이어그램의 뼈대가 `Node ← «deploy» ← Artifact`다. 컴포넌트를 노드로 "
        "그리면 '어디서 도는가'가 사라진다",
        "실행 환경이 있으면 `node \"…\" as \"<id>@host\" { … }`가 컴포넌트를 감싼다",
        "`@host` 접미가 붙은 상자 안에 컴포넌트 상자가 들어 있다",
    ),
    Fact(
        "undecided-replicas",
        "**미정을 미정으로 그린다.** 상자가 하나면 1대로 읽힌다 — 노트에 '몇 대인지 "
        "정할 수 없다'고 써 두어도 그림은 노트 없이 돌아다닌다",
        "`replicas`가 None이면 라벨에 `×?`, 정해졌으면 `×N`",
        "미정 노드의 라벨에 `×?`가 있다",
    ),
    Fact(
        "hedge-origin",
        "우리 추론과 설계자 지정을 그림에서 구별할 수 있어야 한다. 범례에 적어 두는 "
        "것으로는 부족하다",
        "`inferred`·`designer` 노드에 스테레오타입(`<<…>>`)",
        "유보가 필요한 노드 줄에 `<<`가 있다",
    ),
    Fact(
        "edges",
        "무엇이 무엇을 부르는지가 없으면 보안 그룹·네트워크 정책의 근거가 사라진다. "
        "이 사실은 하류에서 **NetworkPolicy로 그대로 내려간다**",
        "`\"from\" --> \"to\" : label`, 비동기는 점선(`..>`)",
        "`parse_back(uml)`의 엣지 집합 = 계획의 엣지 집합",
    ),
    Fact(
        "boundary",
        "외부 시스템과 사용자는 **우리가 만들지 않는 것**이다. 같은 얼굴로 그리면 "
        "배포 대상으로 읽히고, 하류가 그것까지 매니페스트로 만들려 한다",
        "actor는 `actor`, 외부 시스템은 `cloud`",
        "external·actor 노드가 각각 그 키워드로 그려진다",
    ),
    Fact(
        "type-or-candidates",
        "무엇으로 만들지 정해졌는지, 아직 후보인지가 보여야 한다. 후보를 하나로 그리면 "
        "결정된 것으로 읽힌다",
        "라벨 둘째 줄에 `type_id`, 없으면 `N candidates`",
        "type_id가 있으면 라벨에 그 문자열, 없고 후보만 있으면 `candidates`",
    ),
)

#: 사실 목록을 키로 조회.
BY_KEY = {fact.key: fact for fact in REQUIRED_FACTS}
