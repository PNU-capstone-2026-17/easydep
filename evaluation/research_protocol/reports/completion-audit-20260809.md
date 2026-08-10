# VM 기반 졸업과제 개발 완료 감사

## 판정

`docs/research.md`를 변경하지 않고, AWS·Azure·GCP의 Docker-on-Linux-VM 범위에서 요구한
개발·연구 기반을 구현했다. 이 판정은 시스템 구현과 개발 단계 증거의 완료를 뜻하며, 반복 본실험이나
전체 클라우드에 대한 일반화가 완료됐다는 뜻은 아니다.

## 목표별 증거

| 완료 조건 | 구현·실행 증거 | 판정과 한계 |
|---|---|---|
| 요구사항 산출물 검증과 사용자 피드백 | 요구사항→설계→구현→테스트 단계, 질문·보류, 같은 run의 요구사항 revision, revision별 내부 checkpoint, HA–노드 상태 충돌 실행 | 구현 완료. 단일 개발 사례를 질문 정확도의 모집단 성능으로 확대하지 않음 |
| 클라우드 특성·의존성·선택 가이드 | 공식 근거 DepKB, PS/LB/TLS capability와 3사 다대다 projection, 근거에서 파생한 필수 edge, 65,032개 VM 가격·사양 snapshot과 하한·예산·경고 기반 선택 | 제한된 VM 범위 완료. 전체 비용·실제 처리량·최적 VM과 범용 클라우드 모델은 주장하지 않음 |
| 에이전트 연계와 단계별 산출물 | 역할별 member/builtin/LLM provider, 4단계와 구현 하위 작업, 실패 소유 작업부터 재개, 산출물 hash·checkpoint·단계/LLM/provider/기능 시간 | 구현 완료. 멀티 에이전트 구조만의 인과 우월성은 주장하지 않음 |
| 앱 요구와 클라우드 환경 충돌 해소 | build/runtime dependency, DB integration, port, storage path, 파괴적 초기화, 장치 식별, HA–로컬 상태 진단과 수정 소유 작업; full/no-validator 고정입력 및 LLM 수정 파일럿 | 개발 범위 완료. 특정 오류 문자열 대신 구조화 계약과 상태 의미를 사용함 |
| 앱 기능과 클라우드 생성 가능성 분리 | provider validate, build, container, health, 업무 API, 재시작 영속성, cloud apply와 cleanup을 별도 gate로 기록 | 완료. HA 파일럿은 앱 기능 통과 뒤 IaC 영속성 누락으로 적격 실패 처리됨 |

## 오버피팅 방지 확인

- `notes`, SQLite, 고정 mount 경로를 일반 규칙으로 사용하지 않는다. `/srv/catalog-data`와 임의
  capability ID를 사용한 회귀시험에서도 `applicationState.durability=persistent` 의미가 계약
  planner와 VM delivery까지 전달된다.
- cardinality 문자열을 최소 리소스 수로 해석하지 않는다. 구성요소 존재, 명시적 reference,
  cardinality, runtime constraint, 앱 기능을 분리하며 미구현 gate는 `not-measured`로 남긴다.
- P1~P3은 모델 근거나 실험군이 아니라 종단 회귀 사례다. DepKB는 동일 입력 full/no-depkb,
  validator는 동일 snapshot full/no-validator에서만 제한적으로 해석한다.
- 과거 neutral-layer v2 freeze와 현재 평가기를 섞지 않는다. 활성 평가기 hash drift는 동결 실험을
  다시 승인하는 대신 명시적으로 검출한다.

## 코드와 실행환경 상태

- 전체 수집 1,289개 중 1,240개가 통과했고 49개는 live endpoint, 미보유 GCP 원천 snapshot,
  PlantUML 등 명시된 환경 조건으로 skip됐다. 추가 subtest 6개도 통과했으며 skip을 강제 성공으로
  바꾸지 않았다.
- `docs/research.md`는 변경하지 않았다.
- EasyDep Docker container·volume·image와 세 CSP의 실험 식별 범위 잔여 자원은 0이다.
- 장시간 Python 프로세스는 VS Code formatter로 확인했다. 고정 provider cache는 재검증 시간 절감을
  위해 유지하고, 임시 pytest 디렉터리는 제거한다.

## 후속 평가로 남기는 항목

다음은 구현 완료 판정과 분리된 논문 평가 단계다.

1. 검증된 hash 후보와 불변 image digest가 함께 생긴 경우에만 Azure P2 한 셀을
   `apply → ready → 업무 기능 → restart 영속성 → destroy → residual 0`으로 실행한다.
2. 현재 리비전의 EasyDep·CoT·MetaGPT 각 1회 파일럿 후 감당 가능한 반복 수를 정한다.
3. 반복이 부족하면 효과크기나 인과 일반화 대신 원시 사례 결과와 실패·검열 한계를 보고한다.

안전 후보가 없는 상태에서 cloud apply를 시작하거나, 성공할 때까지 LLM 생성을 반복하거나, 새
capability와 범용 메타모델을 추가하는 것은 후속 계획에 포함하지 않는다.
