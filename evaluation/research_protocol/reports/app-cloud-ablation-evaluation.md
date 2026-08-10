# 앱–클라우드 일관성 validator 고정입력 절제평가

## 목적

앱 생성 차이를 섞지 않고 동일한 앱 또는 계약 입력에서 일관성 validator만 켜고 끈다. 범위는 현재 시스템이 실제로 지원하는 Java 21 Spring Boot 애플리케이션의 Docker-on-Linux-VM 배포다. 업무 도메인이나 SQLite 자체를 연구 대상으로 삼지 않고 다음 교차 계층 경계만 평가한다.

- 소스가 사용하는 API와 Gradle build dependency
- 선택한 런타임 통합과 제공 모듈
- 앱 listen port와 cloud/container backend port
- 앱 데이터 접근 경로와 cloud mount path

`no-consistency-validator` variant는 DepKB, 프롬프트, LLM, repair feedback을 그대로 두고 scaffold·logic·VM delivery의 일관성 진단 게이트만 끈다. 자동 계약 관측과 deployment binding 계획은 두 조건에 공통으로 유지한다.

## 근거

- Gradle 공식 문서는 `implementation`과 `runtimeOnly` 등이 compile/runtime classpath에서 서로 다른 역할을 한다고 설명한다: [Gradle dependency 선언](https://docs.gradle.org/current/userguide/declaring_dependencies.html).
- Spring Data JPA는 Jakarta Persistence API용 repository 지원임을 명시한다: [Spring Data JPA 공식 문서](https://docs.spring.io/spring-data/jpa/reference/index.html).
- Hibernate 공식 문서는 SQLite dialect가 core가 아니라 `hibernate-community-dialects` 추가 artifact에 있음을 명시한다: [Hibernate 6.6 dialect](https://docs.hibernate.org/orm/6.6/dialect/).
- Spring Boot는 환경별 external configuration과 `server.port` 재정의를 지원한다: [Spring Boot external configuration](https://docs.spring.io/spring-boot/3.4/reference/features/external-config.html).
- Docker 공식 문서는 `EXPOSE`가 컨테이너의 listen port를 기술하며 실제 publish mapping과 구분된다고 설명한다: [Dockerfile reference](https://docs.docker.com/reference/dockerfile).

이 자료들은 각 기술의 관측 의미를 뒷받침한다. 앱 값과 배포 값의 equality binding, 진단 코드, 수정 소유 작업은 표준 주장이 아니라 EasyDep의 연구 가설이다.

## 사례와 결과

입력은 [`protocols/app-cloud-ablation-cases.json`](../protocols/app-cloud-ablation-cases.json), 원시 결과는 [`measurements/2026-08-development/app-cloud-ablation-result-20260809.json`](../measurements/2026-08-development/app-cloud-ablation-result-20260809.json)이다. 각 경계마다 mismatch 1건과 대응 control 1건을 두었다.

| 지표 | 결과 |
|---|---:|
| 동일 입력 arm | 8/8 |
| mismatch 조기 탐지 | full 4/4, no-validator 0/4 |
| control 오탐 | full 0/4 |
| 진단→수정 소유 작업 일치 | 4/4 |
| evaluator 내부 실행시간 | 약 0.065초 |
| LLM 호출·cloud apply | 0 |

`no-validator 0/4`는 해당 arm이 나쁘다는 종단 결과가 아니라 조기 진단 게이트가 꺼졌다는 처치 충실도다. 이후 build·container·기능 test가 같은 문제를 탐지할 수 있다.

## 해석 제한과 다음 게이트

현재 결과로 주장할 수 있는 것은 “고정된 네 종류의 구조적 불일치에서 validator가 기대 진단을 만들고 적절한 하위 작업으로 연결한다”까지다. 다음은 아직 증명하지 않았다.

- LLM이 진단을 받아 올바르게 수정하는 비율
- 수정하지 않아야 할 파일을 바꾸는 오수정률
- full이 no-validator보다 더 빨리 실패하는지 또는 복구되는지
- 수정 후 build·HTTP 업무 기능·재시작 영속성 성공

따라서 다음 실험은 동일한 사전 생성 앱 snapshot을 두 arm에 복제하고, full은 진단 소유 하위 작업부터 한 번만 수정하며 no-validator는 downstream test까지 진행한다. 발견 단계, 수정 파일, 상류 재실행 여부, 복구시간, 최종 기능을 기록한다. 새 진단 규칙이나 사례별 오류 문자열은 추가하지 않는다.

## 동일 생성 앱 스냅샷 파일럿

[`protocols/app-cloud-snapshot-cases.json`](../protocols/app-cloud-snapshot-cases.json)과 [`measurements/2026-08-development/app-cloud-snapshot-pilot-20260809.json`](../measurements/2026-08-development/app-cloud-snapshot-pilot-20260809.json)은 과거 완료 run의
application artifact를 캐시나 build 출력 없이 복제한다. 변형을 한 번 적용한 뒤 두 arm의 tree
SHA-256이 같은지 확인하므로, arm별 앱 생성 차이는 섞이지 않는다. 기준 스냅샷은 현재 앱 계약과
IaC binding 검증을 다시 통과해야 하며 과거 manifest의 `completed`만으로 채택하지 않는다.

초기 저장 사례에 선택했던 과거 P2 artifact는 현재 기준에서 앱 의존성·DB 통합 오류가 발견되어
제외했다. 최종 세 사례는 모두 현재 검증을 통과한 같은 P1 스냅샷에서 만들었다. 저장 경로는 특정
DB가 아니라 Java 파일 I/O로 관측하며, 연구 처치 파일은 운영 진단 규칙에 추가하지 않았다.

| 사례 | 두 arm 입력 동일 | full 조기 진단 | no-validator Gradle 결과 | Gradle 시간 |
|---|---:|---|---|---:|
| build/runtime dependency | 예 | `APP-DEP-001` | 실패, `APP-DEP-001` | 27.963초 |
| container port binding | 예 | `BIND-PORT-001` | 통과 | 45.085초 |
| container storage target | 예 | `BIND-STORAGE-001` | 통과 | 37.793초 |

세 사례 모두 full은 downstream 전에 올바른 소유 하위 작업으로 연결했다. no-validator에서는
컴파일 의존성만 후속 앱 테스트가 탐지했고, 포트와 저장 target 불일치는 앱 단위 테스트가 모두
통과했다. 이는 앱 테스트 성공이 배포 binding 성공을 대신하지 못한다는 관찰이며, 실제 cloud
기능 실패를 측정한 결과는 아니다.

또한 이 파일럿에서 호스트 디스크 mount와 컨테이너 target을 한 종류로 합치면 합법적인
`/mnt/data:/srv/state` 구성을 잘못 해석할 수 있음을 확인했다. 관측기를 `guestMountPath`와
`containerMountPath`로 분리하고, 앱의 접근 경로는 Docker target에 대응시켰다. 직접 VM에서
실행하는 경우처럼 컨테이너 target이 없을 때만 guest mount를 대체 관측값으로 사용한다.

위의 고정입력 단계 자체는 LLM 수정 실행, 변경 파일 범위, 수정 후 업무 HTTP, 실제 cloud
apply·cleanup을 측정하지 않는다. 따라서 그 단계의 해석은 조기 탐지 시점과 후속 앱 테스트의
관측 사각지대에 한정한다. LLM 수정 파일럿은 아래에서 별도로 보고한다.

> 2026-08-10 재실행 주의: 아래 2026-08-09 파일럿은 당시 코드의 개발 기록이다. 현재 코드로
> 동일 스냅샷 세 건을 다시 실행한 최신 결과는
> `artifacts/confirmatory/app-cloud-repairs.json`이며 dependency·port 2건만 진단 해소와 앱 테스트를
> 통과했고 storage는 제한된 한 번의 수리 뒤 실패했다. 현재 결론에는 최신 재실행을 사용한다.

## 소유 하위 작업 LLM 수정 파일럿

`run_app_cloud_snapshot_repairs.py`는 각 변형 스냅샷을 임시 작업공간에 복제하고 기대 진단을 다시
확인한 뒤, 진단 소유 하위 작업 하나만 실행한다. requirements와 design 에이전트는 재실행하지
않으며, 생성 호출 뒤 검증이 실패할 때 허용되는 LLM 수정은 최대 한 번이다. 결과는 다음 세 파일에
독립적으로 남겼다.

- `measurements/2026-08-development/app-cloud-snapshot-repair-dependency-20260809.json`
- `measurements/2026-08-development/app-cloud-snapshot-repair-port-20260809.json`
- `measurements/2026-08-development/app-cloud-snapshot-repair-storage-20260809.json`

| 사례 | 소유 하위 작업 | LLM 호출 | 수정 단계 | 진단 해소 | HTTP acceptance test | 승격된 변경 |
|---|---|---:|---:|---:|---:|---|
| dependency | `implementation.logic` | 1 | 8.426초 | 예 | 통과, 41.000초 | 실험용 Java 파일 1개 수정 |
| port | `implementation.vm_delivery` | 2 | 78.454초 | 예 | 통과, 47.758초 | IaC 3개 수정, lock 1개 추가 |
| storage target | `implementation.vm_delivery` | 2 | 65.256초 | 예 | 통과, 45.277초 | IaC 교체 |

dependency에서는 LLM이 원 요구에 필요하지 않은 JPA annotation과 import를 제거했다. 특정
dependency를 무조건 추가하는 대신 stateless 원 요구와 일치하는 쪽을 선택했고, 기존 HTTP
acceptance test가 통과했다. port에서는 최초 생성 뒤 provider 검증 피드백 수정 한 번으로 binding을
해소했다. 가장 큰 병목은 LLM보다 고정 provider cache의 init 35.173초였다.

storage의 첫 실행들은 안전하게 실패했다. 검증되지 않은 파일은 승격되지 않았고, 이전 IaC와 새
IaC가 섞이는 버그도 아래와 같이 먼저 제거했다. 이어 production validator가 이미 요구하던 일반
불변식, 즉 Docker container target이 정확히 `applicationMountPath`여야 한다는 내용을 생성 지침에
동일하게 명시했다. 호스트 source 경로는 target과 달라도 된다. 새 사례 별칭이나 DB 규칙 없이
재실행한 결과 Azure provider·binding과 HTTP acceptance test가 통과했다. 이는 일반 계약 정렬 뒤
세 고정 사례가 모두 복구됐다는 개발 결과이며 성공률 추정치는 아니다.

이 실행 중 VM delivery가 새 IaC만 검증하고 이전 소유 파일을 작업공간에 남겨 서로 다른 attempt의
산출물을 섞는 문제가 드러났다. 검증된 IaC 파일 집합을 sibling staging 디렉터리에 만든 뒤 교체하며,
이전 `.tf/.tpl/.tftpl/.sh`, lock, `.terraform` cache를 제거하도록 수정했다. VM delivery가 소유하지
않는 파일은 보존하고 승격 실패 시 이전 디렉터리를 복원한다.

저장 사례의 P2 context는 resource spec, deployment needs, cloud design만 VM delivery에 제공하고
앱 도메인 설계에는 사용하지 않는다. 따라서 이는 통제된 배포 경계 파일럿이지 하나의 자연어
요구에서 시작한 종단 성공 사례가 아니다. 여기서 HTTP acceptance test는 생성 앱의 MockMvc 기반
업무 API 검사이며, 실제 VM·컨테이너·cloud endpoint 호출은 아직 측정하지 않았다.
