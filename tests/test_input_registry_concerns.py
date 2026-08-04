"""레지스트리와 관심사 축이 **서로를 가리키는지** — 층이 갈려 여기서만 볼 수 있다.

`app/core/input_registry.py`는 근거를 `concern:<id>`로 인용하지만 `app/core`는
`app/requirements`를 모른다(그 방향으로 엮으면 순환이다). 그래서 좌표가 실재하는지
확인하는 일이 여기 있다.

## 왜 이 대조가 중요한가

두 축은 따로 지어졌다. 관심사는 *"요구사항에 적혔나"*(커버리지)를 묻고, 계약은
*"값이 뭔가"*를 받는다. 겹치는 지점이 관심사의 `consumer` 선언 7건인데, 그 선언은
**한 방향뿐이었다** — 관심사는 자기 소비자를 알지만 계약 칸은 자기 근거를 몰랐다.

레지스트리가 반대 방향을 채웠고, 이 파일이 **두 방향이 일치하는지** 본다.
어긋나면 둘 중 하나가 낡은 것이다.
"""

from __future__ import annotations

import re

from app.core import input_registry
from app.requirements.knowledge import concerns


def test_user_facing_resource_questions_are_english_only() -> None:
    hangul = re.compile(r"[가-힣]")
    for ask in input_registry.ASKS:
        assert not hangul.search(ask.question), ask.id
        assert not hangul.search(ask.opens), ask.id


def _concern_refs() -> set[str]:
    return {b.ref for a in input_registry.ASKS for b in a.basis
            if b.kind == input_registry.CONCERN}


def test_every_concern_basis_names_a_real_concern() -> None:
    """인용한 관심사 id가 실재한다 — 없는 것을 근거로 대면 근거가 아니다."""
    unknown = _concern_refs() - set(concerns.BY_ID)
    assert not unknown, f"없는 관심사를 근거로 든다: {sorted(unknown)}"


def test_the_two_axes_point_at_each_other() -> None:
    """관심사가 소비자로 지목한 칸 = 그 칸이 근거로 든 관심사.

    한쪽만 있으면 어느 쪽이 낡았는지 알 수 없다. 실제로 이 대조가 없던 동안
    관심사 7건만 이어져 있고 계약 쪽은 그 사실을 몰랐다.
    """
    for concern in concerns.CONCERNS:
        if not concern.consumer:
            continue
        # `RESOURCE_SPEC.a|b` 꼴(대안 칸)도 있다 — 어느 한쪽이 이으면 족하다.
        fields = [part.split(".")[-1]
                  for part in concern.consumer.replace("|", " ").split()]
        linked = [f for f in fields
                  if any(b.kind == input_registry.CONCERN and b.ref == concern.id
                         for a in [input_registry.by_field().get(f)] if a
                         for b in a.basis)]
        assert linked, (
            f"{concern.id}가 {concern.consumer}를 소비자로 선언했는데 그 칸은 "
            f"이 관심사를 근거로 들지 않는다 — 둘 중 하나가 낡았다")


def test_a_concern_basis_only_appears_where_the_concern_declared_a_consumer() -> None:
    """반대 방향 — 근거로 든 관심사는 그 칸을 소비자로 선언하고 있어야 한다.

    이걸 안 보면 "그럴듯해 보여서" 관심사를 근거로 붙이는 길이 열린다.
    """
    for ask in input_registry.ASKS:
        for basis in ask.basis:
            if basis.kind != input_registry.CONCERN:
                continue
            concern = concerns.BY_ID[basis.ref]
            assert concern.consumer and ask.spec_field in concern.consumer, (
                f"{ask.id}가 {basis.ref}를 근거로 드는데 그 관심사의 소비자는 "
                f"{concern.consumer!r}다")


def test_the_new_topology_axis_has_no_concern_behind_it() -> None:
    """**관심사 축에도 공백이 있다** — "무엇을 배포하는가"를 묻는 관심사가 없다.

    `topology.workloads`는 근거를 코드에서만 댄다(`plan_for_anchors`가 앵커를
    요구한다). 관심사 29건 중 그 질문을 하는 것이 없기 때문이다.

    이 테스트는 **없는 것을 지킨다.** 그럴듯하다고 `cn.managed-vs-self`를 갖다
    붙이면 실패한다 — 그건 "관리형을 써도 되는가"이지 "무엇을 배포하는가"가
    아니다. 이으려면 관심사 축의 선정 절차(Nickerson + 코퍼스 probe)를 거쳐
    관심사를 먼저 추가해야 한다.
    """
    ask = input_registry.by_id()["topology.workloads"]
    kinds = {b.kind for b in ask.basis}
    assert kinds == {input_registry.CODE}, (
        "위상축에 관심사 근거가 붙었다 — 관심사를 실제로 추가했다면 이 테스트를 "
        "고쳐라. 붙이기만 했다면 그건 근거가 아니다")
