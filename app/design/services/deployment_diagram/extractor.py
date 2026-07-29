"""앞선 산출물 전부에서 배포 토폴로지 모델을 도출한다.

클래스 다이어그램의 BCE 추출과 같은 모양이다: LLM은 PlantUML을 쓰지 않고 구조화된
배포 모델(노드·아티팩트·연결)만 내놓고, 다이어그램은 plantuml.generate_deployment_from_model
이 결정론적으로 렌더한다.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.design.services.common.structured import parse_structured


class DeploymentNode(BaseModel):
    name: str = Field(default="UnknownNode")
    #: device | executionEnvironment | database | cloud | node
    kind: str = Field(default="node")
    #: <<...>>로 그려질 기술 표기(예: "Spring Boot", "MySQL 8").
    stereotype: str = Field(default="")
    description: str = Field(default="")
    #: 이 노드를 감싸는 상위 노드 이름. 최상위면 빈 문자열.
    parent: str = Field(default="")
    #: 이 노드가 호스팅하는 클래스 이름. 외부 인프라(브라우저 등)는 비운다.
    source_classes: list[str] = Field(default_factory=list)


class DeploymentArtifact(BaseModel):
    name: str = Field(default="UnknownArtifact")
    #: 이 아티팩트가 배포되는 노드 이름(Nodes 중 하나).
    deployed_on: str = Field(default="")
    description: str = Field(default="")
    #: 이 아티팩트가 담고 있는 클래스 이름.
    source_classes: list[str] = Field(default_factory=list)


class DeploymentConnection(BaseModel):
    source: str
    target: str
    #: HTTPS, JDBC, AMQP 등. 라벨로 그려진다.
    protocol: str = Field(default="")
    description: str = Field(default="")


class DeploymentModel(BaseModel):
    Nodes: list[DeploymentNode] = Field(default_factory=list)
    Artifacts: list[DeploymentArtifact] = Field(default_factory=list)
    Connections: list[DeploymentConnection] = Field(default_factory=list)


DEPLOYMENT_EXTRACTION_SYSTEM_PROMPT = """
You are a solution architect deriving a UML deployment model from the design
artifacts of one system: a use-case specification, an analysis-level class diagram
(Boundary-Control-Entity), a sequence diagram, a REST API specification, and an ERD.

## Input
Use every artifact you are given, and ignore the ones that are absent. Do not invent
infrastructure the artifacts do not imply — no caches, queues, or replicas unless
something in the inputs calls for them.

## Nodes
- Derive nodes from where the software must actually run:
  the actor's client (from the Boundary classes and the actor), the application
  runtime (from the Control/Entity classes and the API spec), and the data store
  (from the ERD).
- `kind` is one of: device (physical/user hardware), executionEnvironment (a runtime
  or container inside a device), database (a DBMS), cloud (a managed/external
  service), node (anything else).
- `parent` nests a node inside another — put an executionEnvironment inside the
  device or cloud that hosts it. Leave it empty for top-level nodes.
- `stereotype` names the concrete technology when the inputs justify one; otherwise
  leave it empty rather than guessing a vendor.

## Artifacts
- One artifact per deployable unit the design produces (a web bundle, a service jar,
  a schema migration).
- `deployed_on` must name one of the Nodes you return.

## Connections
- One connection per communication path the sequence diagram or API spec implies.
- `source` and `target` must both name Nodes you return.
- `protocol` is the wire protocol (HTTPS, JDBC, AMQP, WebSocket). Use the one the
  API spec or ERD implies; leave it empty if the inputs do not say.

## Traceability
- `source_classes` on nodes and artifacts: the class diagram classes that run
  there or ship inside it, copied exactly. Leave it empty for infrastructure the
  design does not contain (a user's browser, a managed database engine).
- **Never invent a class name.** An empty list is honest; a made-up reference is
  a lie the trace matrix will believe.

## Self-check before finalizing
(a) every artifact's `deployed_on` names a node you returned,
(b) every connection's source and target name nodes you returned,
(c) every `parent` names a node you returned (or is empty),
(d) the ERD's tables have a database node, and the API spec's endpoints have a
    runtime node that serves them,
(e) every `source_classes` entry names a class in the given class diagram.

Populate the response strictly according to the provided schema. Do not include
markdown, code fences, or any prose outside the schema fields.
"""


def extract_deployment_model(
    scenario_text: str,
    class_diagram_puml: str,
    sequence_diagram_puml: str,
    api_spec: dict[str, Any],
    erd_puml: str,
) -> dict[str, Any]:
    """앞선 산출물 전부 → 구조화된 배포 토폴로지 모델."""
    if not scenario_text:
        return {}

    import json

    messages = [
        {"role": "system", "content": DEPLOYMENT_EXTRACTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"[Use Case Specification]\n{scenario_text}\n\n"
                f"[Class Diagram PlantUML]\n{class_diagram_puml}\n\n"
                f"[Sequence Diagram PlantUML]\n{sequence_diagram_puml}\n\n"
                f"[API Spec JSON]\n{json.dumps(api_spec, ensure_ascii=False, indent=2)}\n\n"
                f"[ERD PlantUML]\n{erd_puml}"
            ),
        },
    ]
    return parse_structured(messages, DeploymentModel)
