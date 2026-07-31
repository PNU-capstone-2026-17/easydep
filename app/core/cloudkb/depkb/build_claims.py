"""주장 산출물 — 스키마·실험의 모든 증거를 (간선 × CSP × 질문)으로 통합한다.

이것이 의존성 분석의 **결과물**이다. 후보(스키마 층)와 측정(실험 기록)은 재료고,
소비자(계획기·사람)가 읽는 것은 이 파일이다.

판정 어휘와 규율:

- `required` / `optional` / `holds`(lifecycle 제약 실재) — **apply 층 증거가
  있을 때만.** 단 preflight **거부**는 required의 충분 증거다(실물 컨트롤
  플레인의 답이라서). preflight/스키마 **통과·침묵은 어떤 판정의 증거도 아니다**
  — 그런 칸은 `unknown`으로 남는다(aws·gcp 전부가 지금 이 상태다 — 계정 없음,
  T9: 정적 상한).
- 실험 증거는 (실험 산출물, 스텝 키, 기대 코드)로 인용하고, **빌드가 그 스텝의
  실측 코드와 대조해 어긋나면 죽는다** — 판정이 측정에서 떨어져 나가는 것을
  기계가 막는다.
- 판정 배정 자체는 **우리 구성**이다(EXPERIMENT_JUDGMENTS). 표시하고, 근거 없는
  배정은 빌드가 거부한다.

실행: `python -m app.core.cloudkb.depkb.build_claims` → `claims.json`
"""

from __future__ import annotations

import json
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_LAYER_RANK = {"schema": 0, "preflight": 1, "apply": 2}

#: 실험 판정표 — 우리 구성. ref = (실험 디렉터리, results.json의 스텝 키, 기대 코드
#: 또는 "ok"). 빌드가 실측과 대조한다.
EXPERIMENT_JUDGMENTS: list[dict] = [
    # ── existence (azure) ──
    dict(csp="azure", subject="nic", object="subnet", question="existence",
         verdict="required",
         evidence=[
             ("azure-preflight-2026-07-30", "omit-nic-subnet.validate",
              "SubnetIsRequired", "preflight"),
             ("azure-apply-2026-07-30", "A.apply.dangling-nic-subnet",
              "InvalidResourceReference", "apply"),
         ]),
    dict(csp="azure", subject="vm", object="nic", question="existence",
         verdict="required",
         evidence=[
             ("azure-apply-2026-07-30", "A.apply.omit-vm-nic",
              "InvalidParameter", "apply"),
             ("azure-apply-2026-07-30", "A.apply.dangling-vm-nic",
              "NotFound", "apply"),
         ],
         note="preflight는 침묵했다(Compute RP 깊이 — P5a findings §3)"),
    dict(csp="azure", subject="subnet", object="network", question="existence",
         verdict="required",
         evidence=[
             ("azure-apply-2026-07-30", "A.apply.dangling-subnet-parent",
              "ResourceNotFound", "apply"),
         ],
         note="경로 중첩(소속) — 부모 없이 만들 방법 자체가 없다"),
    dict(csp="azure", subject="nic", object="firewall", question="existence",
         verdict="optional",
         evidence=[
             ("azure-apply-2026-07-30", "B.build-chain", "ok", "apply"),
         ],
         note="사슬의 nic1이 NSG 없이 생성 성공 — 부재 하 성공이 optional의 증거"),
    dict(csp="azure", subject="loadBalancer", object="subnet|publicIp|publicIPPrefix",
         question="existence", verdict="required",
         predicate="disjunctive: 셋 중 하나",
         evidence=[
             ("azure-preflight-2026-07-30", "omit-lb-frontend-ref.validate",
              "FrontendIPConfigurationHasNoSubnetOrPublicIPAddressOrPublicIPPrefix",
              "preflight"),
             ("azure-apply-2026-07-30", "A.apply.dangling-lb-pip",
              "InvalidResourceReference", "apply"),
         ]),
    # ── existence 2라운드 (azure) ──
    dict(csp="azure", subject="network", object="subnet", question="existence",
         verdict="optional",
         evidence=[
             ("azure-apply2-2026-07-30", "E1.apply-vnet-without-subnet",
              "ok", "apply"),
         ],
         note="서브넷 없는 VNet이 실제로 만들어졌다 — 1라운드 preflight 통과는 "
              "증거가 아니었고 이것이 증거다"),
    dict(csp="azure", subject="nic", object="publicIp", question="existence",
         verdict="optional",
         evidence=[
             ("azure-apply-2026-07-30", "B.build-chain", "ok", "apply"),
         ],
         note="1라운드 nic1이 PIP 없이 생성 성공"),
    dict(csp="azure", subject="subnet", object="firewall", question="existence",
         verdict="optional",
         evidence=[
             ("azure-apply-2026-07-30", "B.build-chain", "ok", "apply"),
         ],
         note="1라운드 s1이 NSG 없이 생성 성공"),
    dict(csp="azure", subject="loadBalancer", object="subnet",
         question="existence", verdict="optional",
         evidence=[
             ("azure-apply2-2026-07-30", "E0.build-chain2", "ok", "apply"),
         ],
         note="공용 LB(lbp)가 PIP만으로 성공 — 단독으론 선택, 선언 술어의 구성원"),
    dict(csp="azure", subject="loadBalancer", object="publicIp",
         question="existence", verdict="optional",
         evidence=[
             ("azure-apply2-2026-07-30", "E0.build-chain2", "ok", "apply"),
         ],
         note="내부 LB(lbi)가 subnet만으로 성공 — 단독으론 선택, 선언 술어의 구성원. "
              "쌍 호환(SKU) 축은 신규 구독에서 도달 불가 — Basic PIP 생성 한도 0"
              "(IPv4BasicSkuPublicIpCountLimitReached, azure-apply4 실측)"),
    dict(csp="azure", subject="vm", object="disk", question="existence",
         verdict="optional",
         evidence=[
             ("azure-apply3-2026-07-30", "F0.build-vm-no-datadisk", "ok", "apply"),
         ],
         note="데이터 디스크 없이 VM 생성 성공. 덤 관측: 선언 안 한 OS 디스크가 "
              "서버 이름으로 생성됐다(F0.disks-after-create) — 서버측 합성"),
    # ── k8s·vpn (2026-07-31 거부 라운드 — 완전 판정된 것만, 나머지는 생성 라운드) ──
    dict(csp="azure", subject="k8sCluster", object="k8sNodeGroup",
         question="existence", verdict="required",
         predicate="수명 조건: 생성 시 필수 · 이후 독립 CRUD(add/delete)",
         evidence=[
             ("azure-k8s-vpn-2026-07-31", "K1.aks-omit-agentpools",
              "InvalidParameter", "apply"),
             ("azure-aks2-2026-07-31", "B1.nodepool-add", "ok", "apply"),
             ("azure-aks2-2026-07-31", "B2.nodepool-delete", "ok", "apply"),
         ],
         note="서버가 필수를 이름으로: 'Required parameter agentPoolProfiles is "
              "missing' — 노드풀은 생성 시 내장(TB의 nodeGroupsOnCreation 관측과 "
              "정합). **생성 시 필수와 이후 CRUD 가능은 별개**임을 재측정으로 "
              "확인했다(add·delete 모두 성공) — gcp와 같은 CRUD 성질을 갖되 "
              "생성 계약만 반전이다"),
    dict(csp="azure", subject="vpn", object="subnet", question="existence",
         verdict="required",
         predicate="이름 조건: 정확히 GatewaySubnet이라는 서브넷이어야 한다",
         evidence=[
             ("azure-k8s-vpn-2026-07-31", "V1.vng-wrong-subnet-name",
              "InvalidResourceReference", "apply"),
         ],
         note="다른 이름의 서브넷만 있는 VNet에서 게이트웨이 생성 → 서비스가 "
              "subnets/GatewaySubnet을 스스로 참조하다 실패. graphkb 소스 관측"
              "(networkinfo.yaml)의 컨트롤 플레인 확인 — 조건이 이름에 걸리는 "
              "극단 사례. **대우도 확인했다**(azure-vpn2): 그 이름으로 만들면 "
              "게이트웨이가 실제로 선다(Succeeded)"),
    dict(csp="azure", subject="vpn", object="subnet", question="lifecycle",
         verdict="holds",
         evidence=[
             ("azure-vpn2-2026-07-31", "L1.delete-gatewaysubnet-in-use",
              "InUseSubnetCannotBeDeleted", "apply"),
             ("azure-vpn2-2026-07-31", "F3.delete-vnet", "ok", "apply"),
         ],
         note="게이트웨이가 쓰는 GatewaySubnet은 못 지운다 — 게이트웨이 삭제 후 "
              "성공(양성 대조). vnet 삭제도 같은 코드로 막힌다"),
    dict(csp="azure", subject="vpn", object="publicIp", question="existence",
         verdict="required",
         predicate="쌍 호환: AZ SKU 게이트웨이는 zone이 구성된 PIP를 요구한다",
         evidence=[
             ("azure-vpn2-2026-07-31", "K0b.pip-zone-pair-constraint",
              "VmssVpnGatewayPublicIpsMustHaveZonesConfigured", "apply"),
             ("azure-vpn2-2026-07-31", "K1.create-vng-nowait", "ok", "apply"),
         ],
         note="zone 없는 Standard PIP로는 거부되고 zone-redundant PIP로는 선다. "
              "**Basic PIP 축이 소멸한 자리에 zone 축이 있었다** — 쌍 호환은 "
              "사라지는 게 아니라 옮겨간다. 리전 능력도 SKU를 제한한다"
              "(K0: koreacentral은 비-AZ SKU 거부)"),
    dict(csp="azure", subject="vpn", object="publicIp", question="lifecycle",
         verdict="holds",
         evidence=[
             ("azure-vpn2-2026-07-31", "L2.delete-pip-in-use",
              "PublicIPAddressCannotBeDeleted", "apply"),
             ("azure-vpn2-2026-07-31", "F2.delete-pip", "ok", "apply"),
         ]),
    dict(csp="aws", subject="k8sCluster", object="subnet", question="existence",
         verdict="required",
         predicate="배치 조건: 서로 다른 AZ의 서브넷 ≥2 (양성 대조로 대우 확인)",
         evidence=[
             ("aws-eks-2026-07-31", "E1.omit-vpc-config",
              "--resources-vpc-config", "preflight"),
             ("aws-eks-2026-07-31", "E3.one-subnet",
              "InvalidParameterException", "apply"),
             ("aws-eks2-2026-07-31", "C1.one-real-subnet",
              "InvalidParameterException", "apply"),
             ("aws-eks2-2026-07-31", "C2.two-subnets-same-az",
              "InvalidParameterException", "apply"),
             ("aws-eks3-2026-07-31", "K1.create-cluster", "ok", "apply"),
             ("aws-eks3-2026-07-31", "K2.cluster-active", "ok", "apply"),
         ],
         note="실역할·실서브넷으로 앞 검사를 통과시켜 격리했다 — 서버가 조건을 "
              "문장으로 말한다: 'Subnets specified must be in at least two "
              "different AZs'. 같은 AZ 둘도 거부(C2)라 수가 아니라 분산이 조건. "
              "roleArn도 SDK 층 필수(E2) — IAM 자원은 어휘 밖 대기열"),
    dict(csp="aws", subject="k8sCluster", object="firewall",
         question="existence", verdict="optional",
         predicate="server-implicit: 클러스터 보안 그룹을 서비스가 만든다",
         evidence=[
             ("aws-eks3-2026-07-31", "K3.cluster-shape", "ok", "apply"),
         ],
         note="SG를 준 적이 없는데 clusterSecurityGroupId가 실재한다"
              "(sg-04b748adcf2b00dad) — azure의 vnet 합성·gcp의 default-pool과 "
              "같은 패턴이 aws k8s에도 있다"),
    dict(csp="aws", subject="k8sCluster", object="subnet", question="lifecycle",
         verdict="holds",
         evidence=[
             ("aws-eks3-2026-07-31", "L1.delete-subnet-in-use",
              "DependencyViolation", "apply"),
             ("aws-eks3-2026-07-31", "F2.delete-subnet1", "ok", "apply"),
         ],
         note="클러스터가 쓰는 서브넷은 못 지운다 — IaaS 층과 같은 코드"
              "(DependencyViolation). 클러스터 삭제 후에는 성공(양성 대조)"),
    dict(csp="aws", subject="k8sCluster", object="network", question="lifecycle",
         verdict="holds",
         evidence=[
             ("aws-eks3-2026-07-31", "L2.delete-vpc-in-use",
              "DependencyViolation", "apply"),
             ("aws-eks3-2026-07-31", "F3.delete-vpc", "ok", "apply"),
         ]),
    dict(csp="azure", subject="k8sCluster", object="subnet", question="existence",
         verdict="optional",
         predicate="server-implicit: 서비스가 노드 리소스 그룹에 vnet을 합성",
         evidence=[
             ("azure-aks2-2026-07-31", "A1.create-no-subnet-nowait", "ok", "apply"),
             ("azure-aks2-2026-07-31", "A2.provisioning-final", "ok", "apply"),
             ("azure-aks2-2026-07-31", "A4.synthesized-vnets-in-node-rg",
              "ok", "apply"),
         ],
         note="서브넷 없이 만든 AKS가 Succeeded까지 갔고, 노드 RG"
              "(MC_depkb-preflight_depkb-aks_koreacentral)에 서비스가 만든 "
              "vnet(aks-vnet-67015217)이 실재한다 — **CB 드라이버 합성과 같은 "
              "일을 관리형 서비스가 한다**는 실측. aws는 정반대로 사용자에게 "
              "다른 AZ 서브넷 2개를 요구한다(양상 반전)"),
    dict(csp="gcp", subject="k8sCluster", object="network", question="existence",
         verdict="optional",
         predicate="server-default: 미지정 시 default 네트워크",
         evidence=[
             ("gcp-gke2-2026-07-31", "G1.create-omit-network", "ok", "apply"),
             ("gcp-gke2-2026-07-31", "G2.status-final", "ok", "apply"),
             ("gcp-gke2-2026-07-31", "G3.server-filled-network", "ok", "apply"),
         ],
         note="network 생략 클러스터가 RUNNING까지 갔고 서버가 network=default를 "
              "채웠다 — firewall→network·lb→network와 같은 대체 패턴이 k8s에도"),
    dict(csp="gcp", subject="k8sCluster", object="subnet", question="existence",
         verdict="optional",
         predicate="server-default: 미지정 시 default 서브넷",
         evidence=[
             ("gcp-gke2-2026-07-31", "G1.create-omit-network", "ok", "apply"),
             ("gcp-gke2-2026-07-31", "G3.server-filled-network", "ok", "apply"),
         ],
         note="같은 관측(G3)이 network·subnetwork·nodePools 셋을 한꺼번에 말한다 "
              "— subnetwork도 default로 채워졌다"),
    dict(csp="gcp", subject="k8sCluster", object="k8sNodeGroup",
         question="existence", verdict="optional",
         predicate="server-implicit: 미지정 시 default-pool 합성 · 이후 add/delete 가능",
         evidence=[
             ("gcp-gke2-2026-07-31", "G3.server-filled-network", "ok", "apply"),
             ("gcp-gke2-2026-07-31", "P1.nodepool-add", "ok", "apply"),
             ("gcp-gke2-2026-07-31", "P3.nodepool-delete-retry", "ok", "apply"),
         ],
         note="**azure와 양상 반전** — azure는 agentPoolProfiles가 생성 시 필수인데 "
              "gcp는 nodePools 없이 만들면 서버가 default-pool을 만든다(G3의 "
              "nodePools 실물). 그리고 gcp 노드풀은 독립 CRUD가 있다(add/delete "
              "성공). azure 노드풀 CRUD는 하네스 결함으로 미측정(아래 note)"),
    # ── lifecycle (azure) ──
    dict(csp="azure", subject="vm", object="disk", question="lifecycle",
         verdict="holds",
         evidence=[
             ("azure-apply3-2026-07-30", "C1.delete-disk-attached",
              "OperationNotAllowed", "apply"),
             ("azure-apply3-2026-07-30", "D.delete-data-disk", "ok", "apply"),
         ],
         note="붙은 디스크 삭제 거부 + 분리 후 성공. 역방향 관측: VM 삭제가 OS "
              "디스크를 남긴다(D.disks-after-vm-delete) — CB 드라이버가 디스크를 "
              "직접 지우는 이유"),
    dict(csp="azure", subject="nic", object="publicIp", question="lifecycle",
         verdict="holds",
         evidence=[
             ("azure-apply2-2026-07-30", "C.delete-pip-attached",
              "PublicIPAddressCannotBeDeleted", "apply"),
             ("azure-apply2-2026-07-30", "D.delete-pip1", "ok", "apply"),
         ],
         note="선택 참조인데 붙어 있으면 삭제 금지 — nic→firewall과 같은 꼴"),
    dict(csp="azure", subject="nic", object="subnet", question="lifecycle",
         verdict="holds",
         evidence=[
             ("azure-apply-2026-07-30", "C.delete-subnet-in-use",
              "InUseSubnetCannotBeDeleted", "apply"),
             ("azure-apply-2026-07-30", "D.delete-subnet", "ok", "apply"),
         ],
         note="사용 중 삭제 거부 + NIC 제거 후 삭제 성공(양성 대조)"),
    dict(csp="azure", subject="subnet", object="network", question="lifecycle",
         verdict="holds",
         evidence=[
             ("azure-apply-2026-07-30", "C.delete-vnet-in-use",
              "InUseSubnetCannotBeDeleted", "apply"),
             ("azure-apply-2026-07-30", "D.delete-vnet", "ok", "apply"),
         ]),
    dict(csp="azure", subject="nic", object="firewall", question="lifecycle",
         verdict="holds",
         evidence=[
             ("azure-apply-2026-07-30", "C.delete-nsg-attached",
              "InUseNetworkSecurityGroupCannotBeDeleted", "apply"),
             ("azure-apply-2026-07-30", "D.delete-nsg", "ok", "apply"),
         ],
         note="선택 참조여도 붙어 있는 동안 삭제는 막힌다 — existence와 lifecycle이 독립"),
    # ── aws (preflight=DryRun — 깊이가 API마다 다름을 관측) ──
    dict(csp="aws", subject="nic", object="subnet", question="existence",
         verdict="required",
         evidence=[
             ("aws-apply-2026-07-30", "A.omit-nic-subnet", "--subnet-id", "preflight"),
         ],
         note="거부가 **클라이언트(CLI/SDK 모델) 층**에서 났다 — 서버 미도달. "
              "CFN Required:true(스키마 층)와 합치하나 서버측 생략 실험은 CLI로 "
              "불가능했다. 한편 CreateNetworkInterface의 DryRun은 허상 서브넷을 "
              "통과시켰다(A.dangling-nic-subnet) — DryRun 깊이가 API마다 다르다"),
    dict(csp="aws", subject="vm", object="subnet", question="existence",
         verdict="optional",
         predicate="server-default: 미지정 시 기본 VPC 서브넷 대체",
         evidence=[
             ("aws-apply-2026-07-30", "A.dryrun-vm-default-vpc",
              "DryRunOperation", "preflight"),
             ("aws-apply-2026-07-30", "A.dangling-vm-subnet",
              "InvalidSubnetID.NotFound", "preflight"),
         ],
         note="DryRunOperation은 '만들었다면 성공했을 것' — CFN 문서 주석의 "
              "기본 VPC 대체가 실측됐다. 명시하면 실재해야 한다"),
    dict(csp="aws", subject="vm", object="sshKey", question="existence",
         verdict="optional",
         evidence=[
             ("aws-apply-2026-07-30", "A.dryrun-vm-default-vpc",
              "DryRunOperation", "preflight"),
             ("aws-apply-2026-07-30", "A.dangling-vm-keyname",
              "InvalidKeyPair.NotFound", "preflight"),
         ],
         note="같은 DryRun이 KeyName도 생략했다 — 성공. CB의 'sshKey 필수'가 "
              "aws에서도 도구의 요구였음이 동적으로 확정됐다(azure 무참조·gcp "
              "자원 부재에 이어 세 번째 형태)"),
    dict(csp="aws", subject="subnet", object="network", question="existence",
         verdict="required",
         evidence=[
             ("aws-apply2-2026-07-31", "B1.dangling-subnet-vpc",
              "InvalidVpcID.NotFound", "apply"),
         ],
         note="허상 VPC 거부(서버). 생략은 클라이언트 층이 막아 서버 실험 불가 — "
              "CFN Required:true와 합치"),
    dict(csp="aws", subject="firewall", object="network", question="existence",
         verdict="optional",
         predicate="server-default: 미지정 시 기본 VPC로 대체",
         evidence=[
             ("aws-apply2-2026-07-31", "B2.sg-omit-vpc", "ok", "apply"),
             ("aws-apply2-2026-07-31", "B2.server-filled-vpc", "ok", "apply"),
         ],
         note="서버가 채운 VpcId 실물을 기록했다 — gcp firewall→network의 default "
              "네트워크 대체와 같은 꼴(2사 수렴)"),
    dict(csp="aws", subject="nic", object="firewall", question="existence",
         verdict="optional",
         predicate="server-default: 미지정 시 VPC의 default SG 부착",
         evidence=[
             ("aws-apply2-2026-07-31", "B3.eni-omit-groups", "ok", "apply"),
             ("aws-apply2-2026-07-31", "B3.server-filled-groups", "ok", "apply"),
         ],
         note="서버가 붙인 ['default'] 그룹 실물을 기록했다"),
    dict(csp="aws", subject="vm", object="nic", question="existence",
         verdict="optional",
         predicate="server-implicit: ENI를 서버가 암묵 생성",
         evidence=[
             ("aws-apply-2026-07-30", "A.dryrun-vm-default-vpc",
              "DryRunOperation", "preflight"),
         ],
         note="RunInstances DryRun은 허상을 잡는 깊이가 증명돼 있어(1라운드) 그 "
              "성공이 유효한 증거다. azure(필수)와 양상 반전 — vm→nic도 CSP "
              "색인이 필요하다"),
    dict(csp="aws", subject="vm", object="firewall", question="existence",
         verdict="optional",
         predicate="server-default: 미지정 시 default SG",
         evidence=[
             ("aws-apply-2026-07-30", "A.dryrun-vm-default-vpc",
              "DryRunOperation", "preflight"),
         ]),
    dict(csp="aws", subject="vm", object="disk", question="existence",
         verdict="optional",
         predicate="server-implicit: AMI의 루트 볼륨을 서버가 만든다",
         evidence=[
             ("aws-apply-2026-07-30", "A.dryrun-vm-default-vpc",
              "DryRunOperation", "preflight"),
         ],
         note="vm→disk 3사 완성: aws 선택 · azure 선택 · **gcp만 필수** — 양상 "
              "반전의 전모"),
    dict(csp="aws", subject="loadBalancer", object="subnet", question="existence",
         verdict="required",
         predicate="ALB는 서로 다른 AZ의 서브넷 ≥2 (NLB는 1)",
         evidence=[
             ("aws-apply2-2026-07-31", "B4.lb-omit-subnets",
              "ValidationError", "apply"),
             ("aws-apply2-2026-07-31", "B4.alb-one-subnet",
              "ValidationError", "apply"),
             ("aws-apply2-2026-07-31", "F2.internal-nlb-omit-subnets",
              "ValidationError", "apply"),
         ],
         note="서버가 필수를 문장으로 말한다: 'At least one subnet' · 'two subnets "
              "in two different AZs' — azure sqlDb의 다른-AZ-서브넷-2와 같은 꼴의 "
              "카디널리티+배치 술어. 생략 거부는 internet-facing·internal 두 스킴 "
              "모두에서 확인(F2)"),
    dict(csp="aws", subject="loadBalancer", object="firewall", question="existence",
         verdict="optional",
         evidence=[
             ("aws-apply2-2026-07-31", "F.internal-nlb-one-subnet-no-sg",
              "ok", "apply"),
         ],
         note="internal NLB가 SG 없이 섰다. 1차 internet-facing 시도는 IGW 부재로 "
              "교란됐다(InvalidSubnet — 환경 전제가 의존 검사보다 먼저)"),
    dict(csp="aws", subject="nic", object="subnet", question="lifecycle",
         verdict="holds",
         evidence=[
             ("aws-apply-2026-07-30", "C.delete-subnet-in-use",
              "DependencyViolation", "apply"),
             ("aws-apply-2026-07-30", "D.delete-subnet", "ok", "apply"),
         ]),
    dict(csp="aws", subject="subnet", object="network", question="lifecycle",
         verdict="holds",
         evidence=[
             ("aws-apply-2026-07-30", "C.delete-vpc-in-use",
              "DependencyViolation", "apply"),
             ("aws-apply-2026-07-30", "D.delete-vpc", "ok", "apply"),
         ]),
    dict(csp="aws", subject="nic", object="firewall", question="lifecycle",
         verdict="holds",
         evidence=[
             ("aws-apply-2026-07-30", "C.delete-sg-attached",
              "DependencyViolation", "apply"),
             ("aws-apply-2026-07-30", "D.delete-sg", "ok", "apply"),
         ],
         note="3사 모두 같은 꼴 — 붙어 있는 SG/NSG는 못 지운다. 코드만 다르다"
              "(DependencyViolation / InUse... / RESOURCE_IN_USE)"),
    # ── gcp (REST 직접 — gcloud CLI 기본값 주입 배제) ──
    dict(csp="gcp", subject="subnet", object="network", question="existence",
         verdict="required",
         evidence=[
             ("gcp-apply-2026-07-31", "A.subnet-omit-network", "invalid", "apply"),
             ("gcp-apply-2026-07-31", "A.subnet-dangling-network", "notFound", "apply"),
         ]),
    dict(csp="gcp", subject="firewall", object="network", question="existence",
         verdict="optional",
         predicate="server-default: 미지정 시 default 네트워크로 대체",
         evidence=[
             ("gcp-apply-2026-07-31", "A.firewall-omit-network", "ok", "apply"),
             ("gcp-apply-2026-07-31", "A.firewall-dangling-network", "notFound", "apply"),
         ],
         note="명시는 선택이나 관계가 없는 것이 아니다 — 서버가 default로 채운다"
              "(스키마 서술의 실측 확인). 명시하면 실재해야 한다(dangling 거부)"),
    dict(csp="gcp", subject="vm", object="nic", question="existence",
         verdict="required",
         evidence=[
             ("gcp-apply-2026-07-31", "A.instance-omit-nic", "invalid", "apply"),
         ],
         note="NIC는 독립 자원이 아니라 내장 구조인데도 최소 하나는 필수다"),
    dict(csp="gcp", subject="vm", object="disk", question="existence",
         verdict="required",
         predicate="쌍 호환: 디스크와 인스턴스의 존이 일치해야 한다",
         evidence=[
             ("gcp-apply-2026-07-31", "A.instance-omit-disks", "invalid", "apply"),
             ("gcp-paircompat-2026-07-31", "P1.zone-mismatch-vm-zoneB",
              "invalid", "apply"),
             ("gcp-paircompat-2026-07-31", "P2.same-zone-control", "ok", "apply"),
         ],
         note="**azure와 양상 반전** — azure는 OS 디스크를 서버가 합성해 선택, "
              "gcp는 부트 디스크 명세가 필수다. 쌍 호환(존)은 대조군으로 축을 "
              "격리해 확정 — 조건이 (주체 속성 × 대상 속성) 쌍에 걸리는 부류의 "
              "첫 어휘 내 실측"),
    dict(csp="gcp", subject="nic", object="network", question="existence",
         verdict="optional",
         evidence=[
             ("gcp-apply-2026-07-31", "F0.create-instance", "ok", "apply"),
         ],
         note="1라운드 인스턴스가 subnetwork만 지정하고 network 없이 성공 — "
              "서버가 서브넷에서 네트워크를 역산한다"),
    dict(csp="gcp", subject="nic", object="subnet", question="existence",
         verdict="optional",
         predicate="network 모드 조건부: custom에선 필수 · auto에선 서버가 리전 서브넷 대체",
         evidence=[
             ("gcp-apply2-2026-07-31", "A1.nic-network-only-custom", "invalid", "apply"),
             ("gcp-apply2-2026-07-31", "A2.nic-network-only-auto", "ok", "apply"),
             ("gcp-apply2-2026-07-31", "A2.server-filled-subnetwork", "ok", "apply"),
         ],
         note="같은 생략이 custom 모드에선 거부되고 auto 모드에선 서버가 채운다 — "
              "서버가 채운 subnetwork 실물을 기록했다. 필연이 (간선×CSP)를 넘어 "
              "**대상 자원의 모드**에까지 걸리는 첫 사례"),
    dict(csp="gcp", subject="loadBalancer", object="network", question="existence",
         verdict="optional",
         predicate="server-default: EXTERNAL은 불참 · INTERNAL은 서브넷에서 역산",
         evidence=[
             ("gcp-apply2-2026-07-31", "B.create-ext-forwardingrule", "ok", "apply"),
             ("gcp-apply4-2026-07-31", "I3.internal-fr-omit-network", "ok", "apply"),
             ("gcp-apply4-2026-07-31", "I3.server-filled-network", "ok", "apply"),
         ],
         note="4라운드에서 INTERNAL 반쪽을 닫았다 — 서버가 채운 network 실물 "
              "기록(nic→network와 같은 역산 패턴). 스킴 양쪽 모두 생략 가능"),
    dict(csp="gcp", subject="loadBalancer", object="subnet", question="existence",
         verdict="optional",
         predicate="스킴 조건부: EXTERNAL 불참 · INTERNAL 필수",
         evidence=[
             ("gcp-apply2-2026-07-31", "B.create-ext-forwardingrule", "ok", "apply"),
             ("gcp-apply3-2026-07-31", "I1.internal-fr-omit-subnet",
              "invalid", "apply"),
             ("gcp-apply3-2026-07-31", "I2.internal-fr-full", "ok", "apply"),
         ],
         note="조건부 필연의 두 번째 실물(첫째는 nic→subnet의 네트워크 모드) — "
              "이번엔 조건이 자기 자신의 속성(스킴)이다"),
    dict(csp="azure", subject="k8sCluster", object="subnet", question="lifecycle",
         verdict="holds",
         evidence=[
             ("azure-aks3-2026-07-31", "L1.delete-subnet-in-use",
              "InUseSubnetCannotBeDeleted", "apply"),
             ("azure-aks3-2026-07-31", "F2.delete-subnet", "ok", "apply"),
         ],
         note="사용자 서브넷에 붙인 클러스터로 쟀다 — 앞 라운드의 합성 vnet은 "
              "노드 RG 안이라 대상이 아니었다. 코드가 IaaS의 nic→subnet과 같다"),
    dict(csp="azure", subject="k8sCluster", object="network", question="lifecycle",
         verdict="holds",
         evidence=[
             ("azure-aks3-2026-07-31", "L2.delete-vnet-in-use",
              "InUseSubnetCannotBeDeleted", "apply"),
             ("azure-aks3-2026-07-31", "F3.delete-vnet", "ok", "apply"),
         ]),
    dict(csp="gcp", subject="k8sCluster", object="subnet", question="lifecycle",
         verdict="holds",
         evidence=[
             ("gcp-gke3-2026-07-31", "L1.delete-subnet-in-use",
              "resourceInUseByAnotherResource", "apply"),
             ("gcp-gke3-2026-07-31", "F2.delete-subnet", "ok", "apply"),
         ],
         note="전용 네트워크 위 클러스터로 쟀다 — 앞 라운드는 default를 써서 "
              "삭제 시도 자체가 불가능했다. aws와 같은 꼴, 코드만 다르다"),
    dict(csp="gcp", subject="k8sCluster", object="network", question="lifecycle",
         verdict="holds",
         evidence=[
             ("gcp-gke3-2026-07-31", "L2.delete-network-in-use",
              "RESOURCE_IN_USE_BY_ANOTHER_RESOURCE", "apply"),
             ("gcp-gke3-2026-07-31", "F3.delete-network", "ok", "apply"),
         ]),
    dict(csp="gcp", subject="nic", object="subnet", question="lifecycle",
         verdict="holds",
         evidence=[
             ("gcp-apply-2026-07-31", "C.delete-subnet-in-use",
              "resourceInUseByAnotherResource", "apply"),
             ("gcp-apply-2026-07-31", "D.delete-subnet", "ok", "apply"),
         ]),
    dict(csp="gcp", subject="subnet", object="network", question="lifecycle",
         verdict="holds",
         evidence=[
             ("gcp-apply-2026-07-31", "C.delete-network-in-use",
              "RESOURCE_IN_USE_BY_ANOTHER_RESOURCE", "apply"),
             ("gcp-apply-2026-07-31", "D.delete-network", "ok", "apply"),
         ]),
    dict(csp="gcp", subject="vm", object="disk", question="lifecycle",
         verdict="holds",
         evidence=[
             ("gcp-apply-2026-07-31", "C.delete-bootdisk-attached",
              "resourceInUseByAnotherResource", "apply"),
             ("gcp-apply-2026-07-31", "D.delete-disk.depkbg-vm", "ok", "apply"),
         ],
         note="부트 디스크가 인스턴스 삭제 후 살아남았다(D.disks-after-delete — "
              "API 기본 autoDelete=false의 실측). azure OS 디스크 잔존과 쌍이다"),
    # ── k8s 층 합성 (2026-07-31 합성 라운드 — Service→LB·PVC→디스크) ──
    # 주체가 클라우드 자원이 아니라 k8s 오브젝트인 첫 간선들. 존재 질문의 뜻:
    # 대상이 미리 있어야 하는가 → 아니다, k8s 층이 스스로 합성한다(optional +
    # server-implicit → 소비층에서 autoFilled — 이중 생성 경계 I2의 근거).
    # 생명주기는 삭제 보호가 아니라 **동반 정리**라 술어로 가른다(closure가
    # deleteBefore가 아닌 cleanupCascades로 소비).
    dict(csp="azure", subject="k8sService", object="loadBalancer",
         question="existence", verdict="optional",
         predicate="server-implicit: type=LoadBalancer 서비스가 노드 RG에 "
                   "클라우드 LB 규칙·공인 IP를 합성한다",
         evidence=[
             ("azure-k8s-synth-2026-07-31", "S3.svc-ingress-hint", "ok", "apply"),
             ("azure-k8s-synth-2026-07-31", "S5.pips-after-svc", "ok", "apply"),
             ("azure-k8s-synth-2026-07-31", "S4b.lb-after-svc-requery",
              "ok", "apply"),
         ],
         note="LB 'kubernetes'에 규칙 1이 실리고 합성 PIP"
              "(kubernetes-a9dde…)의 주소가 Service ingress IP와 일치 — "
              "클러스터 상시 LB에 규칙을 합성하는 꼴(전용 LB 신설이 아니다)"),
    dict(csp="azure", subject="k8sService", object="loadBalancer",
         question="lifecycle", verdict="holds",
         predicate="동반 정리: Service 삭제가 LB 규칙과 합성 PIP를 함께 지운다",
         evidence=[
             ("azure-k8s-synth-2026-07-31", "L2.lb-rules-after-delete",
              "ok", "apply"),
             ("azure-k8s-synth-2026-07-31", "L3.pips-after-delete", "ok", "apply"),
         ],
         note="삭제 후 LB 규칙 0·합성 PIP 소멸(클러스터 아웃바운드 PIP만 잔존). "
              "계획층이 LB 삭제 단계를 내면 안 된다 — 이미 없다"),
    dict(csp="azure", subject="k8sPvc", object="disk",
         question="existence", verdict="optional",
         predicate="server-implicit: CSI가 관리 디스크를 합성한다 — 단 첫 "
                   "소비자(Pod) 시점(기본 SC WaitForFirstConsumer 실측)",
         evidence=[
             ("azure-k8s-synth-2026-07-31", "P2.pvc-status-alone", "ok", "apply"),
             ("azure-k8s-synth-2026-07-31", "P3b.pvc-disks-alone", "ok", "apply"),
             ("azure-k8s-synth-2026-07-31", "P5.pvc-bound", "ok", "apply"),
             ("azure-k8s-synth-2026-07-31", "P6b.pvc-disks-after-pod",
              "ok", "apply"),
             ("azure-k8s-synth-2026-07-31", "P7.pv-volumehandle-hint",
              "ok", "apply"),
         ],
         note="PVC 단독 60초엔 디스크 0(Pending) → Pod 트리거 후 Bound·노드 RG에 "
              "pvc-… 디스크 실재, PV volumeHandle이 그 ARM ID와 일치(층 간 동일성)"),
    dict(csp="azure", subject="k8sPvc", object="disk",
         question="lifecycle", verdict="holds",
         predicate="동반 정리: PVC 삭제가 합성 디스크를 지운다(기본 reclaim "
                   "Delete 실측)",
         evidence=[
             ("azure-k8s-synth-2026-07-31", "L6.pvc-disks-after-delete",
              "ok", "apply"),
         ]),
    dict(csp="gcp", subject="k8sService", object="loadBalancer",
         question="existence", verdict="optional",
         predicate="server-implicit: type=LoadBalancer 서비스가 LB 성좌"
                   "(forwardingRule+targetPool+방화벽)를 합성한다",
         evidence=[
             ("gcp-k8s-synth-2026-07-31", "S2.svc-ingress-hint", "ok", "apply"),
             ("gcp-k8s-synth-2026-07-31", "S3.lb-after-svc", "ok", "apply"),
         ],
         note="합성 성좌 실물: forwardingRule·targetPool 쌍(a040876a…) + 방화벽 "
              "3종(k8s-fw-…, hc, deny) — 'gcp LB는 성좌' 결속 지식이 k8s 합성 "
              "경로에서 재현. azure(상시 LB에 규칙)와 꼴이 다르다"),
    dict(csp="gcp", subject="k8sService", object="loadBalancer",
         question="lifecycle", verdict="holds",
         predicate="동반 정리: Service 삭제가 성좌 전체(FR·targetPool·방화벽)를 "
                   "함께 지운다",
         evidence=[
             ("gcp-k8s-synth-2026-07-31", "L2.lb-after-delete", "ok", "apply"),
             ("gcp-k8s-synth-2026-07-31", "L3.fw-after-delete", "ok", "apply"),
         ],
         note="라운드 끝의 전용 네트워크 삭제 성공(F4)이 방화벽 잔여 0의 독립 "
              "증명이기도 하다 — 규칙이 남았으면 네트워크 삭제가 거부됐다"),
    dict(csp="gcp", subject="k8sPvc", object="disk",
         question="existence", verdict="optional",
         predicate="server-implicit: CSI가 존 디스크를 합성한다 — 단 첫 "
                   "소비자(Pod) 시점(기본 SC standard-rwo WaitForFirstConsumer "
                   "실측)",
         evidence=[
             ("gcp-k8s-synth-2026-07-31", "P2.pvc-status-alone", "ok", "apply"),
             ("gcp-k8s-synth-2026-07-31", "P3.disks-after-pvc-alone",
              "ok", "apply"),
             ("gcp-k8s-synth-2026-07-31", "P5.pvc-bound", "ok", "apply"),
             ("gcp-k8s-synth-2026-07-31", "P6.disks-after-pod", "ok", "apply"),
             ("gcp-k8s-synth-2026-07-31", "P7.pv-volumehandle-hint",
              "ok", "apply"),
         ],
         note="PVC 단독 60초엔 pvc- 디스크 0 → Pod 후 Bound·존에 pvc-… 디스크 "
              "실재, volumeHandle 경로 일치. azure와 동형(2사 수렴)"),
    dict(csp="gcp", subject="k8sPvc", object="disk",
         question="lifecycle", verdict="holds",
         predicate="동반 정리: PVC 삭제가 합성 디스크를 지운다(기본 reclaim "
                   "Delete 실측)",
         evidence=[
             ("gcp-k8s-synth-2026-07-31", "L6.pvc-disks-after-delete",
              "ok", "apply"),
         ]),
    dict(csp="aws", subject="k8sService", object="loadBalancer",
         question="existence", verdict="optional",
         predicate="server-implicit: type=LoadBalancer 서비스가 CLB와 전용 "
                   "SG(k8s-elb-…)를 합성한다 — 노드 0에서도",
         evidence=[
             ("aws-k8s-synth-2026-07-31", "S2.svc-ingress-hint", "ok", "apply"),
             ("aws-k8s-synth-2026-07-31", "S3.elb-after-svc", "ok", "apply"),
             ("aws-k8s-synth-2026-07-31", "S5.sgs-after-svc", "ok", "apply"),
         ],
         note="노드그룹 없는 클러스터에서 CLB(a4cbeb15…)가 태그된 두 서브넷에 "
              "생성 — 이벤트 실물(S4): 'There are no available nodes' 직후 "
              "'Ensured load balancer'. 합성이 노드 존재와 무관함의 증거. "
              "서브넷 태그(kubernetes.io/role/elb)는 실험 전제이지 판정 대상 "
              "아님"),
    # ── 기능 의존 첫 라운드 (2026-07-31 — 셋째 질문 축 question="function".
    # 존재·생명주기는 거부 코드가 오라클이지만 기능 의존은 컨트롤 플레인이
    # 막지 않는(무방비) 지대라 **기능 신호**(외부 TCP 22 도달성)로 잰다.
    # 인과 사다리: 기능 확인 → 변이 성공(무방비) → 상실 → 복원 → 회복.
    # 회복까지 봐야 상실이 변이 탓임이 선다) ──
    dict(csp="azure", subject="nic", object="publicIp", question="function",
         verdict="holds",
         predicate="무방비: 실행 중 VM의 NIC에서 PIP 분리를 컨트롤 플레인이 "
                   "막지 않는다 — 기능 신호는 외부 TCP 22 도달성",
         evidence=[
             ("azure-func-2026-07-31", "F1.reachable-baseline", "ok", "apply"),
             ("azure-func-2026-07-31", "M1.detach-pip-while-running",
              "ok", "apply"),
             ("azure-func-2026-07-31", "M1b.vm-still-running", "ok", "apply"),
             ("azure-func-2026-07-31", "F2.unreachable-after-detach",
              "ok", "apply"),
             ("azure-func-2026-07-31", "M2.reattach-pip", "ok", "apply"),
             ("azure-func-2026-07-31", "F3.reachable-again", "ok", "apply"),
         ],
         note="같은 PIP 재부착으로 회복까지 관측(인과 완결). 존재·생명주기와 "
              "한 쌍에서 세 질문이 다 판정된 첫 간선이다(존재 optional · "
              "생명주기 holds(삭제 보호) · 기능 holds). 덤 관측: Standard SKU "
              "PIP는 NSG 없으면 인바운드가 기본 차단된다(R6 — F1이 NSG 부착 "
              "전 7회 실패, 부착 직후 성공. secure-by-default)"),
    dict(csp="gcp", subject="vm", object="publicIp", question="function",
         verdict="holds",
         predicate="무방비: RUNNING 인스턴스에서 accessConfig 삭제를 컨트롤 "
                   "플레인이 막지 않는다 — 기능 신호는 외부 TCP 22 도달성",
         evidence=[
             ("gcp-func-2026-07-31", "F1.reachable-baseline", "ok", "apply"),
             ("gcp-func-2026-07-31", "M1.delete-accessconfig-while-running",
              "ok", "apply"),
             ("gcp-func-2026-07-31", "M1b.vm-still-running", "ok", "apply"),
             ("gcp-func-2026-07-31", "F2.unreachable-after-delete",
              "ok", "apply"),
             ("gcp-func-2026-07-31", "M2.add-accessconfig", "ok", "apply"),
             ("gcp-func-2026-07-31", "F3.reachable-again", "ok", "apply"),
         ],
         note="재부여 시 임시 IP는 **새 주소**가 온다(34.64.142.22→"
              "34.22.74.114) — 회복은 새 주소로 관측. gcp 공인 IP는 독립 "
              "자원이 아니라 인스턴스의 accessConfig라는 결속 차이가 변이 "
              "경로에도 그대로 나타난다"),
    dict(csp="aws", subject="vm", object="publicIp", question="function",
         verdict="holds",
         predicate="무방비: running 인스턴스에서 EIP 분리를 컨트롤 플레인이 "
                   "막지 않는다 — 기능 신호는 외부 TCP 22 도달성",
         evidence=[
             ("aws-func-2026-07-31", "F1.reachable-baseline", "ok", "apply"),
             ("aws-func-2026-07-31", "M1.disassociate-while-running",
              "ok", "apply"),
             ("aws-func-2026-07-31", "M1b.instance-public-ip-now",
              "ok", "apply"),
             ("aws-func-2026-07-31", "F2.unreachable-after-detach",
              "ok", "apply"),
             ("aws-func-2026-07-31", "M2.reassociate-eip", "ok", "apply"),
             ("aws-func-2026-07-31", "F3.reachable-again", "ok", "apply"),
         ],
         note="EIP는 분리해도 우리 소유로 남아 **같은 주소로 회복**을 관측"
              "(gcp 임시 IP와 대조). 자동 공인 IP 없이 기동해 EIP만이 도달성 "
              "경로임을 격리했다. 22 허용 SG는 전제이지 판정 대상 아님"),
    # ── customImage 라운드 (2026-07-31 — 어휘 편입. graphkb의
    # node→customImage **생명주기 관측과 컨트롤 플레인이 갈린 자리**:
    # 3사 모두 이미지가 원본을 붙잡지 않는다. 결속이 없으므로 lifecycle
    # 행을 만들지 않는다 — 없는 결속을 적지 않는다) ──
    dict(csp="azure", subject="customImage", object="disk",
         question="existence", verdict="required",
         evidence=[
             ("azure-cimg-2026-07-31", "A1.dangling-source-disk",
              "NotFound", "apply"),
             ("azure-cimg-2026-07-31", "A2.create-image-from-disk",
              "ok", "apply"),
         ],
         note="허상 원본은 NotFound, 실제 관리 디스크로는 성공. 전제 함정: "
              "--hyper-v-generation 기본 V1이 소스 디스크 V2와 불일치해 "
              "InvalidParameter(results-round1.json) — 세대는 원본을 따라야 "
              "한다(전제이지 판정 아님). **원본 삭제는 막히지 않았다**"
              "(L1/L1b — 디스크 실제 소멸) → 생명주기 결속 없음, "
              "graphkb의 node→customImage 관측과 컨트롤 플레인이 갈린다"),
    dict(csp="azure", subject="vm", object="customImage",
         question="existence", verdict="optional",
         evidence=[
             ("azure-cimg-2026-07-31", "C1.create-vm-from-custom-image",
              "ok", "apply"),
         ],
         note="사슬 완결(디스크→이미지→VM). 선택인 이유는 vm→image의 선언 "
              "술어와 같다 — 부팅 원천은 플랫폼 이미지·기존 디스크로도 된다"),
    dict(csp="gcp", subject="customImage", object="disk",
         question="existence", verdict="required",
         evidence=[
             ("gcp-cimg-2026-07-31", "A1.dangling-source-disk",
              "notFound", "apply"),
             ("gcp-cimg-2026-07-31", "A3.image-ready", "ok", "apply"),
         ],
         note="원본 삭제 후에도 이미지 READY이고 archiveSizeBytes 709MB가 "
              "찍힌다(복사본 실증) — 그런데 **sourceDisk 참조는 허상으로 "
              "남는다**(L1b). 참조가 있다고 결속인 것이 아니라는 실물 사례"),
    dict(csp="gcp", subject="vm", object="customImage",
         question="existence", verdict="optional",
         evidence=[
             ("gcp-cimg-2026-07-31", "C1.create-vm-from-custom-image",
              "ok", "apply"),
             ("gcp-cimg-2026-07-31", "C2.vm-running", "ok", "apply"),
         ]),
    dict(csp="aws", subject="customImage", object="vm", question="existence",
         verdict="required",
         predicate="원본 종류 반전: AMI는 디스크가 아니라 **인스턴스**에서 "
                   "나온다(create-image)",
         evidence=[
             ("aws-cimg-2026-07-31", "A1.dangling-source-instance",
              "InvalidParameterValue", "apply"),
             ("aws-cimg-2026-07-31", "A3.ami-available", "ok", "apply"),
             ("aws-cimg-2026-07-31", "A4.ami-snapshots", "ok", "apply"),
         ],
         note="**간선의 대상 자체가 3사에서 갈린다** — azure·gcp는 "
              "customImage→disk인데 aws는 customImage→vm이다. 실체는 "
              "스냅샷(snap-0124…)이고, 원본 인스턴스 종료 후에도 AMI는 "
              "available(L1c) — 결속 없음. 정리에 스냅샷 삭제가 따로 필요"
              "하다는 것도 실측(등록 해제만 하면 조용히 남는다)"),
    dict(csp="aws", subject="vm", object="customImage", question="existence",
         verdict="optional",
         evidence=[
             ("aws-cimg-2026-07-31", "C1.run-instance-from-custom-ami",
              "ok", "apply"),
             ("aws-cimg-2026-07-31", "C2.vm-running", "ok", "apply"),
         ],
         note="사슬 완결. vm→image가 aws에서 required인 것과 함께 읽어야 "
              "한다 — 부팅 원천은 필수지만 그것이 **커스텀일 필요는 없다**"),
    # ── iamRole 라운드 (2026-07-31 — 어휘: aws IAM Role/Instance Profile ·
    # azure Managed Identity · gcp Service Account를 iamRole 하나로 묶는다.
    # 대부분 기존 실측의 승격이고 새 실험은 gcp 마이크로 하나) ──
    dict(csp="aws", subject="k8sCluster", object="iamRole",
         question="existence", verdict="required",
         evidence=[
             ("aws-eks-2026-07-31", "E2.omit-role", "--role-arn", "preflight"),
             ("aws-eks3-2026-07-31", "R.create-role", "ok", "apply"),
             ("aws-eks3-2026-07-31", "K1.create-cluster", "ok", "apply"),
         ],
         note="거부 라운드에서 관측만 되고 어휘 밖이라 못 들어갔던 판정의 "
              "승격. 생략 거부는 **클라이언트층**(--role-arn — nic→subnet과 "
              "같은 한계 명시), 실역할 생성·클러스터 성공이 양성 대조. "
              "EKS IAM의 기능 축(정책 분리 시 무엇이 깨지나)은 기능 신호 "
              "정의가 별도 설계라 미판정 유지(eks3 라운드에 무방비 관측만)"),
    dict(csp="aws", subject="vm", object="iamRole", question="existence",
         verdict="optional",
         evidence=[
             ("aws-func2-2026-07-31", "R10.run-instance", "ok", "apply"),
         ],
         note="인스턴스 프로필 없이 run-instances 성공(기존 라운드 전부가 "
              "그랬다 — 대표 인용 하나)"),
    dict(csp="azure", subject="vm", object="iamRole", question="existence",
         verdict="optional",
         evidence=[
             ("azure-func-2026-07-31", "R4.create-vm", "ok", "apply"),
         ],
         note="managed identity 미지정으로 VM 생성 성공. identity 실물 부재는 "
              "미기록 — 생략 성공이 판정 근거다"),
    dict(csp="gcp", subject="vm", object="iamRole", question="existence",
         verdict="optional",
         evidence=[
             ("gcp-iam-2026-07-31", "A1.create-vm-omit-sa", "ok", "apply"),
             ("gcp-iam-2026-07-31", "A2.running", "ok", "apply"),
             ("gcp-iam-2026-07-31", "A3.serviceaccounts-shape", "ok", "apply"),
         ],
         note="SA 생략 인스턴스가 RUNNING이고 **serviceAccounts: null 실물** "
              "— 서버가 기본 SA를 붙이지 않는다(server-default 아님). gcloud "
              "CLI·콘솔은 기본 SA를 주입하므로 REST 직접(CLI 기본값 배제) "
              "결정이 없었다면 server-default로 오판했을 자리"),
    # ── 기능 의존 2라운드 (2026-07-31 — firewall·라우팅 도달성. azure의
    # 라우팅 셀은 **대응 자원 부재**(인터넷 경로가 시스템 라우트)라 간선이
    # 없다 — 빈칸이 아니라 자원 부재의 기록. internetGateway 어휘 첫 등장) ──
    dict(csp="azure", subject="subnet", object="firewall", question="function",
         verdict="holds",
         predicate="무방비: 사용 중 서브넷에서 NSG 분리를 컨트롤 플레인이 "
                   "막지 않는다 — 차단은 NSG 부재와 Standard PIP "
                   "secure-by-default의 합성 효과",
         evidence=[
             ("azure-func2-2026-07-31", "F1.reachable-baseline", "ok", "apply"),
             ("azure-func2-2026-07-31", "M1.detach-nsg-from-subnet",
              "ok", "apply"),
             ("azure-func2-2026-07-31", "F2.unreachable-after-detach",
              "ok", "apply"),
             ("azure-func2-2026-07-31", "M2.reattach-nsg", "ok", "apply"),
             ("azure-func2-2026-07-31", "F3.reachable-again", "ok", "apply"),
         ],
         note="1라운드 덤 관측(NSG 부착 전 7회 실패)의 정식 승격. 분리 경로 "
              "함정: --network-security-group \"\"는 InvalidResourceReference"
              "(results-round1.json) — generic --remove가 분리 경로다. "
              "상실은 연속 2회 확인"),
    dict(csp="aws", subject="vm", object="firewall", question="function",
         verdict="holds",
         predicate="무방비: 실행 중 인스턴스의 SG를 빈 인그레스 SG로 교체하는 "
                   "것을 컨트롤 플레인이 막지 않는다(관계 변이)",
         evidence=[
             ("aws-func2-2026-07-31", "F1.reachable-baseline", "ok", "apply"),
             ("aws-func2-2026-07-31", "M1.swap-to-empty-sg", "ok", "apply"),
             ("aws-func2-2026-07-31", "F2.unreachable-empty-sg", "ok", "apply"),
             ("aws-func2-2026-07-31", "M2.restore-sg22", "ok", "apply"),
             ("aws-func2-2026-07-31", "F3.reachable-again", "ok", "apply"),
         ],
         note="교체 즉시 상실(연속 2회)·원복 즉시 회복 — SG는 상태 비저장 "
              "부착이라 전파가 빠르다는 관측 포함"),
    dict(csp="gcp", subject="vm", object="firewall", question="function",
         verdict="holds",
         predicate="무방비: 네트워크 스코프 방화벽 규칙 삭제를 컨트롤 플레인이 "
                   "막지 않는다 — gcp 방화벽은 자원 간 부착이 아니라 규칙이라 "
                   "관계 변이가 없다(규칙 변이가 유일한 경로)",
         evidence=[
             ("gcp-func2-2026-07-31", "F1.reachable-baseline", "ok", "apply"),
             ("gcp-func2-2026-07-31", "M1.delete-fw-rule", "ok", "apply"),
             ("gcp-func2-2026-07-31", "F2.unreachable-no-rule", "ok", "apply"),
             ("gcp-func2-2026-07-31", "M2.recreate-fw-rule", "ok", "apply"),
             ("gcp-func2-2026-07-31", "F3.reachable-again", "ok", "apply"),
         ],
         note="3사 변이 경로가 3색: azure 서브넷-NSG 분리(관계) · aws SG "
              "교체(관계) · gcp 규칙 삭제(규칙) — 방화벽의 결속 모델 자체가 "
              "다르다는 실측"),
    dict(csp="aws", subject="subnet", object="internetGateway",
         question="function", verdict="holds",
         predicate="무방비: 라우트 테이블에서 0.0.0.0/0→IGW 라우트 삭제를 "
                   "컨트롤 플레인이 막지 않는다",
         evidence=[
             ("aws-func2-2026-07-31", "F3.reachable-again", "ok", "apply"),
             ("aws-func2-2026-07-31", "M3.delete-default-route", "ok", "apply"),
             ("aws-func2-2026-07-31", "F4.unreachable-no-route", "ok", "apply"),
             ("aws-func2-2026-07-31", "M4.recreate-route", "ok", "apply"),
             ("aws-func2-2026-07-31", "F5.reachable-final", "ok", "apply"),
         ],
         note="어휘 밖 대기열이던 internetGateway의 첫 판정 — 존재 전제는 "
              "교란으로 기실측(IGW 없으면 EIP 도달 불가), 이번에 기능 축으로 "
              "닫았다. 셀 기준선은 직전 회복(F3) — 원인 섞임 방지"),
    dict(csp="gcp", subject="network", object="internetGateway",
         question="function", verdict="holds",
         predicate="무방비: 자동 생성 기본 라우트(0.0.0.0/0→"
                   "default-internet-gateway) 삭제를 컨트롤 플레인이 막지 "
                   "않는다",
         evidence=[
             ("gcp-func2-2026-07-31", "M3a2.find-default-route-clientside",
              "ok", "apply"),
             ("gcp-func2-2026-07-31", "M3b.delete-default-route", "ok", "apply"),
             ("gcp-func2-2026-07-31", "F4.unreachable-no-route", "ok", "apply"),
             ("gcp-func2-2026-07-31", "M4.recreate-route", "ok", "apply"),
             ("gcp-func2-2026-07-31", "F5.reachable-final", "ok", "apply"),
         ],
         note="gcp의 IGW는 자원이 아니라 라우트의 개념적 next-hop이다"
              "(default-internet-gateway) — 실물은 자동 생성 라우트"
              "(default-route-31cd…). 재생성은 우리 이름으로 했고 기능이 "
              "회복됐다(이름이 아니라 경로가 기준). azure는 대응 자원 자체가 "
              "없다(시스템 라우트) — 3사 3색"),
    # ── k8s 합성 2라운드 (2026-07-31 — Ingress→LB. **기본 구성 판정**:
    # 관리형 기본에 애드온 0. 컨트롤러를 깔면 답이 바뀔 수 있고 그건 별도
    # 변형이다. RWX PVC 셀은 3사 전부 완주 불가(azure 정책 교란·gcp 드라이버
    # 거부·aws 전제 부재)라 판정 없이 실험 기록으로만 남았다) ──
    dict(csp="gcp", subject="k8sIngress", object="loadBalancer",
         question="existence", verdict="optional",
         predicate="server-implicit: 내장 컨트롤러가 전역 HTTP LB 성좌"
                   "(urlMap·targetHttpProxy·전역 forwardingRule·backendService·"
                   "healthCheck)를 합성한다",
         evidence=[
             ("gcp-k8s-synth2-2026-07-31", "I3.ingress-address-hint",
              "ok", "apply"),
             ("gcp-k8s-synth2-2026-07-31", "I4.http-lb-after-ingress",
              "ok", "apply"),
             ("gcp-k8s-synth2-2026-07-31", "I5.ingress-events-hint",
              "ok", "apply"),
         ],
         note="IP 34.98.66.175 할당·성좌 5종 실물(k8s2-um/tp/fr-…). 이벤트가 "
              "생성 순서를 문장으로(UrlMap→TargetProxy→ForwardingRule). "
              "Service의 지역 성좌와 달리 **전역** 성좌다. 특기: IngressClass "
              "목록은 비어 있었다(K3) — 부재 관측만으론 컨트롤러 부재를 판정할 "
              "수 없다는 실측"),
    dict(csp="gcp", subject="k8sIngress", object="loadBalancer",
         question="lifecycle", verdict="holds",
         predicate="동반 정리: Ingress 삭제가 전역 성좌 전체를 함께 지운다",
         evidence=[
             ("gcp-k8s-synth2-2026-07-31", "I7.http-lb-after-delete",
              "ok", "apply"),
         ]),
    dict(csp="azure", subject="k8sIngress", object="loadBalancer",
         question="existence", verdict="optional",
         evidence=[
             ("azure-k8s-synth2-2026-07-31", "K5.ingressclasses", "ok", "apply"),
             ("azure-k8s-synth2-2026-07-31", "I3.ingress-address-hint",
              "ok", "apply"),
             ("azure-k8s-synth2-2026-07-31", "I5.appgw-after-ingress",
              "ok", "apply"),
         ],
         note="**기본 구성에서 합성 없음** — IngressClass 0(K5)·Ingress 방치"
              "(주소 없음, I3)·상시 LB 규칙 0 유지(I4b 재관측)·AppGW 0(I5). "
              "gcp와 양상 반전: 같은 신호가 gcp에선 성좌를 합성하고 azure에선 "
              "아무것도 만들지 않는다. 노출을 이루는 방법(컨트롤러 애드온 등)은 "
              "사용자 결정이고 우리가 대신 정하지 않는다"),
    dict(csp="aws", subject="k8sIngress", object="loadBalancer",
         question="existence", verdict="optional",
         evidence=[
             ("aws-k8s-synth2-2026-07-31", "K4.ingressclasses", "ok", "apply"),
             ("aws-k8s-synth2-2026-07-31", "I3.ingress-address-hint",
              "ok", "apply"),
             ("aws-k8s-synth2-2026-07-31", "I4.elb-after-ingress",
              "ok", "apply"),
         ],
         note="**기본 구성에서 합성 없음** — IngressClass 0·Ingress 방치·"
              "CLB/ALB 목록 불변. Service→CLB(합성)와 대조: 같은 클러스터 "
              "기본 구성에서 Service 컨트롤러는 내장, Ingress 컨트롤러는 부재"),
    # ── image 라운드 (2026-07-31 — vm→image, 3사. 외부 대조 오류 셋 중
    # spec/image를 닫는다) ──
    dict(csp="azure", subject="vm", object="image", question="existence",
         verdict="optional",
         predicate="disjunctive: 부팅 원천 — imageReference ∨ 기존 OS 디스크 "
                   "attach 중 하나는 있어야 한다",
         evidence=[
             ("azure-image-2026-07-31", "A3.omit-storageprofile",
              "InvalidParameter", "apply"),
             ("azure-image-2026-07-31", "A2.dangling-image", "NotFound", "apply"),
             ("azure-image-2026-07-31", "B0.create-vm-from-image", "ok", "apply"),
             ("azure-image-2026-07-31", "B1.create-vm-attach-disk-no-image",
              "ok", "apply"),
         ],
         note="서버가 필수를 이름으로: \"Required parameter 'storageProfile' is "
              "missing\"(A3b 전문). 잔존 OS 디스크 attach로 이미지 없이 VM이 "
              "선다 — imageReference 슬롯 빈 값 실측(B1). 허상 이미지 id는 "
              "NotFound. FromImage인데 이미지가 없는 모순형(A1)은 별도 문장으로 "
              "거부된다. LB frontend와 같은 선언 술어 꼴"),
    dict(csp="gcp", subject="vm", object="image", question="existence",
         verdict="optional",
         predicate="disjunctive: 부트 디스크 원천 — initializeParams."
                   "sourceImage ∨ 기존 디스크(source) 중 하나는 있어야 한다",
         evidence=[
             ("gcp-image-2026-07-31", "G1.omit-image-and-source",
              "invalid", "apply"),
             ("gcp-image-2026-07-31", "G2.dangling-image", "invalid", "apply"),
             ("gcp-image-2026-07-31", "G3b.boot-from-existing-disk-no-image",
              "ok", "apply"),
             ("gcp-image-2026-07-31", "G3c.instance-shape", "ok", "apply"),
         ],
         note="서버가 술어를 문장으로: 'Boot disk must have a source "
              "specified'(G1). 이미지에서 만든 디스크를 source로 주면 "
              "sourceImage 없이 RUNNING까지 간다(G3c). 허상은 'referenced "
              "image resource cannot be found'. azure와 동형(2사 수렴)"),
    dict(csp="aws", subject="vm", object="image", question="existence",
         verdict="required",
         evidence=[
             ("aws-image-2026-07-31", "D1.omit-image-and-lt",
              "MissingParameter", "preflight"),
             ("aws-image-2026-07-31", "D2b.dangling-wellformed-ami",
              "InvalidAMIID.Malformed", "preflight"),
             ("aws-image-2026-07-31", "D3b.valid-ami-dryrun",
              "DryRunOperation", "preflight"),
         ],
         note="생략 거부가 **서버층**이다(MissingParameter, rejectedAt=server — "
              "nic→subnet의 client-층 한계와 다르다). RunInstances엔 기존 "
              "볼륨으로 부팅을 대신할 경로가 없어 3사 중 유일한 required — "
              "**세 번째 유형의 양상 반전**(선언 술어 ∨ 필수). CFN "
              "Required:False는 위치 플래그(LaunchTemplate가 AMI를 나르는 다른 "
              "자리)라는 기존 결론의 재확인. 허상 검사는 Malformed 층에서 "
              "걸렸다(정형 id도 — NotFound 층 미도달, 관측 그대로 기록). "
              "실물 생성 0(DryRun 사다리)"),
    dict(csp="aws", subject="k8sService", object="loadBalancer",
         question="lifecycle", verdict="holds",
         predicate="동반 정리: Service 삭제가 CLB를 지운다",
         evidence=[
             ("aws-k8s-synth-2026-07-31", "L2.elb-after-delete", "ok", "apply"),
         ],
         note="aws k8sPvc→disk는 이번 라운드 미측정(노드 0·EBS CSI 애드온 "
              "없음 — 전제 부재에서 '합성 없음' 판정은 오판. "
              "실험 기록 P3.unmeasured-note). 그래서 aws에는 k8sPvc 간선이 "
              "없다 — 빈칸이 아니라 범위 표시다"),
]


def _experiment_step(exp: str, key: str) -> dict:
    doc = json.loads(
        (_HERE / "experiments" / exp / "results.json").read_text(encoding="utf-8"))
    pool = doc.get("steps") or doc.get("tests")
    if "." in key and key.split(".")[-1] in ("validate", "what-if"):
        name, phase = key.rsplit(".", 1)
        return pool[name][phase]
    return pool[key]


def _schema_evidence() -> dict[tuple, list[dict]]:
    out: dict[tuple, list[dict]] = {}
    for csp, fname in [("azure", "azure_candidates.json"),
                       ("aws", "aws_candidates.json"),
                       ("gcp", "gcp_candidates.json")]:
        doc = json.loads((_HERE / fname).read_text(encoding="utf-8"))
        for c in doc["candidates"]:
            if c["form"] == "readonly-backlink":
                continue
            out.setdefault((csp, c["subject"], c["object"]), []).append({
                "layer": "schema", "cite": c["cite"], "form": c["form"],
                "requiredInSchema": c["requiredInSchema"],
            })
    return out


def build() -> dict:
    schema = _schema_evidence()
    claims: list[dict] = []
    judged: set[tuple] = set()

    for j in EXPERIMENT_JUDGMENTS:
        evid = []
        for exp, key, expect, layer in j["evidence"]:
            step = _experiment_step(exp, key)
            if expect == "ok":
                assert step["ok"], f"{exp}/{key}: 성공을 인용했는데 실측은 실패다"
            else:
                assert expect in step["errorCodes"], (
                    f"{exp}/{key}: 인용 코드 {expect}가 실측에 없다 "
                    f"{step['errorCodes']}"
                )
            evid.append({"layer": layer, "experiment": exp, "step": key,
                         "code": expect})
        pair_key = (j["csp"], j["subject"], j["object"].split("|")[0])
        evid = schema.get(pair_key, []) + evid
        judged.add((j["csp"], j["subject"], j["object"], j["question"]))
        claims.append({
            "subject": j["subject"], "object": j["object"], "csp": j["csp"],
            "question": j["question"], "verdict": j["verdict"],
            "predicate": j.get("predicate"), "note": j.get("note"),
            "oracle": max((e["layer"] for e in evid),
                          key=lambda x: _LAYER_RANK[x]),
            "evidence": evid,
        })

    # 판정 없는 스키마 후보 → unknown (aws·gcp 전부와 azure 잔여)
    for (csp, s, o), evid in sorted(schema.items()):
        if (csp, s, o, "existence") in judged:
            continue
        claims.append({
            "subject": s, "object": o, "csp": csp, "question": "existence",
            "verdict": "unknown", "predicate": None,
            "note": "스키마 후보만 있다 — 이 간선의 동적 실험 미실행",
            "oracle": "schema", "evidence": evid,
        })

    claims.sort(key=lambda c: (c["csp"], c["subject"], c["object"], c["question"]))
    counts: dict[str, int] = {}
    for c in claims:
        counts[f"{c['csp']}.{c['verdict']}"] = counts.get(
            f"{c['csp']}.{c['verdict']}", 0) + 1
    return {
        "_note": (
            "의존 주장의 통합 산출물 — (간선 × CSP × 질문)마다 판정·증거·도달 "
            "오라클 층. 판정 배정은 우리 구성(build_claims.EXPERIMENT_JUDGMENTS)"
            "이고, 인용 코드가 실험 실측과 어긋나면 빌드가 죽는다. unknown은 "
            "빈칸이 아니라 '동적 층 미실행'의 기록이다."
        ),
        "verdictCounts": counts,
        "claims": claims,
    }


if __name__ == "__main__":
    result = build()
    (_HERE / "claims.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("claims:", len(result["claims"]), "|", result["verdictCounts"])
    for c in result["claims"]:
        if c["verdict"] != "unknown":
            print(f"  {c['csp']:6} {c['subject']}→{c['object']}"
                  f" [{c['question']}] = {c['verdict']} ({c['oracle']})")
