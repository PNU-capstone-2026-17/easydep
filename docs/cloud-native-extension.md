# VM 기반 클라우드 네이티브 확장 계획

> 갱신일: 2026-08-04

## 1. 목표와 범위

EasyDep의 요구사항·설계·구현·테스팅 에이전트를 연결해 Docker 애플리케이션의 VM 배포를 지원한다.

- CSP: AWS, Azure, GCP
- 실행 환경: Linux VM과 Docker
- 포함 리소스: 네트워크, 서브넷, VM, 방화벽과 필요한 경우 Public IP, Load Balancer, 영속 디스크
- 제외: Kubernetes, VPN, 서버리스, 관리형 애플리케이션 서비스, 기존 인프라 연동

핵심 목표는 요구사항과 설계 결과를 근거로 클라우드 리소스를 선택하고, 그 결과를 구현과 테스트까지 일관되게 연결하는 것이다.

## 2. 핵심 산출물

새로운 중간 모델을 불필요하게 추가하지 않는다.

1. 기존 요구사항과 `cloudContext`
2. 기존 설계 에이전트의 배포 구조
3. 클라우드 계획 단계의 `ResourcePlan`

배포 다이어그램, IaC와 테스트 보고서는 이 산출물로부터 생성한다. 별도의 `DeploymentNeeds`나 `DeploymentIntent`는 만들지 않는다.

## 3. 단계별 역할

### 3.1 요구사항

기존 FR/NFR 분석 후 소프트웨어 요구사항으로 알 수 없는 클라우드 제약만 받는다.

```json
{
  "requirements": [
    {"id": "FR-01", "text": "외부 사용자가 HTTPS API를 호출할 수 있어야 한다."},
    {"id": "NFR-01", "text": "동시 사용자 300명을 처리해야 한다."}
  ],
  "cloudContext": {
    "providers": ["aws", "azure", "gcp"],
    "location": "대한민국",
    "monthlyBudgetUSD": 300
  }
}
```

`cloudContext`의 세 필드는 선택값이다. 이미 요구사항에 같은 정보가 있으면 다시 묻지 않는다.

요구사항 단계에서 VM 수·사양, Load Balancer, 디스크, Public IP 등은 묻지 않는다. 이는 후속 계획 단계의 결정이다.

### 3.2 설계

기존 설계 에이전트가 다음 정보를 제공해야 한다.

- Docker로 배포할 애플리케이션 노드
- 노드 간 연결과 포트
- 외부 공개 인터페이스
- 영속 데이터와 볼륨 필요 여부

설계 단계에서는 CSP별 리소스명을 결정하지 않는다.

### 3.3 클라우드 계획

다음 입력으로 `ResourcePlan`을 생성한다.

- FR/NFR와 `cloudContext`
- 설계 에이전트의 배포 구조
- 클라우드 지식베이스

클라우드 지식베이스는 다음을 제공한다.

- 리소스 의존관계
- 요구 용량에 따른 후보 제한
- 성능과 비용에 따른 후보 추천

`ResourcePlan`의 최소 구조는 다음과 같다.

```json
{
  "provider": "aws",
  "region": "ap-northeast-2",
  "resources": [],
  "creationOrder": [],
  "unresolved": [],
  "evidence": []
}
```

모든 리소스 결정은 요구사항, 설계 또는 지식베이스 근거를 가져야 한다. VM 수와 사양은 부하·예산 정보를 사용하며, 근거가 부족하면 확정하지 않는다.

### 3.4 구현

기존 구현 에이전트가 애플리케이션 코드, 테스트와 Dockerfile을 생성한다. 새로운 플로우는 `ResourcePlan`에 맞는 CSP별 VM IaC와 Docker 실행 설정을 조립한다.

### 3.5 테스트

다음 세 범위를 검증한다.

1. 애플리케이션 빌드와 기능 테스트
2. 요구사항·설계·Dockerfile·IaC 간 일관성
3. Docker 및 VM 배포 후 접근·헬스 체크·재시작 검증

## 4. 배포 다이어그램

배포 다이어그램은 설계 구조와 `ResourcePlan`을 결합한 표현이다.

- CSP와 리전
- 네트워크와 서브넷
- VM과 Docker 컨테이너
- 외부 접근 경로
- 방화벽, Load Balancer, 디스크
- 리소스 의존관계

구조화된 입력으로 결정적으로 생성하며, LLM이 지식베이스의 리소스를 임의로 변경하지 않게 한다.

## 5. 실행 흐름

```text
run_requirements()
→ collect_cloud_context()
→ run_design()
→ build_resource_plan()
→ render_deployment_diagram()
→ run_implementation()
→ assemble_vm_deployment()
→ run_testing()
```

기존 에이전트 내부 코드를 직접 수정하지 않고 공개 함수나 API를 호출한다.

## 6. 구현 순서

1. 대표 Docker 애플리케이션과 현재 에이전트 출력을 기준선으로 저장한다.
2. 기존 요구사항 출력에 최소 `cloudContext`를 추가한다.
3. 기존 설계 출력에서 노드·연결·포트·영속성 정보를 확보한다.
4. `ResourcePlan` 스키마를 고정한다.
5. 클라우드 지식베이스의 VM 범위 조회를 연결한다.
6. 배포 다이어그램과 CSP별 IaC를 생성한다.
7. 정적 검사, Docker 실행과 VM 배포 테스트를 연결한다.

낡은 문서와 코드는 바로 삭제하지 않는다. 활성 여부를 분류한 뒤 회귀 테스트를 통과한 별도 변경에서 정리한다.

## 7. 연구 주장 범위

> AWS·Azure·GCP의 Docker-on-VM 시나리오에서 클라우드 지식베이스와 단계별 산출물 계약을 사용하는 멀티 에이전트 방식이 일반 LLM 및 기존 멀티 에이전트 방식보다 리소스 누락과 산출물 불일치를 줄이는지 평가한다.
