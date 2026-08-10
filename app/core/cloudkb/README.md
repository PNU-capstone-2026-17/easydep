# EasyDep Cloud KB

현재 범위는 **AWS·Azure·GCP의 Docker-on-VM 배포**다.

## 유지 패키지

| 패키지 | 역할 |
|---|---|
| `depkb` | 실측 기반 VM 리소스 의존관계 |
| `costkb` | VM 사양·가격과 비용 계산 |
| `perfkb` | VM 성능 특성과 추천 보강 |
| `kbcommon` | 공통 데이터 로더·출처·불변식 |

## 목표 흐름

```text
application requirements
→ depkb
→ costkb + perfkb
→ ResourcePlan
```

요구사항·설계·구현·테스팅 에이전트와의 연결은 각 에이전트 또는 새로운 오케스트레이션 플로우가 담당한다. Cloud KB 내부에는 별도 AI 에이전트를 두지 않는다.

## 제거한 레거시

- NIM 참조 에이전트와 전용 실행기
- `appkb`, `bundlekb`, `patternkb`, `graphkb`, `envkb`
- Cloud KB의 기존 테스트 전체
- 타 CSP, 그래프 구버전, 패턴·번들·환경 데이터
- 전체 CSP 리소스 스키마였던 `capacitykb`
- Kubernetes·컨테이너 프리셋 중심이었던 `sizingkb`
- 관리형 서비스 가격 파서와 IBM 성능 파서

과거 조사 문서는 [`document/archive/`](document/archive/)에 보존한다. 현재 문서 상태는 [`document/README.md`](document/README.md)를 따른다.

## 데이터와 연구 증거의 경계

- `data/*.json.gz`는 검증 후 커밋한 런타임 데이터이며 기본 실행에서 우선한다.
- `output/`과 `.cache/`는 로컬 재빌드 작업공간이다. 현행 모델의 일부가 아니며 커밋하지 않는다.
- `depkb/native/`와 `depkb/neutral_candidates/`는 현행 모델의 근거와 교차 감사를 보존한다.
- `depkb/experiments/`, `depkb/replications/`, 저장소의 `evaluation/`은 연구 증거다.
  제품 런타임 입력으로 직접 읽지 않는다.
- `document/archive/`는 비권위 과거 기록이다.

Cloud-Barista 중립 모델은 검색·비교·감사에 사용한다. 실제 프로비저닝 계획은 검토된
provider-native DepKB를 사용하며, 중립 모델만으로 provider별 연결 리소스나 수명 주기를
추론하지 않는다.

## 아직 구현하지 않은 부분

- 애플리케이션 부하를 VM 최소 vCPU·메모리로 변환하는 근거 기반 모델
- 디스크 크기·IOPS 같은 VM 연계 리소스의 별도 용량 제약 KB
- 애플리케이션 부하에서 도출한 최소 용량을 사용하는 종단 RQ3 평가

`costkb`와 `perfkb`는 현재 `app/core/orchestration/vm_selection.py`에 연결되어 있다.
다만 이 값들은 사용자가 명시한 최소 요구량이 있을 때만 후보 필터에 사용할 수 있다.
근거가 없는 최소 사양을 시스템이 임의로 추정하지 않는다.
