"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const REPOSITORY_ROOT = path.resolve(__dirname, "..");
const DOCS_DIRECTORY = path.join(REPOSITORY_ROOT, "docs");
const data = require(path.join(
  DOCS_DIRECTORY,
  "assets",
  "provider-native-dependency-graphs",
  "provider-native-dependencies-v2.js"
));
const bindingData = require(path.join(
  DOCS_DIRECTORY,
  "assets",
  "provider-native-dependency-graphs",
  "application-deployment-bindings-v1.js"
));

const PROVIDERS = ["aws", "azure", "gcp"];
const NODE_KINDS = new Set(["network", "compute", "ingress", "state", "config", "security"]);
const ENTITY_CLASSES = new Set(["providerResource", "providerComponent", "association"]);
const NAME_AUTHORITIES = new Set(["providerResource", "providerComponent", "terraformAssociation"]);
const ALLOWED_NAME_AUTHORITY = {
  providerResource: new Set(["providerResource"]),
  providerComponent: new Set(["providerComponent"]),
  association: new Set(["terraformAssociation"])
};
const HANDLING = new Set(["create", "providerCreated", "configureInsideOwner", "referenceExisting"]);
const RELATIONS = new Set(["provision", "reference", "contains", "materialize", "association", "policy", "health", "traffic"]);
const SCOPES = new Set(["basic", "direct", "persistence", "managed"]);
const PHASES = new Set(["provisioning", "runtime"]);
const CONCERNS = new Set(["networkIngress", "persistence", "healthRecovery", "security"]);
const EVIDENCE_LEVELS = new Set(["officialOnly", "terraformPlanObserved", "provisioned", "runtimeVerified", "notMeasured"]);
const EVIDENCE_STATUSES = new Set(["documented", "passed", "failed", "notMeasured"]);
const VISUAL_PRIORITIES = new Set(["primary", "context"]);
const PLAN_ENTITY_CLASSES = new Set(["providerResource", "association", "runtimeElement", "externalActor"]);
const BINDING_NODE_CLASSES = new Set(["applicationContract", "applicationRuntime", "runtimeConfiguration", "externalDependency", "externalActor"]);
const BINDING_RELATIONS = new Set(["prerequisite", "configuration", "runtimeTraffic", "healthSignal", "authorization"]);
const ALLOWED_HANDLING = {
  providerResource: new Set(["create", "providerCreated", "referenceExisting"]),
  providerComponent: new Set(["configureInsideOwner", "providerCreated"]),
  association: new Set(["create"])
};
const FORBIDDEN_PSEUDO_IMPLEMENTATIONS = new Set([
  "ami_id", "source_image_reference", "boot_disk.initialize_params.image",
  "vm_tls_termination", "cloud_init_or_baked_image_configuration",
  "metadata_startup_script_or_baked_image_configuration", "guest_bootstrap_process",
  "docker_runtime", "package_or_container_registry", "host_port", "container_port",
  "guest_block_device", "filesystem", "mount_path", "container_data_path", "readiness_endpoint",
  "external_client"
]);
const FORBIDDEN_PSEUDO_DISPLAY_NAMES = new Set([
  "AMI Boot Source Reference", "Azure Platform Image Reference", "GCP Boot Image Reference",
  "External Client", "Docker Runtime", "Host Port", "Container Port", "Guest Block Device",
  "Filesystem", "Mount Path", "Container Data Path", "Readiness Endpoint", "DNS State"
]);
const VM_CREATION_CONTRACTS = {
  aws: {
    roles: {
      image: "aws.ami", placement: "aws.subnet", firewall: "aws.securityGroup",
      directVm: "aws.ec2", directNic: "aws.primaryEni", directBootDisk: "aws.rootVolume",
      groupDefinition: "aws.launchTemplate", managedGroup: "aws.autoScalingGroup",
      managedVm: "aws.asgInstance", managedNic: "aws.primaryEni", managedBootDisk: "aws.rootVolume"
    },
    directEdges: ["aws.ami-ec2", "aws.subnet-ec2", "aws.sg-ec2", "aws.ec2-primary-eni", "aws.ec2-root-volume"],
    managedEdges: ["aws.ami-template", "aws.sg-template", "aws.subnet-asg", "aws.template-asg",
      "aws.asg-instance", "aws.asg-instance-primary-eni", "aws.asg-instance-root-volume"]
  },
  azure: {
    roles: {
      image: "azure.image", placement: "azure.subnet", firewall: "azure.nsg",
      directVm: "azure.vm", directNic: "azure.nic", directBootDisk: "azure.osDisk",
      groupDefinition: "azure.vmss", managedGroup: "azure.vmss",
      managedVm: "azure.vmssInstance", managedNic: "azure.vmssNic", managedBootDisk: "azure.vmssOsDisk"
    },
    directEdges: ["azure.image-vm", "azure.subnet-nic", "azure.nsg-assoc-nic", "azure.nsg-assoc-nsg",
      "azure.nic-vm", "azure.vm-os-disk"],
    managedEdges: ["azure.image-vmss", "azure.subnet-vmss", "azure.vmss-instance",
      "azure.vmss-instance-nic", "azure.vmss-instance-os-disk", "azure.nsg-vmss-nic"]
  },
  gcp: {
    roles: {
      image: "gcp.image", placement: "gcp.subnetwork", firewall: "gcp.firewall",
      directVm: "gcp.instance", directNic: "gcp.networkInterface", directBootDisk: "gcp.bootDisk",
      groupDefinition: "gcp.instanceTemplate", managedGroup: "gcp.mig",
      managedVm: "gcp.migInstance", managedNic: "gcp.networkInterface", managedBootDisk: "gcp.bootDisk"
    },
    directEdges: ["gcp.image-instance", "gcp.subnet-instance", "gcp.firewall-instance",
      "gcp.instance-network-interface", "gcp.instance-boot-disk"],
    managedEdges: ["gcp.image-template", "gcp.subnet-template", "gcp.template-mig", "gcp.mig-instance",
      "gcp.mig-instance-network-interface", "gcp.mig-instance-boot-disk", "gcp.firewall-mig-instance"]
  }
};

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function assertBindingSourceRefs(refs, owner) {
  refs.forEach((ref) => {
    if (/^https?:\/\//.test(ref)) return;
    const match = String(ref).match(/^(.*?)(?::(\d+))?$/);
    const sourcePath = path.join(REPOSITORY_ROOT, match[1]);
    assert(fs.existsSync(sourcePath), `${owner}: 없는 sourceRef ${ref}`);
    if (match[2]) {
      const lineCount = fs.readFileSync(sourcePath, "utf8").split(/\r?\n/).length;
      assert(Number(match[2]) <= lineCount, `${owner}: 범위를 벗어난 sourceRef ${ref}`);
    }
  });
}

function assertNoDependencyCycle(providerName, nodes, edges) {
  const participatingRelations = new Set(["provision", "reference", "contains", "materialize", "association"]);
  const participating = new Set();
  const outgoing = new Map();
  const indegree = new Map();
  nodes.forEach((node) => {
    outgoing.set(node.id, []);
    indegree.set(node.id, 0);
  });
  edges.filter((edge) => participatingRelations.has(edge.relationType)).forEach((edge) => {
    participating.add(edge.source); participating.add(edge.target);
    outgoing.get(edge.source).push(edge.target);
    indegree.set(edge.target, indegree.get(edge.target) + 1);
  });
  const queue = [...participating].filter((id) => indegree.get(id) === 0);
  let visited = 0;
  while (queue.length) {
    const current = queue.shift(); visited += 1;
    outgoing.get(current).forEach((target) => {
      indegree.set(target, indegree.get(target) - 1);
      if (indegree.get(target) === 0) queue.push(target);
    });
  }
  assert(visited === participating.size, `${providerName}: 생성·참조 의존성 순환 발견`);
}

function validateVmCreationContracts() {
  Object.entries(VM_CREATION_CONTRACTS).forEach(([providerName, contract]) => {
    const provider = data.providers[providerName];
    const nodeIds = new Set(provider.nodes.map((node) => node.id));
    const edgesById = new Map(provider.edges.map((edge) => [edge.id, edge]));
    Object.entries(contract.roles).forEach(([role, nodeId]) => {
      assert(nodeIds.has(nodeId), `${providerName}: VM 생성 계약 역할 누락 ${role}=${nodeId}`);
    });
    [...contract.directEdges, ...contract.managedEdges].forEach((edgeId) => {
      assert(edgesById.has(edgeId), `${providerName}: VM 생성 계약 관계 누락 ${edgeId}`);
    });
    const imageNode = provider.nodes.find((node) => node.id === contract.roles.image);
    assert(imageNode.handling === "referenceExisting" && imageNode.entityClass === "providerResource",
      `${providerName}: 부팅 이미지 역할은 실제 기존 provider 리소스 참조여야 함`);
    assert(provider.edges.some((edge) => edge.source === contract.roles.image &&
      edge.target === contract.roles.directVm && edge.relationType === "reference"),
    `${providerName}: image → 직접 VM 부팅 참조 누락`);
    assert(provider.edges.some((edge) => edge.source === contract.roles.image &&
      edge.target === contract.roles.groupDefinition && edge.relationType === "reference"),
    `${providerName}: image → 관리형 VM 정의 부팅 참조 누락`);
    assert(provider.edges.some((edge) => edge.source === contract.roles.managedGroup &&
      edge.target === contract.roles.managedVm && edge.relationType === "materialize"),
    `${providerName}: 관리형 그룹 → child VM 자동 실체화 누락`);
    assert(provider.edges.some((edge) => edge.source === contract.roles.directVm &&
      edge.target === contract.roles.directBootDisk && edge.relationType === "materialize"),
    `${providerName}: 직접 VM → boot Disk 자동 실체화 누락`);
    assert(provider.edges.some((edge) => edge.source === contract.roles.managedVm &&
      edge.target === contract.roles.managedBootDisk && edge.relationType === "materialize"),
    `${providerName}: child VM → boot Disk 자동 실체화 누락`);
    for (const diskRole of ["directBootDisk", "managedBootDisk"]) {
      const diskNode = provider.nodes.find((node) => node.id === contract.roles[diskRole]);
      assert(diskNode.handling === "providerCreated",
        `${providerName}: ${diskRole}는 CSP 자동 생성 실제 Disk여야 함`);
    }
  });
}

function validateProvider(providerName, provider) {
  assert(provider && provider.label && Array.isArray(provider.nodes) && Array.isArray(provider.edges),
    `${providerName}: provider 원장 형식 오류`);
  const nodesById = new Map();
  const implementationNames = new Set();

  provider.nodes.forEach((node) => {
    assert(node.id && !nodesById.has(node.id), `${providerName}: 중복 또는 빈 node id ${node.id}`);
    assert(node.displayName && node.implementationName && node.easyExplanation,
      `${providerName}/${node.id}: 리소스 이름·IaC 이름·설명 누락`);
    assert(node.serviceName && node.apiResourceName && Array.isArray(node.terraformTypes) &&
      node.terraformTypes.length && node.terraformTypes.includes(node.implementationName),
      `${providerName}/${node.id}: 서비스·API·Terraform 명칭 계약 누락`);
    assert(NAME_AUTHORITIES.has(node.nameAuthority) &&
      ALLOWED_NAME_AUTHORITY[node.entityClass]?.has(node.nameAuthority) && node.nameAuthorityReason,
      `${providerName}/${node.id}: entityClass/nameAuthority 불일치 ${node.entityClass}/${node.nameAuthority}`);
    assert(["Amazon Machine Image (AMI)", "AmazonEC2ContainerRegistryReadOnly Policy"].includes(node.displayName) ||
      !/^(Amazon |AWS |Azure |Google Cloud |Compute Engine )/.test(node.displayName),
      `${providerName}/${node.id}: 표시명에 불필요한 provider·service 접두사 포함 ${node.displayName}`);
    assert(NODE_KINDS.has(node.kind), `${providerName}/${node.id}: 허용되지 않은 kind ${node.kind}`);
    assert(ENTITY_CLASSES.has(node.entityClass), `${providerName}/${node.id}: 실제 리소스 경계 밖 entityClass ${node.entityClass}`);
    assert(HANDLING.has(node.handling) && ALLOWED_HANDLING[node.entityClass].has(node.handling),
      `${providerName}/${node.id}: entityClass/handling 불일치 ${node.entityClass}/${node.handling}`);
    assert(!FORBIDDEN_PSEUDO_IMPLEMENTATIONS.has(node.implementationName),
      `${providerName}/${node.id}: 참조값·runtime 상태를 node로 사용함 ${node.implementationName}`);
    assert(!FORBIDDEN_PSEUDO_DISPLAY_NAMES.has(node.displayName),
      `${providerName}/${node.id}: 임의 참조값·runtime 이름을 node로 사용함 ${node.displayName}`);
    assert(Array.isArray(node.scopes) && node.scopes.length && node.scopes.every((scope) => SCOPES.has(scope)),
      `${providerName}/${node.id}: scope 오류`);
    assert(Array.isArray(node.officialDocs) && node.officialDocs.length && node.officialDocs.every((item) => item.title && item.url),
      `${providerName}/${node.id}: 공식 문서 근거 누락`);
    assert(node.entityClassReason && node.handlingReason && node.necessityReason,
      `${providerName}/${node.id}: 분류 이유 누락`);
    assert(!implementationNames.has(node.implementationName),
      `${providerName}: 동일 IaC 항목을 여러 node로 중복함 ${node.implementationName}`);
    implementationNames.add(node.implementationName);
    nodesById.set(node.id, node);
  });

  const edgeIds = new Set();
  const edgeKeys = new Set();
  const incident = new Set();
  provider.edges.forEach((edge) => {
    assert(edge.id && !edgeIds.has(edge.id), `${providerName}: 중복 또는 빈 edge id ${edge.id}`);
    assert(nodesById.has(edge.source) && nodesById.has(edge.target),
      `${providerName}/${edge.id}: 존재하지 않는 endpoint ${edge.source} -> ${edge.target}`);
    assert(edge.source !== edge.target, `${providerName}/${edge.id}: self-loop 금지`);
    assert(RELATIONS.has(edge.relationType), `${providerName}/${edge.id}: 허용되지 않은 관계 ${edge.relationType}`);
    assert(Array.isArray(edge.scopes) && edge.scopes.length && edge.scopes.every((scope) => SCOPES.has(scope)),
      `${providerName}/${edge.id}: scope 오류`);
    assert(edge.easyExplanation && edge.necessityReason && edge.condition && edge.validationGate,
      `${providerName}/${edge.id}: 관계 설명·조건·검증 gate 누락`);
    assert(Array.isArray(edge.referenceValues) && edge.referenceValues.length,
      `${providerName}/${edge.id}: 실제 참조값 누락`);
    assert(Array.isArray(edge.phases) && edge.phases.length && edge.phases.every((phase) => PHASES.has(phase)),
      `${providerName}/${edge.id}: phase 오류`);
    assert(Array.isArray(edge.concerns) && edge.concerns.every((concern) => CONCERNS.has(concern)),
      `${providerName}/${edge.id}: concern 오류`);
    if (edge.relationType === "traffic") {
      assert(edge.phases.includes("runtime") && edge.concerns.includes("networkIngress"),
        `${providerName}/${edge.id}: runtime request metadata 누락`);
    }
    assert(Array.isArray(edge.constraints), `${providerName}/${edge.id}: constraints 누락`);
    assert(Array.isArray(edge.officialDocs) && edge.officialDocs.length,
      `${providerName}/${edge.id}: 공식 문서 근거 누락`);
    assert(VISUAL_PRIORITIES.has(edge.visualPriority),
      `${providerName}/${edge.id}: visualPriority 오류`);
    assert(Array.isArray(edge.evidenceRefs) && edge.evidenceRefs.length &&
      edge.evidenceRefs.every((ref) => /^(official:|depkb:|research:|artifact:)/.test(ref)),
      `${providerName}/${edge.id}: 이름공간이 있는 evidenceRefs 누락`);
    assert(EVIDENCE_LEVELS.has(edge.evidenceAssessment?.level) &&
      EVIDENCE_STATUSES.has(edge.evidenceAssessment?.status),
      `${providerName}/${edge.id}: evidenceAssessment 오류`);
    assert(edge.sourceEntityClass === nodesById.get(edge.source).entityClass &&
      edge.targetEntityClass === nodesById.get(edge.target).entityClass,
      `${providerName}/${edge.id}: endpoint entityClass 불일치`);
    const key = `${edge.source}|${edge.target}|${edge.relationType}|${edge.label}`;
    assert(!edgeKeys.has(key), `${providerName}: 의미가 같은 edge 중복 ${key}`);
    if (edge.relationType === "association") {
      assert(nodesById.get(edge.source).entityClass === "providerResource",
        `${providerName}/${edge.id}: association 관계 출발점은 선행 CSP 리소스여야 함`);
      assert(nodesById.get(edge.target).entityClass === "association",
        `${providerName}/${edge.id}: association 관계 도착점은 Terraform 연결 객체여야 함`);
    }
    if (edge.relationType === "materialize") {
      assert(nodesById.get(edge.source).entityClass === "providerResource",
        `${providerName}/${edge.id}: 자동 실체화 출발점은 상위 CSP 리소스여야 함`);
      assert(nodesById.get(edge.target).handling === "providerCreated",
        `${providerName}/${edge.id}: 자동 실체화 도착점은 providerCreated여야 함`);
    }
    edgeIds.add(edge.id); edgeKeys.add(key); incident.add(edge.source); incident.add(edge.target);
  });

  const isolated = [...nodesById.keys()].filter((id) => !incident.has(id));
  assert(isolated.length === 0, `${providerName}: 고립 node 발견 ${isolated.join(", ")}`);

  provider.nodes.filter((node) => node.entityClass === "association").forEach((association) => {
    const prerequisites = provider.edges.filter((edge) => edge.target === association.id && edge.relationType === "association");
    assert(prerequisites.length >= 2 && new Set(prerequisites.map((edge) => edge.source)).size >= 2,
      `${providerName}/${association.id}: Terraform 연결 객체의 두 선행 리소스 누락`);
  });
  provider.nodes.filter((node) => node.entityClass === "providerComponent").forEach((component) => {
    assert(provider.edges.some((edge) => edge.target === component.id && ["contains", "materialize"].includes(edge.relationType)),
      `${providerName}/${component.id}: 상위 리소스 contains/materialize 관계 누락`);
  });
  provider.nodes.filter((node) => node.handling === "providerCreated").forEach((generated) => {
    assert(provider.edges.some((edge) => edge.target === generated.id && edge.relationType === "materialize"),
      `${providerName}/${generated.id}: CSP 자동 생성 리소스의 materialize 관계 누락`);
  });
  assertNoDependencyCycle(providerName, provider.nodes, provider.edges);
}

function validateComparisonRoles() {
  assert(Array.isArray(data.comparisonRoles) && data.comparisonRoles.length,
    "의미 역할 비교표 누락");
  const roleIds = new Set();
  data.comparisonRoles.forEach((role) => {
    assert(role.id && role.label && !roleIds.has(role.id), `비교 역할 ID 오류 ${role.id}`);
    roleIds.add(role.id);
    PROVIDERS.forEach((providerName) => {
      const refs = role.providers?.[providerName];
      const ids = new Set(data.providers[providerName].nodes.map((node) => node.id));
      assert(Array.isArray(refs) && refs.length && refs.every((ref) => ids.has(ref)),
        `${role.id}/${providerName}: 원장에 없는 역할 매핑`);
    });
  });
}

function validateEvidenceArtifacts() {
  const catalog = data.evidenceArtifacts || {};
  assert(Object.keys(catalog).length, "evidence artifact catalog 누락");
  Object.entries(catalog).forEach(([id, metadata]) => {
    assert(id.startsWith("artifact:") && /^[a-f0-9]{64}$/.test(metadata.sha256),
      `${id}: artifact ID 또는 SHA-256 오류`);
    const relative = id.slice("artifact:".length);
    const artifactPath = path.resolve(REPOSITORY_ROOT, relative);
    assert(fs.existsSync(artifactPath), `${id}: artifact 파일 누락`);
    const actual = crypto.createHash("sha256").update(fs.readFileSync(artifactPath)).digest("hex");
    assert(actual === metadata.sha256, `${id}: SHA-256 불일치`);
  });
  const artifactRefs = [
    ...PROVIDERS.flatMap((providerName) => data.providers[providerName].edges.flatMap((edge) => edge.evidenceRefs)),
    ...PROVIDERS.flatMap((providerName) => data.resourcePlanExamples[providerName].evidenceRefs)
  ].filter((ref) => ref.startsWith("artifact:"));
  artifactRefs.forEach((ref) => assert(catalog[ref.split("#")[0]], `${ref}: catalog에 없는 artifact 참조`));
}

function validateApplicationDeploymentBindings() {
  assert(bindingData.schemaVersion === "ApplicationDeploymentBindingGraph/v2",
    "지원하지 않는 앱-배포 바인딩 schemaVersion");
  assert(bindingData.modelKind === "applicationDeploymentBindingGraph",
    "지원하지 않는 앱-배포 바인딩 modelKind");
  const views = new Set(Object.keys(bindingData.views || {}));
  assert([...views].sort().join(",") === ["build", "health", "postgres", "requestDirect", "requestInternal", "requestLoadBalanced"].sort().join(","),
    "앱-배포 바인딩 view 목록 오류");
  const nodeIds = new Set();
  bindingData.nodes.forEach((node) => {
    assert(node.id && !nodeIds.has(node.id), `앱-배포 바인딩 node 중복 ${node.id}`);
    assert(node.displayName && node.description && BINDING_NODE_CLASSES.has(node.nodeClass),
      `앱-배포 바인딩 node 설명·분류 누락 ${node.id}`);
    assert(node.preparedBy && node.requiredWhen && node.missingEffect && node.boundary,
      `앱-배포 바인딩 node 준비 주체·조건·실패·경계 설명 누락 ${node.id}`);
    assert(node.views?.length && node.views.every((view) => views.has(view)),
      `앱-배포 바인딩 node view 오류 ${node.id}`);
    assert(node.sourceRefs?.length, `앱-배포 바인딩 node 근거 누락 ${node.id}`);
    assertBindingSourceRefs(node.sourceRefs, node.id);
    nodeIds.add(node.id);
  });
  const commonEdgeIds = new Set();
  bindingData.edges.forEach((edge) => {
    assert(edge.id && !commonEdgeIds.has(edge.id), `앱-배포 바인딩 공통 edge 중복 ${edge.id}`);
    assert(nodeIds.has(edge.source) && nodeIds.has(edge.target),
      `앱-배포 바인딩 공통 edge 끝점 오류 ${edge.id}`);
    assert(BINDING_RELATIONS.has(edge.relationType) &&
      edge.views?.length && edge.views.every((view) => views.has(view)),
    `앱-배포 바인딩 공통 edge 관계·view 오류 ${edge.id}`);
    assert(edge.validationGate && edge.sourceRefs?.length,
      `앱-배포 바인딩 공통 edge gate·근거 누락 ${edge.id}`);
    assertBindingSourceRefs(edge.sourceRefs, edge.id);
    edge.views.forEach((view) => assert(
      bindingData.nodes.find((node) => node.id === edge.source).views.includes(view) &&
      bindingData.nodes.find((node) => node.id === edge.target).views.includes(view),
      `앱-배포 바인딩 공통 edge/node view 불일치 ${edge.id}/${view}`
    ));
    commonEdgeIds.add(edge.id);
  });

  PROVIDERS.forEach((providerName) => {
    const binding = bindingData.providers?.[providerName];
    const ledger = data.providers[providerName];
    assert(binding?.identityLabel, `${providerName}: 앱-배포 바인딩 설정 누락`);
    const providerRefs = new Map();
    binding.nodeRefs.forEach(([ref, nodeViews]) => {
      assert(!providerRefs.has(ref) && ledger.nodes.some((node) => node.id === ref),
        `${providerName}: 앱-배포 바인딩 provider node ref 오류 ${ref}`);
      assert(nodeViews.length && nodeViews.every((view) => views.has(view)),
        `${providerName}: 앱-배포 바인딩 provider node view 오류 ${ref}`);
      providerRefs.set(ref, new Set(nodeViews));
    });
    Object.entries(binding.edgeRefs || {}).forEach(([view, refs]) => {
      assert(views.has(view), `${providerName}: 앱-배포 바인딩 provider edge view 오류 ${view}`);
      refs.forEach((ref) => {
        const edge = ledger.edges.find((item) => item.id === ref);
        assert(edge && providerRefs.get(edge.source)?.has(view) && providerRefs.get(edge.target)?.has(view),
          `${providerName}: 앱-배포 바인딩 provider edge ref 오류 ${ref}`);
      });
    });
    const providerEdgeIds = new Set();
    binding.edges.forEach((edge) => {
      assert(edge.id && !providerEdgeIds.has(edge.id) && !commonEdgeIds.has(edge.id),
        `${providerName}: 앱-배포 바인딩 edge 중복 ${edge.id}`);
      assert((nodeIds.has(edge.source) || providerRefs.has(edge.source)) &&
        (nodeIds.has(edge.target) || providerRefs.has(edge.target)),
      `${providerName}: 앱-배포 바인딩 edge 끝점 오류 ${edge.id}`);
      assert(BINDING_RELATIONS.has(edge.relationType) && edge.validationGate && edge.sourceRefs?.length,
      `${providerName}: 앱-배포 바인딩 edge 계약 누락 ${edge.id}`);
      assertBindingSourceRefs(edge.sourceRefs, edge.id);
      edge.views.forEach((view) => {
        assert(views.has(view), `${providerName}: 앱-배포 바인딩 edge view 오류 ${edge.id}/${view}`);
        assert((nodeIds.has(edge.source) ? bindingData.nodes.find((node) => node.id === edge.source).views.includes(view) : providerRefs.get(edge.source).has(view)) &&
          (nodeIds.has(edge.target) ? bindingData.nodes.find((node) => node.id === edge.target).views.includes(view) : providerRefs.get(edge.target).has(view)),
        `${providerName}: 앱-배포 바인딩 edge/node view 불일치 ${edge.id}/${view}`);
      });
      providerEdgeIds.add(edge.id);
    });
    views.forEach((view) => {
      const visibleNodes = new Set([
        ...bindingData.nodes.filter((node) => node.views.includes(view)).map((node) => node.id),
        ...[...providerRefs].filter(([, nodeViews]) => nodeViews.has(view)).map(([ref]) => ref)
      ]);
      const visibleEdges = [
        ...bindingData.edges.filter((edge) => edge.views.includes(view)),
        ...binding.edges.filter((edge) => edge.views.includes(view)),
        ...(binding.edgeRefs?.[view] || []).map((ref) => ledger.edges.find((edge) => edge.id === ref))
      ];
      const incident = new Set(visibleEdges.flatMap((edge) => [edge.source, edge.target]));
      assert([...visibleNodes].every((id) => incident.has(id)),
        `${providerName}/${view}: 앱-배포 바인딩 고립 node 존재`);
    });
  });
  const scenario = bindingData.setupScenario;
  assert(scenario?.id && scenario.title && scenario.topology && scenario.boundary && scenario.resumePolicy,
    "종단 셋업 시나리오 메타데이터 누락");
  assert(Object.keys(scenario.bootSourceChains || {}).sort().join(",") === PROVIDERS.slice().sort().join(",") &&
    PROVIDERS.every((providerName) => scenario.bootSourceChains[providerName].length === 4),
    "셋업 시나리오 CSP별 boot source chain 누락");
  assert(Array.isArray(scenario.phases) && scenario.phases.length === 8,
    "셋업 시나리오는 8개 gate 단계여야 함");
  const setupIds = new Set();
  const bindingRefs = new Set([...nodeIds, ...commonEdgeIds]);
  scenario.phases.forEach((phase, index) => {
    assert(phase.id && !setupIds.has(phase.id) && phase.order === index + 1 && phase.title,
      `셋업 시나리오 단계 ID·순서 오류 ${phase.id}`);
    setupIds.add(phase.id);
    for (const key of ["requires", "actions", "produces", "bindingRefs"]) {
      assert(Array.isArray(phase[key]) && phase[key].length, `셋업 시나리오 ${phase.id}/${key} 누락`);
    }
    assert(phase.gate && phase.bindingRefs.every((ref) => bindingRefs.has(ref)),
      `셋업 시나리오 ${phase.id}: gate 또는 앱-배포 원장 참조 오류`);
  });
  assert(Array.isArray(scenario.variants) && scenario.variants.length >= 4 &&
    scenario.variants.every((row) => row.length === 2), "셋업 시나리오 변형 설명 누락");
  assert(bindingData.targetChecklist.length >= 12 && bindingData.targetChecklist.every((row) =>
    row.length === 4 && row.every(Boolean)), "목표 앱-리소스 체크리스트 오류");
}

function validateHtmlAssets() {
  const htmlPath = path.join(DOCS_DIRECTORY, "provider-native-dependency-graphs.html");
  const html = fs.readFileSync(htmlPath, "utf8");
  assert(html.includes('<script src="https://cdn.jsdelivr.net/npm/cytoscape@3.33.4/dist/cytoscape.min.js"></script>'),
    "HTML의 고정 Cytoscape 3.33.4 CDN 경로 누락");
  assert(html.includes('<script src="./assets/provider-native-dependency-graphs/provider-native-dependencies-v2.js"></script>'),
    "HTML이 단일 외부 원장을 직접 읽지 않음");
  assert(html.includes('<script src="./assets/provider-native-dependency-graphs/application-deployment-bindings-v1.js"></script>'),
    "HTML이 앱-배포 바인딩 원장을 읽지 않음");
  for (const id of ["provider-tabs", "dependency-tabs", "endpoint-select", "compute-select",
    "database-select", "cy", "detail-tabs", "detail-content"]) {
    assert(html.includes(`id="${id}"`), `HTML 단일 화면 구성요소 누락 ${id}`);
  }
  for (const functionName of ["providerNodeLabel", "buildIacGraph", "buildBindingElements",
    "projectIacAuthoredElements", "pruneFunctionalGraph", "collapseFeatureBundle",
    "layoutPositions", "renderGraph", "renderDetailTabs"]) {
    assert(html.includes(`function ${functionName}(`), `HTML 그래프 함수 누락 ${functionName}`);
  }
  for (const removedId of ["setup-title", "setup-phases", "provider-comparison", "resource-delta-summary",
    "binding-delta-summary", "display-mode-tabs", "relation-tabs"]) {
    assert(!html.includes(`id="${removedId}"`), `HTML에 제거하기로 한 누적 화면이 남아 있음 ${removedId}`);
  }
  assert(html.includes('grid-template-columns:minmax(0,1fr) 390px') &&
    html.includes('class="graph-panel"') && html.includes('class="detail-panel"'),
  "그래프와 설명 탭의 단일 viewport 배치 누락");
  assert(html.includes('item.data.handling!=="providerCreated"'),
    "자동 생성 providerCreated 요소 생략 정책 누락");
  assert(html.includes('const GRAPH_LABELS={creation:"생성 의존성",functional:"기능 의존성"}') &&
    html.includes('if(state.graphMode!=="functional")return []') &&
    html.includes('functional?runtimeRelations.has(edge.relationType):!runtimeRelations.has(edge.relationType)'),
  "생성 의존성과 기능 의존성 분리 정책 누락");
  assert(html.includes('return `Terraform: ${node.implementationName}`') &&
    html.includes('return `${COMPONENT_OWNERS[node.id]||"상위 리소스"} / ${node.displayName}`') &&
    html.includes('[화면 묶음] Global external Application Load Balancer 경로'),
  "리소스·내부 구성·Terraform 객체·화면 묶음 이름 구분 누락");
  assert(html.includes('PARTS.registry[providerName]') &&
    html.includes('[화면 묶음] ECR 앱 image 경로') &&
    html.includes('[화면 묶음] Container Registry 앱 image 경로') &&
    html.includes('[화면 묶음] Artifact Registry 앱 image 경로') &&
    html.includes('bindings.setupScenario.phases.map'),
  "Registry 앱 image 경로 또는 사용자 배포 흐름 표시 누락");
  assert(!html.includes('id="runtime-toggle"') && !html.includes("state.showRuntime"),
    "폐기된 혼합 그래프 토글이 남아 있음");
  assert(!html.includes("./vendor/"), "HTML에 삭제된 local vendor 경로가 남아 있음");
  assert(!html.includes("provider-native dependency ledger snapshot: inlined"),
    "HTML에 편집 가능한 중복 원장이 남아 있음");
  const inlineScripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
  assert(inlineScripts.length === 1, "HTML 실행 스크립트 개수 오류");
  new Function(inlineScripts[0][1]);
}

function validateResourcePlanExamples() {
  PROVIDERS.forEach((providerName) => {
    const plan = data.resourcePlanExamples?.[providerName];
    assert(plan?.schemaVersion === "easydep-resource-plan/v1" && plan.provider === providerName,
      `${providerName}: ResourcePlan 예시 형식 오류`);
    assert(plan.deploymentTopology?.replicaCount >= 2 &&
      plan.deploymentTopology?.availabilityClaim === "none",
      `${providerName}: App topology 수량 또는 비가용성 주장 경계 누락`);
    assert(plan.outboundPolicy?.status === "resolved" &&
      ["prebakedImage", "nat", "privateRegistryEndpoint"].includes(plan.outboundPolicy?.strategy),
      `${providerName}: private VM outbound 결정 미해결`);
    assert(plan.artifactDeliveryPolicy?.status === "resolved" &&
      plan.artifactDeliveryPolicy?.strategy === "providerNativeRegistry" &&
      plan.artifactDeliveryPolicy?.buildMode === "easydepBuildOnce" &&
      plan.artifactDeliveryPolicy?.imageReference === "digest" &&
      JSON.stringify(plan.artifactDeliveryPolicy?.providerRefs) === JSON.stringify(plan.nodes.find((node) => node.id === "app-image-registry")?.providerRefs),
      `${providerName}: 앱 image Registry 전달 정책 오류`);
    const nodeIds = new Set();
    plan.nodes.forEach((node) => {
      assert(node.id && !nodeIds.has(node.id) && PLAN_ENTITY_CLASSES.has(node.entityClass),
        `${providerName}: ResourcePlan node 오류 ${node.id}`);
      nodeIds.add(node.id);
      (node.providerRefs || []).forEach((ref) => assert(
        data.providers[providerName].nodes.some((item) => item.id === ref),
        `${providerName}/${node.id}: 원장에 없는 providerRef ${ref}`
      ));
    });
    plan.edges.forEach((edge) => assert(nodeIds.has(edge.from) && nodeIds.has(edge.to),
      `${providerName}: ResourcePlan edge endpoint 오류 ${edge.id}`));
    const attachmentPrerequisites = new Set(plan.edges
      .filter((edge) => edge.to === "disk-attachment")
      .map((edge) => edge.from));
    assert(attachmentPrerequisites.has("state-vm") &&
      attachmentPrerequisites.has("persistent-disk") &&
      !plan.edges.some((edge) => edge.from === "disk-attachment"),
      `${providerName}: ResourcePlan attachment 선행 방향 오류`);
    assert(plan.nodes.some((node) => node.id === "app-compute-group" && node.replicas >= 2),
      `${providerName}: 관리형 App VM 그룹 누락`);
    assert(plan.nodes.some((node) => node.id === "state-vm" && node.replicas === 1) &&
      plan.nodes.some((node) => node.id === "persistent-disk"),
      `${providerName}: 별도 State VM·영속 Disk 누락`);
    assert(Array.isArray(plan.evidenceRefs) && plan.evidenceRefs.length >= 3 &&
      plan.evidenceRefs.every((ref) => ref.startsWith("artifact:")),
      `${providerName}: 계획 evidence artifact 누락`);
    assert(Array.isArray(plan.unresolved), `${providerName}: unresolved 배열 누락`);
  });
}

assert(data.schemaVersion === "3.3", "지원하지 않는 원장 schemaVersion");
assert(data.modelKind === "providerNativeResourceDependencyLedger", "지원하지 않는 원장 modelKind");
assert(Object.keys(data.providers).sort().join(",") === PROVIDERS.slice().sort().join(","), "CSP 목록 불일치");
PROVIDERS.forEach((providerName) => validateProvider(providerName, data.providers[providerName]));
validateComparisonRoles();
validateVmCreationContracts();
validateResourcePlanExamples();
validateEvidenceArtifacts();
validateApplicationDeploymentBindings();
validateHtmlAssets();

const aws = data.providers.aws;
const azure = data.providers.azure;
const gcp = data.providers.gcp;
const byImplementation = (provider) => new Map(provider.nodes.map((node) => [node.implementationName, node]));
const awsByImplementation = byImplementation(aws);
const azureByImplementation = byImplementation(azure);
const gcpByImplementation = byImplementation(gcp);

for (const id of ["aws.primaryEni", "aws.rootVolume", "aws.asgInstance", "aws.albEni", "aws.albPublicAddress", "aws.natEni",
  "aws.mainRouteTable", "aws.localRoute", "aws.defaultNetworkAcl", "aws.defaultSecurityGroup"]) {
  assert(aws.nodes.some((node) => node.id === id && node.handling === "providerCreated"),
    `AWS 자동 생성 리소스 누락 ${id}`);
}
assert(aws.edges.some((edge) => edge.id === "aws.traffic-eip-eni") &&
  aws.edges.some((edge) => edge.id === "aws.traffic-eni-ec2") &&
  !aws.edges.some((edge) => edge.id === "aws.traffic-eip-ec2"),
"AWS 직접 공개 요청은 EIP → Primary ENI → EC2로 표현해야 함");
assert(aws.edges.some((edge) => edge.source === "aws.autoScalingGroup" && edge.target === "aws.asgInstance" &&
  edge.relationType === "materialize"), "AWS ASG가 실제 EC2를 생성하는 관계 누락");

for (const id of ["azure.osDisk", "azure.vmssInstance", "azure.vmssNic", "azure.vmssOsDisk"]) {
  assert(azure.nodes.some((node) => node.id === id && node.handling === "providerCreated"),
    `Azure 자동 생성 리소스 누락 ${id}`);
}
for (const id of ["azure.agwGatewayIp", "azure.agwFrontendIp", "azure.agwFrontendPort",
  "azure.agwListener", "azure.agwBackendPool", "azure.agwBackendSettings", "azure.agwProbe", "azure.agwRoutingRule",
  "azure.vmssHealthExtension", "azure.vmssRepairPolicy"]) {
  assert(azure.nodes.some((node) => node.id === id && node.entityClass === "providerComponent"),
    `Azure 핵심 중첩 구성 누락 ${id}`);
}
assert(azureByImplementation.get("azurerm_nat_gateway_public_ip_association")?.entityClass === "association",
  "Azure NAT Gateway–Public IP Association 누락");
assert(azure.edges.some((edge) => edge.id === "azure.traffic-vmss-nic-instance") &&
  !azure.edges.some((edge) => edge.id === "azure.traffic-agw-vmss"),
"Azure 관리형 요청은 Application Gateway 내부 구성 → VMSS NIC → child VM으로 표현해야 함");

for (const id of ["gcp.bootDisk", "gcp.migInstance", "gcp.migInstanceGroup", "gcp.defaultRoute", "gcp.subnetRoute"]) {
  assert(gcp.nodes.some((node) => node.id === id && node.handling === "providerCreated"),
    `GCP 자동 생성 리소스 누락 ${id}`);
}
for (const id of ["gcp.networkInterface", "gcp.accessConfig", "gcp.autoHealingPolicy"]) {
  assert(gcp.nodes.some((node) => node.id === id && node.entityClass === "providerComponent"),
    `GCP 핵심 내부 구성 누락 ${id}`);
}
assert(gcp.edges.some((edge) => edge.source === "gcp.migInstanceGroup" && edge.target === "gcp.backendService"),
  "GCP Backend Service는 MIG manager가 아니라 underlying Instance Group을 참조해야 함");
assert(gcp.edges.some((edge) => edge.id === "gcp.traffic-interface-mig-instance") &&
  !gcp.edges.some((edge) => edge.id === "gcp.traffic-backend-mig"),
"GCP 관리형 요청은 Backend Service → Instance Group → NIC → managed VM으로 표현해야 함");

assert(awsByImplementation.get("data.aws_ami")?.displayName === "Amazon Machine Image (AMI)" &&
  awsByImplementation.get("data.aws_ami")?.handling === "referenceExisting",
"AWS AMI는 실제 기존 리소스 참조로 표현해야 함");
assert(awsByImplementation.get("aws_route")?.entityClass === "providerComponent",
  "AWS Route는 Route Table 내부 구성요소여야 함");
assert(aws.edges.some((edge) => edge.source === awsByImplementation.get("aws_vpc")?.id &&
  edge.target === awsByImplementation.get("aws_security_group")?.id &&
  edge.referenceValues.includes("vpc_id")),
"AWS Security Group은 생성한 VPC의 vpc_id를 명시적으로 참조해야 함");
assert(awsByImplementation.get("aws_vpc.default_security_group_id")?.handling === "providerCreated" &&
  aws.edges.some((edge) => edge.id === "aws.vpc-default-security-group" && edge.relationType === "materialize") &&
  aws.edges.some((edge) => edge.id === "aws.default-sg-primary-eni" && edge.target === "aws.primaryEni"),
"AWS 플랫폼 최소는 VPC가 자동 생성한 Default Security Group의 Primary ENI 적용을 보존해야 함");
for (const name of ["aws_route_table_association", "aws_eip_association", "aws_volume_attachment"]) {
  assert(awsByImplementation.get(name)?.entityClass === "association" &&
    awsByImplementation.get(name)?.nameAuthority === "terraformAssociation",
    `AWS Terraform 연결 객체 분류 누락 ${name}`);
}
for (const name of ["azurerm_network_interface_security_group_association", "azurerm_virtual_machine_data_disk_attachment", "azurerm_subnet_nat_gateway_association"]) {
  assert(azureByImplementation.get(name)?.entityClass === "association" &&
    azureByImplementation.get(name)?.nameAuthority === "terraformAssociation",
    `Azure Terraform 연결 객체 분류 누락 ${name}`);
}
assert(azureByImplementation.get("data.azurerm_platform_image")?.displayName === "Virtual Machine Image" &&
  azureByImplementation.get("data.azurerm_platform_image")?.handling === "referenceExisting",
  "Azure Virtual Machine Image는 selector 값과 구분되는 기존 provider 이미지 리소스로 표현해야 함");
assert(azureByImplementation.get("azurerm_linux_virtual_machine")?.displayName === "Virtual Machine" &&
  azureByImplementation.get("azurerm_linux_virtual_machine")?.apiResourceName ===
  "Microsoft.Compute/virtualMachines",
  "Azure Linux 구현 타입과 ARM Virtual Machine 리소스명을 구분해야 함");
assert(gcpByImplementation.get("data.google_compute_image")?.displayName === "OS Image" &&
  gcpByImplementation.get("data.google_compute_image")?.handling === "referenceExisting",
"GCP Image는 실제 기존 리소스 참조로 표현해야 함");
assert(gcpByImplementation.get("google_compute_attached_disk")?.entityClass === "association" &&
  gcpByImplementation.get("google_compute_attached_disk")?.nameAuthority === "terraformAssociation",
  "GCP Attached Disk는 Terraform 연결 객체여야 함");
assert(gcpByImplementation.get("google_compute_router_nat")?.entityClass === "providerComponent",
  "GCP Cloud NAT는 Cloud Router 내부 구성요소여야 함");
assert(gcpByImplementation.get("google_compute_network")?.displayName === "VPC Network" &&
  gcpByImplementation.get("google_compute_network")?.serviceName === "Virtual Private Cloud (VPC)" &&
  gcpByImplementation.get("google_compute_network")?.apiResourceName === "compute.v1.Network",
  "GCP VPC Network 명칭 계층 불일치");
assert(gcpByImplementation.get("google_compute_region_instance_group_manager")?.displayName ===
  "Regional Managed Instance Group" &&
  gcpByImplementation.get("google_compute_region_instance_group_manager")?.apiResourceName ===
  "compute.v1.InstanceGroupManager (regionInstanceGroupManagers)",
  "GCP 다중 Zone 경로는 Regional Managed Instance Group이어야 함");
assert(!gcpByImplementation.has("google_compute_instance_group_manager"),
  "다중 Zone 경로에 zonal Managed Instance Group을 사용하면 안 됨");
assert(gcp.edges.some((edge) => edge.source === "gcp.network" && edge.target === "gcp.firewall" &&
  edge.referenceValues.includes("network")), "GCP Firewall의 VPC Network 참조 누락");
for (const target of ["azure.nsg", "azure.publicIp", "azure.nic", "azure.vm", "azure.disk", "azure.applicationGateway", "azure.vmss"]) {
  assert(azure.edges.some((edge) => edge.source === "azure.resourceGroup" && edge.target === target &&
    edge.visualPriority === "context"), `Azure Resource Group 공통 문맥 누락 ${target}`);
}

assert(aws.edges.some((edge) => edge.constraints.some((constraint) =>
  constraint.kind === "distinctPlacementMinimum" && constraint.minimum === 2)),
"AWS ALB의 서로 다른 AZ Subnet 최소 2개 제약 누락");
assert(azure.edges.some((edge) => edge.constraints.some((constraint) =>
  constraint.kind === "dedicatedSubnet")),
"Azure Application Gateway 전용 Subnet 제약 누락");
for (const providerName of PROVIDERS) {
  const constraints = data.providers[providerName].edges.flatMap((edge) => edge.constraints);
  assert(constraints.some((constraint) => constraint.kind === "minimumActiveInstances"),
    `${providerName}: App 최소 active instance 제약 누락`);
  assert(constraints.some((constraint) => ["samePlacementDimension", "compatiblePlacement"].includes(constraint.kind)),
    `${providerName}: VM·Disk Region/Zone 호환 제약 누락`);
}

for (const providerName of PROVIDERS) {
  const provider = data.providers[providerName];
  assert(provider.edges.some((edge) => edge.relationType === "traffic" && edge.scopes.includes("direct")),
    `${providerName}: 단일 VM 런타임 요청 경로 누락`);
  assert(provider.edges.some((edge) => edge.relationType === "traffic" && edge.scopes.includes("managed")),
    `${providerName}: 관리형 진입 런타임 요청 경로 누락`);
  assert(provider.edges.some((edge) => edge.concerns.includes("persistence") && edge.scopes.includes("persistence")),
    `${providerName}: 영속 데이터 관계 분류 누락`);
  assert(provider.edges.some((edge) => edge.concerns.includes("healthRecovery") && edge.scopes.includes("managed")),
    `${providerName}: 장애 감지·복구 관계 분류 누락`);
  assert(provider.edges.some((edge) => edge.concerns.includes("security")),
    `${providerName}: 보안 정책 관계 분류 누락`);
}

for (const providerName of PROVIDERS) {
  const provider = data.providers[providerName];
  const relationCounts = Object.fromEntries([...RELATIONS].map((relation) => [
    relation, provider.edges.filter((edge) => edge.relationType === relation).length
  ]).filter(([, count]) => count));
  const classCounts = Object.fromEntries([...ENTITY_CLASSES].map((entityClass) => [
    entityClass, provider.nodes.filter((node) => node.entityClass === entityClass).length
  ]).filter(([, count]) => count));
  console.log(`${providerName}: ${provider.nodes.length} nodes, ${provider.edges.length} edges ` +
    `(classes=${JSON.stringify(classCounts)}, relations=${JSON.stringify(relationCounts)})`);
}
console.log("실제 CSP 리소스·구성요소·Terraform 연결 객체 원장 검증 통과");
console.log(`앱-배포 바인딩 원장 검증 통과: ${bindingData.nodes.length} 공통 요소, ` +
  `${bindingData.edges.length} 공통 관계, ${bindingData.targetChecklist.length}개 목표 체크리스트 항목, VM 생성 계약 역할 대조 완료`);
