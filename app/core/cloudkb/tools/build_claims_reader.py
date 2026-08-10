"""claims.json을 사람이 읽는 Markdown으로 풀어 쓴다.

산출물은 document/archive/claims-reader-2026-08-03.md다. 이 도구는 설명을
추측하지 않는다. claim과 evidence에 이미 들어 있는 값, 그리고 evidence.definition이
가리키는 이 저장소의 실험 코드 위치만 표로 옮긴다.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLAIMS = ROOT / "depkb" / "claims.json"
OUTPUT = ROOT / "document" / "archive" / "claims-reader-2026-08-03.md"


def cell(value: object) -> str:
    """Markdown 표 한 칸. 줄바꿈과 파이프를 안전하게 만든다."""
    if value is None:
        return "—"
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def step_name_explanation(step: str) -> str:
    """단계 ID의 일반적인 읽는 법. 앞 글자의 뜻을 발명하지 않는다."""
    if "." not in step:
        return (f"`{step}`은 이 실험이 붙인 단계 이름이다. 약어의 정확한 뜻은 "
                "정의 코드를 확인한다.")
    number, label = step.split(".", 1)
    return (f"`{number}`은 이 실험 안에서만 쓰는 묶음·순번이고 전역 표준이 아니다. "
            f"`{label}`은 시험의 짧은 이름이다. 실제 요청과 값은 정의 코드를 따른다.")


def write() -> None:
    doc = json.loads(CLAIMS.read_text(encoding="utf-8"))
    claims = doc["claims"]
    counts = Counter(c["question"] for c in claims)
    references = {}
    for claim in claims:
        for evidence in claim["evidence"]:
            if "experiment" not in evidence:
                continue
            references.setdefault(
                (evidence["experiment"], evidence["step"]), evidence)

    lines = [
        "# `claims.json` 읽는 법 — 실제 값과 실험 추적표",
        "",
        "> **불변 안내 스냅샷 (2026-08-03).** 이 문서는 현재 `claims.json`을 "
        "기계적으로 풀어 쓴 것이다. 새 결과를 만들 때는 "
        "`python -m app.core.cloudkb.depkb.build_claims` 뒤에 이 도구를 다시 실행한다.",
        "",
        "## 1. 이 파일은 무엇인가",
        "",
        "`claims.json`의 한 항목은 **한 CSP에서 한 자원 관계에 대해 한 질문을 던진 결과**다. "
        "예를 들어 `gcp / fileSystem → network / existence / required`는 "
        "\"GCP에서 파일 시스템을 만들 때 네트워크가 필수인가?\"라는 질문의 결론이다.",
        "",
        "이 파일은 실행 명령 모음이나 Cloud-Barista의 원본 데이터가 아니다. 이 저장소가 "
        "CSP 스키마와 직접 실험을 읽어 만든 분석 산출물이다. `experiment`와 `step`도 "
        "AWS·Azure·GCP가 정한 코드가 아니라 **이 저장소의 실험 작성자가 붙인 이름**이다.",
        "",
        "## 2. 한 항목을 읽는 순서",
        "",
        "1. `csp` — 어느 클라우드에서 얻은 결론인지 본다.",
        "2. `subject` → `object` — **subject를 만들거나 작동시킬 때 object를 보는 관계**다.",
        "3. `question` — 생성, 삭제/정리, 기능 중 무엇을 물었는지 본다.",
        "4. `verdict` — 필수·선택·관계 확인 중 무엇인지 읽는다.",
        "5. `predicate` — 그 결론이 성립하는 조건이다. 없으면 `별도 조건 없음`이다.",
        "6. `evidence` — 그 결론을 재현할 수 있는 원천 위치다. 실험 ID를 보고 끝내지 말고 "
        "`resultFile`의 관측값과 `definition.file:line`의 요청 코드를 함께 본다.",
        "",
        "## 3. 필드 사전",
        "",
        "| 필드 | 실제 값의 예 | 뜻 | 어디를 보면 되나 |",
        "|---|---|---|---|",
        "| `subject` | `vm` | 관계의 주체. 이 자원을 만들거나 기능시키는 쪽 | `depkb/vocabulary.py`, claim |",
        "| `object` | `nic` | 주체가 요구·선택·참조·기능상 의존하는 대상 | claim |",
        "| `csp` | `azure` | 이 결론이 관측된 클라우드. 다른 CSP에 그대로 일반화하면 안 됨 | claim |",
        "| `question` | `existence` | `existence`=생성 가능 여부, `lifecycle`=삭제/정리 관계, `function`=실제 기능 영향 | `closure.py`, claim |",
        "| `verdict` | `required` | `required`=없으면 생성 불가, `optional`=없어도 생성 가능, `holds`=삭제/기능 관계를 관측, `unknown`=미확인 | `build_claims.py` |",
        "| `predicate` | `배치 조건: ...` | 위 결론을 제한하는 조건. 예: 서로 다른 AZ, 둘 중 하나만 선택, 서버 자동 생성 | claim |",
        "| `constraint` | `{\"minCount\": 2}` | 조건 중 기계가 검사할 수 있게 구조화한 값. 없으면 `null` | `infra_intent.py`, `check.py` |",
        "| `signal` | `inbound-tcp` | 기능 의존을 무엇으로 측정했는지. 생성/삭제 claim에는 `null` | `build_claims.py:SIGNALS` |",
        "| `oracle` | `apply` | 가장 강한 증거 층. `schema`=후보, `preflight`=사전검증, `apply`=실제 제어면 호출 | `build_claims.py` |",
        "| `note` | 평이한 결론 문장 | 사람이 먼저 읽을 요약. 판정·조건·기능 신호를 다시 풀어 쓴 것 | claim |",
        "| `evidence` | 배열 | 위 결론을 뒷받침하는 원천. 스키마 근거와 실험 근거가 섞일 수 있음 | 아래 §6 |",
        "",
        "## 4. `evidence`의 필드와 `A2.dangling-network`의 정체",
        "",
        "| 필드 | 뜻 |",
        "|---|---|",
        "| `layer` | `schema`, `preflight`, `apply` 중 증거의 층. 실제 API 호출인 `apply`가 가장 강하다. |",
        "| `experiment` | 예: `gcp-fs-2026-07-31`. 이 저장소의 `depkb/experiments/` 아래 실험 폴더 이름이다. CSP가 발급한 ID가 아니다. |",
        "| `step` | 예: `A2.dangling-network`. 그 실험의 `run.py`에서 연구자가 붙인 단계 이름이다. 앞 `A2`는 **그 실험 안의 순번**이며 전역 약어 표준이 아니다. |",
        "| `code` | 기대한 결과. `ok`는 성공, 그 외는 실험에서 확인하려던 CSP 오류 코드다. |",
        "| `observed` | 위 `code`를 문장으로 풀어 쓴 관측 결과다. |",
        "| `resultFile` | 실제 성공/실패·오류 코드·원문 발췌가 저장된 JSON 경로다. |",
        "| `definition.file`, `definition.line` | `step` 이름과 요청을 정의한 이 저장소의 Python 코드 위치다. 이 위치가 **그 단계 이름의 뜻을 정하는 원천**이다. |",
        "| `sourceKind` | 단계 이름이 실험 코드에 정의됐는지, 결과 JSON에만 남았는지 표시한다. |",
        "",
        "### 요청에서 든 예: `A2.dangling-network`",
        "",
        "이 값은 `gcp-fs-2026-07-31` 실험의 단계 이름이다. `A2` 자체에는 "
        "\"두 번째 존재 의존\" 같은 공통 규칙이 없다. `dangling-network`는 존재하지 않는 "
        "네트워크를 가리키는 파일시스템 요청을 뜻하도록 **이 저장소의 실험 코드가 붙인 이름**이다. "
        "정확한 명령·입력값은 아래 §6에서 이 값의 `definition.file:line`을 열어 확인한다. "
        "`results.json`은 그 요청 뒤 CSP가 돌려준 결과를 보관한다.",
        "",
        "## 5. 현재 claims 전체 목록",
        "",
        f"현재 산출물은 {len(claims)}개 claim이다: existence {counts['existence']}개, "
        f"lifecycle {counts['lifecycle']}개, function {counts['function']}개.",
        "",
        "| CSP | 관계 | 질문 | 판정 | 조건 | 사람이 읽는 결론 | 증거 수 |",
        "|---|---|---|---|---|---|---:|",
    ]
    for claim in claims:
        lines.append(
            f"| {cell(claim['csp'])} | `{cell(claim['subject'])}` → `{cell(claim['object'])}` "
            f"| `{cell(claim['question'])}` | `{cell(claim['verdict'])}` "
            f"| {cell(claim['predicate'])} | {cell(claim['note'])} "
            f"| {len(claim['evidence'])} |"
        )

    lines += [
        "",
        "## 6. 모든 실험 evidence 참조",
        "",
        "아래 표는 claims에 실제로 사용된 실험 참조를 중복 없이 모은 것이다. `정의 위치`는 "
        "단계 이름과 요청을 만든 코드다. `결과 위치`에는 CSP 응답의 오류 코드와 발췌가 있다. "
        "따라서 표의 어느 행도 단순한 내부 약어로 끝나지 않고, 항상 재현 원천으로 내려갈 수 있다.",
        "",
        "| 실험 | 단계 ID | 단계 ID를 읽는 법 | 관측 | 정의 위치 | 결과 위치 |",
        "|---|---|---|---|---|---|",
    ]
    for (experiment, step), evidence in sorted(references.items()):
        definition = evidence["definition"]
        where = (f"`{definition['file']}:{definition['line']}`<br>"
                 f"{cell(definition['sourceKind'])}"
                 if definition["file"] else cell(definition["sourceKind"]))
        lines.append(
            f"| `{experiment}` | `{step}` | {cell(step_name_explanation(step))} "
            f"| {cell(evidence['observed'])} | {where} | `{evidence['resultFile']}` |"
        )

    lines += [
        "",
        "## 7. 읽을 때 지켜야 할 경계",
        "",
        "- `required`는 **그 claim이 다루는 생성 경로**에서만 필수라는 뜻이다. 모든 배포 방식에서의 보편 법칙이 아니다.",
        "- `optional`은 생성 가능 여부의 결론이다. 보안·외부 접속·서비스 정상 동작까지 선택이라는 뜻은 아니다.",
        "- `holds`는 lifecycle/function 질문에서 관계를 관측했다는 뜻이다. `required`의 동의어가 아니다.",
        "- `resultFile`의 오류 문자열은 CSP가 준 원문이다. 이해하기 어려운 원문이라도 삭제하지 않는다. 이 문서의 설명과 `note`가 먼저 읽을 층이고, 원문은 검증할 때 본다.",
        "- `definition`은 우리 실험 코드다. 따라서 이름 자체는 우리 것이고, 그 실행 결과·오류 코드는 CSP의 것이다. 둘을 구별해야 한다.",
        "",
    ]
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"claims reader: {len(claims)} claims, {len(references)} evidence references -> {OUTPUT}")


if __name__ == "__main__":
    write()
