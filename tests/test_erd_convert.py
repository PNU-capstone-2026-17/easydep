"""ERD 변환 — 테이블 사이에 선이 실제로 그려지는가 (네트워크 불필요).

ERD 는 클래스 다이어그램의 <<Entity>> 를 투영한 것이라 LLM 을 안 부른다. 그래서
변환기가 전부다 — 여기서 놓치면 ERD 가 조용히 "테이블만 있고 관계는 없는" 그림이 된다.

실제로 그런 일이 있었다. FK 를 Relationships 에서만 뽑았는데, BCE 의 관계는 행위 흐름
(Boundary→Control→Entity)이라 **엔티티끼리의 데이터 관계를 거의 담지 않는다.** LLM 은
`Loan` 에 `memberId`·`copyId` 를 필드로 넣어두고 관계는 안 만들어서, 선이 하나만 그려졌다.
"""
from __future__ import annotations

from app.design.services.erd.plantuml import generate_erd_from_bce_json

# 실제 LLM(gpt-oss-120b)이 도서 대출 시스템에서 만들어낸 BCE 를 그대로 옮긴 것.
LIBRARY_BCE = {
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


def test_foreign_keys_are_inferred_from_field_names():
    """`Loan.memberId` 는 컬럼이 아니라 Member 를 가리키는 FK 다.

    관계 목록에 없어도 필드 이름이 이미 그 정보를 담고 있다.
    """
    puml = generate_erd_from_bce_json(LIBRARY_BCE)

    assert "member_id : BIGINT <<FK>>" in puml
    assert "copy_id : BIGINT <<FK>>" in puml
    # FK 로 승격됐으므로 평범한 문자열 컬럼으로 또 나오면 안 된다.
    assert "memberId : VARCHAR" not in puml
    assert "copyId : VARCHAR" not in puml


def test_relationship_lines_are_drawn_for_inferred_keys():
    """FK 를 찾았으면 선도 그려야 한다 — 컬럼만 있고 선이 없으면 ERD 가 아니다."""
    puml = generate_erd_from_bce_json(LIBRARY_BCE)

    assert "Member ||..o{ Loan" in puml     # 회원 1 : 대출 N
    assert "Copy ||..o{ Loan" in puml       # 사본 1 : 대출 N
    assert "Book ||..|{ Copy" in puml       # 명시된 Composition


def test_non_entity_relationships_are_ignored():
    """Control 이 낀 관계는 데이터 관계가 아니다 — ERD 에 그리면 거짓말이 된다."""
    puml = generate_erd_from_bce_json(LIBRARY_BCE)

    assert "BorrowBookController" not in puml


def test_the_entitys_own_id_field_does_not_duplicate_the_primary_key():
    """`Member.memberId` 와 합성 PK `member_id` 는 같은 것이다.

    둘 다 찍으면 한 테이블에 식별자가 두 개 있는 것처럼 보인다.
    """
    puml = generate_erd_from_bce_json(LIBRARY_BCE)
    member_block = puml.split('entity "Member"')[1].split("}")[0]

    assert "* member_id : BIGINT" in member_block
    assert "memberId" not in member_block


def test_a_field_pointing_at_no_entity_stays_a_plain_column():
    """`dueDate` 는 아무 엔티티도 안 가리킨다 — 그냥 컬럼이어야 한다."""
    puml = generate_erd_from_bce_json(LIBRARY_BCE)
    assert "dueDate : VARCHAR(255)" in puml


def test_self_reference_does_not_create_a_loop():
    """자기 자신을 가리키는 것처럼 보이는 필드로 자기→자기 선을 그리면 안 된다."""
    puml = generate_erd_from_bce_json({
        "Classes": [{"className": "Node", "stereotype": "Entity",
                     "fields": ["nodeId", "label"]}],
        "Relationships": [],
    })
    assert "Node ||..o{ Node" not in puml


def test_entities_only():
    """Boundary/Control 은 테이블이 아니다."""
    puml = generate_erd_from_bce_json(LIBRARY_BCE)
    for table in ("Member", "Book", "Copy", "Loan"):
        assert f'entity "{table}"' in puml
    assert generate_erd_from_bce_json(
        {"Classes": [{"className": "Form", "stereotype": "Boundary", "fields": []}],
         "Relationships": []}
    ) == ""
