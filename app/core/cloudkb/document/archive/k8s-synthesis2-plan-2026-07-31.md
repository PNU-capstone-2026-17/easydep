# k8s 층 합성 2라운드 — Ingress→LB · RWX PVC, 3사 (2026-07-31, 실행 전 기록)

## 왜 이것인가

1라운드(Service→LB·RWO PVC→디스크)가 남긴 소비자 공백 둘:

- **Ingress**: deployment-intent에 `ingress` 칸이 실재하고 지금 `loadBalancer`
  앵커로 간다(우리 구성). Ingress 오브젝트가 LB를 **합성**한다면 그 앵커는
  이중 생성을 낳는다 — Service에서 실제로 그랬다.
- **RWX PVC**: 1라운드는 RWO(블록 디스크)만 쟀다. RWX의 실체는 파일
  스토리지(fileSystem — TB 경계 안인데 파서가 안 뽑는 셋 중 하나)다.

## 셀 — 기본 구성 판정임을 명시한다

관리형 기본 구성(추가 애드온 0)에서 잰다. 컨트롤러·CSI 애드온을 깔면 답이
바뀔 수 있고, 그것은 별도 변형이다(T-기본값 의존, 1라운드와 같은 지위).

| 셀 | azure(AKS) | gcp(GKE) | aws(EKS) |
| --- | --- | --- | --- |
| IngressClass 실물 | 관측 | 관측(gce 내장 예상) | 관측 |
| Ingress 오브젝트 → 클라우드 실물 | 기본 컨트롤러 부재 예상 → 합성 없음 관측 | 내장 컨트롤러 → HTTP LB 성좌 합성 예상 | 부재 예상 → 합성 없음 관측 |
| Ingress 삭제 → 정리 | (합성 없으면 해당 없음) | 성좌 정리 관측 | (해당 없음) |
| RWX PVC | `azurefile-csi`(1라운드 K5에서 실재 관측, bind=Immediate) → 파일 스토리지 합성 예상. **Immediate면 Pod 트리거 불요** — 그 자체가 관측 | RWX 가능 SC 부재 예상(1라운드 K4: standard-rwo 계열뿐) → Pending+이벤트 관측 | 전제 부재(CSI 애드온·노드 0) → 관측만, 미측정 명시(1라운드와 동일) |

가설은 실측으로만 판정에 들어간다. gcp Ingress의 백엔드로 NodePort Service를
같이 넣는다(셀렉터 없는 무엔드포인트 — LB 성좌 합성은 백엔드 건강과 별개라는
1라운드 관측을 전제로 하되, 어긋나면 그대로 기록).

## 오라클

1라운드와 동일 — 클라우드 컨트롤 플레인 열거가 진실, kubectl 상태는 힌트.
gcp HTTP LB는 **전역** 자원이다(urlMaps·targetHttpProxies·전역 forwardingRules·
backendServices) — 1라운드의 지역 성좌와 열거 범위가 다르다. 전용 네트워크
삭제 성공을 잔여 0의 독립 증명으로 다시 쓴다.

## 소비 갱신 (라운드 후)

- `ingress` 신호의 앵커를 `loadBalancer` → `k8sIngress`로: gcp는 합성이면
  autoFilled+동반 정리(이중 생성 방지), azure·aws는 기본 구성 합성 없음이면
  attachable(비자동)로 남는다 — 노출을 이루는 방법(컨트롤러 설치 등)은 우리가
  대신 정하지 않는다.
- 계약 문서의 "명시적 미해결 — Ingress" 항목을 실측 결과로 갱신.

## 비용·시간

클러스터 3개 재생성(1라운드와 동일 경로) ~1h · gcp HTTP LB 분 단위 소액 ·
azure 파일 스토리지 분 단위 소액. 라운드 끝 3사 전수 점검(잔여 0).
