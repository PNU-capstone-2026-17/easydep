"""긴 영향 목록은 **묶어서** 답한다 — 그리고 묶느라 잃은 것을 센다.

`deletion_impact`가 목록을 통째로 찍고 있었다. `AWS::EC2::VPC` 하나가 466줄,
`Microsoft.KeyVault/vaults`가 561줄이다. 길이 자체보다 나쁜 것은 **그 아래 붙는
근거 꼬리말이 묻힌다**는 점이다 — *"이 중 32%는 이름 추론이니 실제 참조를 확인하라"*
가 466줄 뒤에 있으면 아무도 안 읽는다. 13장의 377,439자 사고와 같은 모양이다.

여기서 고정하는 규약은 넷이다.

1. **문턱 아래는 그대로.** 실측(2026-07-28, 노드 9,822 전수)에서 영향 개수는
   중앙값 0·평균 2.3이고 25를 넘는 노드가 158개(1.6%)뿐이라, 요약은 98.4%의
   질문에서 답을 한 글자도 바꾸지 않는다.
2. **총계와 그룹별 개수는 완전하다.** 줄어드는 것은 이름 예시뿐이다.
3. **버린 것을 센다.** 조용한 절단은 "이게 전부"로 읽힌다.
4. **못 묶으면 못 묶는다고 말한다.** GCP(KCC) 타입 이름에는 서비스 부분이 없는데,
   CamelCase를 쪼개 그룹을 만드는 것은 이름을 짐작하는 일이다(19장).
"""

from __future__ import annotations

import pytest

from app.core.cloudkb.graphkb.agent_api import (
    _SUMMARY_THRESHOLD,
    deletion_impact,
)
from app.core.cloudkb.graphkb.model import Edge, Graph, Node


def _node(node_id: str, provider: str) -> Node:
    return Node(
        id=node_id, layer="vendor", provider=provider,
        display_name=node_id.split("::", 1)[-1], source="t",
    )


def _edge(a: str, b: str, evidence: str) -> Edge:
    return Edge(
        from_id=a, to_id=b, type="references", via_property="x",
        required=True, cardinality="one", evidence=evidence, reviewed=True,
    )


def _graph_with(target: str, provider: str, dependent_ids: list[str]) -> Graph:
    g = Graph()
    g.add_node(_node(target, provider))
    for i, dep in enumerate(dependent_ids):
        g.add_node(_node(dep, provider))
        # 근거를 섞는다 — 짐작 비율 경고가 살아 있는지 보려면 둘 다 있어야 한다.
        g.add_edge(_edge(dep, target, "heuristic" if i % 3 == 0 else "cdk-oob"))
    return g


def _use(graph: Graph, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.core.cloudkb.graphkb.agent_api.load_merged", lambda output_dir=None: graph
    )


@pytest.fixture
def aws_wide(monkeypatch) -> None:
    """서비스가 여럿인 넓은 영향 — 실제 VPC의 모양(135개 서비스)을 줄인 것."""
    deps = [
        f"aws::AWS::{service}::Thing{n}"
        for service, count in (("EC2", 40), ("Cognito", 12), ("Lambda", 9),
                               ("ApiGateway", 7), ("AppSync", 5), ("Athena", 3),
                               ("Backup", 2), ("Batch", 2), ("Budgets", 1),
                               ("Cloud9", 1), ("CodeBuild", 1))
        for n in range(count)
    ]
    _use(_graph_with("aws::AWS::EC2::VPC", "aws", deps), monkeypatch)
    return len(deps)


def test_short_list_is_still_printed_whole(monkeypatch) -> None:
    """문턱 아래는 예전 그대로다 — 이 변경이 98%의 답을 건드리면 안 된다."""
    deps = [f"aws::AWS::RDS::Thing{n}" for n in range(_SUMMARY_THRESHOLD)]
    _use(_graph_with("aws::AWS::RDS::DBInstance", "aws", deps), monkeypatch)
    text = deletion_impact("AWS::RDS::DBInstance")
    for dep in deps:
        assert dep in text, "문턱 아래인데 목록이 잘렸다"
    assert "too many to list" not in text


def test_long_list_is_grouped_not_dumped(aws_wide, monkeypatch) -> None:
    text = deletion_impact("AWS::EC2::VPC")
    body = [ln for ln in text.splitlines() if ln.startswith("- ")]
    assert len(body) <= 10, f"묶었다면서 {len(body)}줄이 나왔다"
    assert str(aws_wide) in text, "총계가 빠졌다 — 요약은 규모를 숨기면 안 된다"
    assert "AWS::EC2 (40)" in text, "가장 큰 그룹이 개수와 함께 앞에 와야 한다"


def test_group_counts_account_for_every_type(aws_wide, monkeypatch) -> None:
    """보여 준 그룹 + "나머지 N종"이 총계와 맞아야 한다. 조용히 사라지면 안 된다."""
    import re

    text = deletion_impact("AWS::EC2::VPC")
    shown = sum(int(n) for n in re.findall(r"^- \S+ \((\d+)\):", text, re.MULTILINE))
    rest = re.search(r"and \d+ more services covering (\d+) types", text)
    assert rest, "안 보여 준 그룹을 세지 않았다 — 조용한 절단"
    assert shown + int(rest.group(1)) == aws_wide, (
        f"그룹별 개수 합({shown} + {rest.group(1)})이 총계 {aws_wide}와 다르다"
    )


def test_evidence_warning_is_not_buried(aws_wide, monkeypatch) -> None:
    """**이 변경의 진짜 이유.** 짐작 비율 경고가 목록에 묻히면 안 된다.

    꼬리말의 비율은 보여 준 그룹이 아니라 **영향 전수** 기준이어야 한다 —
    8개 그룹만 세면 그 경고가 사실이 아니게 된다.
    """
    text = deletion_impact("AWS::EC2::VPC")
    lines = text.splitlines()
    warned = next(i for i, ln in enumerate(lines) if "name inference" in ln)
    # **경고에 닿기까지 읽어야 하는 줄 수**가 이 결함의 크기였다 — 예전엔 466줄 뒤였다.
    assert warned < 15, f"경고 앞에 {warned}줄이 깔려 있다 — 그만큼 안 읽힌다"
    # 비율은 보여 준 그룹이 아니라 전수 기준이다. 40+12+9+7+5+3+2+2+1+1+1 = 83건 중
    # 3의 배수 인덱스가 heuristic이므로 28건(34%)이 짐작이다.
    assert "**28 (34%)" in text, "짐작 비율이 보여 준 그룹만으로 계산됐다"


def test_full_returns_everything(aws_wide, monkeypatch) -> None:
    """전체가 필요한 프로그램 호출자를 위한 문."""
    text = deletion_impact("AWS::EC2::VPC", full=True)
    assert len([ln for ln in text.splitlines() if ln.startswith("- ")]) == aws_wide


def test_ungroupable_names_say_so(monkeypatch) -> None:
    """GCP(KCC) 이름에는 서비스 부분이 없다 — **CamelCase를 쪼개 지어내지 않는다.**"""
    deps = [f"gcp::Compute{n}" for n in range(_SUMMARY_THRESHOLD + 30)]
    _use(_graph_with("gcp::Organization", "gcp", deps), monkeypatch)
    text = deletion_impact("gcp::Organization")
    assert "cut list, not the whole answer" in text, "잘랐다고 말하지 않았다"
    assert "nothing to group them by" in text
    assert "gcp::Compute0" in text, "앞머리는 실제 이름이어야 한다"
    # 없는 서비스 이름을 만들어 내지 않았다.
    assert "- Compute (" not in text


def test_switch_name_never_leaks_into_the_answer(aws_wide, monkeypatch) -> None:
    """도구 출력에 스위치·도구 이름을 적지 않는다 — 모델이 그대로 복사한다(13장)."""
    text = deletion_impact("AWS::EC2::VPC")
    for leak in ("full=True", "deletion_impact", "kb_deletion_impact"):
        assert leak not in text
