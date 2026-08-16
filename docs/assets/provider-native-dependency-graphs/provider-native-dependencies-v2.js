/*
 * AWS·Azure·GCP의 Docker-on-VM 범위에서 실제 CSP 리소스와 Terraform 연결 객체만 기록한다.
 * ID, port, path, image selector, DNS state 같은 값은 노드가 아니라 edge.referenceValues와
 * edge.validationGate로 표현한다. 단, selector가 가리키는 AMI·Virtual Machine Image·OS Image처럼
 * CSP에서 조회·재사용되는 실제 이미지 리소스는 referenceExisting 노드로 보존한다.
 */
(function expose(root) {
  "use strict";

  const doc = (title, url) => ({title, url});
  const entityClassReasons = {
    providerResource: "CSP API에서 독립적으로 식별되며 생성하거나 기존 항목을 참조할 수 있는 리소스다.",
    providerComponent: "상위 CSP 리소스 안에서 독립적으로 구성되지만 별도 최상위 리소스는 아닌 구성요소다.",
    association: "두 리소스의 연결을 Terraform이 독립 객체로 생성·교체·삭제하는 연결 리소스다."
  };
  const handlingReasons = {
    create: "이번 배포의 IaC가 생성하고 수명주기를 관리한다.",
    providerCreated: "상위 리소스를 생성하면 CSP가 실제 하위 리소스로 만들고 수명주기를 함께 관리한다.",
    configureInsideOwner: "상위 리소스 내부 구성으로 관리한다.",
    referenceExisting: "이번 배포가 새로 만들지 않고 기존 CSP 리소스를 조회·참조한다."
  };
  const necessityReasons = {
    selectedPathRequired: "선택한 배포 경로가 성립하려면 필요하다.",
    conditional: "해당 기능이나 구현 방식을 선택했을 때 필요하다.",
    alternative: "다른 합법적인 구현으로 대체할 수 있다."
  };
  const nameAuthorityReasons = {
    providerResource: "CSP 공식 문서와 API의 독립 리소스 명칭을 사용한다.",
    providerComponent: "CSP 공식 문서와 API의 상위 리소스 내부 구성 명칭을 사용한다.",
    terraformAssociation: "CSP API 참조를 Terraform이 별도 연결 리소스로 표현한 명칭이다."
  };
  const namingMetadata = {
    "aws.vpc": {serviceName: "Amazon VPC", apiResourceName: "Vpc", nameAuthority: "providerResource"},
    "aws.subnet": {serviceName: "Amazon VPC", apiResourceName: "Subnet", nameAuthority: "providerResource"},
    "aws.securityGroup": {serviceName: "Amazon VPC", apiResourceName: "SecurityGroup", nameAuthority: "providerResource"},
    "aws.defaultSecurityGroup": {serviceName: "Amazon VPC", apiResourceName: "SecurityGroup (groupName=default)", nameAuthority: "providerResource"},
    "aws.ami": {serviceName: "Amazon EC2", apiResourceName: "Image", nameAuthority: "providerResource"},
    "aws.ec2": {serviceName: "Amazon EC2", apiResourceName: "Instance", nameAuthority: "providerResource"},
    "aws.asgInstance": {serviceName: "Amazon EC2 Auto Scaling", apiResourceName: "Instance", nameAuthority: "providerResource"},
    "aws.primaryEni": {serviceName: "Amazon EC2", apiResourceName: "NetworkInterface", nameAuthority: "providerResource"},
    "aws.rootVolume": {serviceName: "Amazon EBS", apiResourceName: "Volume", nameAuthority: "providerResource"},
    "aws.internetGateway": {serviceName: "Amazon VPC", apiResourceName: "InternetGateway", nameAuthority: "providerResource"},
    "aws.routeTable": {serviceName: "Amazon VPC", apiResourceName: "RouteTable", nameAuthority: "providerResource"},
    "aws.mainRouteTable": {serviceName: "Amazon VPC", apiResourceName: "RouteTable", nameAuthority: "providerResource"},
    "aws.route": {serviceName: "Amazon VPC", apiResourceName: "Route", nameAuthority: "providerComponent"},
    "aws.localRoute": {serviceName: "Amazon VPC", apiResourceName: "Route", nameAuthority: "providerComponent"},
    "aws.defaultNetworkAcl": {serviceName: "Amazon VPC", apiResourceName: "NetworkAcl", nameAuthority: "providerResource"},
    "aws.routeTableAssociation": {serviceName: "Amazon VPC", apiResourceName: "RouteTableAssociation", nameAuthority: "terraformAssociation"},
    "aws.eip": {serviceName: "Amazon EC2", apiResourceName: "Address", nameAuthority: "providerResource"},
    "aws.eipAssociation": {serviceName: "Amazon EC2", apiResourceName: "AddressAssociation", nameAuthority: "terraformAssociation"},
    "aws.ebs": {serviceName: "Amazon EBS", apiResourceName: "Volume", nameAuthority: "providerResource"},
    "aws.volumeAttachment": {serviceName: "Amazon EBS", apiResourceName: "VolumeAttachment", nameAuthority: "terraformAssociation"},
    "aws.alb": {serviceName: "Elastic Load Balancing", apiResourceName: "LoadBalancer", nameAuthority: "providerResource"},
    "aws.albEni": {serviceName: "Elastic Load Balancing", apiResourceName: "NetworkInterface", nameAuthority: "providerResource"},
    "aws.albPublicAddress": {serviceName: "Elastic Load Balancing", apiResourceName: "Address", nameAuthority: "providerResource"},
    "aws.listener": {serviceName: "Elastic Load Balancing", apiResourceName: "Listener", nameAuthority: "providerResource"},
    "aws.targetGroup": {serviceName: "Elastic Load Balancing", apiResourceName: "TargetGroup", nameAuthority: "providerResource"},
    "aws.launchTemplate": {serviceName: "Amazon EC2", apiResourceName: "LaunchTemplate", nameAuthority: "providerResource"},
    "aws.autoScalingGroup": {serviceName: "Amazon EC2 Auto Scaling", apiResourceName: "AutoScalingGroup", nameAuthority: "providerResource"},
    "aws.natGateway": {serviceName: "Amazon VPC", apiResourceName: "NatGateway", nameAuthority: "providerResource"},
    "aws.natEni": {serviceName: "Amazon VPC", apiResourceName: "NetworkInterface", nameAuthority: "providerResource"},
    "aws.ecrRepository": {serviceName: "Amazon Elastic Container Registry", apiResourceName: "Repository", nameAuthority: "providerResource"},
    "aws.registryPullRole": {serviceName: "AWS Identity and Access Management", apiResourceName: "Role", nameAuthority: "providerResource"},
    "aws.registryPullPolicy": {serviceName: "AWS Identity and Access Management", apiResourceName: "Policy", nameAuthority: "providerResource"},
    "aws.registryPullPolicyAttachment": {serviceName: "AWS Identity and Access Management", apiResourceName: "AttachedPolicy", nameAuthority: "terraformAssociation"},
    "aws.registryInstanceProfile": {serviceName: "AWS Identity and Access Management", apiResourceName: "InstanceProfile", nameAuthority: "providerResource"},
    "azure.resourceGroup": {serviceName: "Azure Resource Manager", apiResourceName: "Microsoft.Resources/resourceGroups", nameAuthority: "providerResource"},
    "azure.vnet": {serviceName: "Azure Virtual Network", apiResourceName: "Microsoft.Network/virtualNetworks", nameAuthority: "providerResource"},
    "azure.subnet": {serviceName: "Azure Virtual Network", apiResourceName: "Microsoft.Network/virtualNetworks/subnets", nameAuthority: "providerResource"},
    "azure.nsg": {serviceName: "Azure Virtual Network", apiResourceName: "Microsoft.Network/networkSecurityGroups", nameAuthority: "providerResource"},
    "azure.nic": {serviceName: "Azure Virtual Network", apiResourceName: "Microsoft.Network/networkInterfaces", nameAuthority: "providerResource"},
    "azure.nicNsgAssociation": {serviceName: "Azure Virtual Network", apiResourceName: "Microsoft.Network/networkInterfaces.properties.networkSecurityGroup", nameAuthority: "terraformAssociation"},
    "azure.image": {serviceName: "Azure Virtual Machines", apiResourceName: "Microsoft.Compute/locations/publishers/artifacttypes/vmimage/offers/skus/versions", nameAuthority: "providerResource"},
    "azure.vm": {serviceName: "Azure Virtual Machines", apiResourceName: "Microsoft.Compute/virtualMachines", nameAuthority: "providerResource"},
    "azure.osDisk": {serviceName: "Azure Managed Disks", apiResourceName: "Microsoft.Compute/disks", nameAuthority: "providerResource"},
    "azure.publicIp": {serviceName: "Azure Virtual Network", apiResourceName: "Microsoft.Network/publicIPAddresses", nameAuthority: "providerResource"},
    "azure.disk": {serviceName: "Azure Managed Disks", apiResourceName: "Microsoft.Compute/disks", nameAuthority: "providerResource"},
    "azure.diskAttachment": {serviceName: "Azure Virtual Machines", apiResourceName: "Microsoft.Compute/virtualMachines.properties.storageProfile.dataDisks", nameAuthority: "terraformAssociation"},
    "azure.applicationGateway": {serviceName: "Azure Application Gateway", apiResourceName: "Microsoft.Network/applicationGateways", nameAuthority: "providerResource"},
    "azure.agwGatewayIp": {serviceName: "Azure Application Gateway", apiResourceName: "Microsoft.Network/applicationGateways/gatewayIPConfigurations", nameAuthority: "providerComponent"},
    "azure.agwFrontendIp": {serviceName: "Azure Application Gateway", apiResourceName: "Microsoft.Network/applicationGateways/frontendIPConfigurations", nameAuthority: "providerComponent"},
    "azure.agwFrontendPort": {serviceName: "Azure Application Gateway", apiResourceName: "Microsoft.Network/applicationGateways/frontendPorts", nameAuthority: "providerComponent"},
    "azure.agwListener": {serviceName: "Azure Application Gateway", apiResourceName: "Microsoft.Network/applicationGateways/httpListeners", nameAuthority: "providerComponent"},
    "azure.agwBackendPool": {serviceName: "Azure Application Gateway", apiResourceName: "Microsoft.Network/applicationGateways/backendAddressPools", nameAuthority: "providerComponent"},
    "azure.agwBackendSettings": {serviceName: "Azure Application Gateway", apiResourceName: "Microsoft.Network/applicationGateways/backendHttpSettingsCollection", nameAuthority: "providerComponent"},
    "azure.agwProbe": {serviceName: "Azure Application Gateway", apiResourceName: "Microsoft.Network/applicationGateways/probes", nameAuthority: "providerComponent"},
    "azure.agwRoutingRule": {serviceName: "Azure Application Gateway", apiResourceName: "Microsoft.Network/applicationGateways/requestRoutingRules", nameAuthority: "providerComponent"},
    "azure.vmss": {serviceName: "Azure Virtual Machine Scale Sets", apiResourceName: "Microsoft.Compute/virtualMachineScaleSets", nameAuthority: "providerResource"},
    "azure.vmssInstance": {serviceName: "Azure Virtual Machine Scale Sets", apiResourceName: "Microsoft.Compute/virtualMachineScaleSets/virtualMachines", nameAuthority: "providerResource"},
    "azure.vmssNic": {serviceName: "Azure Virtual Network", apiResourceName: "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/networkInterfaces", nameAuthority: "providerResource"},
    "azure.vmssOsDisk": {serviceName: "Azure Managed Disks", apiResourceName: "Microsoft.Compute/disks", nameAuthority: "providerResource"},
    "azure.vmssHealthExtension": {serviceName: "Azure Virtual Machine Scale Sets", apiResourceName: "Microsoft.Compute/virtualMachineScaleSets/extensions (ApplicationHealthLinux)", nameAuthority: "providerComponent"},
    "azure.vmssRepairPolicy": {serviceName: "Azure Virtual Machine Scale Sets", apiResourceName: "Microsoft.Compute/virtualMachineScaleSets.properties.automaticRepairsPolicy", nameAuthority: "providerComponent"},
    "azure.natGateway": {serviceName: "Azure NAT Gateway", apiResourceName: "Microsoft.Network/natGateways", nameAuthority: "providerResource"},
    "azure.subnetNatAssociation": {serviceName: "Azure NAT Gateway", apiResourceName: "Microsoft.Network/virtualNetworks/subnets.properties.natGateway", nameAuthority: "terraformAssociation"},
    "azure.natPublicIpAssociation": {serviceName: "Azure NAT Gateway", apiResourceName: "Microsoft.Network/natGateways.properties.publicIpAddresses", nameAuthority: "terraformAssociation"},
    "azure.containerRegistry": {serviceName: "Azure Container Registry", apiResourceName: "Microsoft.ContainerRegistry/registries", nameAuthority: "providerResource"},
    "azure.registryPullIdentity": {serviceName: "Managed Identities for Azure Resources", apiResourceName: "Microsoft.ManagedIdentity/userAssignedIdentities", nameAuthority: "providerResource"},
    "azure.registryPullRoleAssignment": {serviceName: "Azure Role-based Access Control", apiResourceName: "Microsoft.Authorization/roleAssignments", nameAuthority: "terraformAssociation"},
    "gcp.network": {serviceName: "Virtual Private Cloud (VPC)", apiResourceName: "compute.v1.Network", nameAuthority: "providerResource"},
    "gcp.subnetwork": {serviceName: "Virtual Private Cloud (VPC)", apiResourceName: "compute.v1.Subnetwork", nameAuthority: "providerResource"},
    "gcp.firewall": {serviceName: "Cloud Next Generation Firewall", apiResourceName: "compute.v1.Firewall", nameAuthority: "providerResource"},
    "gcp.image": {serviceName: "Compute Engine", apiResourceName: "compute.v1.Image", nameAuthority: "providerResource"},
    "gcp.instance": {serviceName: "Compute Engine", apiResourceName: "compute.v1.Instance", nameAuthority: "providerResource"},
    "gcp.migInstance": {serviceName: "Compute Engine", apiResourceName: "compute.v1.Instance", nameAuthority: "providerResource"},
    "gcp.networkInterface": {serviceName: "Compute Engine", apiResourceName: "compute.v1.NetworkInterface", nameAuthority: "providerComponent"},
    "gcp.accessConfig": {serviceName: "Compute Engine", apiResourceName: "compute.v1.AccessConfig", nameAuthority: "providerComponent"},
    "gcp.bootDisk": {serviceName: "Compute Engine", apiResourceName: "compute.v1.Disk", nameAuthority: "providerResource"},
    "gcp.regionalAddress": {serviceName: "Compute Engine", apiResourceName: "compute.v1.Address (addresses)", nameAuthority: "providerResource"},
    "gcp.disk": {serviceName: "Compute Engine", apiResourceName: "compute.v1.Disk", nameAuthority: "providerResource"},
    "gcp.diskAttachment": {serviceName: "Compute Engine", apiResourceName: "instances.attachDisk", nameAuthority: "terraformAssociation"},
    "gcp.globalAddress": {serviceName: "Cloud Load Balancing", apiResourceName: "compute.v1.Address (globalAddresses)", nameAuthority: "providerResource"},
    "gcp.forwardingRule": {serviceName: "Cloud Load Balancing", apiResourceName: "compute.v1.ForwardingRule (globalForwardingRules)", nameAuthority: "providerResource"},
    "gcp.httpProxy": {serviceName: "Cloud Load Balancing", apiResourceName: "compute.v1.TargetHttpProxy", nameAuthority: "providerResource"},
    "gcp.urlMap": {serviceName: "Cloud Load Balancing", apiResourceName: "compute.v1.UrlMap", nameAuthority: "providerResource"},
    "gcp.backendService": {serviceName: "Cloud Load Balancing", apiResourceName: "compute.v1.BackendService", nameAuthority: "providerResource"},
    "gcp.healthCheck": {serviceName: "Cloud Load Balancing", apiResourceName: "compute.v1.HealthCheck", nameAuthority: "providerResource"},
    "gcp.instanceTemplate": {serviceName: "Compute Engine", apiResourceName: "compute.v1.InstanceTemplate", nameAuthority: "providerResource"},
    "gcp.mig": {serviceName: "Compute Engine", apiResourceName: "compute.v1.InstanceGroupManager (regionInstanceGroupManagers)", nameAuthority: "providerResource"},
    "gcp.migInstanceGroup": {serviceName: "Compute Engine", apiResourceName: "compute.v1.InstanceGroup (regionInstanceGroups)", nameAuthority: "providerResource"},
    "gcp.autoHealingPolicy": {serviceName: "Compute Engine", apiResourceName: "compute.v1.InstanceGroupManager.autoHealingPolicies", nameAuthority: "providerComponent"},
    "gcp.defaultRoute": {serviceName: "Virtual Private Cloud (VPC)", apiResourceName: "compute.v1.Route", nameAuthority: "providerResource"},
    "gcp.subnetRoute": {serviceName: "Virtual Private Cloud (VPC)", apiResourceName: "compute.v1.Route", nameAuthority: "providerResource"},
    "gcp.router": {serviceName: "Cloud Router", apiResourceName: "compute.v1.Router", nameAuthority: "providerResource"},
    "gcp.nat": {serviceName: "Cloud NAT", apiResourceName: "compute.v1.RouterNat", nameAuthority: "providerComponent"},
    "gcp.artifactRegistryRepository": {serviceName: "Artifact Registry", apiResourceName: "artifactregistry.v1.Repository", nameAuthority: "providerResource"},
    "gcp.registryPullServiceAccount": {serviceName: "Identity and Access Management", apiResourceName: "iam.v1.ServiceAccount", nameAuthority: "providerResource"},
    "gcp.registryPullIamMember": {serviceName: "Artifact Registry", apiResourceName: "Repository IAM Policy Binding", nameAuthority: "terraformAssociation"}
  };

  function node(id, displayName, implementationName, kind, entityClass, handling, scopes,
    easyExplanation, officialDocs, necessity = "selectedPathRequired") {
    const naming = namingMetadata[id];
    return {
      id, displayName, implementationName, kind, entityClass, handling,
      serviceName: naming.serviceName,
      apiResourceName: naming.apiResourceName,
      terraformTypes: [implementationName],
      nameAuthority: naming.nameAuthority,
      nameAuthorityReason: nameAuthorityReasons[naming.nameAuthority],
      scopes, easyExplanation, officialDocs, necessity,
      necessityReason: necessityReasons[necessity],
      entityClassReason: entityClassReasons[entityClass],
      handlingReason: handlingReasons[handling],
      subtitle: entityClass === "association" ? "Terraform 연결 객체" : ""
    };
  }

  function edge(id, source, target, relationType, label, scopes, easyExplanation,
    officialDocs, necessity = "selectedPathRequired", options = {}) {
    const defaultPhases = relationType === "traffic" ? ["runtime"]
      : ["policy", "health"].includes(relationType) ? ["provisioning", "runtime"]
        : ["provisioning"];
    const defaultConcerns = relationType === "traffic" ? ["networkIngress"]
      : relationType === "policy" ? ["security"]
        : relationType === "health" ? ["healthRecovery"] : [];
    return {
      id, source, target, relationType, label, scopes,
      scope: scopes.join(","), easyExplanation, officialDocs, necessity,
      necessityReason: necessityReasons[necessity],
      dependencyClass: options.dependencyClass || `${relationType}Dependency`,
      condition: options.condition || "선택한 배포 경로",
      constraints: options.constraints || [],
      referenceValues: options.referenceValues || [],
      phases: options.phases || defaultPhases,
      concerns: options.concerns || defaultConcerns,
      validationGate: options.validationGate || "Terraform plan의 참조와 실제 기능 경로 확인",
      visualPriority: options.visualPriority || "primary",
      appComputeMode: options.appComputeMode || "",
      evidenceRefs: options.evidenceRefs || officialDocs.map((item) => `official:${item.url}`),
      evidenceAssessment: options.evidenceAssessment || {
        level: "officialOnly",
        status: "documented"
      }
    };
  }

  const awsDocs = {
    vpc: doc("Amazon VPC", "https://docs.aws.amazon.com/vpc/latest/userguide/what-is-amazon-vpc.html"),
    subnet: doc("VPC Subnets", "https://docs.aws.amazon.com/vpc/latest/userguide/configure-subnets.html"),
    sg: doc("Security groups", "https://docs.aws.amazon.com/vpc/latest/userguide/vpc-security-groups.html"),
    ami: doc("Amazon Machine Images", "https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/AMIs.html"),
    ec2: doc("EC2 instances", "https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/Instances.html"),
    eni: doc("Elastic network interfaces", "https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-eni.html"),
    requesterEni: doc("Requester-managed network interfaces", "https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/requester-managed-eni.html"),
    rootVolume: doc("AMI root device storage", "https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ComponentsAMIs.html"),
    igw: doc("Internet gateways", "https://docs.aws.amazon.com/vpc/latest/userguide/VPC_Internet_Gateway.html"),
    route: doc("Route tables", "https://docs.aws.amazon.com/vpc/latest/userguide/VPC_Route_Tables.html"),
    nacl: doc("Network ACLs", "https://docs.aws.amazon.com/vpc/latest/userguide/vpc-network-acls.html"),
    eip: doc("Elastic IP addresses", "https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/elastic-ip-addresses-eip.html"),
    ebs: doc("Amazon EBS volumes", "https://docs.aws.amazon.com/ebs/latest/userguide/ebs-volumes.html"),
    attach: doc("Attach an EBS volume", "https://docs.aws.amazon.com/ebs/latest/userguide/ebs-attaching-volume.html"),
    alb: doc("Application Load Balancers", "https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html"),
    listener: doc("ALB listeners", "https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-listeners.html"),
    target: doc("ALB target groups", "https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-target-groups.html"),
    template: doc("EC2 launch templates", "https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-launch-templates.html"),
    asg: doc("EC2 Auto Scaling groups", "https://docs.aws.amazon.com/autoscaling/ec2/userguide/auto-scaling-groups.html"),
    nat: doc("NAT gateways", "https://docs.aws.amazon.com/vpc/latest/userguide/nat-gateway-basics.html"),
    ecr: doc("Amazon ECR private repositories", "https://docs.aws.amazon.com/AmazonECR/latest/userguide/Repositories.html"),
    ecrPush: doc("Push an image to Amazon ECR", "https://docs.aws.amazon.com/AmazonECR/latest/userguide/docker-push-ecr-image.html"),
    ec2Role: doc("IAM roles for Amazon EC2", "https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/iam-roles-for-amazon-ec2.html")
  };

  const awsNodes = [
    node("aws.vpc", "VPC", "aws_vpc", "network", "providerResource", "create", ["basic", "direct", "persistence", "managed"], "AWS 리소스를 배치하는 사설 주소·네트워크 경계다.", [awsDocs.vpc]),
    node("aws.subnet", "Subnet", "aws_subnet", "network", "providerResource", "create", ["basic", "direct", "persistence", "managed"], "VPC 안에서 EC2와 ALB를 특정 가용 영역의 주소 구역에 배치한다.", [awsDocs.subnet]),
    node("aws.securityGroup", "Security Group", "aws_security_group", "security", "providerResource", "create", ["basic", "direct", "persistence", "managed"], "EC2 또는 ALB에 적용되어 필요한 protocol·port만 허용하는 상태 저장형 방화벽이다.", [awsDocs.sg]),
    node("aws.defaultSecurityGroup", "Default Security Group", "aws_vpc.default_security_group_id", "security", "providerResource", "providerCreated", ["basic", "direct", "persistence", "managed"], "VPC와 함께 자동 생성된다. EC2 생성 요청에 전용 Security Group을 주지 않으면 Primary ENI에 자동 적용되며, 기본 inbound는 같은 Default Security Group 구성원에서 오는 트래픽만 허용한다.", [awsDocs.sg], "alternative"),
    node("aws.ami", "Amazon Machine Image (AMI)", "data.aws_ami", "compute", "providerResource", "referenceExisting", ["basic", "direct", "persistence", "managed"], "EC2를 부팅할 운영체제와 루트 디스크 구성을 제공하는 기존 이미지다.", [awsDocs.ami], "conditional"),
    node("aws.ec2", "EC2 Instance", "aws_instance", "compute", "providerResource", "create", ["basic", "direct", "persistence"], "Docker 애플리케이션을 실행하는 Linux 가상 서버다.", [awsDocs.ec2]),
    node("aws.asgInstance", "EC2 Instance (ASG member)", "DescribeAutoScalingGroups.Instances", "compute", "providerResource", "providerCreated", ["managed"], "Auto Scaling Group이 Launch Template에서 자동 생성하고 교체하는 실제 EC2 Instance다.", [awsDocs.asg, awsDocs.ec2]),
    node("aws.primaryEni", "Primary Network Interface", "aws_instance.primary_network_interface_id", "network", "providerResource", "providerCreated", ["basic", "direct", "persistence", "managed"], "EC2 생성 요청에 별도 ENI를 주지 않으면 CSP가 자동 생성해 device index 0에 붙이는 실제 ENI다. 필요하면 aws_network_interface로 명시 생성하는 방식으로 바꿀 수 있다.", [awsDocs.eni]),
    node("aws.rootVolume", "Root EBS Volume", "aws_instance.root_block_device", "state", "providerResource", "providerCreated", ["basic", "direct", "persistence", "managed"], "EBS-backed AMI에서 EC2를 만들 때 AMI block device mapping에 따라 함께 생성되는 부팅 Volume이다. 별도 데이터 EBS와 수명 목적이 다르다.", [awsDocs.rootVolume, awsDocs.ebs]),
    node("aws.internetGateway", "Internet Gateway", "aws_internet_gateway", "network", "providerResource", "create", ["direct", "persistence", "managed"], "VPC와 인터넷 사이의 통신 관문이다.", [awsDocs.igw], "conditional"),
    node("aws.routeTable", "Route Table", "aws_route_table", "network", "providerResource", "create", ["direct", "persistence", "managed"], "Subnet 트래픽을 어느 대상에 보낼지 정하는 경로표다.", [awsDocs.route], "conditional"),
    node("aws.mainRouteTable", "Main Route Table", "aws_vpc.main_route_table_id", "network", "providerResource", "providerCreated", ["basic"], "VPC와 함께 자동 생성되며 명시적 Route Table Association이 없는 Subnet이 사용하는 실제 main Route Table이다.", [awsDocs.route]),
    node("aws.route", "Route", "aws_route", "network", "providerComponent", "configureInsideOwner", ["direct", "persistence", "managed"], "Route Table 안에서 인터넷 또는 NAT Gateway 방향을 지정하는 경로 항목이다.", [awsDocs.route], "conditional"),
    node("aws.localRoute", "Local Route", "route_table.routes[origin=CreateRouteTable]", "network", "providerComponent", "providerCreated", ["basic", "direct", "persistence", "managed"], "각 Route Table에 자동 생성되어 VPC CIDR 내부 통신을 local 대상으로 전달하는 Route 항목이다. 삭제할 수 없다.", [awsDocs.route]),
    node("aws.defaultNetworkAcl", "Default Network ACL", "aws_vpc.default_network_acl_id", "security", "providerResource", "providerCreated", ["basic", "direct", "persistence", "managed"], "VPC와 함께 자동 생성되고 별도 Network ACL Association이 없는 Subnet에 적용되는 실제 Network ACL이다. 기본 규칙은 inbound와 outbound를 허용한다.", [awsDocs.nacl]),
    node("aws.routeTableAssociation", "Subnet Route Table Association", "aws_route_table_association", "network", "association", "create", ["direct", "persistence", "managed"], "Subnet과 Route Table을 연결하는 독립 Terraform 연결 객체다.", [awsDocs.route], "conditional"),
    node("aws.eip", "Elastic IP Address", "aws_eip", "network", "providerResource", "create", ["direct", "persistence", "managed"], "EC2 또는 NAT Gateway에 사용할 고정 공인 IPv4 주소다.", [awsDocs.eip], "alternative"),
    node("aws.eipAssociation", "Elastic IP Association", "aws_eip_association", "network", "association", "create", ["direct", "persistence"], "Elastic IP와 EC2를 연결하는 독립 Terraform 연결 객체다.", [awsDocs.eip], "alternative"),
    node("aws.ebs", "EBS Volume", "aws_ebs_volume", "state", "providerResource", "create", ["persistence"], "EC2 수명과 분리해 보관하는 블록 데이터 디스크다.", [awsDocs.ebs]),
    node("aws.volumeAttachment", "EBS Volume Attachment", "aws_volume_attachment", "state", "association", "create", ["persistence"], "EC2와 EBS Volume을 연결하는 독립 Terraform 연결 객체다.", [awsDocs.attach]),
    node("aws.alb", "Application Load Balancer", "aws_lb", "ingress", "providerResource", "create", ["managed"], "여러 가용 영역에서 HTTP 요청을 받아 정상 EC2 대상으로 분배한다.", [awsDocs.alb]),
    node("aws.albEni", "ALB Service-managed Network Interface", "EC2 DescribeNetworkInterfaces (requester-managed)", "network", "providerResource", "providerCreated", ["managed"], "Elastic Load Balancing이 선택된 각 Subnet에서 생성하고 관리하는 실제 Network Interface다.", [awsDocs.alb, awsDocs.requesterEni]),
    node("aws.albPublicAddress", "ALB Service-managed Public IPv4 Address", "EC2 DescribeAddresses (service_managed=ALB)", "network", "providerResource", "providerCreated", ["managed"], "internet-facing ALB의 DNS가 가리키며 ALB 서비스가 할당·회수하는 공인 IPv4 주소다. 사용자가 만든 Elastic IP가 아니다.", [awsDocs.alb]),
    node("aws.listener", "Listener", "aws_lb_listener", "ingress", "providerResource", "create", ["managed"], "ALB에서 HTTP port 80을 열고 기본 전달 대상을 연결한다.", [awsDocs.listener]),
    node("aws.targetGroup", "Target Group", "aws_lb_target_group", "ingress", "providerResource", "create", ["managed"], "요청을 받을 EC2 대상과 port·health check 규칙을 관리한다.", [awsDocs.target]),
    node("aws.launchTemplate", "Launch Template", "aws_launch_template", "compute", "providerResource", "create", ["managed"], "반복 생성할 EC2의 AMI·사양·Security Group·startup 설정을 보관한다.", [awsDocs.template]),
    node("aws.autoScalingGroup", "Auto Scaling Group", "aws_autoscaling_group", "compute", "providerResource", "create", ["managed"], "Launch Template을 사용해 원하는 EC2 개수를 유지하고 비정상 인스턴스를 교체한다.", [awsDocs.asg]),
    node("aws.natGateway", "NAT Gateway", "aws_nat_gateway", "network", "providerResource", "create", ["direct", "persistence", "managed"], "공인 IP가 없는 EC2가 외부 package·container image 저장소에 접속하도록 한다.", [awsDocs.nat], "alternative"),
    node("aws.natEni", "NAT Gateway Requester-managed Network Interface", "aws_nat_gateway.network_interface_id", "network", "providerResource", "providerCreated", ["direct", "persistence", "managed"], "NAT Gateway를 만들 때 AWS가 Subnet에 자동 생성하고 NAT Gateway 수명과 함께 관리하는 실제 ENI다.", [awsDocs.nat, awsDocs.requesterEni], "alternative"),
    node("aws.ecrRepository", "ECR Repository", "aws_ecr_repository", "config", "providerResource", "create", ["basic", "direct", "persistence", "managed"], "EasyDep가 한 번 build한 앱 image를 push하고 VM이 digest로 pull하는 private repository다.", [awsDocs.ecr, awsDocs.ecrPush]),
    node("aws.registryPullRole", "IAM Role", "aws_iam_role", "security", "providerResource", "create", ["basic", "direct", "persistence", "managed"], "App EC2가 secret 없이 Amazon ECR 인증 token과 image layer를 읽도록 신뢰 정책을 제공한다.", [awsDocs.ec2Role]),
    node("aws.registryPullPolicy", "AmazonEC2ContainerRegistryReadOnly Policy", "data.aws_iam_policy", "security", "providerResource", "referenceExisting", ["basic", "direct", "persistence", "managed"], "AWS가 관리하는 ECR read-only 권한 정책을 기존 IAM Policy로 참조한다.", [awsDocs.ecr], "conditional"),
    node("aws.registryPullPolicyAttachment", "IAM Role Policy Attachment", "aws_iam_role_policy_attachment", "security", "association", "create", ["basic", "direct", "persistence", "managed"], "ECR read-only Policy와 App EC2용 IAM Role을 연결하는 Terraform 객체다.", [awsDocs.ec2Role]),
    node("aws.registryInstanceProfile", "IAM Instance Profile", "aws_iam_instance_profile", "security", "providerResource", "create", ["basic", "direct", "persistence", "managed"], "IAM Role을 EC2 또는 Launch Template에 전달하는 Instance Profile이다.", [awsDocs.ec2Role])
  ];

  const awsEdges = [
    edge("aws.vpc-subnet", "aws.vpc", "aws.subnet", "provision", "VPC 배치", ["basic", "direct", "persistence", "managed"], "Subnet은 어느 VPC에 속하는지 VPC ID를 참조한다.", [awsDocs.subnet], "selectedPathRequired", {referenceValues: ["vpc_id"]}),
    edge("aws.vpc-security-group", "aws.vpc", "aws.securityGroup", "provision", "VPC에 생성", ["basic", "direct", "persistence", "managed"], "Security Group은 반드시 하나의 VPC에 속하며, 이번 배포에서는 생성한 VPC ID를 명시적으로 참조한다.", [awsDocs.vpc, awsDocs.sg], "selectedPathRequired", {referenceValues: ["vpc_id"], validationGate: "Terraform plan에서 aws_security_group.vpc_id가 이번 배포의 aws_vpc.id를 참조하는지 확인"}),
    edge("aws.vpc-default-security-group", "aws.vpc", "aws.defaultSecurityGroup", "materialize", "Default Security Group 자동 생성", ["basic", "direct", "persistence", "managed"], "VPC 생성 시 AWS가 Default Security Group을 자동 생성한다.", [awsDocs.vpc, awsDocs.sg], "alternative", {referenceValues: ["default_security_group_id", "group_name=default"], validationGate: "VPC defaultSecurityGroupId와 Security Groups API 결과를 대조"}),
    edge("aws.default-sg-primary-eni", "aws.defaultSecurityGroup", "aws.primaryEni", "policy", "기본 방화벽 자동 적용", ["basic", "direct", "persistence", "managed"], "EC2 또는 Launch Template에 전용 Security Group을 지정하지 않으면 Default Security Group이 Primary ENI에 자동 적용된다.", [awsDocs.sg, awsDocs.eni], "alternative", {referenceValues: ["default_security_group_id", "primary_network_interface_id"], condition: "전용 Security Group을 선택하지 않았을 때", validationGate: "Primary ENI의 groups 목록이 VPC Default Security Group ID를 포함하는지 확인"}),
    edge("aws.vpc-main-route-table", "aws.vpc", "aws.mainRouteTable", "materialize", "Main Route Table 자동 생성", ["basic"], "VPC 생성 시 AWS가 main Route Table을 자동 생성한다.", [awsDocs.vpc, awsDocs.route], "selectedPathRequired", {referenceValues: ["main_route_table_id"], validationGate: "VPC mainRouteTableId와 Route Tables API 결과를 대조"}),
    edge("aws.vpc-default-network-acl", "aws.vpc", "aws.defaultNetworkAcl", "materialize", "Default Network ACL 자동 생성", ["basic", "direct", "persistence", "managed"], "VPC 생성 시 AWS가 default Network ACL을 자동 생성한다.", [awsDocs.vpc, awsDocs.nacl], "selectedPathRequired", {referenceValues: ["default_network_acl_id", "isDefault=true"], validationGate: "VPC defaultNetworkAclId와 Network ACL API 결과를 대조"}),
    edge("aws.default-network-acl-subnet", "aws.defaultNetworkAcl", "aws.subnet", "policy", "기본 Network ACL 적용", ["basic", "direct", "persistence", "managed"], "별도 Network ACL association을 만들지 않은 Subnet은 VPC의 default Network ACL에 자동 연결된다.", [awsDocs.nacl, awsDocs.subnet], "selectedPathRequired", {referenceValues: ["networkAclAssociation", "subnet_id", "default allow inbound/outbound entries"], validationGate: "각 Subnet association의 Network ACL ID와 실제 inbound/outbound 규칙을 확인"}),
    edge("aws.ami-ec2", "aws.ami", "aws.ec2", "reference", "부팅 이미지 참조", ["basic", "direct", "persistence"], "EC2는 기존 AMI ID를 참조해 운영체제와 루트 디스크를 만든다.", [awsDocs.ami, awsDocs.ec2], "conditional", {referenceValues: ["aws_instance.ami"], condition: "EC2를 직접 생성하고 Launch Template이 이미지를 제공하지 않을 때"}),
    edge("aws.subnet-ec2", "aws.subnet", "aws.ec2", "reference", "Subnet 참조", ["basic", "direct", "persistence"], "EC2는 배치될 Subnet ID를 참조한다.", [awsDocs.subnet, awsDocs.ec2], "selectedPathRequired", {referenceValues: ["subnet_id"]}),
    edge("aws.sg-ec2", "aws.securityGroup", "aws.ec2", "policy", "방화벽 적용", ["basic", "direct", "persistence"], "EC2가 사용할 Security Group ID를 참조하고 필요한 host port만 허용한다.", [awsDocs.sg, awsDocs.ec2], "selectedPathRequired", {referenceValues: ["vpc_security_group_ids", "application host port"], validationGate: "Terraform plan 참조와 실제 허용 port 연결 시험"}),
    edge("aws.ec2-primary-eni", "aws.ec2", "aws.primaryEni", "materialize", "기본 ENI 자동 생성", ["basic", "direct", "persistence"], "별도 network_interface를 제공하지 않은 EC2 생성이 Primary Network Interface를 자동으로 만들고 device index 0에 붙인다.", [awsDocs.eni, awsDocs.ec2], "selectedPathRequired", {referenceValues: ["primary_network_interface_id", "device_index=0", "subnet_id", "vpc_security_group_ids"], validationGate: "apply 뒤 EC2 primary_network_interface_id와 EC2 콘솔의 ENI ID가 일치하는지 확인"}),
    edge("aws.ec2-root-volume", "aws.ec2", "aws.rootVolume", "materialize", "Root Volume 자동 생성", ["basic", "direct", "persistence"], "EBS-backed AMI의 block device mapping에 따라 EC2와 함께 부팅 EBS Volume이 생성된다.", [awsDocs.rootVolume, awsDocs.ebs], "selectedPathRequired", {referenceValues: ["root_block_device", "block_device_mappings", "delete_on_termination"], concerns: ["persistence"], validationGate: "apply 뒤 EC2 root device와 생성된 EBS Volume ID·deleteOnTermination 값을 확인"}),
    edge("aws.vpc-igw", "aws.vpc", "aws.internetGateway", "reference", "VPC 연결", ["direct", "persistence", "managed"], "Internet Gateway는 연결할 VPC를 참조한다.", [awsDocs.igw], "conditional", {referenceValues: ["vpc_id"]}),
    edge("aws.vpc-route-table", "aws.vpc", "aws.routeTable", "provision", "VPC 경로표", ["direct", "persistence", "managed"], "Route Table은 어느 VPC의 경로표인지 VPC ID를 참조한다.", [awsDocs.route], "conditional", {referenceValues: ["vpc_id"]}),
    edge("aws.main-route-table-local-route", "aws.mainRouteTable", "aws.localRoute", "materialize", "Main Table Local Route 자동 생성", ["basic"], "Main Route Table에 VPC CIDR을 local로 보내는 Route가 자동 생성된다.", [awsDocs.route], "selectedPathRequired", {referenceValues: ["destination=VPC CIDR", "gatewayId=local", "origin=CreateRouteTable"], validationGate: "Main Route Table에서 local Route를 확인"}),
    edge("aws.main-route-table-subnet", "aws.mainRouteTable", "aws.subnet", "reference", "암묵적 Main Route Table 연결", ["basic"], "명시적 Route Table Association이 없는 Subnet은 VPC의 Main Route Table에 자동 연결된다.", [awsDocs.route, awsDocs.subnet], "selectedPathRequired", {referenceValues: ["main=true", "implicit subnet association"], validationGate: "Subnet의 effective Route Table이 VPC Main Route Table인지 확인"}),
    edge("aws.route-table-local-route", "aws.routeTable", "aws.localRoute", "materialize", "Local Route 자동 생성", ["direct", "persistence", "managed"], "명시적으로 만든 Route Table에도 VPC CIDR의 local Route가 자동 생성된다.", [awsDocs.route], "selectedPathRequired", {referenceValues: ["destination=VPC CIDR", "gatewayId=local", "origin=CreateRouteTable"], validationGate: "각 custom Route Table에서 local Route를 확인"}),
    edge("aws.route-table-route", "aws.routeTable", "aws.route", "contains", "경로 항목 소유", ["direct", "persistence", "managed"], "Route는 Route Table 안에 목적지와 다음 관문을 구성한다.", [awsDocs.route], "conditional", {referenceValues: ["route_table_id", "destination_cidr_block"]}),
    edge("aws.igw-route", "aws.internetGateway", "aws.route", "reference", "인터넷 다음 관문", ["direct", "persistence", "managed"], "공개 인터넷 경로는 Internet Gateway ID를 다음 관문으로 참조한다.", [awsDocs.igw, awsDocs.route], "conditional", {referenceValues: ["gateway_id"]}),
    edge("aws.route-assoc-table", "aws.routeTable", "aws.routeTableAssociation", "association", "Route Table 선행", ["direct", "persistence", "managed"], "연결 객체가 Route Table ID를 참조하므로 Route Table이 먼저 필요하다.", [awsDocs.route], "conditional", {referenceValues: ["route_table_id"]}),
    edge("aws.route-assoc-subnet", "aws.subnet", "aws.routeTableAssociation", "association", "Subnet 선행", ["direct", "persistence", "managed"], "연결 객체가 Subnet ID를 참조하므로 Subnet이 먼저 필요하다.", [awsDocs.route], "conditional", {referenceValues: ["subnet_id"]}),
    edge("aws.eip-assoc-eip", "aws.eip", "aws.eipAssociation", "association", "Elastic IP 선행", ["direct", "persistence"], "연결 객체가 Elastic IP allocation ID를 참조하므로 Elastic IP가 먼저 필요하다.", [awsDocs.eip], "alternative", {referenceValues: ["allocation_id"]}),
    edge("aws.eip-assoc-ec2", "aws.ec2", "aws.eipAssociation", "association", "EC2 선행", ["direct", "persistence"], "연결 객체가 공인 IP를 받을 EC2 instance ID를 참조하므로 EC2가 먼저 필요하다.", [awsDocs.eip, awsDocs.ec2], "alternative", {referenceValues: ["instance_id"]}),
    edge("aws.attach-ebs", "aws.ebs", "aws.volumeAttachment", "association", "EBS 선행", ["persistence"], "Volume Attachment가 EBS Volume ID를 참조하므로 EBS Volume이 먼저 필요하다.", [awsDocs.attach], "selectedPathRequired", {referenceValues: ["volume_id"], phases: ["provisioning", "runtime"], concerns: ["persistence"]}),
    edge("aws.attach-ec2", "aws.ec2", "aws.volumeAttachment", "association", "EC2 선행", ["persistence"], "Volume Attachment가 EBS를 부착할 EC2 instance ID를 참조하므로 EC2가 먼저 필요하다.", [awsDocs.attach], "selectedPathRequired", {referenceValues: ["instance_id", "device_name"], phases: ["provisioning", "runtime"], concerns: ["persistence"], constraints: [{kind: "samePlacementDimension", dimension: "availabilityZone", participants: ["aws.ec2", "aws.ebs"], validationGate: "Terraform plan에서 EC2와 EBS Availability Zone 일치 확인"}], validationGate: "attach 성공, guest block device 확인, filesystem·mount·application data path와 VM 재생성 후 데이터 보존 시험"}),
    edge("aws.subnet-alb", "aws.subnet", "aws.alb", "reference", "ALB Subnet 참조", ["managed"], "ALB는 서로 다른 가용 영역의 Subnet들을 참조한다.", [awsDocs.alb], "selectedPathRequired", {referenceValues: ["subnets"], constraints: [{kind: "distinctPlacementMinimum", dimension: "availabilityZone", minimum: 2, validationGate: "Terraform plan Subnet AZ 개수"}]}),
    edge("aws.sg-alb", "aws.securityGroup", "aws.alb", "policy", "ALB 방화벽", ["managed"], "ALB가 HTTP를 받을 Security Group을 참조한다.", [awsDocs.sg, awsDocs.alb], "selectedPathRequired", {referenceValues: ["security_groups", "80"]}),
    edge("aws.alb-listener", "aws.alb", "aws.listener", "reference", "Listener 소유 ALB", ["managed"], "Listener는 요청을 받을 ALB ARN을 참조한다.", [awsDocs.listener], "selectedPathRequired", {referenceValues: ["load_balancer_arn"]}),
    edge("aws.target-listener", "aws.targetGroup", "aws.listener", "reference", "기본 전달 대상", ["managed"], "Listener 기본 action이 Target Group ARN을 참조한다.", [awsDocs.listener, awsDocs.target], "selectedPathRequired", {referenceValues: ["default_action.target_group_arn"]}),
    edge("aws.vpc-target", "aws.vpc", "aws.targetGroup", "reference", "Target Group VPC", ["managed"], "Target Group은 backend EC2가 속한 VPC를 참조한다.", [awsDocs.target], "selectedPathRequired", {referenceValues: ["vpc_id", "application port", "readiness path"]}),
    edge("aws.ami-template", "aws.ami", "aws.launchTemplate", "reference", "Template 부팅 이미지", ["managed"], "Launch Template이 기존 AMI ID를 참조한다.", [awsDocs.ami, awsDocs.template], "conditional", {referenceValues: ["image_id"]}),
    edge("aws.sg-template", "aws.securityGroup", "aws.launchTemplate", "policy", "Template 방화벽", ["managed"], "Launch Template이 backend EC2용 Security Group을 참조한다.", [awsDocs.sg, awsDocs.template], "selectedPathRequired", {referenceValues: ["vpc_security_group_ids"]}),
    edge("aws.template-asg", "aws.launchTemplate", "aws.autoScalingGroup", "reference", "VM 설정 청사진", ["managed"], "Auto Scaling Group이 EC2 생성에 사용할 Launch Template을 참조한다.", [awsDocs.template, awsDocs.asg], "selectedPathRequired", {referenceValues: ["launch_template.id", "launch_template.version"]}),
    edge("aws.subnet-asg", "aws.subnet", "aws.autoScalingGroup", "reference", "VM 배치 Subnet", ["managed"], "Auto Scaling Group이 EC2를 배치할 Subnet ID 목록을 참조한다.", [awsDocs.subnet, awsDocs.asg], "selectedPathRequired", {referenceValues: ["vpc_zone_identifier"], constraints: [{kind: "distinctPlacementMinimum", dimension: "availabilityZone", minimum: 2, condition: "App 장애 대응을 선택했을 때", validationGate: "Terraform plan에서 ASG Subnet의 서로 다른 Availability Zone 수 확인"}]}),
    edge("aws.target-asg", "aws.targetGroup", "aws.autoScalingGroup", "health", "Target 등록·Health", ["managed"], "Auto Scaling Group이 Target Group에 EC2를 등록하고 health 결과로 비정상 backend를 교체한다.", [awsDocs.target, awsDocs.asg], "selectedPathRequired", {referenceValues: ["target_group_arns", "health_check_type", "application port", "readiness path"], constraints: [{kind: "minimumActiveInstances", target: "aws.autoScalingGroup", minimum: 2, condition: "App 장애 대응을 선택했을 때", validationGate: "ResourcePlan minimumInstances와 Terraform ASG min_size·desired_capacity 대조"}], validationGate: "ready backend만 요청을 받고 App VM 중지 후 허용 복구시간 안에 기능이 유지되는지 시험"}),
    edge("aws.asg-instance", "aws.autoScalingGroup", "aws.asgInstance", "materialize", "EC2 Instance 자동 생성", ["managed"], "Auto Scaling Group이 desired capacity와 Launch Template에 따라 실제 EC2 Instance를 생성·교체한다.", [awsDocs.asg, awsDocs.ec2], "selectedPathRequired", {referenceValues: ["desired_capacity", "min_size", "launch_template"], validationGate: "ASG instance 목록과 실제 EC2 instance ID·가용 영역을 대조"}),
    edge("aws.asg-instance-primary-eni", "aws.asgInstance", "aws.primaryEni", "materialize", "그룹 VM 기본 ENI 자동 생성", ["managed"], "Auto Scaling이 만든 각 EC2에도 Launch Template network interface 설정에 따라 Primary ENI가 생성된다.", [awsDocs.template, awsDocs.eni], "selectedPathRequired", {referenceValues: ["network_interfaces 또는 vpc_security_group_ids", "device_index=0"], validationGate: "각 ASG instance의 primary ENI와 Subnet·Security Group을 확인"}),
    edge("aws.asg-instance-root-volume", "aws.asgInstance", "aws.rootVolume", "materialize", "그룹 VM Root Volume 자동 생성", ["managed"], "Auto Scaling이 만든 각 EC2에 AMI·Launch Template block device mapping을 따른 Root EBS Volume이 생긴다.", [awsDocs.template, awsDocs.rootVolume], "selectedPathRequired", {referenceValues: ["block_device_mappings", "root device", "delete_on_termination"], concerns: ["persistence"], validationGate: "각 ASG instance의 root EBS Volume과 삭제 정책을 확인"}),
    edge("aws.alb-managed-eni", "aws.alb", "aws.albEni", "materialize", "ALB ENI 자동 생성", ["managed"], "Elastic Load Balancing이 활성화한 각 가용 영역 Subnet에 서비스 관리 Network Interface를 만든다.", [awsDocs.alb, awsDocs.requesterEni], "selectedPathRequired", {referenceValues: ["subnets", "ENI reserved by ELB for subnet"], validationGate: "선택한 각 ALB Subnet에서 ELB 설명의 requester-managed ENI를 확인"}),
    edge("aws.alb-managed-address", "aws.alb", "aws.albPublicAddress", "materialize", "ALB 공인 IPv4 자동 할당", ["managed"], "internet-facing ALB가 각 활성 가용 영역 노드에 서비스 관리 공인 IPv4를 할당하고 DNS 응답에 반영한다.", [awsDocs.alb], "selectedPathRequired", {referenceValues: ["scheme=internet-facing", "service_managed=ALB", "ALB DNS A records"], validationGate: "ALB DNS 조회 결과와 service_managed=ALB Address를 대조"}),
    edge("aws.subnet-nat", "aws.subnet", "aws.natGateway", "reference", "NAT 배치 Subnet", ["direct", "persistence", "managed"], "NAT Gateway는 인터넷 경로가 있는 Subnet을 참조한다.", [awsDocs.nat], "alternative", {referenceValues: ["subnet_id"]}),
    edge("aws.eip-nat", "aws.eip", "aws.natGateway", "reference", "NAT 공인 IP", ["direct", "persistence", "managed"], "Public NAT Gateway는 Elastic IP allocation ID를 참조한다.", [awsDocs.eip, awsDocs.nat], "alternative", {referenceValues: ["allocation_id"]}),
    edge("aws.nat-managed-eni", "aws.natGateway", "aws.natEni", "materialize", "NAT ENI 자동 생성", ["direct", "persistence", "managed"], "NAT Gateway 생성 시 AWS가 선택한 Subnet에 requester-managed ENI를 자동 생성한다.", [awsDocs.nat, awsDocs.requesterEni], "alternative", {referenceValues: ["network_interface_id", "subnet_id"], validationGate: "NAT Gateway networkInterfaceId와 EC2 Network Interfaces 목록을 대조"}),
    edge("aws.registry-role-policy-attachment", "aws.registryPullRole", "aws.registryPullPolicyAttachment", "association", "IAM Role 선행", ["basic", "direct", "persistence", "managed"], "ECR pull Policy를 연결할 IAM Role이 먼저 필요하다.", [awsDocs.ec2Role], "selectedPathRequired", {referenceValues: ["role"]}),
    edge("aws.registry-policy-policy-attachment", "aws.registryPullPolicy", "aws.registryPullPolicyAttachment", "association", "ECR read-only Policy 선행", ["basic", "direct", "persistence", "managed"], "Role Policy Attachment가 AWS 관리 ECR read-only Policy ARN을 참조한다.", [awsDocs.ecr, awsDocs.ec2Role], "selectedPathRequired", {referenceValues: ["policy_arn"]}),
    edge("aws.registry-role-instance-profile", "aws.registryPullRole", "aws.registryInstanceProfile", "reference", "Instance Profile Role", ["basic", "direct", "persistence", "managed"], "IAM Instance Profile이 App EC2용 IAM Role 이름을 참조한다.", [awsDocs.ec2Role], "selectedPathRequired", {referenceValues: ["role"]}),
    edge("aws.registry-profile-ec2", "aws.registryInstanceProfile", "aws.ec2", "reference", "App EC2 Registry identity", ["basic", "direct", "persistence"], "단일 App EC2가 ECR pull 권한을 가진 Instance Profile을 참조한다.", [awsDocs.ec2Role, awsDocs.ecr], "selectedPathRequired", {referenceValues: ["iam_instance_profile"], appComputeMode: "single"}),
    edge("aws.registry-policy-ec2", "aws.registryPullPolicyAttachment", "aws.ec2", "provision", "ECR pull 권한 선행", ["basic", "direct", "persistence"], "App EC2 bootstrap 전에 ECR read-only Role Policy Attachment가 완료되어야 한다.", [awsDocs.ecr, awsDocs.ec2Role], "selectedPathRequired", {referenceValues: ["depends_on"], appComputeMode: "single"}),
    edge("aws.registry-repository-ec2", "aws.ecrRepository", "aws.ec2", "reference", "앱 image digest", ["basic", "direct", "persistence"], "App EC2 startup 설정이 ECR repository URL과 EasyDep가 확정한 image digest를 참조한다.", [awsDocs.ecrPush, awsDocs.ec2], "selectedPathRequired", {referenceValues: ["repository_url", "image@sha256", "user_data"], appComputeMode: "single"}),
    edge("aws.registry-profile-template", "aws.registryInstanceProfile", "aws.launchTemplate", "reference", "Template Registry identity", ["managed"], "Launch Template가 각 App EC2에 전달할 ECR pull Instance Profile을 참조한다.", [awsDocs.ec2Role, awsDocs.template], "selectedPathRequired", {referenceValues: ["iam_instance_profile"], appComputeMode: "group"}),
    edge("aws.registry-policy-template", "aws.registryPullPolicyAttachment", "aws.launchTemplate", "provision", "ECR pull 권한 선행", ["managed"], "관리형 App VM 생성 전에 ECR read-only Role Policy Attachment가 완료되어야 한다.", [awsDocs.ecr, awsDocs.template], "selectedPathRequired", {referenceValues: ["depends_on"], appComputeMode: "group"}),
    edge("aws.registry-repository-template", "aws.ecrRepository", "aws.launchTemplate", "reference", "Template 앱 image digest", ["managed"], "Launch Template startup 설정이 ECR repository URL과 EasyDep가 확정한 image digest를 참조한다.", [awsDocs.ecrPush, awsDocs.template], "selectedPathRequired", {referenceValues: ["repository_url", "image@sha256", "user_data"], appComputeMode: "group"}),
    edge("aws.traffic-igw-eip", "aws.internetGateway", "aws.eip", "traffic", "인터넷 요청 진입", ["direct", "persistence"], "Internet Gateway를 지난 외부 요청이 EC2에 연결된 Elastic IP 주소로 향한다.", [awsDocs.igw, awsDocs.eip], "selectedPathRequired", {referenceValues: ["destination public IPv4", "application port"], validationGate: "외부에서 Elastic IP의 application port로 요청해 응답 확인"}),
    edge("aws.traffic-eip-eni", "aws.eip", "aws.primaryEni", "traffic", "Primary ENI로 NAT 매핑", ["direct", "persistence"], "Elastic IP의 one-to-one NAT 매핑이 EC2 Primary ENI의 private IPv4로 요청을 전달한다.", [awsDocs.eip, awsDocs.eni], "selectedPathRequired", {referenceValues: ["association_id", "primary private IPv4", "application port"], validationGate: "EIP association 대상과 EC2 primary ENI/private IP를 대조"}),
    edge("aws.traffic-eni-ec2", "aws.primaryEni", "aws.ec2", "traffic", "EC2로 요청 전달", ["direct", "persistence"], "Primary ENI가 Security Group에서 허용된 요청을 EC2 guest의 application port로 전달한다.", [awsDocs.eni, awsDocs.ec2], "selectedPathRequired", {referenceValues: ["device_index=0", "application port"], validationGate: "외부 요청이 EC2 애플리케이션 응답까지 도달하는지 확인"}),
    edge("aws.traffic-igw-alb-address", "aws.internetGateway", "aws.albPublicAddress", "traffic", "ALB 공인 주소로 HTTP 진입", ["managed"], "DNS로 확인한 ALB 서비스 관리 공인 IPv4에 인터넷 HTTP 요청이 도달한다.", [awsDocs.igw, awsDocs.alb], "selectedPathRequired", {referenceValues: ["ALB DNS A record", "80"]}),
    edge("aws.traffic-alb-address-alb", "aws.albPublicAddress", "aws.alb", "traffic", "ALB node 수신", ["managed"], "ALB DNS가 반환한 서비스 관리 공인 주소로 보낸 요청을 활성 가용 영역의 Load Balancer node가 수신한다. ELB가 만든 requester-managed ENI는 표시하지만 공개 문서만으로 해당 reserved ENI를 요청 hop이라고 단정하지 않는다.", [awsDocs.alb], "selectedPathRequired", {referenceValues: ["service-managed IPv4", "load balancer node", "80"]}),
    edge("aws.traffic-alb-listener", "aws.alb", "aws.listener", "traffic", "HTTP Listener 수신", ["managed"], "Application Load Balancer가 HTTP Listener port 80으로 요청을 수신한다.", [awsDocs.alb, awsDocs.listener], "selectedPathRequired", {referenceValues: ["80"]}),
    edge("aws.traffic-listener-target", "aws.listener", "aws.targetGroup", "traffic", "Target Group 전달", ["managed"], "Listener routing action이 요청을 Target Group으로 전달한다.", [awsDocs.listener, awsDocs.target], "selectedPathRequired", {referenceValues: ["listener rule", "target application port"]}),
    edge("aws.traffic-target-eni", "aws.targetGroup", "aws.primaryEni", "traffic", "정상 EC2 Primary ENI로 전달", ["managed"], "instance target type이면 Target Group이 정상 EC2의 Primary ENI에 있는 primary private IP로 요청을 보낸다.", [awsDocs.target, awsDocs.eni], "selectedPathRequired", {referenceValues: ["target_type=instance", "primary private IP", "application port", "readiness path"]}),
    edge("aws.traffic-eni-asg-instance", "aws.primaryEni", "aws.asgInstance", "traffic", "그룹 EC2로 요청 전달", ["managed"], "Primary ENI가 허용된 backend 요청을 Auto Scaling EC2 Instance의 application port로 전달한다.", [awsDocs.target, awsDocs.ec2], "selectedPathRequired", {referenceValues: ["registered instance", "application port"], validationGate: "정상 ASG EC2만 요청을 받고 업무 API가 응답하는지 확인"})
  ];

  const azureDocs = {
    rg: doc("Azure Resource groups", "https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/manage-resource-groups-portal"),
    vnet: doc("Azure Virtual Network", "https://learn.microsoft.com/en-us/azure/virtual-network/virtual-networks-overview"),
    subnet: doc("Azure Virtual Network subnets", "https://learn.microsoft.com/en-us/azure/virtual-network/virtual-network-manage-subnet"),
    nsg: doc("Network security groups", "https://learn.microsoft.com/en-us/azure/virtual-network/network-security-groups-overview"),
    nic: doc("Azure network interfaces", "https://learn.microsoft.com/en-us/azure/virtual-network/virtual-network-network-interface"),
    image: doc("Azure Marketplace VM images", "https://learn.microsoft.com/en-us/azure/virtual-machines/linux/cli-ps-findimage"),
    imageApi: doc("Virtual Machine Images REST API", "https://learn.microsoft.com/en-us/rest/api/compute/virtual-machine-images/get"),
    galleryImage: doc("Azure Compute Gallery image versions", "https://learn.microsoft.com/en-us/azure/virtual-machines/shared-image-galleries"),
    vm: doc("Azure Linux virtual machines", "https://learn.microsoft.com/en-us/azure/virtual-machines/linux/overview"),
    pip: doc("Azure Public IP", "https://learn.microsoft.com/en-us/azure/virtual-network/ip-services/public-ip-addresses"),
    disk: doc("Azure Managed Disks", "https://learn.microsoft.com/en-us/azure/virtual-machines/managed-disks-overview"),
    attach: doc("Attach a data disk to a Linux VM", "https://learn.microsoft.com/en-us/azure/virtual-machines/linux/attach-disk-portal"),
    agw: doc("Azure Application Gateway", "https://learn.microsoft.com/en-us/azure/application-gateway/overview"),
    agwTemplate: doc("Application Gateway ARM resource structure", "https://learn.microsoft.com/en-us/azure/application-gateway/quick-create-template"),
    vmss: doc("Azure Virtual Machine Scale Sets", "https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/overview"),
    vmssNic: doc("VM Scale Set network interfaces", "https://learn.microsoft.com/en-us/rest/api/virtualnetwork/network-interfaces/get-virtual-machine-scale-set-network-interface"),
    vmssRepair: doc("VM Scale Set automatic instance repairs", "https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-automatic-instance-repairs"),
    nat: doc("Azure NAT Gateway", "https://learn.microsoft.com/en-us/azure/nat-gateway/manage-nat-gateway-v2"),
    acr: doc("Azure Container Registry", "https://learn.microsoft.com/en-us/azure/container-registry/container-registry-intro"),
    acrPush: doc("Push images to Azure Container Registry", "https://learn.microsoft.com/en-us/azure/container-registry/container-registry-get-started-azure-cli"),
    acrIdentity: doc("Authenticate to Azure Container Registry with managed identity", "https://learn.microsoft.com/en-us/azure/container-registry/container-registry-authentication-managed-identity")
  };

  const azureNodes = [
    node("azure.resourceGroup", "Resource Group", "azurerm_resource_group", "config", "providerResource", "create", ["basic", "direct", "persistence", "managed"], "Azure 리소스의 배포·권한·수명주기를 묶는 관리 경계다.", [azureDocs.rg]),
    node("azure.vnet", "Virtual Network", "azurerm_virtual_network", "network", "providerResource", "create", ["basic", "direct", "persistence", "managed"], "Azure VM과 진입 리소스를 배치하는 사설 주소·네트워크 경계다.", [azureDocs.vnet]),
    node("azure.subnet", "Subnet", "azurerm_subnet", "network", "providerResource", "create", ["basic", "direct", "persistence", "managed"], "Virtual Network 안에서 NIC·VMSS·Application Gateway를 배치하는 주소 구역이다.", [azureDocs.subnet]),
    node("azure.nsg", "Network Security Group", "azurerm_network_security_group", "security", "providerResource", "create", ["basic", "direct", "persistence", "managed"], "Subnet 또는 NIC에 적용해 필요한 protocol·port만 허용하는 방화벽이다.", [azureDocs.nsg]),
    node("azure.nic", "Network Interface", "azurerm_network_interface", "network", "providerResource", "create", ["basic", "direct", "persistence"], "Linux VM의 사설 IP와 선택적 Public IP 연결을 소유하는 네트워크 장치다.", [azureDocs.nic]),
    node("azure.nicNsgAssociation", "Network Interface–NSG Association", "azurerm_network_interface_security_group_association", "network", "association", "create", ["basic", "direct", "persistence"], "Network Interface와 Network Security Group을 연결하는 독립 Terraform 객체다.", [azureDocs.nsg, azureDocs.nic]),
    node("azure.image", "Virtual Machine Image", "data.azurerm_platform_image", "compute", "providerResource", "referenceExisting", ["basic", "direct", "persistence", "managed"], "Virtual Machine 또는 VM Scale Set의 OS Disk를 초기화하는 기존 Marketplace image다. 자체 이미지를 선택하면 같은 역할을 Azure Compute Gallery Image Version ID가 대신한다.", [azureDocs.imageApi, azureDocs.image, azureDocs.galleryImage], "conditional"),
    node("azure.vm", "Virtual Machine", "azurerm_linux_virtual_machine", "compute", "providerResource", "create", ["basic", "direct", "persistence"], "Docker 애플리케이션을 실행하는 Linux 가상 서버다.", [azureDocs.vm]),
    node("azure.osDisk", "OS Managed Disk", "azurerm_linux_virtual_machine.os_disk", "state", "providerResource", "providerCreated", ["basic", "direct", "persistence"], "image에서 Virtual Machine을 만들 때 storage profile에 따라 자동 생성되는 실제 OS Managed Disk다. 별도 data disk와 구분한다.", [azureDocs.vm, azureDocs.disk]),
    node("azure.publicIp", "Public IP Address", "azurerm_public_ip", "network", "providerResource", "create", ["direct", "persistence", "managed"], "직접 VM 또는 Application Gateway 공개 진입에 사용하는 공인 주소다.", [azureDocs.pip]),
    node("azure.disk", "Managed Disk", "azurerm_managed_disk", "state", "providerResource", "create", ["persistence"], "VM 수명과 분리해 보관하는 블록 데이터 디스크다.", [azureDocs.disk]),
    node("azure.diskAttachment", "VM–Data Disk Attachment", "azurerm_virtual_machine_data_disk_attachment", "state", "association", "create", ["persistence"], "Linux VM과 Managed Disk를 연결하는 독립 Terraform 객체다.", [azureDocs.attach]),
    node("azure.applicationGateway", "Application Gateway", "azurerm_application_gateway", "ingress", "providerResource", "create", ["managed"], "HTTP routing·backend pool·probe를 제공하는 관리형 L7 진입 리소스다.", [azureDocs.agw]),
    node("azure.agwGatewayIp", "Gateway IP Configuration", "azurerm_application_gateway.gateway_ip_configuration", "network", "providerComponent", "configureInsideOwner", ["managed"], "Application Gateway 전용 Subnet을 가리키는 내부 구성이다.", [azureDocs.agwTemplate]),
    node("azure.agwFrontendIp", "Frontend IP Configuration", "azurerm_application_gateway.frontend_ip_configuration", "ingress", "providerComponent", "configureInsideOwner", ["managed"], "Public IP를 Application Gateway frontend에 연결하는 내부 구성이다.", [azureDocs.agwTemplate]),
    node("azure.agwFrontendPort", "Frontend Port", "azurerm_application_gateway.frontend_port", "ingress", "providerComponent", "configureInsideOwner", ["managed"], "HTTP Listener가 수신할 frontend port 80을 정의하는 내부 구성이다.", [azureDocs.agwTemplate]),
    node("azure.agwListener", "HTTP Listener", "azurerm_application_gateway.http_listener", "ingress", "providerComponent", "configureInsideOwner", ["managed"], "Frontend IP와 port 80을 묶어 HTTP 요청을 받는 내부 구성이다.", [azureDocs.agwTemplate]),
    node("azure.agwBackendPool", "Backend Address Pool", "azurerm_application_gateway.backend_address_pool", "ingress", "providerComponent", "configureInsideOwner", ["managed"], "VMSS NIC IP configuration이 가입하고 요청 대상 주소 집합을 제공하는 내부 구성이다.", [azureDocs.agwTemplate]),
    node("azure.agwBackendSettings", "Backend HTTP Settings", "azurerm_application_gateway.backend_http_settings", "ingress", "providerComponent", "configureInsideOwner", ["managed"], "backend protocol·port·timeout과 Probe 연결을 정의하는 내부 구성이다.", [azureDocs.agwTemplate]),
    node("azure.agwProbe", "Health Probe", "azurerm_application_gateway.probe", "ingress", "providerComponent", "configureInsideOwner", ["managed"], "backend application readiness path를 확인하는 내부 구성이다.", [azureDocs.agw, azureDocs.agwTemplate]),
    node("azure.agwRoutingRule", "Request Routing Rule", "azurerm_application_gateway.request_routing_rule", "ingress", "providerComponent", "configureInsideOwner", ["managed"], "Listener를 Backend Address Pool과 Backend HTTP Settings에 연결하는 내부 routing 구성이다.", [azureDocs.agwTemplate]),
    node("azure.vmss", "Virtual Machine Scale Set", "azurerm_linux_virtual_machine_scale_set", "compute", "providerResource", "create", ["managed"], "같은 VM 모델의 인스턴스 수를 유지하고 비정상 VM을 교체한다.", [azureDocs.vmss]),
    node("azure.vmssInstance", "Virtual Machine Scale Set VM", "ARM virtualMachineScaleSets/virtualMachines/{instanceId}", "compute", "providerResource", "providerCreated", ["managed"], "VM Scale Set이 model과 capacity에 따라 자동 생성·교체하는 실제 child Virtual Machine이다.", [azureDocs.vmss]),
    node("azure.vmssNic", "Scale Set Network Interface", "ARM virtualMachineScaleSets/virtualMachines/{instanceId}/networkInterfaces", "network", "providerResource", "providerCreated", ["managed"], "VMSS network profile에서 각 child VM과 함께 생성되는 조회 가능한 Network Interface다.", [azureDocs.vmssNic, azureDocs.vmss]),
    node("azure.vmssOsDisk", "Scale Set OS Managed Disk", "ARM VMSS VM storageProfile.osDisk.managedDisk.id", "state", "providerResource", "providerCreated", ["managed"], "VMSS storage profile에서 각 child VM과 함께 생성되는 실제 OS Managed Disk다.", [azureDocs.disk, azureDocs.vmss]),
    node("azure.vmssHealthExtension", "Application Health Extension", "azurerm_linux_virtual_machine_scale_set.extension.ApplicationHealthLinux", "compute", "providerComponent", "configureInsideOwner", ["managed"], "각 VMSS instance 안에서 application health endpoint를 평가해 플랫폼에 상태를 보고한다.", [azureDocs.vmssRepair]),
    node("azure.vmssRepairPolicy", "Automatic Repairs Policy", "azurerm_linux_virtual_machine_scale_set.automatic_instance_repair", "compute", "providerComponent", "configureInsideOwner", ["managed"], "Application Health 결과와 grace period를 사용해 비정상 VMSS instance를 교체하는 정책이다.", [azureDocs.vmssRepair]),
    node("azure.natGateway", "NAT Gateway", "azurerm_nat_gateway", "network", "providerResource", "create", ["direct", "persistence", "managed"], "Public IP가 없는 VM이 외부 package·container image 저장소에 접속하도록 한다.", [azureDocs.nat], "alternative"),
    node("azure.subnetNatAssociation", "Subnet–NAT Gateway Association", "azurerm_subnet_nat_gateway_association", "network", "association", "create", ["direct", "persistence", "managed"], "Subnet과 NAT Gateway를 연결하는 독립 Terraform 객체다.", [azureDocs.nat], "alternative"),
    node("azure.natPublicIpAssociation", "NAT Gateway–Public IP Association", "azurerm_nat_gateway_public_ip_association", "network", "association", "create", ["direct", "persistence", "managed"], "NAT Gateway에 outbound용 Public IP를 연결하는 독립 Terraform 객체다.", [azureDocs.nat], "alternative"),
    node("azure.containerRegistry", "Container Registry", "azurerm_container_registry", "config", "providerResource", "create", ["basic", "direct", "persistence", "managed"], "EasyDep가 한 번 build한 앱 image를 push하고 VM이 digest로 pull하는 private registry다.", [azureDocs.acr, azureDocs.acrPush]),
    node("azure.registryPullIdentity", "User-assigned Managed Identity", "azurerm_user_assigned_identity", "security", "providerResource", "create", ["basic", "direct", "persistence", "managed"], "App VM 또는 VM Scale Set에 사전 연결되어 secret 없이 Azure Container Registry token을 얻는 identity다.", [azureDocs.acrIdentity]),
    node("azure.registryPullRoleAssignment", "AcrPull Role Assignment", "azurerm_role_assignment", "security", "association", "create", ["basic", "direct", "persistence", "managed"], "Azure Container Registry scope의 AcrPull 역할을 User-assigned Managed Identity에 부여하는 Terraform 객체다.", [azureDocs.acrIdentity])
  ];

  const azureEdges = [
    edge("azure.rg-vnet", "azure.resourceGroup", "azure.vnet", "provision", "Resource Group 배치", ["basic", "direct", "persistence", "managed"], "Virtual Network는 생성될 Resource Group 이름을 참조한다.", [azureDocs.rg, azureDocs.vnet], "selectedPathRequired", {referenceValues: ["resource_group_name"]}),
    edge("azure.rg-subnet", "azure.resourceGroup", "azure.subnet", "provision", "Resource Group 문맥", ["basic", "direct", "persistence", "managed"], "Subnet은 Virtual Network와 함께 Resource Group 이름을 명시한다.", [azureDocs.rg, azureDocs.subnet], "selectedPathRequired", {referenceValues: ["resource_group_name"], visualPriority: "context"}),
    edge("azure.rg-nsg", "azure.resourceGroup", "azure.nsg", "provision", "Resource Group·위치", ["basic", "direct", "persistence", "managed"], "Network Security Group은 Resource Group 이름과 location을 사용해 생성된다.", [azureDocs.rg, azureDocs.nsg], "selectedPathRequired", {referenceValues: ["resource_group_name", "location"], visualPriority: "context"}),
    edge("azure.rg-nic", "azure.resourceGroup", "azure.nic", "provision", "Resource Group·위치", ["basic", "direct", "persistence"], "Network Interface는 Resource Group 이름과 location을 사용해 생성된다.", [azureDocs.rg, azureDocs.nic], "selectedPathRequired", {referenceValues: ["resource_group_name", "location"], visualPriority: "context"}),
    edge("azure.rg-vm", "azure.resourceGroup", "azure.vm", "provision", "Resource Group·위치", ["basic", "direct", "persistence"], "Linux VM은 Resource Group 이름과 location을 사용해 생성된다.", [azureDocs.rg, azureDocs.vm], "selectedPathRequired", {referenceValues: ["resource_group_name", "location"], visualPriority: "context"}),
    edge("azure.rg-public-ip", "azure.resourceGroup", "azure.publicIp", "provision", "Resource Group·위치", ["direct", "persistence", "managed"], "Public IP는 Resource Group 이름과 location을 사용해 생성된다.", [azureDocs.rg, azureDocs.pip], "selectedPathRequired", {referenceValues: ["resource_group_name", "location"], visualPriority: "context"}),
    edge("azure.rg-disk", "azure.resourceGroup", "azure.disk", "provision", "Resource Group·위치", ["persistence"], "Managed Disk는 Resource Group 이름과 VM에 호환되는 location·zone을 사용한다.", [azureDocs.rg, azureDocs.disk], "selectedPathRequired", {referenceValues: ["resource_group_name", "location", "zone"], visualPriority: "context"}),
    edge("azure.rg-agw", "azure.resourceGroup", "azure.applicationGateway", "provision", "Resource Group·위치", ["managed"], "Application Gateway는 Resource Group 이름과 location을 사용해 생성된다.", [azureDocs.rg, azureDocs.agw], "selectedPathRequired", {referenceValues: ["resource_group_name", "location"], visualPriority: "context"}),
    edge("azure.rg-vmss", "azure.resourceGroup", "azure.vmss", "provision", "Resource Group·위치", ["managed"], "VM Scale Set은 Resource Group 이름과 location을 사용해 생성된다.", [azureDocs.rg, azureDocs.vmss], "selectedPathRequired", {referenceValues: ["resource_group_name", "location", "zones"], visualPriority: "context"}),
    edge("azure.rg-nat", "azure.resourceGroup", "azure.natGateway", "provision", "Resource Group·위치", ["direct", "persistence", "managed"], "NAT Gateway는 Resource Group 이름과 location을 사용해 생성된다.", [azureDocs.rg, azureDocs.nat], "conditional", {referenceValues: ["resource_group_name", "location"], visualPriority: "context"}),
    edge("azure.vnet-subnet", "azure.vnet", "azure.subnet", "reference", "Virtual Network 참조", ["basic", "direct", "persistence", "managed"], "Subnet은 어느 Virtual Network에 속하는지 참조한다.", [azureDocs.vnet, azureDocs.subnet], "selectedPathRequired", {referenceValues: ["virtual_network_name", "resource_group_name"]}),
    edge("azure.subnet-nic", "azure.subnet", "azure.nic", "reference", "NIC Subnet 참조", ["basic", "direct", "persistence"], "Network Interface의 IP configuration이 Subnet ID를 참조한다.", [azureDocs.subnet, azureDocs.nic], "selectedPathRequired", {referenceValues: ["ip_configuration.subnet_id"]}),
    edge("azure.nsg-assoc-nic", "azure.nic", "azure.nicNsgAssociation", "association", "NIC 선행", ["basic", "direct", "persistence"], "연결 객체가 Network Interface ID를 참조하므로 Network Interface가 먼저 필요하다.", [azureDocs.nic, azureDocs.nsg], "selectedPathRequired", {referenceValues: ["network_interface_id"]}),
    edge("azure.nsg-assoc-nsg", "azure.nsg", "azure.nicNsgAssociation", "association", "NSG 선행", ["basic", "direct", "persistence"], "연결 객체가 Network Security Group ID를 참조하므로 NSG가 먼저 필요하다.", [azureDocs.nsg], "selectedPathRequired", {referenceValues: ["network_security_group_id", "application host port"], validationGate: "Terraform plan association과 실제 허용 port 연결 시험"}),
    edge("azure.image-vm", "azure.image", "azure.vm", "reference", "부팅 이미지 참조", ["basic", "direct", "persistence"], "Virtual Machine storageProfile.imageReference가 기존 Marketplace VM Image를 참조한다. Gallery 기반이면 source_image_id가 Image Version ID를 참조한다.", [azureDocs.image, azureDocs.galleryImage, azureDocs.vm], "conditional", {referenceValues: ["source_image_reference.publisher", "offer", "sku", "version", "source_image_id"], condition: "image에서 새 OS Disk를 만드는 현재 경로"}),
    edge("azure.nic-vm", "azure.nic", "azure.vm", "reference", "VM NIC 참조", ["basic", "direct", "persistence"], "Linux VM이 사용할 Network Interface ID 목록을 참조한다.", [azureDocs.nic, azureDocs.vm], "selectedPathRequired", {referenceValues: ["network_interface_ids", "source_image_reference 또는 기존 OS disk ID"]}),
    edge("azure.vm-os-disk", "azure.vm", "azure.osDisk", "materialize", "OS Disk 자동 생성", ["basic", "direct", "persistence"], "image 기반 Virtual Machine 생성이 storage profile의 os_disk 설정으로 실제 OS Managed Disk를 만든다.", [azureDocs.vm, azureDocs.disk], "selectedPathRequired", {referenceValues: ["storage_profile.osDisk", "os_disk", "deleteOption"], concerns: ["persistence"], validationGate: "apply 뒤 VM storageProfile.osDisk.managedDisk.id와 Managed Disk 리소스를 대조"}),
    edge("azure.public-ip-nic", "azure.publicIp", "azure.nic", "reference", "NIC Public IP", ["direct", "persistence"], "Network Interface의 IP configuration이 Public IP ID를 참조한다.", [azureDocs.pip, azureDocs.nic], "alternative", {referenceValues: ["ip_configuration.public_ip_address_id"]}),
    edge("azure.attach-disk", "azure.disk", "azure.diskAttachment", "association", "Managed Disk 선행", ["persistence"], "연결 객체가 Managed Disk ID를 참조하므로 Managed Disk가 먼저 필요하다.", [azureDocs.attach], "selectedPathRequired", {referenceValues: ["managed_disk_id"], phases: ["provisioning", "runtime"], concerns: ["persistence"]}),
    edge("azure.attach-vm", "azure.vm", "azure.diskAttachment", "association", "Virtual Machine 선행", ["persistence"], "연결 객체가 데이터 디스크를 부착할 Virtual Machine ID를 참조하므로 VM이 먼저 필요하다.", [azureDocs.attach], "selectedPathRequired", {referenceValues: ["virtual_machine_id", "lun"], phases: ["provisioning", "runtime"], concerns: ["persistence"], constraints: [{kind: "compatiblePlacement", dimensions: ["region", "availabilityZone"], participants: ["azure.vm", "azure.disk"], validationGate: "Terraform plan에서 VM과 Managed Disk의 Region·Zone 호환 확인"}], validationGate: "attach 성공, guest data device 확인, filesystem·mount·application data path와 VM 재생성 후 데이터 보존 시험"}),
    edge("azure.agw-gateway-ip", "azure.applicationGateway", "azure.agwGatewayIp", "contains", "Gateway IP 구성 소유", ["managed"], "Gateway IP Configuration은 Application Gateway 리소스 안에 중첩된다.", [azureDocs.agwTemplate], "selectedPathRequired", {referenceValues: ["gateway_ip_configuration"]}),
    edge("azure.agw-frontend-ip", "azure.applicationGateway", "azure.agwFrontendIp", "contains", "Frontend IP 구성 소유", ["managed"], "Frontend IP Configuration은 Application Gateway 리소스 안에 중첩된다.", [azureDocs.agwTemplate], "selectedPathRequired", {referenceValues: ["frontend_ip_configuration"]}),
    edge("azure.agw-frontend-port", "azure.applicationGateway", "azure.agwFrontendPort", "contains", "Frontend Port 구성 소유", ["managed"], "Frontend Port는 Application Gateway 리소스 안에 중첩된다.", [azureDocs.agwTemplate], "selectedPathRequired", {referenceValues: ["frontend_port"]}),
    edge("azure.agw-listener", "azure.applicationGateway", "azure.agwListener", "contains", "Listener 구성 소유", ["managed"], "HTTP Listener는 Application Gateway 리소스 안에 중첩된다.", [azureDocs.agwTemplate], "selectedPathRequired", {referenceValues: ["http_listener"]}),
    edge("azure.agw-backend-pool", "azure.applicationGateway", "azure.agwBackendPool", "contains", "Backend Pool 구성 소유", ["managed"], "Backend Address Pool은 Application Gateway 리소스 안에 중첩된다.", [azureDocs.agwTemplate], "selectedPathRequired", {referenceValues: ["backend_address_pool"]}),
    edge("azure.agw-backend-settings", "azure.applicationGateway", "azure.agwBackendSettings", "contains", "Backend Settings 구성 소유", ["managed"], "Backend HTTP Settings는 Application Gateway 리소스 안에 중첩된다.", [azureDocs.agwTemplate], "selectedPathRequired", {referenceValues: ["backend_http_settings"]}),
    edge("azure.agw-probe", "azure.applicationGateway", "azure.agwProbe", "contains", "Probe 구성 소유", ["managed"], "Health Probe는 Application Gateway 리소스 안에 중첩된다.", [azureDocs.agwTemplate], "selectedPathRequired", {referenceValues: ["probe"]}),
    edge("azure.agw-routing-rule", "azure.applicationGateway", "azure.agwRoutingRule", "contains", "Routing Rule 구성 소유", ["managed"], "Request Routing Rule은 Application Gateway 리소스 안에 중첩된다.", [azureDocs.agwTemplate], "selectedPathRequired", {referenceValues: ["request_routing_rule"]}),
    edge("azure.subnet-agw-gateway-ip", "azure.subnet", "azure.agwGatewayIp", "reference", "Gateway 전용 Subnet", ["managed"], "Gateway IP Configuration이 Application Gateway 전용 Subnet ID를 참조한다.", [azureDocs.subnet, azureDocs.agwTemplate], "selectedPathRequired", {referenceValues: ["gateway_ip_configuration.subnet_id"], constraints: [{kind: "dedicatedSubnet", owner: "Azure Application Gateway", validationGate: "Terraform plan Subnet 사용 대상 확인"}, {kind: "separatePlacement", participants: ["azure.applicationGateway", "azure.vmss"], dimension: "subnet", validationGate: "Application Gateway Subnet과 VMSS Subnet ID가 다른지 확인"}]}),
    edge("azure.public-ip-agw-frontend", "azure.publicIp", "azure.agwFrontendIp", "reference", "Gateway Frontend Public IP", ["managed"], "Frontend IP Configuration이 Public IP ID를 참조한다.", [azureDocs.pip, azureDocs.agwTemplate], "selectedPathRequired", {referenceValues: ["frontend_ip_configuration.public_ip_address_id"]}),
    edge("azure.frontend-ip-listener", "azure.agwFrontendIp", "azure.agwListener", "reference", "Listener Frontend IP", ["managed"], "HTTP Listener가 Frontend IP Configuration ID를 참조한다.", [azureDocs.agwTemplate], "selectedPathRequired", {referenceValues: ["http_listener.frontend_ip_configuration_name"]}),
    edge("azure.frontend-port-listener", "azure.agwFrontendPort", "azure.agwListener", "reference", "Listener Frontend Port", ["managed"], "HTTP Listener가 Frontend Port 80을 참조한다.", [azureDocs.agwTemplate], "selectedPathRequired", {referenceValues: ["http_listener.frontend_port_name", "80"]}),
    edge("azure.probe-backend-settings", "azure.agwProbe", "azure.agwBackendSettings", "health", "Backend Probe", ["managed"], "Backend HTTP Settings가 application readiness path의 Health Probe를 참조한다.", [azureDocs.agw, azureDocs.agwTemplate], "selectedPathRequired", {referenceValues: ["probe_name", "probe.path", "application port"]}),
    edge("azure.listener-routing-rule", "azure.agwListener", "azure.agwRoutingRule", "reference", "Rule Listener", ["managed"], "Request Routing Rule이 요청을 받을 HTTP Listener를 참조한다.", [azureDocs.agwTemplate], "selectedPathRequired", {referenceValues: ["request_routing_rule.http_listener_name"]}),
    edge("azure.backend-pool-routing-rule", "azure.agwBackendPool", "azure.agwRoutingRule", "reference", "Rule Backend Pool", ["managed"], "Request Routing Rule이 대상 Backend Address Pool을 참조한다.", [azureDocs.agwTemplate], "selectedPathRequired", {referenceValues: ["request_routing_rule.backend_address_pool_name"]}),
    edge("azure.backend-settings-routing-rule", "azure.agwBackendSettings", "azure.agwRoutingRule", "reference", "Rule Backend Settings", ["managed"], "Request Routing Rule이 Backend HTTP Settings를 참조한다.", [azureDocs.agwTemplate], "selectedPathRequired", {referenceValues: ["request_routing_rule.backend_http_settings_name"]}),
    edge("azure.backend-pool-vmss", "azure.agwBackendPool", "azure.vmss", "reference", "VMSS Backend Pool 참조", ["managed"], "VMSS network_interface ip_configuration이 가입할 Application Gateway Backend Address Pool ID를 참조한다.", [azureDocs.agwTemplate, azureDocs.vmss], "selectedPathRequired", {referenceValues: ["application_gateway_backend_address_pool_ids"]}),
    edge("azure.subnet-vmss", "azure.subnet", "azure.vmss", "reference", "VMSS Subnet", ["managed"], "VMSS network_interface configuration이 인스턴스를 배치할 Subnet ID를 참조한다.", [azureDocs.subnet, azureDocs.vmss], "selectedPathRequired", {referenceValues: ["network_interface.ip_configuration.subnet_id", "source_image_reference 또는 기존 OS disk ID"]}),
    edge("azure.image-vmss", "azure.image", "azure.vmss", "reference", "VMSS 부팅 이미지 참조", ["managed"], "VM Scale Set의 virtualMachineProfile.storageProfile.imageReference가 기존 Marketplace VM Image를 참조한다. Gallery 기반이면 Image Version ID를 참조한다.", [azureDocs.image, azureDocs.galleryImage, azureDocs.vmss], "conditional", {referenceValues: ["source_image_reference", "source_image_id", "virtualMachineProfile.storageProfile.imageReference"], condition: "image에서 각 child VM의 OS Disk를 만드는 현재 경로"}),
    edge("azure.vmss-instance", "azure.vmss", "azure.vmssInstance", "materialize", "Scale Set VM 자동 생성", ["managed"], "VM Scale Set이 capacity와 VM model에 따라 child Virtual Machine을 생성·교체한다.", [azureDocs.vmss], "selectedPathRequired", {referenceValues: ["instances", "sku.capacity", "zones"], constraints: [{kind: "minimumActiveInstances", target: "azure.vmss", minimum: 2, condition: "App 장애 대응을 선택했을 때", validationGate: "ResourcePlan minimumInstances와 Terraform VMSS instances 대조"}], validationGate: "VMSS instance view와 실제 child VM ID·Zone을 확인"}),
    edge("azure.vmss-instance-nic", "azure.vmssInstance", "azure.vmssNic", "materialize", "Scale Set NIC 자동 생성", ["managed"], "각 Scale Set VM의 network profile에서 실제 child Network Interface가 생성된다.", [azureDocs.vmssNic, azureDocs.vmss], "selectedPathRequired", {referenceValues: ["networkInterfaceConfigurations", "ipConfigurations", "subnet.id"], validationGate: "VMSS VM별 Network Interface REST 목록과 Subnet을 확인"}),
    edge("azure.vmss-instance-os-disk", "azure.vmssInstance", "azure.vmssOsDisk", "materialize", "Scale Set OS Disk 자동 생성", ["managed"], "각 Scale Set VM의 storage profile에서 실제 OS Managed Disk가 생성된다.", [azureDocs.disk, azureDocs.vmss], "selectedPathRequired", {referenceValues: ["storageProfile.osDisk", "deleteOption"], concerns: ["persistence"], validationGate: "VMSS VM별 OS Managed Disk ID와 삭제 정책을 확인"}),
    edge("azure.nsg-vmss-nic", "azure.nsg", "azure.vmssNic", "policy", "Scale Set NIC 방화벽", ["managed"], "VMSS network profile에서 만든 NIC 또는 그 Subnet에 Network Security Group 정책이 적용된다.", [azureDocs.nsg, azureDocs.vmss], "selectedPathRequired", {referenceValues: ["network_security_group_id", "application host port"]}),
    edge("azure.vmss-health-extension", "azure.vmss", "azure.vmssHealthExtension", "contains", "Health Extension 구성", ["managed"], "Application Health Extension은 VM Scale Set model 안에 구성되어 각 instance에 배포된다.", [azureDocs.vmssRepair], "selectedPathRequired", {referenceValues: ["extension ApplicationHealthLinux", "protocol", "port", "requestPath"]}),
    edge("azure.vmss-repair-policy", "azure.vmss", "azure.vmssRepairPolicy", "contains", "Automatic Repairs 구성", ["managed"], "Automatic Repairs Policy는 VM Scale Set 안에 구성된다.", [azureDocs.vmssRepair], "selectedPathRequired", {referenceValues: ["automatic_instance_repair", "grace_period"]}),
    edge("azure.health-repair-policy", "azure.vmssHealthExtension", "azure.vmssRepairPolicy", "health", "App Health로 교체 판단", ["managed"], "Application Health Extension이 보고한 상태를 Automatic Repairs Policy가 사용해 비정상 instance 교체를 결정한다.", [azureDocs.vmssRepair], "selectedPathRequired", {referenceValues: ["health state", "grace period", "repair action"], validationGate: "App health 실패 뒤 VMSS가 child VM을 교체하는지 확인"}),
    edge("azure.repair-instance", "azure.vmssRepairPolicy", "azure.vmssInstance", "health", "비정상 VM 교체", ["managed"], "Automatic Repairs Policy가 비정상 Scale Set VM을 삭제·재생성해 desired capacity를 회복한다.", [azureDocs.vmssRepair], "selectedPathRequired", {referenceValues: ["instance health", "repair action"], validationGate: "instance ID 변경과 정상 capacity 회복 시간을 측정"}),
    edge("azure.nat-assoc-subnet", "azure.subnet", "azure.subnetNatAssociation", "association", "Subnet 선행", ["direct", "persistence", "managed"], "연결 객체가 outbound가 필요한 Subnet ID를 참조하므로 Subnet이 먼저 필요하다.", [azureDocs.nat], "alternative", {referenceValues: ["subnet_id"]}),
    edge("azure.nat-assoc-gateway", "azure.natGateway", "azure.subnetNatAssociation", "association", "NAT Gateway 선행", ["direct", "persistence", "managed"], "연결 객체가 사용할 NAT Gateway ID를 참조하므로 NAT Gateway가 먼저 필요하다.", [azureDocs.nat], "alternative", {referenceValues: ["nat_gateway_id"]}),
    edge("azure.nat-public-ip-address", "azure.publicIp", "azure.natPublicIpAssociation", "association", "NAT Public IP 선행", ["direct", "persistence", "managed"], "연결 객체가 outbound 주소로 사용할 Public IP ID를 참조한다.", [azureDocs.nat, azureDocs.pip], "alternative", {referenceValues: ["public_ip_address_id"]}),
    edge("azure.nat-public-ip-gateway", "azure.natGateway", "azure.natPublicIpAssociation", "association", "NAT Gateway 선행", ["direct", "persistence", "managed"], "연결 객체가 Public IP를 연결할 NAT Gateway ID를 참조한다.", [azureDocs.nat], "alternative", {referenceValues: ["nat_gateway_id"]}),
    edge("azure.rg-container-registry", "azure.resourceGroup", "azure.containerRegistry", "provision", "Registry Resource Group·위치", ["basic", "direct", "persistence", "managed"], "Azure Container Registry는 Resource Group 이름과 location을 사용해 생성된다.", [azureDocs.rg, azureDocs.acr], "selectedPathRequired", {referenceValues: ["resource_group_name", "location"]}),
    edge("azure.rg-registry-identity", "azure.resourceGroup", "azure.registryPullIdentity", "provision", "Identity Resource Group·위치", ["basic", "direct", "persistence", "managed"], "User-assigned Managed Identity는 Resource Group 이름과 location을 사용해 생성된다.", [azureDocs.rg, azureDocs.acrIdentity], "selectedPathRequired", {referenceValues: ["resource_group_name", "location"]}),
    edge("azure.registry-role-scope", "azure.containerRegistry", "azure.registryPullRoleAssignment", "association", "AcrPull scope", ["basic", "direct", "persistence", "managed"], "AcrPull Role Assignment가 Azure Container Registry ID를 scope로 참조한다.", [azureDocs.acr, azureDocs.acrIdentity], "selectedPathRequired", {referenceValues: ["scope", "role_definition_name=AcrPull"]}),
    edge("azure.registry-role-principal", "azure.registryPullIdentity", "azure.registryPullRoleAssignment", "association", "AcrPull principal", ["basic", "direct", "persistence", "managed"], "AcrPull Role Assignment가 User-assigned Managed Identity principal ID를 참조한다.", [azureDocs.acrIdentity], "selectedPathRequired", {referenceValues: ["principal_id"]}),
    edge("azure.registry-identity-vm", "azure.registryPullIdentity", "azure.vm", "reference", "App VM Registry identity", ["basic", "direct", "persistence"], "단일 App VM이 AcrPull 권한을 가진 User-assigned Managed Identity ID를 참조한다.", [azureDocs.acrIdentity, azureDocs.vm], "selectedPathRequired", {referenceValues: ["identity_ids"], appComputeMode: "single"}),
    edge("azure.registry-role-vm", "azure.registryPullRoleAssignment", "azure.vm", "provision", "AcrPull 권한 선행", ["basic", "direct", "persistence"], "App VM bootstrap 전에 AcrPull Role Assignment가 완료되어야 한다.", [azureDocs.acrIdentity], "selectedPathRequired", {referenceValues: ["depends_on"], appComputeMode: "single"}),
    edge("azure.registry-registry-vm", "azure.containerRegistry", "azure.vm", "reference", "앱 image digest", ["basic", "direct", "persistence"], "App VM startup 설정이 ACR login server와 EasyDep가 확정한 image digest를 참조한다.", [azureDocs.acrPush, azureDocs.vm], "selectedPathRequired", {referenceValues: ["login_server", "image@sha256", "custom_data"], appComputeMode: "single"}),
    edge("azure.registry-identity-vmss", "azure.registryPullIdentity", "azure.vmss", "reference", "VMSS Registry identity", ["managed"], "VM Scale Set model이 각 App VM에 전달할 User-assigned Managed Identity를 참조한다.", [azureDocs.acrIdentity, azureDocs.vmss], "selectedPathRequired", {referenceValues: ["identity_ids"], appComputeMode: "group"}),
    edge("azure.registry-role-vmss", "azure.registryPullRoleAssignment", "azure.vmss", "provision", "AcrPull 권한 선행", ["managed"], "관리형 App VM 생성 전에 AcrPull Role Assignment가 완료되어야 한다.", [azureDocs.acrIdentity], "selectedPathRequired", {referenceValues: ["depends_on"], appComputeMode: "group"}),
    edge("azure.registry-registry-vmss", "azure.containerRegistry", "azure.vmss", "reference", "VMSS 앱 image digest", ["managed"], "VM Scale Set startup 설정이 ACR login server와 EasyDep가 확정한 image digest를 참조한다.", [azureDocs.acrPush, azureDocs.vmss], "selectedPathRequired", {referenceValues: ["login_server", "image@sha256", "custom_data"], appComputeMode: "group"}),
    edge("azure.traffic-public-ip-nic", "azure.publicIp", "azure.nic", "traffic", "공인 IP 요청 진입", ["direct", "persistence"], "Public IP로 들어온 요청이 연결된 Network Interface의 IP configuration으로 전달된다.", [azureDocs.pip, azureDocs.nic], "selectedPathRequired", {referenceValues: ["public IPv4", "application port"]}),
    edge("azure.traffic-nic-vm", "azure.nic", "azure.vm", "traffic", "Linux VM으로 전달", ["direct", "persistence"], "Network Interface가 허용된 요청을 연결된 Linux VM의 application port로 전달한다.", [azureDocs.nic, azureDocs.vm], "selectedPathRequired", {referenceValues: ["private IP configuration", "application port"], validationGate: "Public IP에서 Linux VM의 업무 API까지 요청 성공 확인"}),
    edge("azure.traffic-public-ip-frontend", "azure.publicIp", "azure.agwFrontendIp", "traffic", "Gateway Frontend IP 진입", ["managed"], "Public IP의 HTTP 요청이 Application Gateway Frontend IP Configuration에 도달한다.", [azureDocs.pip, azureDocs.agwTemplate], "selectedPathRequired", {referenceValues: ["frontend public IP", "80"]}),
    edge("azure.traffic-frontend-listener", "azure.agwFrontendIp", "azure.agwListener", "traffic", "HTTP Listener 수신", ["managed"], "Frontend IP·port에 도달한 요청을 HTTP Listener가 수신한다.", [azureDocs.agwTemplate], "selectedPathRequired", {referenceValues: ["frontend IP", "frontend port 80"]}),
    edge("azure.traffic-listener-rule", "azure.agwListener", "azure.agwRoutingRule", "traffic", "Routing Rule 평가", ["managed"], "Listener가 수신한 요청에 Request Routing Rule을 적용한다.", [azureDocs.agwTemplate], "selectedPathRequired", {referenceValues: ["listener", "rule priority"]}),
    edge("azure.traffic-rule-pool", "azure.agwRoutingRule", "azure.agwBackendPool", "traffic", "Backend Pool 선택", ["managed"], "Request Routing Rule이 요청 대상 Backend Address Pool을 선택한다.", [azureDocs.agwTemplate], "selectedPathRequired", {referenceValues: ["backend pool", "backend HTTP settings"]}),
    edge("azure.traffic-pool-settings", "azure.agwBackendPool", "azure.agwBackendSettings", "traffic", "Backend 전송 설정", ["managed"], "선택한 Backend Pool에 Backend HTTP Settings의 protocol·port를 적용한다.", [azureDocs.agwTemplate], "selectedPathRequired", {referenceValues: ["backend protocol", "application port", "probe health"]}),
    edge("azure.traffic-settings-vmss-nic", "azure.agwBackendSettings", "azure.vmssNic", "traffic", "정상 Scale Set NIC로 전달", ["managed"], "Application Gateway가 Probe를 통과한 Backend Pool 구성원의 Scale Set NIC private IP로 요청을 보낸다.", [azureDocs.agw, azureDocs.vmssNic], "selectedPathRequired", {referenceValues: ["backend pool membership", "private IP", "application port", "probe path"]}),
    edge("azure.traffic-vmss-nic-instance", "azure.vmssNic", "azure.vmssInstance", "traffic", "Scale Set VM으로 전달", ["managed"], "Scale Set Network Interface가 허용된 요청을 child Virtual Machine의 application port로 전달한다.", [azureDocs.vmssNic, azureDocs.vmss], "selectedPathRequired", {referenceValues: ["private IP configuration", "application port"], validationGate: "정상 VMSS instance만 요청을 받고 업무 API가 응답하는지 확인"})
  ];

  const gcpDocs = {
    network: doc("VPC networks", "https://cloud.google.com/vpc/docs/vpc"),
    subnet: doc("VPC subnets", "https://cloud.google.com/vpc/docs/subnets"),
    firewall: doc("VPC firewall rules", "https://cloud.google.com/firewall/docs/firewalls"),
    image: doc("Compute Engine images", "https://cloud.google.com/compute/docs/images"),
    vm: doc("Compute Engine instances", "https://cloud.google.com/compute/docs/instances"),
    instanceApi: doc("Compute Engine Instance REST resource", "https://docs.cloud.google.com/compute/docs/reference/rest/v1/instances"),
    bootDisk: doc("Create a VM boot Persistent Disk", "https://docs.cloud.google.com/compute/docs/disks/create-root-persistent-disks"),
    address: doc("Reserve static external IP", "https://cloud.google.com/compute/docs/ip-addresses/reserve-static-external-ip-address"),
    disk: doc("Persistent Disk", "https://cloud.google.com/compute/docs/disks"),
    attach: doc("Attach a non-boot disk", "https://cloud.google.com/compute/docs/disks/attach-disks"),
    lb: doc("External Application Load Balancer", "https://cloud.google.com/load-balancing/docs/https"),
    backend: doc("Backend services", "https://cloud.google.com/load-balancing/docs/backend-service"),
    health: doc("Health checks", "https://cloud.google.com/load-balancing/docs/health-checks"),
    template: doc("Instance templates", "https://cloud.google.com/compute/docs/instance-templates"),
    mig: doc("Regional managed instance groups", "https://cloud.google.com/compute/docs/instance-groups/distributing-instances-with-regional-instance-groups"),
    instanceGroup: doc("Regional instance groups REST resource", "https://docs.cloud.google.com/compute/docs/reference/rest/v1/regionInstanceGroups"),
    routes: doc("VPC routes", "https://docs.cloud.google.com/vpc/docs/routes"),
    nat: doc("Cloud NAT", "https://cloud.google.com/nat/docs/overview"),
    artifactRegistry: doc("Artifact Registry Docker repositories", "https://docs.cloud.google.com/artifact-registry/docs/docker/store-docker-container-images"),
    artifactRegistryPush: doc("Push and pull Artifact Registry images", "https://docs.cloud.google.com/artifact-registry/docs/docker/pushing-and-pulling"),
    artifactRegistryAccess: doc("Artifact Registry access control", "https://cloud.google.com/artifact-registry/docs/access-control")
  };

  const gcpNodes = [
    node("gcp.network", "VPC Network", "google_compute_network", "network", "providerResource", "create", ["basic", "direct", "persistence", "managed"], "GCP VM과 진입 리소스를 배치하는 프로젝트 범위의 사설 네트워크다.", [gcpDocs.network]),
    node("gcp.subnetwork", "Subnetwork", "google_compute_subnetwork", "network", "providerResource", "create", ["basic", "direct", "persistence", "managed"], "VPC Network 안에서 VM과 MIG를 특정 Region 주소 구역에 배치한다.", [gcpDocs.subnet]),
    node("gcp.firewall", "VPC Firewall Rule", "google_compute_firewall", "security", "providerResource", "create", ["basic", "direct", "persistence", "managed"], "target tag 또는 service account로 필요한 protocol·port만 허용한다.", [gcpDocs.firewall]),
    node("gcp.image", "OS Image", "data.google_compute_image", "compute", "providerResource", "referenceExisting", ["basic", "direct", "persistence", "managed"], "VM boot disk를 초기화하는 기존 Compute Engine OS Image다. public image나 custom image를 선택할 수 있다.", [gcpDocs.image], "conditional"),
    node("gcp.instance", "VM Instance", "google_compute_instance", "compute", "providerResource", "create", ["basic", "direct", "persistence"], "Docker 애플리케이션을 실행하는 Linux 가상 서버다.", [gcpDocs.vm]),
    node("gcp.migInstance", "VM Instance (MIG member)", "regionInstanceGroupManagers.listManagedInstances", "compute", "providerResource", "providerCreated", ["managed"], "Regional Managed Instance Group이 Instance Template에서 자동 생성·교체하는 실제 VM Instance다.", [gcpDocs.mig, gcpDocs.vm]),
    node("gcp.networkInterface", "Network Interface", "google_compute_instance.network_interface", "network", "providerComponent", "configureInsideOwner", ["basic", "direct", "persistence", "managed"], "Instance 또는 Instance Template 안에 구성되고 VM 생성 시 nic0 같은 server-generated 이름을 받는 네트워크 인터페이스 구성이다.", [gcpDocs.instanceApi]),
    node("gcp.accessConfig", "Access Configuration", "google_compute_instance.network_interface.access_config", "network", "providerComponent", "configureInsideOwner", ["direct", "persistence"], "VM Network Interface에 one-to-one NAT 외부 IPv4를 연결하는 내부 구성이다. 블록 자체를 생략하면 외부 IP가 없다.", [gcpDocs.instanceApi]),
    node("gcp.bootDisk", "Boot Persistent Disk", "google_compute_instance.boot_disk", "state", "providerResource", "providerCreated", ["basic", "direct", "persistence", "managed"], "image로 VM을 만들 때 initializeParams에 따라 자동 생성되는 실제 boot Persistent Disk다. 별도 data disk와 구분한다.", [gcpDocs.bootDisk, gcpDocs.disk]),
    node("gcp.regionalAddress", "Regional Static External IP Address", "google_compute_address", "network", "providerResource", "create", ["direct", "persistence"], "직접 VM 공개 진입에 사용하는 Region 고정 외부 IP 주소다.", [gcpDocs.address], "alternative"),
    node("gcp.disk", "Persistent Disk", "google_compute_disk", "state", "providerResource", "create", ["persistence"], "VM 수명과 분리해 보관하는 블록 데이터 디스크다.", [gcpDocs.disk]),
    node("gcp.diskAttachment", "VM–Disk Attachment", "google_compute_attached_disk", "state", "association", "create", ["persistence"], "VM과 Persistent Disk를 연결하는 독립 Terraform 객체다.", [gcpDocs.attach]),
    node("gcp.globalAddress", "Global Static External IP Address", "google_compute_global_address", "network", "providerResource", "create", ["managed"], "Global external Application Load Balancer frontend의 고정 공인 IP 주소다.", [gcpDocs.lb]),
    node("gcp.forwardingRule", "Global Forwarding Rule", "google_compute_global_forwarding_rule", "ingress", "providerResource", "create", ["managed"], "Global IP의 HTTP port 80 요청을 Target HTTP Proxy로 전달한다.", [gcpDocs.lb]),
    node("gcp.httpProxy", "Target HTTP Proxy", "google_compute_target_http_proxy", "ingress", "providerResource", "create", ["managed"], "HTTP 요청을 받아 URL Map을 참조한다.", [gcpDocs.lb]),
    node("gcp.urlMap", "URL Map", "google_compute_url_map", "ingress", "providerResource", "create", ["managed"], "hostname과 URL path 규칙으로 Backend Service를 선택한다.", [gcpDocs.lb]),
    node("gcp.backendService", "Backend Service", "google_compute_backend_service", "ingress", "providerResource", "create", ["managed"], "MIG·named port·Health Check를 연결하는 backend 정책 리소스다.", [gcpDocs.backend]),
    node("gcp.healthCheck", "Health Check", "google_compute_health_check", "ingress", "providerResource", "create", ["managed"], "Backend Service와 MIG autohealing이 사용할 수 있는 독립 health check 리소스다.", [gcpDocs.health]),
    node("gcp.instanceTemplate", "Instance Template", "google_compute_instance_template", "compute", "providerResource", "create", ["managed"], "반복 생성할 VM의 image·machine type·network·startup 설정을 보관한다.", [gcpDocs.template]),
    node("gcp.mig", "Regional Managed Instance Group", "google_compute_region_instance_group_manager", "compute", "providerResource", "create", ["managed"], "Instance Template로 여러 Zone의 원하는 VM 개수를 유지하고 비정상 인스턴스를 교체한다.", [gcpDocs.mig]),
    node("gcp.migInstanceGroup", "Regional Instance Group", "google_compute_region_instance_group_manager.instance_group", "compute", "providerResource", "providerCreated", ["managed"], "Regional Managed Instance Group이 실제 member VM 집합을 노출하는 underlying Instance Group이다. Backend Service가 manager가 아니라 이 group URL을 참조한다.", [gcpDocs.instanceGroup, gcpDocs.mig]),
    node("gcp.autoHealingPolicy", "Autohealing Policy", "google_compute_region_instance_group_manager.auto_healing_policies", "compute", "providerComponent", "configureInsideOwner", ["managed"], "MIG가 별도 Health Check와 initial delay를 사용해 비정상 VM을 재생성하는 내부 정책이다.", [gcpDocs.mig, gcpDocs.health]),
    node("gcp.defaultRoute", "System-generated IPv4 Default Route", "compute.routes.list (default-internet-gateway)", "network", "providerResource", "providerCreated", ["basic", "direct", "persistence", "managed"], "VPC Network 생성 시 기본적으로 생기는 0.0.0.0/0 → default-internet-gateway Route다. 삭제하거나 명시 Route로 대체할 수 있다.", [gcpDocs.routes], "alternative"),
    node("gcp.subnetRoute", "System-generated Subnet Route", "compute.routes.list (subnet route)", "network", "providerResource", "providerCreated", ["basic", "direct", "persistence", "managed"], "Subnetwork 생성 시 해당 primary·secondary IP range에 대해 자동 생성되며 Subnetwork가 존재하는 동안 유지되는 Route다.", [gcpDocs.routes]),
    node("gcp.router", "Cloud Router", "google_compute_router", "network", "providerResource", "create", ["direct", "persistence", "managed"], "Cloud NAT configuration을 소유하는 Region 단위 Router 리소스다.", [gcpDocs.nat], "alternative"),
    node("gcp.nat", "Cloud NAT Configuration", "google_compute_router_nat", "network", "providerComponent", "configureInsideOwner", ["direct", "persistence", "managed"], "공인 IP가 없는 VM의 외부 접속을 Cloud Router 안에 구성한다.", [gcpDocs.nat], "alternative"),
    node("gcp.artifactRegistryRepository", "Artifact Registry Repository", "google_artifact_registry_repository", "config", "providerResource", "create", ["basic", "direct", "persistence", "managed"], "DOCKER format으로 EasyDep 앱 image를 저장하고 VM이 digest로 pull하는 Region repository다.", [gcpDocs.artifactRegistry, gcpDocs.artifactRegistryPush]),
    node("gcp.registryPullServiceAccount", "Service Account", "google_service_account", "security", "providerResource", "create", ["basic", "direct", "persistence", "managed"], "App VM 또는 Instance Template에 연결되어 secret 없이 Artifact Registry token을 얻는 service account다.", [gcpDocs.artifactRegistryAccess]),
    node("gcp.registryPullIamMember", "Artifact Registry Reader IAM Member", "google_artifact_registry_repository_iam_member", "security", "association", "create", ["basic", "direct", "persistence", "managed"], "Artifact Registry Repository의 Reader 역할을 App VM Service Account에 부여하는 Terraform 객체다.", [gcpDocs.artifactRegistryAccess])
  ];

  const gcpEdges = [
    edge("gcp.network-subnet", "gcp.network", "gcp.subnetwork", "reference", "VPC Network 참조", ["basic", "direct", "persistence", "managed"], "Subnetwork는 어느 VPC Network에 속하는지 network ID를 참조한다.", [gcpDocs.network, gcpDocs.subnet], "selectedPathRequired", {referenceValues: ["network"]}),
    edge("gcp.network-firewall", "gcp.network", "gcp.firewall", "reference", "Firewall VPC Network", ["basic", "direct", "persistence", "managed"], "Firewall Rule은 정책을 적용할 VPC Network self link 또는 이름을 참조한다.", [gcpDocs.network, gcpDocs.firewall], "selectedPathRequired", {referenceValues: ["network"], validationGate: "Terraform plan에서 google_compute_firewall.network가 선택한 VPC Network를 참조하는지 확인"}),
    edge("gcp.network-default-route", "gcp.network", "gcp.defaultRoute", "materialize", "IPv4 Default Route 자동 생성", ["basic", "direct", "persistence", "managed"], "VPC Network 생성 시 delete_default_routes_on_create를 사용하지 않으면 0.0.0.0/0 default-internet-gateway Route가 자동 생성된다.", [gcpDocs.routes], "alternative", {condition: "system-generated default route를 삭제하거나 명시적 Route로 대체하지 않을 때", referenceValues: ["0.0.0.0/0", "default-internet-gateway", "delete_default_routes_on_create"], validationGate: "Routes API에서 network와 nextHopGateway가 일치하는 default route를 확인"}),
    edge("gcp.subnet-subnet-route", "gcp.subnetwork", "gcp.subnetRoute", "materialize", "Subnet Route 자동 생성", ["basic", "direct", "persistence", "managed"], "Subnetwork 생성 시 primary·secondary IPv4 range마다 system-generated subnet route가 자동 생성된다.", [gcpDocs.routes], "selectedPathRequired", {referenceValues: ["subnetwork primary/secondary IPv4 ranges", "nextHopNetwork"], validationGate: "Routes API에서 Subnetwork IP range의 subnet route를 확인"}),
    edge("gcp.image-instance", "gcp.image", "gcp.instance", "reference", "부팅 이미지 참조", ["basic", "direct", "persistence"], "VM boot disk initialize_params가 기존 Compute Engine Image를 참조한다.", [gcpDocs.image, gcpDocs.vm], "conditional", {referenceValues: ["boot_disk.initialize_params.image"], condition: "image로 새 boot disk를 초기화할 때"}),
    edge("gcp.subnet-instance", "gcp.subnetwork", "gcp.instance", "reference", "VM Subnetwork", ["basic", "direct", "persistence"], "VM network_interface가 배치될 Subnetwork를 참조한다.", [gcpDocs.subnet, gcpDocs.vm], "selectedPathRequired", {referenceValues: ["network_interface.subnetwork"]}),
    edge("gcp.firewall-instance", "gcp.firewall", "gcp.instance", "policy", "VM 방화벽 선택", ["basic", "direct", "persistence"], "Firewall Rule selector가 VM의 target tag 또는 service account와 일치하고 application port를 허용해야 한다.", [gcpDocs.firewall, gcpDocs.vm], "selectedPathRequired", {referenceValues: ["target_tags 또는 target_service_accounts", "application host port"], validationGate: "Terraform selector 일치와 실제 허용 port 연결 시험"}),
    edge("gcp.instance-network-interface", "gcp.instance", "gcp.networkInterface", "contains", "VM Network Interface 구성", ["basic", "direct", "persistence"], "Network Interface는 VM Instance 요청 안에 중첩되며 Subnetwork와 내부 IP 설정을 가진다.", [gcpDocs.instanceApi], "selectedPathRequired", {referenceValues: ["networkInterfaces[]", "name=nic0 (server-generated)", "subnetwork"]}),
    edge("gcp.instance-boot-disk", "gcp.instance", "gcp.bootDisk", "materialize", "Boot Disk 자동 생성", ["basic", "direct", "persistence"], "VM Instance의 boot disk initializeParams가 image에서 실제 Persistent Disk를 만든다.", [gcpDocs.bootDisk, gcpDocs.instanceApi], "selectedPathRequired", {referenceValues: ["disks[].boot=true", "initializeParams.sourceImage", "autoDelete"], concerns: ["persistence"], validationGate: "apply 뒤 Instance boot disk source URL과 Disk ID·autoDelete를 확인"}),
    edge("gcp.interface-access-config", "gcp.networkInterface", "gcp.accessConfig", "contains", "외부 NAT 구성", ["direct", "persistence"], "Access Configuration은 VM Network Interface 안에 중첩된다.", [gcpDocs.instanceApi], "alternative", {referenceValues: ["networkInterfaces[].accessConfigs[]", "ONE_TO_ONE_NAT"]}),
    edge("gcp.address-access-config", "gcp.regionalAddress", "gcp.accessConfig", "reference", "고정 외부 IP 참조", ["direct", "persistence"], "Access Configuration의 natIP가 예약한 Regional Static External IP 주소를 참조한다.", [gcpDocs.address, gcpDocs.instanceApi], "alternative", {referenceValues: ["network_interface.access_config.nat_ip"]}),
    edge("gcp.attach-disk", "gcp.disk", "gcp.diskAttachment", "association", "Persistent Disk 선행", ["persistence"], "Attached Disk 연결 객체가 Persistent Disk ID를 참조하므로 Persistent Disk가 먼저 필요하다.", [gcpDocs.attach], "selectedPathRequired", {referenceValues: ["disk"], phases: ["provisioning", "runtime"], concerns: ["persistence"]}),
    edge("gcp.attach-instance", "gcp.instance", "gcp.diskAttachment", "association", "VM 선행", ["persistence"], "Attached Disk 연결 객체가 디스크를 부착할 VM ID를 참조하므로 VM이 먼저 필요하다.", [gcpDocs.attach], "selectedPathRequired", {referenceValues: ["instance"], phases: ["provisioning", "runtime"], concerns: ["persistence"], constraints: [{kind: "samePlacementDimension", dimension: "zone", participants: ["gcp.instance", "gcp.disk"], validationGate: "Terraform plan에서 zonal VM과 Persistent Disk Zone 일치 확인"}], validationGate: "attach 성공, guest device 확인, filesystem·mount·application data path와 VM 재생성 후 데이터 보존 시험"}),
    edge("gcp.address-forwarding", "gcp.globalAddress", "gcp.forwardingRule", "reference", "Frontend IP", ["managed"], "Global Forwarding Rule이 frontend Global IP 주소를 참조한다.", [gcpDocs.lb], "selectedPathRequired", {referenceValues: ["ip_address", "80"]}),
    edge("gcp.proxy-forwarding", "gcp.httpProxy", "gcp.forwardingRule", "reference", "Forwarding target", ["managed"], "Global Forwarding Rule이 요청을 보낼 Target HTTP Proxy를 참조한다.", [gcpDocs.lb], "selectedPathRequired", {referenceValues: ["target"]}),
    edge("gcp.urlmap-proxy", "gcp.urlMap", "gcp.httpProxy", "reference", "URL Map", ["managed"], "Target HTTP Proxy가 routing 규칙을 가진 URL Map을 참조한다.", [gcpDocs.lb], "selectedPathRequired", {referenceValues: ["url_map"]}),
    edge("gcp.backend-urlmap", "gcp.backendService", "gcp.urlMap", "reference", "기본 Backend Service", ["managed"], "URL Map의 default service 또는 path rule이 Backend Service를 참조한다.", [gcpDocs.lb, gcpDocs.backend], "selectedPathRequired", {referenceValues: ["default_service 또는 path_matcher.service"]}),
    edge("gcp.health-backend", "gcp.healthCheck", "gcp.backendService", "health", "Backend Health Check", ["managed"], "Backend Service가 application port·readiness path를 검사할 Health Check를 참조한다.", [gcpDocs.backend, gcpDocs.health], "selectedPathRequired", {referenceValues: ["health_checks", "port 또는 port_name", "request_path"]}),
    edge("gcp.instance-group-backend", "gcp.migInstanceGroup", "gcp.backendService", "reference", "Backend Instance Group", ["managed"], "Backend Service의 backend.group은 manager가 노출하는 underlying Regional Instance Group URL을 참조한다.", [gcpDocs.backend, gcpDocs.instanceGroup], "selectedPathRequired", {referenceValues: ["backend.group", "instance_group", "named_port"], validationGate: "Terraform plan backend.group과 MIG instance_group output URL을 대조"}),
    edge("gcp.image-template", "gcp.image", "gcp.instanceTemplate", "reference", "Template 부팅 이미지", ["managed"], "Instance Template boot disk가 기존 Compute Engine Image를 참조한다.", [gcpDocs.image, gcpDocs.template], "conditional", {referenceValues: ["disk.source_image"]}),
    edge("gcp.subnet-template", "gcp.subnetwork", "gcp.instanceTemplate", "reference", "Template Subnetwork", ["managed"], "Instance Template network_interface가 VM을 배치할 Subnetwork를 참조한다.", [gcpDocs.subnet, gcpDocs.template], "selectedPathRequired", {referenceValues: ["network_interface.subnetwork"]}),
    edge("gcp.template-mig", "gcp.instanceTemplate", "gcp.mig", "reference", "VM 설정 청사진", ["managed"], "Regional Managed Instance Group이 VM 생성에 사용할 Instance Template을 참조한다.", [gcpDocs.template, gcpDocs.mig], "selectedPathRequired", {referenceValues: ["version.instance_template 또는 instance_template"], constraints: [{kind: "minimumActiveInstances", target: "gcp.mig", minimum: 2, condition: "App 장애 대응을 선택했을 때", validationGate: "ResourcePlan minimumInstances와 Terraform MIG target_size 대조"}, {kind: "distinctPlacementMinimum", dimension: "zone", minimum: 2, condition: "다중 Zone 분산을 요구하고 regional MIG를 선택했을 때", validationGate: "Terraform plan에서 regional MIG distribution_policy_zones 확인"}]}),
    edge("gcp.mig-instance-group", "gcp.mig", "gcp.migInstanceGroup", "materialize", "Regional Instance Group 자동 생성", ["managed"], "Regional MIG 생성 시 member 집합을 나타내는 underlying Regional Instance Group이 함께 생긴다.", [gcpDocs.instanceGroup, gcpDocs.mig], "selectedPathRequired", {referenceValues: ["instanceGroup", "regionInstanceGroups"], validationGate: "MIG instanceGroup output과 Region Instance Groups API 결과를 대조"}),
    edge("gcp.mig-instance", "gcp.mig", "gcp.migInstance", "materialize", "MIG VM 자동 생성", ["managed"], "Regional MIG가 target size와 Instance Template에 따라 여러 Zone에 실제 VM Instance를 생성·교체한다.", [gcpDocs.mig, gcpDocs.vm], "selectedPathRequired", {referenceValues: ["target_size", "distribution_policy_zones", "versions[].instanceTemplate"], validationGate: "MIG managed instances 목록의 VM ID·Zone·currentAction을 확인"}),
    edge("gcp.mig-instance-network-interface", "gcp.migInstance", "gcp.networkInterface", "contains", "MIG VM Network Interface 구성", ["managed"], "MIG가 만든 각 VM에 Instance Template network interface 설정이 실체화된다.", [gcpDocs.instanceApi, gcpDocs.template], "selectedPathRequired", {referenceValues: ["networkInterfaces[]", "subnetwork", "name=nic0"]}),
    edge("gcp.mig-instance-boot-disk", "gcp.migInstance", "gcp.bootDisk", "materialize", "MIG VM Boot Disk 자동 생성", ["managed"], "MIG가 만든 각 VM에 Instance Template image를 따른 실제 boot Persistent Disk가 생성된다.", [gcpDocs.bootDisk, gcpDocs.template], "selectedPathRequired", {referenceValues: ["disks[].sourceImage", "boot=true", "autoDelete"], concerns: ["persistence"], validationGate: "각 managed VM의 boot disk와 autoDelete 정책을 확인"}),
    edge("gcp.firewall-mig-instance", "gcp.firewall", "gcp.migInstance", "policy", "MIG VM 방화벽", ["managed"], "Firewall Rule selector가 Instance Template에서 각 managed VM에 부여된 tag 또는 service account와 일치해야 한다.", [gcpDocs.firewall, gcpDocs.mig], "selectedPathRequired", {referenceValues: ["target_tags 또는 target_service_accounts", "application host port"]}),
    edge("gcp.mig-autohealing", "gcp.mig", "gcp.autoHealingPolicy", "contains", "Autohealing 구성", ["managed"], "Autohealing Policy는 Regional MIG 안에 구성된다.", [gcpDocs.mig], "selectedPathRequired", {referenceValues: ["auto_healing_policies", "initial_delay_sec"]}),
    edge("gcp.health-autohealing", "gcp.healthCheck", "gcp.autoHealingPolicy", "health", "복구용 Health Check", ["managed"], "Autohealing Policy가 VM 재생성 판단에 사용할 Health Check를 참조한다. LB traffic health와 별도 Health Check를 쓰는 구성이 권장된다.", [gcpDocs.health, gcpDocs.mig], "selectedPathRequired", {referenceValues: ["health_check", "initial_delay_sec", "readiness path"]}),
    edge("gcp.autohealing-instance", "gcp.autoHealingPolicy", "gcp.migInstance", "health", "비정상 VM 재생성", ["managed"], "Autohealing Policy가 비정상 managed VM을 재생성해 target size를 회복한다.", [gcpDocs.mig], "selectedPathRequired", {referenceValues: ["health status", "recreate action"], validationGate: "VM 장애 뒤 instance ID 변경과 정상 target size 회복 시간을 측정"}),
    edge("gcp.network-router", "gcp.network", "gcp.router", "reference", "Router VPC Network", ["direct", "persistence", "managed"], "Cloud Router가 연결될 VPC Network를 참조한다.", [gcpDocs.nat], "alternative", {referenceValues: ["network", "region"]}),
    edge("gcp.router-nat", "gcp.router", "gcp.nat", "contains", "Cloud NAT 구성 소유", ["direct", "persistence", "managed"], "Cloud NAT Configuration은 어느 Cloud Router 안에 구성되는지 참조한다.", [gcpDocs.nat], "alternative", {referenceValues: ["router", "region"]}),
    edge("gcp.subnet-nat", "gcp.subnetwork", "gcp.nat", "reference", "NAT 대상 Subnetwork", ["direct", "persistence", "managed"], "Cloud NAT가 outbound를 제공할 Subnetwork 또는 전체 Region Subnet 범위를 참조한다.", [gcpDocs.nat], "alternative", {referenceValues: ["source_subnetwork_ip_ranges_to_nat", "subnetwork"]}),
    edge("gcp.registry-repository-iam", "gcp.artifactRegistryRepository", "gcp.registryPullIamMember", "association", "Artifact Registry IAM scope", ["basic", "direct", "persistence", "managed"], "Repository IAM Member가 Artifact Registry Repository ID를 참조한다.", [gcpDocs.artifactRegistryAccess], "selectedPathRequired", {referenceValues: ["repository", "role=roles/artifactregistry.reader"]}),
    edge("gcp.registry-service-account-iam", "gcp.registryPullServiceAccount", "gcp.registryPullIamMember", "association", "Artifact Registry IAM member", ["basic", "direct", "persistence", "managed"], "Repository IAM Member가 App VM Service Account member를 참조한다.", [gcpDocs.artifactRegistryAccess], "selectedPathRequired", {referenceValues: ["member=serviceAccount:email"]}),
    edge("gcp.registry-service-account-instance", "gcp.registryPullServiceAccount", "gcp.instance", "reference", "App VM Registry identity", ["basic", "direct", "persistence"], "단일 App VM이 Artifact Registry Reader 권한을 가진 Service Account를 참조한다.", [gcpDocs.artifactRegistryAccess, gcpDocs.vm], "selectedPathRequired", {referenceValues: ["service_account.email", "cloud-platform scope"], appComputeMode: "single"}),
    edge("gcp.registry-iam-instance", "gcp.registryPullIamMember", "gcp.instance", "provision", "Artifact Registry Reader 선행", ["basic", "direct", "persistence"], "App VM startup 전에 Artifact Registry Reader IAM Member가 완료되어야 한다.", [gcpDocs.artifactRegistryAccess], "selectedPathRequired", {referenceValues: ["depends_on"], appComputeMode: "single"}),
    edge("gcp.registry-repository-instance", "gcp.artifactRegistryRepository", "gcp.instance", "reference", "앱 image digest", ["basic", "direct", "persistence"], "App VM startup 설정이 Artifact Registry repository 경로와 EasyDep가 확정한 image digest를 참조한다.", [gcpDocs.artifactRegistryPush, gcpDocs.vm], "selectedPathRequired", {referenceValues: ["LOCATION-docker.pkg.dev/PROJECT/REPOSITORY/image@sha256", "metadata_startup_script"], appComputeMode: "single"}),
    edge("gcp.registry-service-account-template", "gcp.registryPullServiceAccount", "gcp.instanceTemplate", "reference", "Template Registry identity", ["managed"], "Instance Template가 각 App VM에 전달할 Artifact Registry Reader Service Account를 참조한다.", [gcpDocs.artifactRegistryAccess, gcpDocs.template], "selectedPathRequired", {referenceValues: ["service_account.email", "cloud-platform scope"], appComputeMode: "group"}),
    edge("gcp.registry-iam-template", "gcp.registryPullIamMember", "gcp.instanceTemplate", "provision", "Artifact Registry Reader 선행", ["managed"], "관리형 App VM 생성 전에 Artifact Registry Reader IAM Member가 완료되어야 한다.", [gcpDocs.artifactRegistryAccess], "selectedPathRequired", {referenceValues: ["depends_on"], appComputeMode: "group"}),
    edge("gcp.registry-repository-template", "gcp.artifactRegistryRepository", "gcp.instanceTemplate", "reference", "Template 앱 image digest", ["managed"], "Instance Template startup 설정이 Artifact Registry repository 경로와 EasyDep가 확정한 image digest를 참조한다.", [gcpDocs.artifactRegistryPush, gcpDocs.template], "selectedPathRequired", {referenceValues: ["LOCATION-docker.pkg.dev/PROJECT/REPOSITORY/image@sha256", "metadata_startup_script"], appComputeMode: "group"}),
    edge("gcp.traffic-address-access-config", "gcp.regionalAddress", "gcp.accessConfig", "traffic", "Access Configuration으로 NAT", ["direct", "persistence"], "Regional External IP 요청이 VM Network Interface의 ONE_TO_ONE_NAT Access Configuration으로 매핑된다.", [gcpDocs.address, gcpDocs.instanceApi], "selectedPathRequired", {referenceValues: ["external IPv4", "natIP", "application port"]}),
    edge("gcp.traffic-access-config-interface", "gcp.accessConfig", "gcp.networkInterface", "traffic", "Network Interface로 전달", ["direct", "persistence"], "Access Configuration이 요청을 nic0의 primary internal IPv4로 전달한다.", [gcpDocs.instanceApi], "selectedPathRequired", {referenceValues: ["ONE_TO_ONE_NAT", "networkIP"]}),
    edge("gcp.traffic-interface-instance", "gcp.networkInterface", "gcp.instance", "traffic", "VM으로 전달", ["direct", "persistence"], "Network Interface가 Firewall Rule에서 허용된 요청을 VM guest의 application port로 전달한다.", [gcpDocs.instanceApi, gcpDocs.firewall], "selectedPathRequired", {referenceValues: ["nic0", "application port"], validationGate: "External IP에서 VM의 업무 API까지 요청 성공 확인"}),
    edge("gcp.traffic-address-forwarding", "gcp.globalAddress", "gcp.forwardingRule", "traffic", "Global IP 요청 진입", ["managed"], "Global External IP의 HTTP 요청을 Global Forwarding Rule이 수신한다.", [gcpDocs.lb], "selectedPathRequired", {referenceValues: ["global IPv4", "80"]}),
    edge("gcp.traffic-forwarding-proxy", "gcp.forwardingRule", "gcp.httpProxy", "traffic", "HTTP Proxy 전달", ["managed"], "Global Forwarding Rule이 요청을 Target HTTP Proxy로 전달한다.", [gcpDocs.lb], "selectedPathRequired", {referenceValues: ["forwarding target", "80"]}),
    edge("gcp.traffic-proxy-urlmap", "gcp.httpProxy", "gcp.urlMap", "traffic", "URL Map routing", ["managed"], "Target HTTP Proxy가 URL Map의 host·path 규칙으로 요청을 넘긴다.", [gcpDocs.lb], "selectedPathRequired", {referenceValues: ["URL path"]}),
    edge("gcp.traffic-urlmap-backend", "gcp.urlMap", "gcp.backendService", "traffic", "Backend Service 선택", ["managed"], "URL Map이 요청 규칙에 맞는 Backend Service를 선택한다.", [gcpDocs.lb, gcpDocs.backend], "selectedPathRequired", {referenceValues: ["default service 또는 path rule"]}),
    edge("gcp.traffic-backend-instance-group", "gcp.backendService", "gcp.migInstanceGroup", "traffic", "정상 Instance Group 선택", ["managed"], "Backend Service가 Health Check 결과와 named port를 사용해 Regional Instance Group의 정상 member를 선택한다.", [gcpDocs.backend, gcpDocs.instanceGroup], "selectedPathRequired", {referenceValues: ["backend group", "named port", "health status"]}),
    edge("gcp.traffic-instance-group-interface", "gcp.migInstanceGroup", "gcp.networkInterface", "traffic", "정상 VM Network Interface로 전달", ["managed"], "선택된 member VM의 Network Interface private IP와 named port로 요청을 보낸다.", [gcpDocs.backend, gcpDocs.instanceApi], "selectedPathRequired", {referenceValues: ["managed instance", "networkIP", "named port"]}),
    edge("gcp.traffic-interface-mig-instance", "gcp.networkInterface", "gcp.migInstance", "traffic", "Managed VM으로 전달", ["managed"], "Network Interface가 허용된 요청을 managed VM guest의 application port로 전달한다.", [gcpDocs.instanceApi, gcpDocs.mig], "selectedPathRequired", {referenceValues: ["nic0", "application port"], validationGate: "정상 MIG VM만 요청을 받고 업무 API가 응답하는지 확인"})
  ];

  const measuredEvidence = {
    "aws.subnet-alb": {
      refs: [
        "depkb:aws/loadBalancer->subnet/provisioning",
        "research:aws.load-balancer-subnet.existence",
        "research:aws.load-balancer-subnet.necessity"
      ],
      assessment: {level: "provisioned", status: "passed"}
    },
    "aws.target-asg": {
      refs: ["artifact:evaluation/dependency_audit/multi-provider-sample-app-postgres-e2-adjudication-20260815.json#aws"],
      assessment: {level: "runtimeVerified", status: "passed"}
    },
    "aws.attach-ec2": {
      refs: ["artifact:evaluation/dependency_audit/multi-provider-sample-app-postgres-e3-adjudication-20260815.json#aws"],
      assessment: {level: "runtimeVerified", status: "passed"}
    },
    "azure.attach-vm": {
      refs: ["artifact:evaluation/dependency_audit/multi-provider-sample-app-postgres-e3-adjudication-20260815.json#azure"],
      assessment: {level: "runtimeVerified", status: "passed"}
    },
    "gcp.attach-instance": {
      refs: [
        "depkb:gcp/vm->disk/provisioning",
        "artifact:evaluation/dependency_audit/multi-provider-sample-app-postgres-e3-adjudication-20260815.json#gcp"
      ],
      assessment: {level: "runtimeVerified", status: "passed"}
    },
    "gcp.traffic-interface-mig-instance": {
      refs: ["artifact:evaluation/dependency_audit/multi-provider-sample-app-postgres-e2-adjudication-20260815.json#gcp"],
      assessment: {level: "runtimeVerified", status: "passed"}
    }
  };

  function finishProvider(label, nodes, edges) {
    const byId = new Map(nodes.map((item) => [item.id, item]));
    return {
      label,
      nodes,
      edges: edges.map((item) => {
        const measured = measuredEvidence[item.id];
        return {
          ...item,
          evidenceRefs: [...item.evidenceRefs, ...(measured?.refs || [])],
          evidenceAssessment: measured?.assessment || item.evidenceAssessment,
          sourceEntityClass: byId.get(item.source)?.entityClass,
          targetEntityClass: byId.get(item.target)?.entityClass
        };
      })
    };
  }

  const comparisonRoles = [
    {id: "network-boundary", label: "네트워크 경계", providers: {aws: ["aws.vpc"], azure: ["azure.vnet"], gcp: ["gcp.network"]}},
    {id: "firewall", label: "방화벽", providers: {aws: ["aws.securityGroup"], azure: ["azure.nsg"], gcp: ["gcp.firewall"]}},
    {id: "boot-image", label: "VM 부팅 원본 이미지", providers: {aws: ["aws.ami"], azure: ["azure.image"], gcp: ["gcp.image"]}},
    {id: "application-image-registry", label: "앱 Image Registry", providers: {aws: ["aws.ecrRepository"], azure: ["azure.containerRegistry"], gcp: ["gcp.artifactRegistryRepository"]}},
    {id: "vm-network-interface", label: "VM 네트워크 인터페이스", providers: {aws: ["aws.primaryEni"], azure: ["azure.nic", "azure.vmssNic"], gcp: ["gcp.networkInterface"]}},
    {id: "boot-disk", label: "VM 부팅 Disk", providers: {aws: ["aws.rootVolume"], azure: ["azure.osDisk", "azure.vmssOsDisk"], gcp: ["gcp.bootDisk"]}},
    {id: "direct-address", label: "직접 공개 주소", providers: {aws: ["aws.eip"], azure: ["azure.publicIp"], gcp: ["gcp.regionalAddress"]}},
    {id: "managed-compute-group", label: "관리형 VM 그룹", providers: {aws: ["aws.autoScalingGroup"], azure: ["azure.vmss"], gcp: ["gcp.mig"]}},
    {id: "http-ingress", label: "HTTP 진입", providers: {aws: ["aws.alb", "aws.listener"], azure: ["azure.applicationGateway"], gcp: ["gcp.forwardingRule", "gcp.httpProxy", "gcp.urlMap"]}},
    {id: "health", label: "Health·복구", providers: {aws: ["aws.targetGroup", "aws.autoScalingGroup"], azure: ["azure.applicationGateway", "azure.vmss"], gcp: ["gcp.healthCheck", "gcp.mig"]}},
    {id: "persistent-disk", label: "영속 Disk", providers: {aws: ["aws.ebs"], azure: ["azure.disk"], gcp: ["gcp.disk"]}},
    {id: "disk-attachment", label: "Disk 연결", providers: {aws: ["aws.volumeAttachment"], azure: ["azure.diskAttachment"], gcp: ["gcp.diskAttachment"]}},
    {id: "private-outbound", label: "사설 VM outbound", providers: {aws: ["aws.natGateway"], azure: ["azure.natGateway"], gcp: ["gcp.router", "gcp.nat"]}}
  ];

  const planMappings = {
    aws: {
      ingress: ["aws.alb", "aws.listener", "aws.targetGroup"],
      registry: ["aws.ecrRepository", "aws.registryPullRole", "aws.registryPullPolicyAttachment", "aws.registryInstanceProfile"],
      appGroup: ["aws.launchTemplate", "aws.autoScalingGroup"], stateVm: ["aws.ec2"],
      disk: ["aws.ebs"], attachment: ["aws.volumeAttachment"]
    },
    azure: {
      ingress: ["azure.applicationGateway"], registry: ["azure.containerRegistry", "azure.registryPullIdentity", "azure.registryPullRoleAssignment"], appGroup: ["azure.vmss"], stateVm: ["azure.vm"],
      disk: ["azure.disk"], attachment: ["azure.diskAttachment"]
    },
    gcp: {
      ingress: ["gcp.forwardingRule", "gcp.httpProxy", "gcp.urlMap", "gcp.backendService"],
      registry: ["gcp.artifactRegistryRepository", "gcp.registryPullServiceAccount", "gcp.registryPullIamMember"],
      appGroup: ["gcp.instanceTemplate", "gcp.mig"], stateVm: ["gcp.instance"],
      disk: ["gcp.disk"], attachment: ["gcp.diskAttachment"]
    }
  };

  function resourcePlanExample(provider, refs) {
    const artifact = `artifact:evaluation/dependency_audit/multi-provider-sample-app-postgres-e3-adjudication-20260815.json#${provider}`;
    const registryName = {aws: "ECR Repository", azure: "Container Registry", gcp: "Artifact Registry Repository"}[provider];
    return {
      schemaVersion: "easydep-resource-plan/v1",
      provider,
      purpose: "관리형 App 계층과 별도 영속 State 계층을 함께 표현하는 선택 계획 예시",
      deploymentTopology: {familyId: "managedGroupManyMultiZone.dedicated.loadBalanced", computeProfile: "managedGroupManyMultiZone", replicaCount: 2, zoneLayout: "multiZoneSpread", databasePlacement: "dedicated", publicIngress: "loadBalanced", availabilityClaim: "none"},
      outboundPolicy: {
        requiredBy: ["registry-image-pull", "postgres-image-pull"],
        strategy: "nat",
        alternatives: ["publicAddress", "privateRegistryEndpoint"],
        assumption: "사설 App VM은 provider-native Registry의 고정 digest를, State VM은 선택 시 Docker Hub의 postgres:17-bookworm을 pull한다.",
        status: "resolved"
      },
      artifactDeliveryPolicy: {
        strategy: "providerNativeRegistry",
        buildMode: "easydepBuildOnce",
        imageReference: "digest",
        providerRefs: refs.registry,
        status: "resolved"
      },
      nodes: [
        {id: "public-client", name: "외부 HTTP 사용자", entityClass: "externalActor", group: "endpoint"},
        {id: "http-ingress", name: "관리형 HTTP 진입", entityClass: "providerResource", group: "ingress", providerRefs: refs.ingress},
        {id: "app-image-registry", name: registryName, entityClass: "providerResource", group: "artifact", providerRefs: refs.registry},
        {id: "app-compute-group", name: "관리형 App VM 그룹 × 2", entityClass: "providerResource", group: "compute", providerRefs: refs.appGroup, replicas: 2},
        {id: "app-workload", name: "App workload", entityClass: "runtimeElement", group: "runtime", replicas: 2},
        {id: "state-vm", name: "별도 State VM × 1", entityClass: "providerResource", group: "compute", providerRefs: refs.stateVm, replicas: 1},
        {id: "state-workload", name: "State workload", entityClass: "runtimeElement", group: "runtime", replicas: 1},
        {id: "disk-attachment", name: "State Disk attachment", entityClass: "association", group: "state", providerRefs: refs.attachment},
        {id: "persistent-disk", name: "영속 Disk × 1", entityClass: "providerResource", group: "state", providerRefs: refs.disk, replicas: 1}
      ],
      edges: [
        {id: "plan.client-ingress", from: "public-client", to: "http-ingress", label: "HTTP 80"},
        {id: "plan.registry-app", from: "app-image-registry", to: "app-compute-group", label: "확정 image digest pull"},
        {id: "plan.ingress-app", from: "http-ingress", to: "app-compute-group", label: "정상 App VM으로 전달"},
        {id: "plan.app-allocation", from: "app-compute-group", to: "app-workload", label: "replicas=2 실행"},
        {id: "plan.app-state", from: "app-workload", to: "state-workload", label: "사설 endpoint로 연결"},
        {id: "plan.state-allocation", from: "state-vm", to: "state-workload", label: "replicas=1 실행"},
        {id: "plan.attachment-vm", from: "state-vm", to: "disk-attachment", label: "State VM 선행"},
        {id: "plan.attachment-disk", from: "persistent-disk", to: "disk-attachment", label: "Disk 선행"}
      ],
      constraints: [
        {kind: "replicaCount", target: "app-compute-group", exact: 2, status: "resolved"},
        {kind: "multiZoneSpread", target: "app-compute-group", minimumZones: 2, status: "resolved"},
        {kind: "compatiblePlacement", participants: ["state-vm", "persistent-disk"], dimensions: ["region", "zone"], status: "resolved"}
      ],
      evidenceRefs: [
        `artifact:evaluation/dependency_audit/multi-provider-sample-app-postgres-e1-adjudication-20260814.json#${provider}`,
        `artifact:evaluation/dependency_audit/multi-provider-sample-app-postgres-e2-adjudication-20260815.json#${provider}`,
        artifact
      ],
      evidenceStatus: provider === "azure" ? "partiallyObserved" : "observed",
      unresolved: []
    };
  }

  const ledger = {
    schemaVersion: "3.3",
    modelKind: "providerNativeResourceDependencyLedger",
    boundary: "실제 CSP 리소스, 실제 상위 리소스 내부 구성요소, 독립 Terraform 연결 객체만 node로 둔다. 참조값과 runtime 검증 조건은 edge에 둔다.",
    relationDirection: {
      provision: "선행 리소스 → 그 식별자를 사용해 생성되는 리소스",
      reference: "참조되는 실제 리소스 → 그 리소스를 참조하는 리소스",
      contains: "상위 실제 리소스 → 그 안에 구성되는 실제 구성요소",
      materialize: "사용자가 생성한 상위 리소스 → CSP가 자동으로 실체화하는 조회 가능한 실제 리소스",
      association: "먼저 존재해야 하는 실제 리소스 → 그 ID를 참조하는 Terraform 연결 객체",
      policy: "보안 정책 리소스 → 정책이 적용되는 리소스",
      health: "Health Check 또는 진입 리소스 → health 결과를 사용하는 backend 리소스",
      traffic: "실행 중 요청을 보내는 리소스 → 다음 요청 수신 리소스"
    },
    omissionPolicy: "선택 경로에서 생성·참조되거나 요청·복구에 직접 참여하는 CSP 리소스는 provider 자동 생성이어도 node로 보존한다. 독립 조회되는 자동 생성 객체는 providerResource/providerCreated, 상위 API payload의 중첩 블록은 providerComponent/configureInsideOwner로 구분한다. public IP·private IP·port·path처럼 독립 수명주기가 없는 값은 edge.referenceValues에 둔다. 명시 리소스로 대체되어 사용하지 않는 VPC default security group 같은 기본 객체와 고객이 직접 선택·조회할 수 없는 CSP fabric 내부 구현은 제외한다. Resource Group·location 같은 반복 배포 문맥은 visualPriority=context로 접을 수만 있고 원장에서는 삭제하지 않는다.",
    evidenceArtifacts: {
      "artifact:evaluation/dependency_audit/multi-provider-sample-app-postgres-e1-adjudication-20260814.json": {
        stage: "runtimeVerified", sha256: "1cdcd5dd1d5a3fab57c572bc3e6c289177b799534caf0275cba8599294fdb25a"
      },
      "artifact:evaluation/dependency_audit/multi-provider-sample-app-postgres-e2-adjudication-20260815.json": {
        stage: "runtimeVerified", sha256: "39fe08777195003331a18480a75696d8c57e5dcd8aa390bec5b518d3aceb0856"
      },
      "artifact:evaluation/dependency_audit/multi-provider-sample-app-postgres-e3-adjudication-20260815.json": {
        stage: "runtimeVerified", sha256: "ad4b5660709b98b818d30d1ef71fe55eda55b539109087d242717cbd3b3e1425"
      }
    },
    comparisonRoles,
    resourcePlanExamples: Object.fromEntries(Object.entries(planMappings).map(([provider, refs]) => [provider, resourcePlanExample(provider, refs)])),
    providers: {
      aws: finishProvider("AWS", awsNodes, awsEdges),
      azure: finishProvider("Azure", azureNodes, azureEdges),
      gcp: finishProvider("GCP", gcpNodes, gcpEdges)
    }
  };

  root.EASYDEP_PROVIDER_DEPENDENCIES = ledger;
  if (typeof module !== "undefined" && module.exports) module.exports = ledger;
})(globalThis);
