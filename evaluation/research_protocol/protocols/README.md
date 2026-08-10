# 활성 연구 프로토콜 입력

이 디렉터리는 현재 실행기가 직접 읽는 사례와 실행 설정만 보관한다. 측정 결과와 과거 실행 기록은 상위 디렉터리 또는 `artifacts/`에 두며 입력과 섞지 않는다.

- `ambiguity-cases.json`: 질문·보류 정책 사례
- `app-cloud-ablation-cases.json`: 앱-클라우드 계약 validator 절제 사례
- `app-cloud-snapshot-cases.json`: 동일 스냅숏 검증·부분 복구 사례
- `capacity-recommendation-cases.json`: 저장된 부하 측정의 용량 추천 사례
- `component-fixed-input-config.json`: 동일 앱·동일 capability 입력 component 매트릭스 설정

사례별 경로·요구·oracle은 이 JSON에서 선언한다. Python 실행기에 사례 기본값을 다시 넣지 않는다.
