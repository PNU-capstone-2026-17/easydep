"""재배포 판단을 **사람 기억에 맡기지 않는다.**

저장소에 남의 데이터에서 유도한 파일을 넣는 것은 법적 판단이지 기술적 판단이 아니다.
그래서 코드가 할 수 있는 일은 하나뿐이다 — **판단한 것을 잊지 않게 하는 것.**

    denied      원본이 재배포를 명시적으로 막는다      → 산출물이 `data/`에 있으면 실패
    not-stated  허가 문구가 없다(금지 문구도 없다)     → `NOTICE`에 이름이 없으면 실패
    fair-use    라이선스 부여 없음, 교육 목적 공정이용  → `NOTICE`와 파일 `_note`에
                판단으로 수록(사용자 결정)               고지가 없으면 실패
    ""          따로 판단하지 않았다                   → 아무 요구도 하지 않는다

빈 값이 "괜찮다"가 아니라 **"안 봤다"**인 것이 요점이다. 소스 40개를 다 감사하지
않았으면서 기본값으로 허가를 주장하면 그 주장 자체가 거짓이 된다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.cloudkb.kbcommon import artifact
from app.core.cloudkb.kbcommon.sources import SOURCES

_NOTICE = Path(__file__).resolve().parent.parent / "NOTICE"

#: 재배포를 막는 소스에서 나온 산출물. 여기 이름이 `data/`에 있으면 안 된다.
#: **파생물까지 막지는 못한다** — AWS Price List에서 뽑은 EBS 한도 20건은
#: `aws-capacity.json`에 들어 있고, 그건 값이 아니라 스키마 수준이라 괜찮다고
#: 판단한 것이다(NOTICE에 그렇게 적혀 있다). 이 검사가 보는 것은 **가격 값을 그대로
#: 담은 파일**이 들어오지 않는가다.
_DENIED_ARTIFACTS = ("aws-price-list.json", "aws-pricing.json")


def _notice_text() -> str:
    return _NOTICE.read_text(encoding="utf-8")


def test_notice_exists() -> None:
    assert _NOTICE.exists(), "NOTICE가 없으면 이 검사 전체가 무의미하다"


@pytest.mark.parametrize(
    "key", sorted(k for k, s in SOURCES.items() if s.redistribution == "not-stated")
)
def test_unstated_sources_are_named_in_notice(key: str) -> None:
    """**허가가 없는 것과 명시하는 것은 다른 문제다.**

    출처를 밝힌다고 라이선스가 생기지는 않는다. 그래도 밝혀야 하는 이유는, 나중에
    누가 이 저장소를 볼 때 **무엇이 남의 것이고 어디서 왔는지 한눈에 보여야** 지우든
    바꾸든 할 수 있기 때문이다. 그걸 사람 기억에 맡기지 않는다.
    """
    assert key in _notice_text(), (
        f"'{key}'는 재배포 허가 문구가 없는 소스인데 NOTICE에 이름이 없다. "
        "산출물을 저장소에 넣기로 했으면 NOTICE에 무엇을 왜 넣었는지 적어야 한다."
    )


@pytest.mark.parametrize(
    "key", sorted(k for k, s in SOURCES.items() if s.redistribution == "denied")
)
def test_denied_sources_are_named_in_notice_too(key: str) -> None:
    """막힌 소스는 **왜 안 넣었는지**를 적는다 — 안 적으면 다음 사람이 다시 넣는다."""
    assert key in _notice_text() or "Price List" in _notice_text()


@pytest.mark.parametrize(
    "key", sorted(k for k, s in SOURCES.items() if s.redistribution == "fair-use")
)
def test_fair_use_sources_disclose_the_judgment_in_notice(key: str) -> None:
    """공정이용은 허가가 아니라 **판단**이다 — 판단이면 판단이라고 적어야 한다.

    수록 근거(교육 목적)·허가 아님·요청 시 제거, 셋이 NOTICE에 있어야 한다.
    """
    # 줄바꿈이 문구 중간에 올 수 있다 — 공백을 정규화하고 본다 (한국어 부분
    # 문자열 함정의 문서판).
    text = " ".join(_notice_text().split())
    assert key in text, f"'{key}'가 NOTICE에 없다"
    assert "공정이용" in text and "요청하면" in text and "허가를 받았다는 뜻이 아니" in text


def test_no_artifact_from_a_denied_source_is_committed() -> None:
    if not artifact.BUNDLED_DIR.exists():
        return
    packed = {path.name for path in artifact.BUNDLED_DIR.glob("*.gz")}
    for name in _DENIED_ARTIFACTS:
        assert f"{name}.gz" not in packed, f"{name}은 재배포가 막힌 소스의 산출물이다"


@pytest.fixture(scope="module")
def bundled() -> list[tuple[str, dict]]:
    """`data/*.gz`를 **한 번만** 푼다.

    파일마다 따로 풀면 압축 4MB를 검사 수만큼 반복해서 전체 테스트가 두 배로
    느려진다(실측 44초 → 108초).
    """
    if not artifact.BUNDLED_DIR.exists():
        return []
    return [
        (path.name, artifact.load_json(path))
        for path in sorted(artifact.BUNDLED_DIR.glob("*.json.gz"))
    ]


def test_committed_artifacts_declare_their_source(bundled) -> None:
    """`data/`의 모든 파일이 어디서 왔는지 말해야 한다 — 못 말하면 출처 불명 덩어리다.

    키가 `_source`와 `provenance` 둘이다(`aws-endpoints`가 뒤쪽). 여기서 하나로
    강제하면 **검사가 계약보다 엄격해진다** — `test_bundled_artifacts.py`가 이미
    둘 다 받는다.
    """
    for name, data in bundled:
        assert data.get("_source") or data.get("provenance"), f"{name}에 출처가 없다"


def test_unstated_artifacts_carry_the_disclosure_in_the_file_itself(bundled) -> None:
    """**NOTICE만으로는 부족하다.** 산출물은 저장소 밖으로도 나간다 — 파일 하나만
    떼어 봐도 어디서 왔고 허가가 어떤 상태인지 보여야 한다.

    fair-use 소스도 같은 규율이다 — 공정이용 판단은 파일에 실려 함께 다녀야 한다.
    """
    unstated = {
        k for k, s in SOURCES.items()
        if s.redistribution in ("not-stated", "fair-use")
    }
    checked = 0
    for name, data in bundled:
        keys = {row.get("source") for row in data.get("_source") or []}
        if not (keys & unstated):
            continue
        checked += 1
        # 산출물 `_note`는 모델이 보는 면이라 영어로 옮겼다. **고지가 사라지지
        # 않았는지**가 이 검사의 요점이므로 두 언어를 다 받는다 — 한국어 후보를
        # 지우면 아직 안 옮긴 산출물의 고지 누락을 못 잡는다.
        note = " ".join((data.get("_note") or "").split()).lower()
        assert ("재배포" in note or "공정이용" in note
                or "redistribution" in note or "fair use" in note), (
            f"{name}은 재배포 허가가 없는 소스에서 왔는데 `_note`에 그 사실이 없다"
        )
    if bundled and unstated:
        # **0건이면 검사가 아무것도 안 문 것이다.** 이 저장소에서 "아무것도 안 무는
        # 검사가 조용히 통과"한 사고가 있었다(정규식이 백스페이스가 된 건).
        assert checked, "허가 미확인 소스의 산출물이 data/에 하나도 없다 — 검사가 헛돈다"
