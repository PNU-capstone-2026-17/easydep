"""ERD 변환 — 관계가 실제로 선이 되는가 (네트워크 불필요).

## 이 파일은 한 번 뒤집혔다

예전 판은 **이름 추론을 옳은 동작으로 고정하고 있었다**: `Loan.memberId`라는 필드 이름을
보고 `Member`를 가리키는 외래키를 만드는 것을 확인하는 테스트가 둘 있었다. 그 코드는
실재하는 문제를 풀려고 들어왔었다 — BCE의 관계는 행위 흐름(Boundary→Control→Entity)이라
엔티티끼리의 데이터 관계를 거의 담지 않았고, 그래서 ERD에 선이 하나도 안 그려졌다.

**증상은 진짜였지만 해결 방향이 틀렸다.** 관계가 없는 것을 이름으로 메우면, 결정론적이긴
해도 근거는 없다 — `memberId → Member`는 이름에서 의도를 추론하는 것이다. 지금은 관계
자체를 요구하도록 고쳤고(추출 프롬프트 절차 8), 이름 추론은 지웠다.

아래 픽스처는 **실제 LLM(gpt-oss-120b)이 도서 대출 시스템에서 만든 BCE 그대로**라 값이
있다. 그래서 지우지 않고 두 판으로 갈랐다:

  `LIBRARY_BCE_TODAY`  다중도를 갖춘 판 — 외래키가 관계에서 나온다
  `LIBRARY_BCE_LEGACY` 원본 그대로 — 다중도가 없으므로 **사상되지 않고**, 그 사실이
                       검사 결과로 드러난다 (조용히 1:N으로 단정하지 않는다)
"""
from __future__ import annotations

import copy

from app.design.knowledge.detectors import erd_findings
from app.design.services.erd.mapping import build_logical_model
from app.design.services.erd.plantuml import generate_erd_from_bce_json

#: 실제 LLM 출력 그대로. 다중도가 없고, 엔티티 사이 관계도 Composition 하나뿐이다.
LIBRARY_BCE_LEGACY = {
    "Classes": [
        {"className": "Member", "stereotype": "Entity",
         "fields": ["memberId", "name", "loanLimit"]},
        {"className": "Book", "stereotype": "Entity",
         "fields": ["isbn", "title", "author"]},
        {"className": "Copy", "stereotype": "Entity",
         "fields": ["copyId", "isbn", "status"]},
        {"className": "Loan", "stereotype": "Entity",
         "fields": ["loanId", "memberId", "copyId", "dueDate"]},
        {"className": "BorrowBookController", "stereotype": "Control", "fields": []},
    ],
    "Relationships": [
        {"source": "Book", "target": "Copy", "type": "Composition"},
        # 행위 관계 — 엔티티가 아닌 쪽이 끼어 있으므로 ERD 에는 안 그려져야 한다.
        {"source": "BorrowBookController", "target": "Loan", "type": "Dependency"},
    ],
}


def _today() -> dict:
    """같은 도메인을 지금 스키마로 적은 판 — 자연키와 다중도가 있다."""
    model = copy.deepcopy(LIBRARY_BCE_LEGACY)
    by_name = {c["className"]: c for c in model["Classes"]}
    by_name["Member"].update(
        {"fields": ["memberId : String", "name : String", "loanLimit : Int"],
         "identifier": ["memberId"]}
    )
    by_name["Book"].update(
        {"fields": ["isbn : String", "title : String", "author : String"],
         "identifier": ["isbn"]}
    )
    by_name["Copy"].update(
        {"fields": ["copyId : String", "status : String"], "identifier": ["copyId"]}
    )
    by_name["Loan"].update({"fields": ["dueDate : Date"], "identifier": []})
    model["Relationships"] = [
        {"source": "Book", "target": "Copy", "type": "Composition",
         "sourceMultiplicity": "1", "targetMultiplicity": "1..*"},
        {"source": "Member", "target": "Loan", "type": "Association",
         "sourceMultiplicity": "1", "targetMultiplicity": "*"},
        {"source": "Copy", "target": "Loan", "type": "Association",
         "sourceMultiplicity": "1", "targetMultiplicity": "*"},
        {"source": "BorrowBookController", "target": "Loan", "type": "Dependency"},
    ]
    return model


LIBRARY_BCE_TODAY = _today()


# ---------------------------------------------------------------------------
# 지금: 외래키는 관계에서 나온다
# ---------------------------------------------------------------------------
def test_foreign_keys_come_from_relationships():
    """다쪽이 1쪽의 키를 든다. 자연키를 선언했으면 그 키를 든다.

    컬럼 이름에 테이블 이름을 겹쳐 붙이지 않는다 — `Member.memberId`를 가리키는 칸은
    `member_memberId`가 아니라 `memberId`다.
    """
    puml = generate_erd_from_bce_json(LIBRARY_BCE_TODAY)

    assert "memberId : VARCHAR(255) <<FK>>" in puml
    assert "copyId : VARCHAR(255) <<FK>>" in puml


def test_relationship_lines_are_drawn():
    """컬럼만 있고 선이 없으면 ERD 가 아니다.

    기호가 다중도를 그대로 옮긴다: 끝의 모양이 몇 개인지를, **하한까지** 말한다.
    """
    puml = generate_erd_from_bce_json(LIBRARY_BCE_TODAY)

    assert "Member ||..o{ Loan" in puml     # 회원 1 : 대출 다(0 이상)
    assert "Copy ||..o{ Loan" in puml       # 사본 1 : 대출 다(0 이상)
    assert "Book ||--|{ Copy" in puml       # 도서 1 : 사본 다(**1 이상**)


def test_composition_is_drawn_as_an_identifying_relationship():
    """합성과 연관이 **구별되는가.**

    한동안 구별되지 않았다. 다중도만 보고 기호를 정하도록 바꾸면서 관계의 종류를 안 쓰게
    됐고, 같은 다중도를 가진 Composition과 Association의 출력이 완전히 같았다. 종류를
    받아서 "아는 값인가"까지 검사해 놓고 쓰지 않는 것은 앞뒤가 안 맞는다.

    합성은 부분이 전체 없이 존재할 수 없다는 말이므로 **식별 관계**로 옮긴다: 실선,
    그리고 부분 쪽 외래키가 필수다. `Copy`는 `Book` 없이 존재할 수 없다.
    """
    puml = generate_erd_from_bce_json(LIBRARY_BCE_TODAY)
    copy_block = puml.split('entity "Copy"')[1].split("}")[0]

    assert "Book ||--|{ Copy" in puml        # 실선 = 식별 관계
    assert "Member ||..o{ Loan" in puml      # 점선 = 비식별 관계
    assert "<<not null>>" in copy_block


def test_not_null_comes_from_multiplicity_not_from_the_relationship_kind():
    """**필수 여부는 합성이 아니라 다중도가 정한다.**

    `Member "1" — Loan "*"`는 평범한 연관이지만, 회원 쪽이 `"1"`이므로 *"대출 하나마다
    회원이 정확히 하나"*다. 그러니 `Loan.memberId`는 비어 있을 수 없다.

    한동안 합성일 때만 `NOT NULL`이 붙었다. 그림에서는 안 보이는 차이라 눈치채기 어려운데,
    하류가 이 산출물로 DDL을 만들므로 제약 하나가 실제 스키마에서 사라지고 있었다.
    """
    puml = generate_erd_from_bce_json(LIBRARY_BCE_TODAY)
    loan_block = puml.split('entity "Loan"')[1].split("}")[0]

    # 합성이 아닌데도 필수다 — 다중도가 그렇게 말했으므로.
    assert "memberId : VARCHAR(255) <<FK>> <<not null>>" in loan_block
    assert "Member ||..o{ Loan" in puml       # 그런데 선은 여전히 점선(비식별)이다


def test_a_declared_identifier_is_the_key():
    """`identifier`가 있으면 대리키를 붙이지 않는다.

    예전에는 언제나 `{테이블}_id BIGINT`를 만들고, 이름이 겹치는 필드를 조용히 **버렸다.**
    그래서 `Book.isbn` 같은 자연키가 산출물에서 사라졌다.
    """
    puml = generate_erd_from_bce_json(LIBRARY_BCE_TODAY)
    book = puml.split('entity "Book"')[1].split("}")[0]

    assert "* isbn : VARCHAR(255)" in book
    assert "book_id" not in book


def test_a_table_without_an_identifier_gets_a_surrogate_key_and_says_so():
    """자연키가 없으면 우리가 붙이되, **붙였다는 사실이 모델에 남는다.**"""
    loan = next(
        t for t in build_logical_model(LIBRARY_BCE_TODAY)["Tables"] if t["name"] == "Loan"
    )

    assert loan["primaryKey"] == ["loan_id"]
    assert loan["keyOrigin"] == "surrogate"


def test_a_composite_unique_constraint_is_drawn_inside_the_table_block():
    """복합 유일 제약이 **표 블록 안에** 나오는가.

    컬럼의 `unique`는 불리언 하나라 여러 칸이 함께 유일한 것을 못 담는다. 담을 곳을
    표에 만들었으니 그리는 자리도 있어야 한다 — 안 그리면 상속이 복합 자연키를 강등한
    표에서 유일성이 그림과 하류에서 통째로 사라진다.

    **블록 안이어야 한다.** `design_context.slice_erd`가 `entity "N" as N {` 부터 `}`
    까지를 잘라 가므로, 밖에 두면 이 제약만 하류에서 조용히 빠진다.
    """
    puml = generate_erd_from_bce_json({
        "Classes": [
            {"className": "Base", "stereotype": "Entity", "fields": ["x : Int"],
             "identifier": [], "methods": []},
            {"className": "Sub", "stereotype": "Entity", "fields": ["a : String", "b : String"],
             "identifier": ["a", "b"], "methods": []},
        ],
        "Relationships": [{"source": "Sub", "target": "Base", "type": "Inheritance"}],
    })
    block = puml.split('entity "Sub" as Sub {')[1].split("}")[0]

    assert "unique (a, b)" in block
    # 칸마다 걸리지는 않는다 — `UNIQUE(a,b)`와 `UNIQUE(a) AND UNIQUE(b)`는 다른 제약이다.
    assert "<<unique>>" not in block


def test_non_entity_relationships_are_ignored():
    """Control 이 낀 관계는 데이터 관계가 아니다 — ERD 에 그리면 거짓말이 된다."""
    puml = generate_erd_from_bce_json(LIBRARY_BCE_TODAY)
    assert "BorrowBookController" not in puml


def test_a_behavioural_link_is_not_reported_as_unmapped():
    """행위 링크는 **결함이 아니다.** 안 그려지는 것이 정상이라 지적도 없어야 한다."""
    unmapped = build_logical_model(LIBRARY_BCE_TODAY)["Unmapped"]
    assert unmapped == [], unmapped


def test_entities_only():
    """Boundary/Control 은 테이블이 아니다."""
    puml = generate_erd_from_bce_json(LIBRARY_BCE_TODAY)
    for table in ("Member", "Book", "Copy", "Loan"):
        assert f'entity "{table}"' in puml
    assert generate_erd_from_bce_json(
        {"Classes": [{"className": "Form", "stereotype": "Boundary", "fields": []}],
         "Relationships": []}
    ) == ""


# ---------------------------------------------------------------------------
# 예전 데이터: 단정하지 않고 드러낸다
# ---------------------------------------------------------------------------
def test_a_relationship_without_multiplicity_is_not_guessed_at():
    """다중도가 없으면 1:N으로 **단정하지 않는다.**

    예전에는 단정했다. `Book ||..|{ Copy` 한 줄이 그려졌고, 그것이 모델이 말한 것인지
    렌더러가 정한 것인지 그림만 봐서는 알 수 없었다.
    """
    logical = build_logical_model(LIBRARY_BCE_LEGACY)

    assert logical["Relations"] == []
    assert [u["reason"] for u in logical["Unmapped"]] == ["multiplicity-missing"]


def test_the_missing_multiplicity_reaches_the_user_as_a_finding():
    """사상되지 못한 관계는 **검사 결과로 드러난다.**

    안 그러면 관계가 조용히 사라진 것을 아무도 못 본다 — 그림에는 그냥 선이 없을 뿐이다.
    """
    issues = [f.as_issue() for f in erd_findings(LIBRARY_BCE_LEGACY, {})]

    assert any("erd.relationship-mapped" in i for i in issues), issues


def test_fields_that_point_at_entities_by_name_are_reported_not_obeyed():
    """`Loan.memberId`는 외래키가 **되지 않고**, 대신 지적이 된다.

    지운 이름 추론이 하던 일과 지금 하는 일이 정확히 갈리는 자리다.
    """
    puml = generate_erd_from_bce_json(LIBRARY_BCE_LEGACY)
    issues = [f.as_issue() for f in erd_findings(LIBRARY_BCE_LEGACY, {})]

    assert "<<FK>>" not in puml
    assert any("erd.field-looks-like-reference" in i for i in issues), issues
