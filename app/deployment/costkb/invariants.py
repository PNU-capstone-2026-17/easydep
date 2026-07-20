"""costkb 산출물의 레코드 간 불변식.

costkb는 cb-tumblebug 덤프의 **미러**다. 그래서 여기 있는 검사는 전부 `report`다 —
상류가 모순돼 있어도 우리가 고쳐 쓰면 미러가 아니게 된다. 대신 몇 건인지 밝혀서,
상류가 나빠지고 있는지 좋아지고 있는지 빌드마다 보이게 한다.

얼개는 `kbcommon/invariants.py` 참고.
"""

from __future__ import annotations

import collections
from collections.abc import Iterable

from kbcommon.invariants import (
    Invariant,
    Violation,
    accelerator_fields_agree,
    no_negative_measurements,
)


def _disk_size_is_ambiguous(dataset: dict) -> Iterable[Violation]:
    """`diskSizeGB == 0`이 몇 건인가 — 사실인지 미기입인지 **가릴 수 없는** 값이다.

    0은 "로컬 디스크 없음"이라는 사실일 수도, 드라이버가 안 채운 것일 수도 있다.
    Azure에서 이름상 로컬 디스크가 확실히 있는 v6 계열(`Standard_E48ads_v6` 등)
    5,225건이 0으로 오므로 사실이라고 단정할 수 없고, 반대로 0이 맞는 스펙도 있어
    미기입이라고 단정할 수도 없다.

    미러는 애매한 값을 다시 쓰지 않는다. 대신 **몇 건인지 말한다** — 소비자가
    "디스크 0GB"를 사실로 읽지 않도록.
    """
    by_provider: collections.Counter = collections.Counter()
    for record in dataset.get("specs") or []:
        if isinstance(record, dict) and record.get("diskSizeGB") == 0:
            by_provider[record.get("provider")] += 1
    for provider, count in by_provider.most_common():
        yield Violation(
            where=str(provider),
            detail=f"diskSizeGB=0이 {count:,}건 (로컬 디스크 없음인지 미기입인지 불명)",
        )


INVARIANTS = (
    Invariant(
        name="accelerator-fields-agree",
        question="가속기 종류가 적힌 스펙에 개수도 적혀 있는가?",
        severity="report",
        check=accelerator_fields_agree("specs"),
    ),
    Invariant(
        name="no-negative-measurements",
        question="크기·개수 필드에 음수가 남아 있지 않은가?",
        # 이건 우리 책임이다 — 상류의 -1을 null로 옮기는 건 파서의 일이다.
        severity="error",
        check=no_negative_measurements(
            "specs", "diskSizeGB", "acceleratorMemoryGB", "acceleratorCount",
            "memGiB", "vCPU", "hourlyUSD",
        ),
    ),
    Invariant(
        name="disk-size-zero-is-ambiguous",
        question="디스크 크기 0이 몇 건인가? (사실/미기입을 가릴 수 없다)",
        severity="report",
        check=_disk_size_is_ambiguous,
    ),
)
