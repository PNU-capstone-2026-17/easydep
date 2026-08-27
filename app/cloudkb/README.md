# EasyDep Cloud KB

현재 범위는 **AWS·Azure·GCP의 Docker-on-VM 배포**다.

## 유지 패키지

| 패키지 | 역할 |
|---|---|
| `depkb` | 실측 기반 VM 리소스 의존관계 |
| `costkb` | VM 사양·가격과 비용 계산 |
| `perfkb` | VM 성능 특성과 추천 보강 |
| `kbcommon` | 공통 데이터 로더·출처·불변식 |
| `speckb` | CSP가 발행한 VM 카탈로그 원본 응답 (무가공 보관) |

`speckb`는 위 규칙의 예외다. 다른 KB가 제3자 가공본(cb-tumblebug 덤프, Cyclenerd·
vantage-sh 저장소)을 파싱하는 것과 달리 CSP 1차 응답을 그대로 보관하며, 그 대비를
유지하려고 `kbcommon`을 포함해 저장소의 어떤 모듈도 import하지 않는다. 이유는
`speckb/__init__.py`에 있다.

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
- `depkb/native/`는 CSP별 공식 원천에서 추출한 경계와 관계 검토 자료를 보존한다.
- `depkb/experiments/`, `depkb/replications/`, 저장소의 `evaluation/`은 연구 증거다.
  제품 런타임 입력으로 직접 읽지 않는다.
- `document/archive/`는 비권위 과거 기록이다.

실제 프로비저닝 계획은 선택된 CSP의 provider-native DepKB와 capability 실현 카탈로그를
사용한다. CSP 사이에 같은 리소스 ID가 존재한다고 가정하지 않는다.

## 아직 구현하지 않은 부분

- 애플리케이션 부하를 VM 최소 vCPU·메모리로 변환하는 근거 기반 모델
- 디스크 크기·IOPS 같은 VM 연계 리소스의 별도 용량 제약 KB
- 애플리케이션 부하에서 도출한 최소 용량을 사용하는 종단 RQ3 평가

`costkb`와 `perfkb`는 현재 `app/implementation/planning/vm_selection.py`에 연결되어 있다.
다만 이 값들은 사용자가 명시한 최소 요구량이 있을 때만 후보 필터에 사용할 수 있다.
근거가 없는 최소 사양을 시스템이 임의로 추정하지 않는다.

## 패키지 계약

`app.cloudkb`는 클라우드 사실과 그 사실에서 파생한 후보·의존관계만 소유한다.

- **입력:** 커밋된 `data/*.json.gz`와 `depkb/native/`의 공급자 원천 증거, 그리고
  명시적으로 요청된 재빌드 작업의 경로·공급자 응답.
- **출력:** 검증된 카탈로그, 의존관계·비용·성능 조회 결과, `ResourcePlan`에 넘길
  공급자별 후보. 연구 산출물은 `experiments/`·`replications/` 아래에만 남긴다.
- **부수효과:** 기본 조회는 저장소의 번들 데이터를 읽기만 한다. fetch/rebuild CLI가
  요청된 경우에만 네트워크를 읽고 `output/`·`.cache/`에 작업 산출물을 쓴다.
- **금지 의존성:** `app.requirements`, `app.design`, `app.implementation`을 import하지
  않는다. 애플리케이션 단계의 상태·프롬프트·LLM 호출을 알지 못하며, 패키지 내부
  연결은 `app.cloudkb`의 canonical 경로를 사용한다.
- **실패 조건:** 번들·스키마가 없거나 손상됨, 공급자 응답이 계약을 위반함, 고정된
  증거와 재빌드 결과가 불일치함, 또는 네트워크 fetch가 실패하면 명시적인 검증/
  fetch 오류로 중단한다. 근거 없는 최소 용량이나 공급자 간 ID 동일성은 추정하지
  않는다.
