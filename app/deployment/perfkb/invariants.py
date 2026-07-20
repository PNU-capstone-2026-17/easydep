"""perfkb 산출물의 레코드 간 불변식.

perfkb도 cb-tumblebug 덤프에서 나오지만 costkb와 달리 **성능 신호를 골라 담는다.**
그래서 같은 검사라도 뜻이 다르다 — 현재 위반 0건인 것은 우연이 아니라 담는 과정에서
걸러졌다는 뜻이고, 0이 깨지면 그 필터가 뚫렸다는 신호다.

성능 KB에서는 과대 진술이 과소 진술보다 해롭다는 원칙(감사 §5-6)에 따라, 여기서는
가속기 모순을 `error`로 둔다. costkb 쪽은 미러 계약 때문에 `report`다.

얼개는 `kbcommon/invariants.py` 참고.
"""

from __future__ import annotations

from kbcommon.invariants import Invariant, accelerator_fields_agree

INVARIANTS = (
    Invariant(
        name="accelerator-fields-agree",
        question="가속기 종류가 적힌 스펙에 개수도 적혀 있는가?",
        severity="error",
        check=accelerator_fields_agree("specs"),
    ),
)
