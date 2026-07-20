"""costkb 산출물의 레코드 간 불변식.

costkb는 cb-tumblebug 덤프의 **미러**다. 그래서 여기 있는 검사는 전부 `report`다 —
상류가 모순돼 있어도 우리가 고쳐 쓰면 미러가 아니게 된다. 대신 몇 건인지 밝혀서,
상류가 나빠지고 있는지 좋아지고 있는지 빌드마다 보이게 한다.

얼개는 `kbcommon/invariants.py` 참고.
"""

from __future__ import annotations

from kbcommon.invariants import Invariant, accelerator_fields_agree

INVARIANTS = (
    Invariant(
        name="accelerator-fields-agree",
        question="가속기 종류가 적힌 스펙에 개수도 적혀 있는가?",
        severity="report",
        check=accelerator_fields_agree("specs"),
    ),
)
