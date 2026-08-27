# 뷰 공백 둘 메우기 — 자격 요건 · 연산 성질 (2026-08-01, 실행 전)

## 어떻게 찾았나 (worked example)

`provision_view(["k8sCluster"], "aws")`만 보고 `aws-eks3` 라운드 19스텝을
재현할 수 있는지 대조했다. 16스텝은 뷰에서 도출됐고 **둘이 안 됐다**.
(나머지 하나 `F5/F6 잔여 확인`은 우리 규율이지 뷰의 것이 아니다.)

> 뷰 구조가 소비자 요구에서 나온 것이 아니라는 지적(사용자, 2026-08-01)에
> 대한 검증이었고, **지적이 맞았다** — 검증하니 공백이 나왔다.

## 공백 A — 자격 요건: "만들라"고만 하고 "어때야 쓸모 있는지" 안 말한다

`createOrder`가 `iamRole`을 넣지만 **빈 역할로는 클러스터가 안 선다**.
실험은 `AmazonEKSClusterPolicy`를 붙였는데(`R.attach-policy`) 그 사실이
claims의 증거 목록에도 없다.

이것은 **의존의 새 종류**다. 지금까지는 *"A는 B를 요구한다"*였는데 이건
*"B가 이러이러해야 A가 선다"* — 대상의 **자격**에 걸린다. 가장 가까운 기존
부류는 `이름 조건:`(azure GatewaySubnet)이고, 같은 자리에 놓는다.

**측정**(3사):

| CSP | 셀 | 비용 |
| --- | --- | --- |
| aws | 정책 **없는** 역할로 EKS 생성 → 거부되나 | 거부 라운드(무료) |
| azure | 이미 실측 — 서버가 identity를 합성한다(`azure-csi2/K4`) → **사용자가 자격을 갖춰 줄 필요가 없다**가 답이고, 그것도 판정이다 | 0 |
| gcp | 이미 실측 — VM은 `serviceAccounts: null`(`gcp-iam/A3`)이고 GKE는 기본 SA를 쓴다. 노드 SA 권한 축은 별도 | 0 |

aws만 새 실험이고 나머지는 **기존 실측의 승격**이다. 승격이 정직한 이유:
azure·gcp는 "자격 요건이 없다"가 관측된 것이지 안 재서 비운 것이 아니다.

## 공백 B — 연산 성질: 동기인가 비동기인가

`createOrder`를 그대로 실행하면 클러스터가 `CREATING`인 채 다음 단계를
시도한다. 실험은 매번 폴링했다(`K2.cluster-active`·`F1.cluster-gone`).

**새 측정이 거의 없다** — 우리 실험 스크립트가 **어디서 폴링했는지**가 곧
데이터다. 52라운드에서 추출하면 자원별 (완료 신호, 대기 필요 여부)가 나온다.

추출하니 **비동기성 자체가 3사 3색**이었다(예상 못 한 발견):

| CSP | 성격 | 근거 |
| --- | --- | --- |
| aws | **자원별로 갈린다** — VPC·서브넷·IAM·SG는 즉시, EKS(`ACTIVE`)·EC2(`running`)·EFS(`available`)·AMI(`available`)·VGW attach(`attached`)는 폴링 | 실험 스크립트의 폴링 유무 |
| azure | ARM이 `provisioningState`로 통일. CLI가 **기본으로 기다린다** — 비동기는 `--no-wait`로 우리가 고른 것 | aks·VNG 라운드 |
| gcp | **모든 mutate가 Operation을 반환**한다 — 구조적으로 통일이고 `wait_op`이 그걸 감춘다 | `gcp-apply3/mutate` |

**claims에 넣지 않는다.** (간선 × CSP × 질문)은 의존의 형식이고 이건 자원
하나의 성질이다 — 억지로 넣으면 `required:true`가 세 판정을 겸하다 어긋났던
그 실수의 반복이다. 별도 산출물 `operations.json`으로 낸다.

## 산출물

1. `depkb/operations.json` — 자원별 `{create: {async, doneSignal}, delete: {...}}`.
   빌드가 실험 기록과 대조한다(claims와 같은 규율).
2. claims에 술어 부류 `자격 요건:` 추가 — aws `k8sCluster→iamRole`에.
3. `provision_view`에 `waitFor`(연산 성질)와 `qualifications`(자격 요건) 추가.
4. worked example을 **테스트로 박는다** — "뷰가 aws-eks3의 생성·삭제 스텝을
   전부 도출하는가". 사후 확인을 규율로 승격한다.

## 위협

- **T-추출의 근사**: 폴링 유무는 우리가 스크립트를 그렇게 쓴 결과이지 CSP가
  그렇다는 증명이 아니다. 안 기다려도 됐는데 기다린 자원이 있을 수 있다.
  완화: 관측된 **중간 상태**(CREATING·PROVISIONING)가 기록에 있으면 비동기가
  확실하고, 없으면 "우리는 기다렸다"로만 적는다.
- **T-자격 요건의 범위**: 정책 이름(`AmazonEKSClusterPolicy`)은 aws 고유
  문자열이라 중립 어휘가 아니다. 술어에 그대로 적고 **우리가 고른 값이
  아니라 서버가 요구한 것**임을 증거로 남긴다.
