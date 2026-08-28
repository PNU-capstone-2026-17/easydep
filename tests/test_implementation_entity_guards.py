from pathlib import Path

from app.implementation.agents.workspace import ensure_referenced_entity_collections


def test_entity_collections_follow_existing_service_calls(tmp_path: Path) -> None:
    root = tmp_path / "application" / "src" / "main" / "java" / "example"
    entity = root / "persistence" / "entity"
    control = root / "application" / "impl"
    entity.mkdir(parents=True)
    control.mkdir(parents=True)
    (entity / "StudentEntity.java").write_text(
        "package example.persistence.entity;\n\n"
        "import jakarta.persistence.ManyToOne;\n"
        "public class StudentEntity {\n"
        "    @ManyToOne private SessionEntity session;\n"
        "}\n",
        encoding="utf-8",
    )
    (entity / "EnrollmentEntity.java").write_text(
        "package example.persistence.entity;\n\n"
        "import jakarta.persistence.ManyToOne;\n"
        "public class EnrollmentEntity {\n"
        "    @ManyToOne private StudentEntity student;\n"
        "    public void setStudent(StudentEntity value) {}\n"
        "}\n",
        encoding="utf-8",
    )
    (control / "DropService.java").write_text(
        "class DropService {\n"
        "  void drop(StudentEntity student, EnrollmentEntity enrollment) {\n"
        "    StudentEntity current = student;\n"
        "    Set<EnrollmentEntity> values = current.getEnrollments();\n"
        "    current.removeEnrollment(enrollment);\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )

    changed = ensure_referenced_entity_collections(tmp_path)

    source = (entity / "StudentEntity.java").read_text(encoding="utf-8")
    assert "@OneToMany(mappedBy = \"student\")" in source
    assert "getEnrollments()" in source
    assert "addEnrollment(EnrollmentEntity value)" in source
    assert "removeEnrollment(EnrollmentEntity value)" in source
    assert "application/src/main/java/example/persistence/entity/StudentEntity.java" in changed
