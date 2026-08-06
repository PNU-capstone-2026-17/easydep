# 클라우드 리소스 의존성 분석 평가 방법

## 1. 연구 질문

- RQ1: 지식베이스가 필요한 VM 리소스와 의존 간선의 누락·과잉을 줄이는가?
- RQ2: 지식베이스가 잘못된 생성 순서와 배포 불가능한 IaC를 줄이는가?
- RQ3: 용량·성능·가격 지식이 요구와 예산을 만족하는 VM 선택을 늘리는가?
- RQ4: 그 효과를 얻기 위한 시간·토큰·비용은 얼마인가?

## 2. 비교군

| ID | 방식 |
|---|---|
| B1 | 동일 LLM의 단일 CoT |
| B2 | 동일 LLM 설정을 사용하는 MetaGPT |
| A1 | EasyDep 내부의 Cloud KB를 끈 paired 구성요소 실험 |
| P | 전체 EasyDep |

`P-B1/B2`는 전체 시스템의 효과를 측정한다. `P-A1`은 동일한 시작 리소스에서 KB 조회만
끄는 별도 paired 구성요소 실험이다. 전체 파이프라인을 두 번 생성하면 LLM 변동이 KB
효과에 섞이므로 A1을 종단 실행군으로 두지 않는다.

의존성 A1은 동일한 시작 리소스에 대해 depkb 조회만 끄며 RQ1·RQ2에 사용한다. RQ3은
최소 용량을 고정한 별도 여섯 사례에서 VM 카탈로그 전체판과 제거판을
paired 비교한다. 이는 선택 경로의 배선·판정 능력 검사이며, 기존 P1~P3에는 최소 용량이
없으므로 종단 RQ3 효과로 확대 해석하지 않는다.

두 비교는 평가 대상을 구분한다.

- **종단 비교(P-B1/B2)**: 각 방식이 만든 최종 구현물만 평가한다.
- **구성요소 제거 실험(P-A1)**: EasyDep 내부의 리소스 계획도 함께 평가해 KB의 직접
  효과를 진단한다.

종단 비교의 필수 구현물은 애플리케이션 소스코드, 컨테이너 빌드 설정, IaC이다. 배포
매니페스트는 해당 구현에 필요할 때만 포함한다. 배포 다이어그램과 `cloud-plan.json`은
EasyDep의 설계·중간 산출물이며 비교군에 강제하지 않는다.

## 3. 평가 사례

각 CSP에 다음 세 구현 가능 유형을 사용한다.

1. 외부 HTTPS를 제공하는 무상태 단일 VM 앱
2. 재시작 후 데이터 보존이 필요한 앱
3. 단일 VM 장애를 허용하지 않는 고가용성 앱

가용성·성능 요구와 예산이 충돌하는 입력은 만족 가능한 최종 구현물이 없으므로 별도
충돌 탐지 실험으로 분리한다. P1~P3는 오케스트레이션 안정화에 사용하는 개발 세트다.
홀드아웃은 원격진료-Azure, 물류-GCP, 파트너보고-AWS 세 도메인 사례로 분리하며 결과를
본 뒤 시스템이나 골드셋을 수정하지 않는다.

## 4. 골드셋

사례별로 다음을 사전에 고정한다.

- 필수·선택·금지 리소스
- CSP별 필수·조건부 의존 간선
- 허용 가능한 대안과 미결 질문
- 생성 순서 제약
- 최소 용량과 예산 상한
- 포트, 볼륨, 헬스체크 조건

각 항목에는 공식 스키마·문서 위치 또는 재현 가능한 실험 ID를 붙인다. 한 사람이 라벨링한
경우 그 한계를 명시하고, 가능하면 두 번째 검토자가 일부 표본을 독립 판정한다.

## 5. 핵심 지표

종단 비교에서는 IaC와 선택적 매니페스트를 독립 평가기가 읽어 리소스와 간선을 CSP 중립
이름으로 정규화한 뒤 다음을 계산한다. 다이어그램이나 자체 보고 계획은 정답으로 사용하지
않는다.

```text
Precision = 맞게 생성한 항목 / 생성한 전체 항목
Recall    = 맞게 생성한 항목 / 골드의 전체 항목
F1        = 2 × Precision × Recall / (Precision + Recall)
```

주 지표는 리소스 F1, 의존 간선 F1, 생성 순서 위반 수, IaC 정적 검증률, Docker 실행률,
용량 미충족률과 예산 위반률이다. 요구사항 추적률과 포트·볼륨 불일치 수도 측정한다.

순환복잡도 분포와 테스트 커버리지는 생성 코드의 보조 품질 지표다. 의존성 분석의
정확도나 배포 가능성을 대신하지 않는다.

### 5.1 최종 구현물 검사 도구

평가기가 IaC 문법이나 코드 복잡도를 새로 정의하지 않는다. 다음 도구의 기계 판독 결과를
같은 형식으로 모은다.

| 대상 | 주 도구 | 측정값 |
|---|---|---|
| IaC 구문·스키마 | OpenTofu `fmt`, `validate -json` | 포맷 준수, 검증 성공, 오류·경고 수 |
| IaC 의존 그래프 | OpenTofu `graph` | 리소스 유형, 직접 참조 간선 |
| Terraform 의미 정규화 | python-hcl2 | VM 수·디스크·LB·중첩 NIC 및 CSP 의존 관계 |
| 컨테이너 실행 | Docker CLI + HTTP 블랙박스 검사 | 빌드·시작, `/health`, 사례별 API 입력·출력 |
| IaC·Docker 보안 구성 | Trivy `config --format json` | 심각도별 구성 오류 수 |
| 소스 복잡도 | Lizard | 함수별 CCN·NLOC, 평균·중앙값·95백분위·최댓값 |
| 테스트 커버리지 | JaCoCo XML | 라인·분기·복잡도 covered/missed 비율 |

OpenTofu가 없거나 초기화·검증·그래프 생성에 실패한 실행은 본 실험 표본에서 성공으로
처리하지 않는다. `fmt -check` 실패는 스타일 위반으로 별도 집계하되 유효한 IaC를 배포
불가능으로 바꾸지는 않는다.
HCL 구문은 python-hcl2가 파싱하며 평가기는 실험 범위의 CSP 리소스를 공통 개념으로만
정규화한다. 런타임 부하처럼 정적으로 확인할 수 없는 값은 `unknown`으로 둔다. Trivy 결과는
보안 보조 지표이고 리소스 의존성 정답으로 사용하지 않는다. JaCoCo 보고서가 없으면
커버리지를 0으로 간주하지 않고 `측정 불가`로 기록하며, 산출물 누락률을 별도로 보고한다.

복잡도는 평균 하나만 쓰지 않는다. 생성 코드 규모가 다르면 평균이 낮아질 수 있으므로
함수 수와 NLOC를 함께 기록하고 CCN 중앙값·95백분위·최댓값·CCN 10 초과 함수 비율을
보고한다. `decisionPointDensityPer100Nloc = Σ max(CCN-1, 0) / NLOC × 100`도 보조로
기록한다. 이 값은 Lizard가 센 결정 지점의 밀도이며 JaCoCo 분기 커버리지와 다른 지표다.
실험군 요약에는 run별 CCN 평균·95백분위·최댓값·CCN 10 초과 비율과 분기점 밀도의
분포를 각각 집계한다. JaCoCo 분기·복잡도 커버리지는 보고서가 생성된 run만 집계하고,
보고서가 없는 완료 run의 수를 함께 제시한다.
Docker 검사는 run마다 고유한 임시 이미지 이름과 임의 호스트 포트를 사용하고 검사 후
컨테이너와 이미지를 제거한다. Docker 데몬을 사용할 수 없으면 실패율에 섞지 않고 도구
미사용 run 수로 별도 보고한다.

`/health` 성공은 기능 성공이 아니다. P1은 단위 변환의 수치, P2는 노트 생성·조회, P3은
동결된 상품 목록·상세 응답을 컨테이너 외부에서 요청한다. 기대 HTTP 상태와 JSON 일부만
비교하므로 클래스명·프레임워크 내부 구조·EasyDep 중간 산출물에는 의존하지 않는다. 이
검사를 통과하지 못하면 IaC 점수가 높아도 `experimentEligible=false`이다. P2의 재시작 후
보존과 P3의 실제 부하·장애 복구는 로컬 API 검사로 대신하지 않고 별도 동적 평가가 준비될
때까지 `unknown`으로 남긴다.

## 6. 공정성과 재현성

- 입력, 기반 모델, temperature, seed, 최대 토큰, 재시도와 시간 제한을 고정한다.
- 모든 방식에 같은 **최종 구현물의 최소 계약**과 검사기를 적용하되, 내부 작업 절차와
  중간 산출물 형식은 강제하지 않는다.
- 핵심 실험에서는 외부 웹 검색을 금지한다.
- 실행 순서를 무작위화하고 각 조건을 최소 3회 반복한다.
- 실패와 중단도 제외하지 않고 원인과 비용을 기록한다.
- 프롬프트, KB 해시, 가격 기준일, 실행 설정과 원출력을 run ID 아래 저장한다.
- OpenTofu, Trivy, Lizard, JaCoCo의 버전과 원본 JSON/XML 출력도 run ID 아래 저장한다.
- 작업별 wall-clock 제한은 모든 방식에 7,200초로 동일 적용하고 timeout도 결과에 포함한다.

## 7. 분석

같은 사례의 결과를 paired 비교하고 평균만이 아니라 중앙값, 분산, 개별 실행값과 효과
크기를 보고한다. 표본이 작으면 과도한 유의확률 해석을 피하고 절대 오류 감소량을 함께
제시한다. 결론은 세 CSP의 Docker-on-VM 사례 범위로 제한한다.

## 8. 완료 판정

다음이 있어야 효과 측정이 완료된 것이다.

1. 동결된 입력과 골드셋
2. 실제로 KB를 끄는 A1 실행 경로
3. 세 종단 방식의 최종 구현물과 이를 공통 형식으로 정규화하는 독립 평가기
4. 자동 채점 결과와 재현 명령
5. 실패를 포함한 원시 결과와 요약 통계

현재 VM 의존성 구성요소 실험은 세 CSP 골드셋과 실제 A1 비활성화 경로까지 준비되어 있다.
VM 선택 구성요소 실험도 세 CSP 정상 선택과 예산 충돌·정보 부족을 포함한 여섯 사례,
스냅샷 해시가 고정된 정답, 실제 카탈로그 제거 경로와 자동 채점까지 준비되어 있다.
종단 비교는 P1~P3와 세 CSP를 교차한 9개 동결 입력, 능력·의존성 골드, CoT·MetaGPT
실행기와 공통 평가기까지 준비됐다. `python-hcl2` 의미 추출은 중첩 NIC·디스크도 정규화한다.
EasyDep 구현 경로는 애플리케이션 코드 생성 뒤 depkb 의존 계획을 사용해 IaC 생성 LLM을
호출한다. 생성된 Terraform은 OpenTofu와 공통 의미 평가기로 검사한다. A1은 별도 구성요소
실험이며 두 번째 종단 EasyDep 군이 아니다. EasyDep 내부 테스팅 단계는 생성 애플리케이션의
Gradle 테스트 결과만 `04-testing`에 저장하며, 실험 oracle이나 대조군 채점기를 호출하지
않는다. Docker·Terraform·Lizard·JaCoCo 검사는 세 방식의 최종 산출물에 공통 평가기가
동일하게 적용한다.
아직 없는 것은 개발 세트 81개(P1~P3 × 3 CSP × 3방식 × 3회)와 홀드아웃 27개
(3 도메인 × 3방식 × 3회), 총 108개 최종 구현물의 자동 채점 원시 결과와 요약 통계다.
따라서 전체 효과는 아직 입증되지 않았다.
반복 순서·실패 보존·재개·공통 채점·요약은 `python -m evaluation.experiment`로 수행한다.
`python -m evaluation.experiment --check-environment`는 자격증명을 노출하지 않고 본실험 필수
도구를 점검한다. 개발 파일럿은 `--arm`과 `--case`로 좁힐 수 있으며, 이 선택은 동결된
사례나 전체 본실험 순서를 수정하지 않는다.

### 8.1 종단 파일럿 상태

파일럿에서 IaC 의미 검사는 통과했지만 업무 로직이 `null` 또는 예외 스텁이어서 외부 API
검사에 실패한 사례가 반복됐다. 내부 에이전트가 자기 테스트 기대값을 낮춘 사례도 있어,
구현 단계의 자체 end-to-end 테스트를 최종 판정에서 분리하고 고정 HTTP oracle을 사용하는
공통 평가기로 판정하도록 바꿨다. 중단·수정 전 파일럿은 효과 크기에서 제외한다. run별
원인, 시간과 후속 변경은 `evaluation/pilot-results.md`에 보존한다.

## 참고문헌

- P. T. J. Kon et al., "IaC-Eval: A Code Generation Benchmark for Cloud
  Infrastructure-as-Code Programs," NeurIPS 2024 Datasets and Benchmarks Track,
  https://proceedings.neurips.cc/paper_files/paper/2024/hash/f26b29298ae8acd94bd7e839688e329b-Abstract-Datasets_and_Benchmarks_Track.html
- P. Ralph et al., *Empirical Standards for Software Engineering Research*,
  arXiv:2010.03525, 2021, https://www2.sigsoft.org/EmpiricalStandards/
- W. Hasselbring, "Benchmarking as Empirical Standard in Software Engineering Research,"
  EASE 2021, DOI: 10.1145/3463274.3463361.
- A. Nekrasov et al., "IaC Generation with LLMs: An Error Taxonomy and A Study on
  Configuration Knowledge Injection," ACM TOSEM, DOI: 10.1145/3817608.
- OpenTofu, `validate`와 JSON 출력 형식: https://opentofu.org/docs/cli/commands/validate/
- OpenTofu, 계획 JSON 형식: https://opentofu.org/docs/internals/json-format/
- Trivy, Terraform 구성 검사: https://www.trivy.dev/docs/latest/guide/coverage/iac/terraform/
- Lizard, 지원 언어와 CCN·NLOC 측정: https://github.com/terryyin/lizard
- JaCoCo, 분기·순환복잡도·커버리지 카운터: https://www.jacoco.org/jacoco/trunk/doc/counters.html
