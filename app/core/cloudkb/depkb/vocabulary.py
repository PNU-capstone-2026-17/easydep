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

#: ARM id 참조 래퍼 — 다른 RP의 자원을 id로 가리키는 정의. **우리 구성**이되,
#: 후보마다 원문 인용이 붙으므로 대응이 틀리면 인용에서 드러난다.
REFERENCE_WRAPPERS: dict[str, str] = {
    "NetworkInterfaceReference": "nic",
    "ManagedDiskParameters": "disk",
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
