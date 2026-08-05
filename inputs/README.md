# inputs — 파이프라인 입력 데이터셋

파이프라인(step2 액터·유스케이스 → step3 명세 → step4 다이어그램)을 돌릴 입력을 JSON으로
보관한다. 파일 하나 = 데이터셋 하나이며, 테스트(`tests/conftest.py`의 `dataset_names()`)와
러너(`app/runner.py`)가 **이 폴더를 공유 소스**로 사용한다.

## 형식

```json
{
  "name": "<데이터셋 이름>",
  "description": "<무엇을 검증하려는 세트인지>",
  "resource_constraints_text": "<별도로 입력받은 클라우드 제약 원문>",
  "classified": [
    { "id": "R1", "text": "...", "type": "FR" },
    { "id": "N1", "text": "...", "type": "NFR" }
  ]
}
```

- `classified`는 step1 reconcile 산출물과 같은 형태다(개별 요구사항 + FR/NFR 라벨 + id).
- id 규칙은 자유지만, FR/NFR을 구분 가능한 접두어(R*/N* 등)를 권장한다.
- `resource_constraints_text`는 선택이며, 있으면 `RESOURCE_SPEC` 구조화 단계에 전달한다.
- 요구사항 항목은 `id`, `text`, `type`만 사용하므로 나머지 필드는 없어도 된다.

## 아티팩트로 실행 (러너)

입력을 실제 파이프라인에 태워 `artifacts/run_<UTC>_<sha>/`에 결과를 남긴다:

```bash
python -m app.run_pipeline shopping_mall            # 이름 하나
python -m app.run_pipeline shopping_mall note_taking
python -m app.run_pipeline --all                    # inputs/*.json 전부
python -m app.run_pipeline --input path/to/custom.json
```

산출물(run 디렉토리): `input.json`(재현용), `manifest.json`(config·input_sha256·스테이지 요약),
`deployment_needs.json` / `resource_spec.json` / `resource_intake.json` / `traceability.json`,
`actors.json` / `use_cases.json` / `coverage.json` / `relationships.json`, `diagram.puml`,
`use_cases/uc_NN_<slug>/{use_case.json, spec.json}`. LLM(NIM)을 호출하므로 `.env`(API_KEY) 필요.

## 테스트로 선택 실행

라이브 테스트(`test_live_step2/3/4`)는 데이터셋별로 파라미터라이즈된다.

```bash
# 전체 데이터셋
RUN_LIVE_TESTS=1 python -m pytest tests/test_step4.py -k live -s

# 이름으로 선택 (pytest -k 또는 환경변수)
RUN_LIVE_TESTS=1 python -m pytest tests/test_step4.py -k "live and shopping_mall" -s
STEP2_DATASET=shopping_mall RUN_LIVE_TESTS=1 python -m pytest tests/test_step4.py -k live -s
```

## 새 데이터셋 추가 / PURE 등 외부 데이터셋

이 폴더에 위 형식의 `*.json`을 떨구면 테스트·러너가 자동으로 잡는다.

PURE(공개 요구사항 문서 데이터셋) 같은 원문 코퍼스는 FR/NFR 라벨·id가 없으므로, step1 분류를
한 번 태워 `classified` 형태로 변환한 뒤 여기에 저장하는 변환 스텝이 필요하다(추후 스크립트로
추가 예정). 변환만 되면 실행/테스트 방식은 위와 동일하다.
