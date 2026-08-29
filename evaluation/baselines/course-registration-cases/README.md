# 수강신청 단일 골드셋

현재 EasyDep의 유일한 골드셋 후보는
[`e1-course-registration-aws.json`](e1-course-registration-aws.json)이다. 시스템 입력은 영어만
지원한다. 다른 도메인·가용성 변형·대표 7-case 입력은 골드 판정에 사용하지 않는다.

## 평가 의도

이 사례는 요구사항에서 배포 다이어그램까지 하나의 산출물 연쇄를 검증한다. 특정 클래스명이나
문장 수를 정답으로 고정하지 않고 다음 구조와 추적 가능성을 판정한다.

- `University User`를 일반 액터로 하고 Student, Professor, Academic Administrator가 일반화한다.
- 공통 강좌 검색은 일반 액터의 목표이며 전문 액터에 중복 연결하지 않는다.
- 등록과 수강 변경은 공통 자격 검증을 필수로 사용하므로 `include`로 표현한다.
- 시간표 내보내기는 현재 신청 조회 중 선택 가능한 `extend`로 표현하며 조건을 보존한다.
- 대기열 참여는 정원 때문에 등록이 거절되고 대기열이 활성화된 경우의 `extend`로 표현한다.
- DBMS는 업무 액터가 아니며 유스케이스 다이어그램에 투영하지 않는다.
- 정확한 UC 이름이나 개수를 고정하지 않고 위 업무 목표와 관계의 근거·추적 가능성을 판정한다.

## 권위 입력과 독립 oracle

- `e1-course-registration-aws.json`: 자연어 요구사항, AWS Seoul Region과 E1 배포 제약
- `business-oracle.json`: 생성 앱의 순차·동시성 업무 검증
- `persistence-oracle.json`: 데이터 계층 재생성 후 영속성 검증
- `database-unavailable-oracle.json`: DBMS 장애 시 애플리케이션 동작 검증

API 경로, Java·Spring Boot, PostgreSQL 버전, 환경변수 이름과 합성 데이터는 요구사항의
정답으로 넣지 않는다. 설계·구현 결과는 위 독립 oracle로 검증한다.

## 현재 실행 방법

구형 체크포인트 전용 실행기는 제거했다. 사례를 다시 실행할 때에는 개발 서버를 켠 뒤
프론트엔드와 같은 Workspace API 실행기에 입력 파일을 전달한다. 결과는 지정한 JSON 파일에
원시 Workspace 상태와 산출물 응답으로 저장된다.

```powershell
python -X utf8 -m evaluation.easydep.product `
  --message-file C:\temp\course-registration-requirements.txt `
  --stop-after testing `
  --output .easydep/course-registration-result.json
```

텍스트 파일에는 위 JSON의 `requirements` 문장과 `cloudConstraints`를 사람이 읽는 일반
문장으로 옮긴다. 결과 JSON은 정답 판정이나 gold 승격을 자동으로 수행하지 않는다. LLM
결과는 실행마다 달라질 수 있으므로 사람이 업무 목표, 단계 연결과 실패 위치를 확인한다.
