# 용량 측정 방법론 조사 — 우리 측정을 문헌에 대다 (2026-08-02)

> 계기: `measure_capacity`·`load_probe`를 다소 즉흥적으로 지었다(단일 실행 피크·
> 시동 스파이크·닫힌 부하). 사용자 지시로 **확립된 측정 방법론**을 조사해 우리
> 접근이 어디가 맞고 어디가 틀렸는지 근거에 댄다. 저장소 규율: 지어내지 말고
> 문헌에. 출처 등급을 표시한다 — 학술(피어리뷰) vs 산업 관행(벤더·문서).

## 1. 네 기둥 (조사한 것)

### ① JVM 성능은 정상상태 도달이 어렵다 — 단일 피크는 못 믿는다 (학술)

Kalibera & Jones(2013)는 VM 벤치마크에서 **워밍업 완료 시점을 단순 휴리스틱이
자주 틀린다**고 보이고, 사람이 자기상관·런시퀀스 플롯으로 정상상태를 판정하는
수작업 절차를 제시했다. Barrett 외(2017, "VM Warmup Blows Hot and Cold")는 더
나아가 **JIT가 정상상태에 아예 도달하지 못하는 경우가 흔하다**를 실측하고,
변화점 탐지로 이를 자동화했다. 함의: **한 번 돌려 얻은 피크는 가장 약한 통계**다 —
워밍업을 지나 여러 프로세스 실행에 걸쳐 정상상태를 확인해야 한다.

### ② k8s 라이트사이징은 피크가 아니라 백분위 분포를 쓴다 (산업 관행)

VPA Recommender는 **감쇠 가중 히스토그램**으로 사용량 분포를 추적하고,
메모리는 관측치의 **p95~p99를 타깃**, CPU는 p50~p95를 쓴다. 권장 절차:
**2~4주 창**에서 p50/p75/p90/p99를 잡되 **피크·오프피크·배포 이벤트를 포함**,
24시간 스냅샷이 아니다. 메모리 request ≈ p99 + 10~20% 버퍼, CPU request ≈
정상 사용의 p95. 가드레일: HPA↔VPA death spiral(min/maxAllowed 경계, 다른
메트릭). 함의: **단일 실행 피크 하나로 request/limit을 정하는 것은 관행 밖**이다.

### ③ 닫힌 부하 모델은 coordinated omission에 빠진다 (산업 관행 + 방법론)

- **닫힌(closed) 모델**: 고정 동시 사용자 N, 요청률이 시스템 응답에 **암묵적으로
  묶인다**(앞 요청이 끝나야 다음). **열린(open) 모델**: 고정 도착률, 응답과
  무관하게 요청을 낸다.
- **Coordinated omission**: 열린 시스템을 닫힌 생성기로 재면 SUT가 느려질 때
  생성기도 같이 멈춰 **최악 지연을 체계적으로 숨긴다**. 구조적 해법은 **고정
  도착률(open)**로 몰아 SUT 응답성과 부하 생성을 분리하는 것.
- **Little의 법칙** L = λW로 동시성·도착률·응답시간을 잇는다 — 목표 동시성을
  도착률로 환산하는 근거.
- 함의: 우리 `load_probe`는 **N개 워커가 앞 요청 끝나면 다음을 던지는 닫힌
  루프**다 — 정확히 이 함정이고, SUT가 스스로 부하를 조절해 포화를 못 본다.

### ④ 컨테이너 메모리는 힙이 아니라 RSS 전체 + 헤드룸 (산업 관행)

JVM RSS는 힙(-Xmx) 외에 **네이티브·Metaspace·스레드 스택·코드 캐시·다이렉트
버퍼**를 포함하고 이들은 -Xmx로 안 잡힌다 — 힙만 보고 사이징하면 OOMKilled.
관행: RSS 전량 + 25~50% 헤드룸, 안정적이면 limit=request. 그리고 **시동기
(클래스 로딩·초기 힙 확장)의 GC·CPU가 정상상태보다 크다**(AWS) — 우리가 관측한
"시동 스파이크"가 정확히 이것. -XX:ActiveProcessorCount로 GC/JIT 스레드 수도
컨테이너에 맞춰야 한다.

## 2. 우리 측정을 문헌에 대다

| 우리가 한 것 | 문헌 판정 | 근거 |
|---|---|---|
| **단일 실행 피크**(두 도구) | ✗ 가장 약한 통계 | ①(정상상태 미확인) + ②(분포·백분위여야) |
| **시동 스파이크를 CPU로**(measure_capacity 4.73 vCPU) | ✗ 정상상태 아님 | ①·④ — 시동 GC/CPU > 정상. 우리도 evidence에 그렇게 적었다(맞음) |
| **테스트 부하 = 서빙**(measure_capacity) | ✗ 대표성 없음 | ② — 실측은 실서비스 부하 분포여야 |
| **닫힌 루프 부하**(load_probe) | ✗ coordinated omission | ③ — open 모델·고정 도착률이어야 |
| **RSS를 잼**(힙 아니라) | ✓ 방향은 맞음 | ④ — 단 단일값이라 p99·헤드룸 없음 |
| **빌드 CPU 격리**(테스트 JVM만) | ✓ 오염 제거는 맞음 | 측정 위생의 정당한 조치 |
| **못 재면 지어내지 않음** | ✓ 규율 정합 | — |

**정직한 결론**: 우리 도구는 *"앱이 이 실행에서 적어도 이만큼 썼다"*는 **거친
하한**을 낸다. 그건 사실이지만 **right-sizing이 아니다**. 방법론이 요구하는
것(정상상태·백분위·open 부하·반복·헤드룸)이 거의 다 빠져 있다.

## 3. 제대로 된 측정이 요구하는 것 (방법론 종합)

1. **워밍업**: 시동·JIT를 지나 정상상태에 이르게 한 뒤 잰다. 정상상태 판정은
   변화점 탐지(Barrett) 또는 고정 워밍업 + 안정성 검사.
2. **open 모델 부하**: 목표 도착률(scale→Little의 법칙)로 몰고, 포화까지 램프.
   coordinated omission 회피(고정 스케줄).
3. **시계열 표본 → 백분위**: 피크 하나가 아니라 p50/p95/p99. CPU request≈p95,
   메모리≈p99+버퍼(VPA 관행).
4. **반복 실행**: 여러 프로세스 실행으로 신뢰구간·분산(Kalibera & Jones).
5. **시동과 정상을 분리**: 시동 스파이크는 readiness·limit에, 정상은 request에.
6. **RSS 전량 + 헤드룸**: 힙만이 아니라 네이티브 포함, 25~50%.

## 4. 우리 계획에 대한 함의

- **`load_probe`를 open 모델로 고쳐야** 한다(현재 닫힌 루프). 고정 도착률
  생성기 + 워밍업 + 백분위 집계.
- **`measure_capacity`의 CPU는 서빙 하한이 아니다** — 시동 스파이크임을 이미
  라벨했으니(맞음), 이걸 sizing 하한으로 쓰는 것은 신중해야. 메모리 RSS도
  단일값이라 p99가 아니다.
- **부하 프로브가 옳은 도구**이되, 앱이 독립 부팅돼야 하고(field-report 미부팅)
  open 모델·반복·백분위를 갖춰야 논문 등급이 된다.
- **표본 크기**: 단일 앱·단일 실행이라 일반화 못 함 — 반복이 방법론의 요구.

## 5. 출처와 등급

**학술(피어리뷰)** — 정상상태·워밍업의 근거:
- Kalibera, T. & Jones, R. E. (2013). *Rigorous Benchmarking in Reasonable Time.*
  ISMM. https://kar.kent.ac.uk/33611/45/p63-kaliber.pdf
- Barrett et al. (2017). *Virtual Machine Warmup Blows Hot and Cold.* OOPSLA.
  https://arxiv.org/pdf/1602.00602
- Traini et al. (2023). *Towards effective assessment of steady state performance
  in Java software.* Empirical SE. https://link.springer.com/article/10.1007/s10664-022-10247-x

**산업 관행(벤더·문서)** — 라이트사이징·부하·JVM 컨테이너:
- Kubernetes VPA 백분위/히스토그램: https://scaleops.com/blog/kubernetes-vpa/ ·
  https://www.datadoghq.com/blog/rightsize-kubernetes-workloads/
- open/closed·coordinated omission: https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/ ·
  https://redhatperf.github.io/post/coordinated-omission/
- JVM 컨테이너 메모리(RSS·헤드룸): https://aws.amazon.com/blogs/containers/jvm-memory-cpu-and-classpath-best-practices-for-java-containers-on-aws/

**등급 주의**: ①은 학술로 강하고, ②③④는 산업 관행이라 수렴하지만 통제 실험이
아니다. 우리 주장은 "관행이 단일 피크를 안 쓴다"까지이고, 그 관행의 최적성
자체를 우리가 검증한 것은 아니다(depkb가 CB 소스를 관측 코퍼스로만 읽은 것과
같은 자세).
