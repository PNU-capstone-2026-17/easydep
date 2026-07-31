"""어휘 — 다루는 자원 타입의 우주와 CSP 결속.

## 타입 우주는 우리 구성이다

도구의 swagger가 아니라 **용도에서 역산했다**: 컨테이너/VM 워크로드 하나를
클라우드에 앉히기 위해 계획기가 결정해야 하는 최소 자원 종류. 그래서 아홉이다 —
네트워크·서브넷·방화벽·NIC·공인IP·로드밸런서·VM·디스크·(등록형) 키.

선정 기준을 명시한다: **(1)** 하류 계획이 그 자원의 생성 여부·개수를 결정해야 하고
**(2)** azure 컨트롤 플레인에 독립 CRUD가 있는 것. k8s·관리형 DB는 다음 수직
절단면으로 미룬다 — 배제가 아니라 순서다.

## 결속(binding)은 원문으로 검증된다

각 타입의 azure 결속은 핀 박힌 스키마 캐시(`cache/azure/`)의 definition 실물과
1:1로 맞아야 하고, `test_depkb.py`가 그것을 강제한다. 결속이 깨지면(판 갱신 등)
어휘가 조용히 낡는 대신 테스트가 죽는다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AzureBinding:
    """어휘 타입 하나의 azure 스키마 결속."""

    file: str  #: 캐시 키 (fetch_azure.FILES의 키)
    definition: str  #: 그 파일 `definitions/` 안의 이름


#: 타입 우주. 키가 주장(claim)의 주체·대상 어휘다.
TYPES: dict[str, AzureBinding] = {
    "network": AzureBinding("network-virtualNetwork", "Common.VirtualNetwork"),
    "subnet": AzureBinding("network-virtualNetwork", "Common.Subnet"),
    "firewall": AzureBinding("network-virtualNetwork", "Common.NetworkSecurityGroup"),
    "nic": AzureBinding("network-virtualNetwork", "Common.NetworkInterface"),
    "publicIp": AzureBinding("network-virtualNetwork", "Common.PublicIPAddress"),
    "loadBalancer": AzureBinding("network-loadBalancer", "Common.LoadBalancer"),
    "vm": AzureBinding("compute-ComputeRP", "VirtualMachine"),
    "disk": AzureBinding("compute-DiskRP", "Disk"),
    "sshKey": AzureBinding("compute-ComputeRP", "SshPublicKeyResource"),
}

#: aws 결속 — CloudFormation 리소스 타입 이름. 원문은 CFN 스펙(핀은
#: fetch_vendors.SOURCES). CFN은 azure와 달리 **Required 플래그를 실제로 쓴다.**
AWS_TYPES: dict[str, str] = {
    "network": "AWS::EC2::VPC",
    "subnet": "AWS::EC2::Subnet",
    "firewall": "AWS::EC2::SecurityGroup",
    "nic": "AWS::EC2::NetworkInterface",
    "publicIp": "AWS::EC2::EIP",
    "loadBalancer": "AWS::ElasticLoadBalancingV2::LoadBalancer",
    "vm": "AWS::EC2::Instance",
    "disk": "AWS::EC2::Volume",
    "sshKey": "AWS::EC2::KeyPair",
}

#: gcp 결속 — compute 디스커버리 문서의 schema 이름. 특기 둘을 결속에 박는다:
#: **sshKey는 대응 자원이 없고**(None — 키는 메타데이터 값, 중립화 지도와 정합)
#: **NIC는 독립 자원이 아니라 Instance 내장 스키마다**(gcp에 NIC CRUD가 없다).
GCP_TYPES: dict[str, str | None] = {
    "network": "Network",
    "subnet": "Subnetwork",
    "firewall": "Firewall",
    "nic": "NetworkInterface",  # 내장 — 독립 CRUD 없음
    "publicIp": "Address",
    "loadBalancer": "ForwardingRule",  # gcp LB는 성좌 — 진입점만 결속
    "vm": "Instance",
    "disk": "Disk",
    "sshKey": None,
}

#: aws 이름 기반 참조 휴리스틱 — CFN은 참조가 문자열이라 **속성 이름**으로
#: 겨눈다. **우리 구성**이고 과대·과소근사 둘 다 가능하다(계획 T1과 같은 지위) —
#: 후보마다 원문 인용이 붙어 틀리면 인용에서 드러난다.
AWS_NAME_REFS: dict[str, str] = {
    "SubnetId": "subnet", "SubnetIds": "subnet", "Subnets": "subnet",
    "SubnetMappings": "subnet",
    "VpcId": "network",
    "SecurityGroupIds": "firewall", "SecurityGroups": "firewall",
    "GroupSet": "firewall", "Groups": "firewall",
    "NetworkInterfaceId": "nic",
    "AllocationId": "publicIp",
    "VolumeId": "disk",
    "KeyName": "sshKey",
    # image 라운드(2026-07-31). CFN엔 EC2 이미지 자원 타입이 없어 image는
    # aws에서 대상으로만 나타난다. Required:False는 위치 플래그다(LaunchTemplate
    # 경로) — 서버 요구는 동적 층이 판정.
    "ImageId": "image",
}

#: gcp (스키마, 속성) 쌍 한정 참조 — 이름만으로는 'source' 같은 일반어가
#: 오탐하므로 쌍으로 좁힌다. **우리 구성**, 인용 동반.
GCP_PAIR_REFS: dict[tuple[str, str], str] = {
    ("NetworkInterface", "network"): "network",
    ("NetworkInterface", "subnetwork"): "subnet",
    ("AttachedDisk", "source"): "disk",
    ("Firewall", "network"): "network",
    ("Subnetwork", "network"): "network",
    ("ForwardingRule", "network"): "network",
    ("ForwardingRule", "subnetwork"): "subnet",
    # image 라운드(2026-07-31): 부트 디스크의 이미지 참조. 대안 슬롯
    # (sourceSnapshot·AttachedDisk.source)의 존재가 선언 술어 가설의 근거다.
    ("AttachedDiskInitializeParams", "sourceImage"): "image",
}

#: ARM id 참조 래퍼 — 다른 RP의 자원을 id로 가리키는 정의. **우리 구성**이되,
#: 후보마다 원문 인용이 붙으므로 대응이 틀리면 인용에서 드러난다.
REFERENCE_WRAPPERS: dict[str, str] = {
    "NetworkInterfaceReference": "nic",
    "ManagedDiskParameters": "disk",
    # image 라운드(2026-07-31): 참조 훅만 연다 — image를 TYPES 주체로 올리면
    # image→vm·image→disk 후보가 생겨 미판정이 쌓인다(customImage 대기열의 것).
    "ImageReference": "image",
}

#: ARM 경로 세그먼트 → 어휘 타입. 경로 중첩(부모/{}/자식/{}) 추출에 쓴다.
PATH_SEGMENTS: dict[str, str] = {
    "virtualNetworks": "network",
    "subnets": "subnet",
    "networkSecurityGroups": "firewall",
    "networkInterfaces": "nic",
    "publicIPAddresses": "publicIp",
    "loadBalancers": "loadBalancer",
    "virtualMachines": "vm",
    "disks": "disk",
    "sshPublicKeys": "sshKey",
}
