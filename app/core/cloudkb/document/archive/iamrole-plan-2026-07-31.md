# iamRole 라운드 계획 — 3사 존재 의존 (2026-07-31, 실행 전 기록)

## 왜 이것인가

어휘 밖 대기열의 iamRole. EKS 거부 라운드에서 "roleArn도 SDK 층 필수"가
관측만 되고 어휘 밖이라 판정에 못 들어갔다. CNA에서 관리형 서비스를 쓸수록
ID·권한 자원 의존이 커진다.

어휘는 `iamRole` 하나로 묶고 CSP 대응은 이름표가 나른다(firewall=SG/NSG/
rules와 같은 규율): aws IAM Role/Instance Profile · azure Managed Identity ·
gcp Service Account.

## 이 라운드의 특징 — 대부분 기존 증거의 승격이다

새 실험을 먼저 만들지 않는다. 이미 있는 실측이 판정을 나른다:

| 간선 | CSP | 근거 | 예상 판정 |
| --- | --- | --- | --- |
| k8sCluster→iamRole | aws | E2.omit-role(`--role-arn` 클라이언트층 거부) + eks3 실역할 생성 성공(양성) | required (클라이언트층 한계 명시) |
| vm→iamRole | aws | func2 R10 — 인스턴스 프로필 없이 run-instances 성공 | optional |
| vm→iamRole | azure | func R4 — identity 미지정 VM 생성 성공 | optional |
| vm→iamRole | gcp | **미기록** — 인스턴스에 SA를 생략하면 서버가 기본 SA를 붙이는지 기존 결과에 없다 | 마이크로 실험 1개 |

gcp 마이크로 실험: 인스턴스를 serviceAccounts 생략으로 만들고 GET으로
`serviceAccounts` 실물을 기록 → 삭제. 예상은 둘 중 하나 — 서버가 기본
compute SA를 붙이면 server-default, 안 붙이면 단순 optional. 예상은
판정에 안 들어간다(실물이 정한다).

## 명시적으로 남기는 것

- **k8sCluster→iamRole의 azure(AKS managed identity 합성 여부)·gcp(GKE
  노드 기본 SA)**: 기존 결과에 identity 실물이 미기록이고 재관측엔 클러스터
  생성이 필요하다 — 대기열로 남긴다(빈칸이 아니라 비용 결정).
- 기능 축(EKS IAM 정책 분리 → 어떤 기능이 깨지나)은 기능 신호 정의가 별도
  설계라 1라운드 결정 그대로 미판정 유지.

비용: gcp e2-small 1대 ~3분. 나머지는 0.
