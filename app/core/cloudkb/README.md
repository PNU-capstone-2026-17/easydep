# EasyDep Cloud KB

현재 범위는 **AWS·Azure·GCP의 Docker-on-VM 배포**다.

## 유지 패키지

| 패키지 | 역할 |
|---|---|
| `depkb` | 실측 기반 VM 리소스 의존관계 |
| `capacitykb` | VM 및 연계 리소스의 용량·제약 |
| `sizingkb` | 애플리케이션 요구량을 VM 하한으로 변환 |
| `costkb` | VM 사양·가격과 비용 계산 |
| `perfkb` | VM 성능 특성과 추천 보강 |
| `kbcommon` | 공통 데이터 로더·출처·불변식 |

## 목표 흐름

```text
application requirements
→ sizingkb
→ depkb
→ capacitykb
→ costkb + perfkb
→ ResourcePlan
```

요구사항·설계·구현·테스팅 에이전트와의 연결은 각 에이전트 또는 새로운 오케스트레이션 플로우가 담당한다. Cloud KB 내부에는 별도 AI 에이전트를 두지 않는다.

## 제거한 레거시

- NIM 참조 에이전트와 전용 실행기
- `appkb`, `bundlekb`, `patternkb`, `graphkb`, `envkb`
- Cloud KB의 기존 테스트 전체
- 타 CSP, 그래프 구버전, 패턴·번들·환경 데이터

과거 조사 문서는 [`document/archive/`](document/archive/)에 보존한다. 현재 문서 상태는 [`document/README.md`](document/README.md)를 따른다.

## 남은 정리

- `capacitykb`, `costkb`, `perfkb` 내부의 관리형 서비스·타 CSP 코드 제거
- `data/`의 AWS·Azure·GCP VM 데이터만 최종 검증
- 새 VM 전용 계약을 기준으로 테스트를 다시 작성
