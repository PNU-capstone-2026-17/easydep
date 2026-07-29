"""소스 라이선스 — 빈 칸을 허가로 읽지 않게 한다.

**왜 이 파일이 있나**: 감사(2026-07-28)에서 `document/kb-book.md` 17장이 라이선스
5등급 표를 실어 **47종 전부가 분류된 것처럼** 읽혔는데, 실제로 판단이 붙어 있던
것은 `redistribution` 4종뿐이었고 라이선스 기재가 아예 없는 소스가 16종이었다.

이 저장소의 모든 규율은 "모르는 것을 모른다고 말한다"인데, **문서가 코드보다 자신
있게 말한 유일한 자리**였다. 그래서 라이선스를 등록부의 칸으로 올리고, 그 칸의 빈
값이 무엇을 뜻하는지를 검사로 못 박는다 — `unpinnable()`과 같은 방식이다.

    핀        재현 가능한가        `unpinnable()`  · test_source_pinning.py
    라이선스  재배포해도 되는가    `unlicensed()`  · 이 파일
"""

from __future__ import annotations

from app.deployment.kbcommon.sources import SOURCES, unlicensed

#: `license` 칸에 쓸 수 있는 어휘. 자유 서술을 막는다 — "Apache 2.0"과
#: "apache-2.0"이 섞이면 세는 것부터 안 된다. 늘리려면 **여기 적어야** 늘어난다.
ALLOWED = {
    "",  # 확인 안 함 (아래 단언이 목록을 못 박는다)
    "MIT", "MIT-0", "Apache-2.0", "MPL-2.0", "CC-BY-4.0",
    "all-rights-reserved",  # 부여 조항 없음이 명시돼 있다
    "not-stated",  # 공개돼 있으나 문구를 못 찾았다 (금지 문구도 없다)
    "bundled-own",  # 우리가 만든 파일
}


def test_license_values_are_from_the_known_vocabulary() -> None:
    unknown = {s.key: s.license for s in SOURCES.values() if s.license not in ALLOWED}
    assert not unknown, f"모르는 라이선스 표기: {unknown}"


def test_unlicensed_sources_are_declared_as_such() -> None:
    """확인 안 한 소스는 숨기지 말고 **세어서 드러낸다** — 목록을 여기 못 박는다.

    이 목록이 자라는 것은 나쁜 일이므로 자동으로 늘어나지 않게 한다. 줄이려면
    실제로 확인하고 `license=`를 채운다.

        cfn-schema           **저장소가 없다** — AWS가 서빙하는 zip뿐이라 LICENSE
                             파일 자체가 존재하지 않는다. 넷 중 유일하게 원리적으로
                             어려운 건이고, 하필 이 저장소 **최대 근거**(46,911건)다.
        cdk-oob              awscdk-service-spec의 LICENSE를 아직 안 봤다.
        aws-price-list       약관이 재배포를 **금지**하는 것은 확인했으나
                             (`redistribution="denied"`), 라이선스 부여 자체는 따로
                             확인하지 않았다. 산출물에 가격 값이 남지 않아 급하지 않다.
        azure-rest-api-specs 저장소 LICENSE를 아직 안 봤다. `x-ms-mutability` 필드
                             하나만 쓴다.
    """
    keys = {s.key for s in unlicensed()}
    assert keys == {"cfn-schema", "cdk-oob", "aws-price-list", "azure-rest-api-specs"}, (
        f"라이선스 미확인 소스가 예상과 다르다: {keys}"
    )


def test_sources_without_a_license_grant_declare_why_we_included_them() -> None:
    """부여가 없는 소스는 **왜 수록했는지**가 반드시 적혀 있어야 한다.

    `all-rights-reserved`·`not-stated`는 "괜찮겠지"로 넘어가면 안 되는 자리다.
    라이선스가 답을 안 주므로 `redistribution`이 답을 줘야 한다.
    """
    for source in SOURCES.values():
        if source.license in ("all-rights-reserved", "not-stated"):
            assert source.redistribution, (
                f"{source.key}: 라이선스 부여가 없는데 재배포 판단이 비어 있다"
            )


def test_attribution_required_licenses_are_named_in_notice() -> None:
    """CC-BY-4.0은 **저작자 표시가 의무**다 — NOTICE에 이름이 없으면 위반이다.

    `test_redistribution_notice.py`가 `not-stated`·`denied`를 강제하는 것과 같은
    이유이고, 대상이 다르다. CC-BY 소스는 "넣어도 되나"가 아니라 "표시했나"가 조건이다.
    """
    from pathlib import Path

    notice = (Path(__file__).resolve().parent.parent / "NOTICE").read_text(encoding="utf-8")
    missing = [
        s.key for s in SOURCES.values()
        if s.license == "CC-BY-4.0" and s.key not in notice and _repo_of(s.url) not in notice
    ]
    assert not missing, f"CC-BY 소스가 NOTICE에 없다(저작자 표시 의무): {missing}"


def _repo_of(url: str) -> str:
    """`https://raw.githubusercontent.com/Org/Repo/ref/...` → `Org/Repo`.

    NOTICE는 소스 키가 아니라 저장소 이름으로 적는 곳이 있어(표기가 사람용이라)
    둘 중 하나만 나와도 표시된 것으로 친다.
    """
    parts = url.split("/")
    for host in ("raw.githubusercontent.com", "codeload.github.com", "github.com"):
        if host in parts:
            i = parts.index(host)
            if len(parts) > i + 2:
                return f"{parts[i + 1]}/{parts[i + 2]}"
    return url
