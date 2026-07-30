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
         evidence=[
             ("azure-k8s-vpn-2026-07-31", "K1.aks-omit-agentpools",
              "InvalidParameter", "apply"),
         ],
         note="서버가 필수를 이름으로: 'Required parameter agentPoolProfiles is "
              "missing' — 노드풀은 생성 시 내장(TB의 nodeGroupsOnCreation 관측과 "
              "정합). 허상 서브넷 거부(K2)도 확인했으나 subnet 필수성 자체는 "
              "생성 라운드 몫(관리형 vnet 합성이 가설)"),
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
              "극단 사례"),
    dict(csp="aws", subject="k8sCluster", object="subnet", question="existence",
         verdict="required",
         predicate="배치 조건: 서로 다른 AZ의 서브넷 ≥2",
         evidence=[
             ("aws-eks-2026-07-31", "E1.omit-vpc-config",
              "--resources-vpc-config", "preflight"),
             ("aws-eks-2026-07-31", "E3.one-subnet",
              "InvalidParameterException", "apply"),
             ("aws-eks2-2026-07-31", "C1.one-real-subnet",
              "InvalidParameterException", "apply"),
             ("aws-eks2-2026-07-31", "C2.two-subnets-same-az",
              "InvalidParameterException", "apply"),
         ],
         note="실역할·실서브넷으로 앞 검사를 통과시켜 격리했다 — 서버가 조건을 "
              "문장으로 말한다: 'Subnets specified must be in at least two "
              "different AZs'. 같은 AZ 둘도 거부(C2)라 수가 아니라 분산이 조건. "
              "roleArn도 SDK 층 필수(E2) — IAM 자원은 어휘 밖 대기열"),
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
