/*
 * EasyDep가 목표로 하는 Docker-on-Linux-VM 배포 경로를 기록한다.
 * - 생성한 배포 번들로 앱 image를 한 번 build해 선택한 CSP의 Registry에 push
 * - App VM 또는 VM Group이 고정 digest로 앱 image를 pull해 실행
 * - 별도 State VM이 docker.io/library/postgres:17-bookworm을 pull해 실행
 * - endpoint 결정에 따라 진입 없음, 내부 직접, 공개 직접, 공개 LB를 구분
 */
(function exposeApplicationDeploymentBindings(root) {
  "use strict";

  const node = (id, displayName, nodeClass, views, description, sourceRefs, options = {}) => ({
    id, displayName, nodeClass, views, description, sourceRefs,
    conditional: Boolean(options.conditional), preparedBy: options.preparedBy,
    requiredWhen: options.requiredWhen, missingEffect: options.missingEffect,
    boundary: options.boundary
  });
  const edge = (id, source, target, label, relationType, views, validationGate, sourceRefs, options = {}) => ({
    id, source, target, label, relationType, views, validationGate, sourceRefs,
    conditional: Boolean(options.conditional), boundary: options.boundary || "",
    appComputeMode: options.appComputeMode || ""
  });

  const graph = {
    schemaVersion: "ApplicationDeploymentBindingGraph/v2",
    modelKind: "applicationDeploymentBindingGraph",
    scope: "EasyDep 배포 번들로 앱 image를 한 번 build해 provider-native Registry에 push하고, App VM 또는 VM Group이 digest로 pull해 실행하며, 선택 시 공식 PostgreSQL image를 별도 State VM에서 실행하는 경로",
    exclusions: ["third-party application registries", "external or managed databases", "secret managers", "external APIs", "Kubernetes", "serverless"],
    directionPolicy: {
      prerequisite: "먼저 준비되어야 하는 요소 → 그 준비 결과를 사용하는 요소",
      configuration: "설정값의 근거 → 그 값을 소비하는 배포 또는 앱 구성",
      runtimeTraffic: "실행 중 실제 요청·데이터가 이동하는 방향",
      healthSignal: "상태 확인 요청 또는 그 결과가 전달되는 방향"
    },
    views: {
      build:{label:"앱 image build·Registry 전달",summary:"EasyDep 배포 번들로 앱 image를 한 번 build해 선택한 CSP의 Registry에 push하고, App VM 또는 VM Group은 고정 digest를 pull해 시작합니다.",boundary:"앱 image 저장소는 AWS ECR Repository, Azure Container Registry, GCP Artifact Registry Repository 중 선택한 CSP의 실제 리소스로 생성합니다. build 실행 위치 자체는 CSP 리소스가 아닙니다."},
      requestInternal:{label:"내부 직접 요청",summary:"VPC·VNet 내부 호출자가 사설 NIC를 통해 단일 VM 또는 선택된 App VM으로 요청합니다.",boundary:"공인 주소와 Load Balancer를 만들지 않습니다."},
      requestDirect:{label:"공개 HTTP 직접 요청",summary:"인터넷 호출자가 고정 공인 주소의 HTTP endpoint를 거쳐 앱 port로 요청합니다.",boundary:"HTTPS/TLS는 지원 범위 밖입니다. Load Balancer가 없으므로 VM 장애 시 자동 트래픽 전환을 주장하지 않습니다."},
      requestLoadBalanced:{label:"공개 LB 요청",summary:"인터넷 호출자가 공개 Load Balancer를 거쳐 정상 backend VM의 앱 port로 요청합니다.",boundary:"LB는 공개 진입 방식이며 VM 그룹과 동일한 결정이 아닙니다. 다만 현재 GCP 실현은 MIG backend를 사용합니다."},
      postgres:{label:"PostgreSQL image pull·영속 실행",summary:"별도 State VM이 공식 postgres:17-bookworm을 pull하고 Disk mount를 data path에 연결해 실행합니다.",boundary:"외부 DB를 제공받거나 managed DB를 생성하는 경로는 포함하지 않습니다."},
      health:{label:"앱 상태·복구",summary:"readiness는 LB backend 선택에, liveness는 관리형 VM 교체 판단에 연결합니다.",boundary:"직접 요청만 선택했다면 LB readiness 경로는 활성화하지 않습니다."}
    },
    nodes: [
      node("build.applicationBundle","EasyDep 배포 번들 (앱·IaC·스크립트)","applicationContract",["build"],"생성 앱과 Dockerfile, Terraform, doctor·plan·deploy·status·destroy 스크립트를 함께 전달하는 최종 산출물입니다.",["docs/logical-deployment-topology-decisions.md"],{preparedBy:"EasyDep 구현·배포 생성 단계가 만듭니다.",requiredWhen:"모든 Docker-on-VM 앱 배포에서 필요합니다.",missingEffect:"사용자가 재현 가능한 방식으로 인프라와 앱을 배포할 수 없습니다.",boundary:"사용자에게 전달되는 실행 가능한 배포 입력입니다."}),
      node("external.gradleBuildImage","Docker Hub Gradle build image","externalDependency",["build"],"application/Dockerfile의 build stage가 사용하는 docker.io/library/gradle:8.14.2-jdk21 image입니다.",["app/core/orchestration/adapters/vm_delivery.py"],{preparedBy:"Docker 공식 image 저장소가 게시합니다.",requiredWhen:"Spring Boot 앱 image를 build할 때 필요합니다.",missingEffect:"Gradle build stage를 시작할 수 없습니다.",boundary:"EasyDep가 현재 생성하는 Dockerfile의 정확한 외부 base image입니다."}),
      node("external.temurinRuntimeImage","Docker Hub Eclipse Temurin runtime image","externalDependency",["build"],"application/Dockerfile의 runtime stage가 사용하는 docker.io/library/eclipse-temurin:21-jre image입니다.",["app/core/orchestration/adapters/vm_delivery.py"],{preparedBy:"Docker 공식 image 저장소가 게시합니다.",requiredWhen:"Spring Boot runtime image를 build할 때 필요합니다.",missingEffect:"최종 runtime stage를 만들 수 없습니다.",boundary:"EasyDep가 현재 생성하는 Dockerfile의 정확한 외부 base image입니다."}),
      node("build.easyDepImageBuild","EasyDep 1회 docker build","runtimeConfiguration",["build"],"배포 번들의 Dockerfile로 앱 image를 한 번 build하고 선택한 provider-native Registry에 push하는 배포 단계입니다.",["docs/logical-deployment-topology-decisions.md"],{preparedBy:"배포 번들의 deploy 절차가 수행합니다.",requiredWhen:"모든 앱 VM 또는 VM Group 배포에서 필요합니다.",missingEffect:"Registry에 App VM이 pull할 앱 image가 없습니다.",boundary:"CSP 리소스가 아니라 사용자 실행 환경 또는 CI에서 수행되는 배포 동작입니다."}),
      node("artifact.appImageDigest","Registry의 앱 image@sha256","runtimeConfiguration",["build"],"Registry push 결과로 확정된 immutable 앱 image digest입니다. 모든 App VM과 VM Group 시작 구성은 tag가 아니라 이 digest를 소비합니다.",["docs/logical-deployment-topology-decisions.md"],{preparedBy:"EasyDep build·push 단계가 Registry 응답에서 기록합니다.",requiredWhen:"동일한 앱 산출물을 여러 VM에 재현해서 실행할 때 필요합니다.",missingEffect:"재시도 시 mutable tag가 다른 image를 가리킬 수 있습니다.",boundary:"생성된 CSP Registry에 저장된 앱 image의 불변 참조입니다."}),
      node("runtime.outboundPath","VM의 Registry·PostgreSQL pull outbound","runtimeConfiguration",["build","postgres"],"앱 Registry와 Docker Hub PostgreSQL image에 도달하기 위한 DNS·route·NAT 또는 VM 공인 주소 경로입니다.",["app/core/orchestration/adapters/vm_delivery.py"],{preparedBy:"ResourcePlan과 IaC가 실제 CSP route·NAT·공인 주소를 준비합니다.",requiredWhen:"VM이 앱 image 또는 postgres image를 pull할 때 필요합니다.",missingEffect:"Registry 또는 Docker Hub image pull 단계에서 실패합니다.",boundary:"여러 실제 네트워크 리소스가 함께 만드는 VM의 도달 가능 상태입니다."}),
      node("runtime.appImageReady","VM에 pull된 앱 image","runtimeConfiguration",["build"],"App VM의 Docker daemon이 Registry의 고정 digest 앱 image를 pull해 실행할 수 있는 상태입니다.",["docs/logical-deployment-topology-decisions.md"],{preparedBy:"VM guest 시작 절차가 pull identity와 outbound를 사용해 준비합니다.",requiredWhen:"Spring Boot 컨테이너 시작 직전에 필요합니다.",missingEffect:"docker run이 image not found 또는 authorization 오류로 실패합니다.",boundary:"App VM 내부 Docker image cache의 구체적인 상태입니다."}),
      node("runtime.environment","앱 runtime 환경설정","runtimeConfiguration",["build"],"앱 계약에서 확인한 일반 환경값과 port를 컨테이너 시작 명령에 반영한 결과입니다.",["app/core/orchestration/app_cloud_contracts.py"],{preparedBy:"EasyDep가 앱 계약을 읽어 guest 시작 구성에 반영합니다.",requiredWhen:"생성 앱이 외부 설정을 소비할 때 필요합니다.",missingEffect:"앱이 시작하지 않거나 잘못된 port·profile로 실행됩니다.",boundary:"VM·컨테이너 내부 실행 구성입니다."}),
      node("app.process","실행 중인 Spring Boot 앱","applicationRuntime",["build","requestInternal","requestDirect","requestLoadBalanced","postgres","health"],"build된 앱 image로 실행되는 Spring Boot 컨테이너 프로세스입니다.",["app/core/orchestration/app_cloud_contracts.py"],{preparedBy:"VM guest의 Docker 시작 구성이 실행합니다.",requiredWhen:"모든 앱 배포에서 필요합니다.",missingEffect:"클라우드 리소스가 있어도 업무 요청을 처리할 프로세스가 없습니다.",boundary:"VM 위에서 EasyDep가 실행하는 애플리케이션 workload입니다."}),
      node("caller.service","서비스 호출자","externalActor",["requestInternal","requestDirect","requestLoadBalanced"],"선택된 endpoint로 업무 요청을 보내는 내부 client 또는 인터넷 client입니다.",["docs/logical-deployment-topology-decisions.md"],{preparedBy:"EasyDep가 생성하는 대상이 아닙니다.",requiredWhen:"Endpoint 계약이 '진입 없음'이 아닐 때 필요합니다.",missingEffect:"요청 경로의 종단 검증을 수행할 수 없습니다.",boundary:"배포된 서비스를 실제로 호출하는 주체입니다."}),
      node("app.httpPort","앱 수신 port (server.port)","applicationContract",["requestInternal","requestDirect","requestLoadBalanced"],"Spring Boot가 수신하는 실제 port이며 NIC·방화벽·LB backend 설정과 일치해야 합니다.",["app/core/orchestration/app_cloud_contracts.py"],{preparedBy:"앱 설정과 계약 검사가 결정합니다.",requiredWhen:"HTTP endpoint를 제공할 때 필요합니다.",missingEffect:"연결 거부 또는 잘못된 backend 전달이 발생합니다.",boundary:"애플리케이션의 수신 계약입니다."}),
      node("app.readiness","Readiness endpoint","applicationContract",["health"],"LB가 새 요청을 보내도 되는지 판단하는 앱 endpoint입니다.",["docs/logical-deployment-topology-decisions.md"],{preparedBy:"EasyDep 생성 앱과 배포 설정이 path·port를 맞춥니다.",requiredWhen:"Load Balancer를 사용하는 경우 필요합니다.",missingEffect:"준비되지 않은 backend에 요청하거나 정상 backend를 제외할 수 있습니다.",boundary:"앱이 제공하는 상태 계약입니다."}),
      node("app.liveness","Liveness endpoint","applicationContract",["health"],"관리형 VM 그룹이 복구 불가능한 앱 정지를 판단하는 endpoint입니다.",["docs/logical-deployment-topology-decisions.md"],{preparedBy:"EasyDep 생성 앱과 VM 그룹 복구 설정이 연결합니다.",requiredWhen:"관리형 VM 자동 교체를 선택한 경우 필요합니다.",missingEffect:"멈춘 VM을 교체하지 못하거나 외부 장애로 정상 VM을 교체할 수 있습니다.",boundary:"앱이 제공하는 생존 상태 계약입니다."}),
      node("external.postgresImage","Docker Hub 공식 postgres:17-bookworm","externalDependency",["postgres"],"현재 지원 정책이 고정한 docker.io/library/postgres:17-bookworm image입니다.",["app/core/orchestration/adapters/cloud_design.py"],{preparedBy:"Docker 공식 image 저장소가 게시하고 EasyDep가 정확한 image reference를 사용합니다.",requiredWhen:"별도 self-hosted PostgreSQL State VM을 선택할 때 필요합니다.",missingEffect:"State VM에서 PostgreSQL 컨테이너를 시작할 수 없습니다.",boundary:"현재 실제로 pull하는 하나의 외부 image입니다."}),
      node("runtime.postgresImageReady","State VM에 pull된 postgres image","runtimeConfiguration",["postgres"],"State VM Docker daemon에서 postgres:17-bookworm을 실행할 수 있는 상태입니다.",["evaluation/dependency_audit/inter_vm_postgres_intervention.py"],{preparedBy:"State VM이 명시적 outbound로 docker pull합니다.",requiredWhen:"PostgreSQL 컨테이너 시작 전에 필요합니다.",missingEffect:"PostgreSQL docker run이 실패합니다.",boundary:"State VM 내부 Docker image cache 상태입니다."}),
      node("runtime.blockDevice","Linux가 식별한 data Disk","runtimeConfiguration",["postgres"],"provider attachment 결과를 stable device identity로 식별한 Linux block device입니다.",["app/core/orchestration/adapters/vm_delivery.py"],{preparedBy:"CSP attachment와 guest 초기화가 함께 준비합니다.",requiredWhen:"PostgreSQL 데이터를 독립 Disk에 저장할 때 필요합니다.",missingEffect:"format·mount할 올바른 Disk를 찾을 수 없습니다.",boundary:"State VM 운영체제에서 관찰하는 장치입니다."}),
      node("runtime.filesystem","data Disk filesystem","runtimeConfiguration",["postgres"],"기존 filesystem을 보존하고 새 Disk에만 조건부로 생성하는 파일시스템입니다.",["app/core/orchestration/adapters/vm_delivery.py"],{preparedBy:"guest 초기화가 blkid 후 조건부 mkfs로 준비합니다.",requiredWhen:"Disk를 디렉터리에 mount할 때 필요합니다.",missingEffect:"mount가 실패하거나 기존 데이터를 지울 수 있습니다.",boundary:"Disk 안의 guest 데이터 구조입니다."}),
      node("runtime.guestMount","UUID mount + fstab","runtimeConfiguration",["postgres"],"filesystem을 State VM 디렉터리에 mount하고 재부팅 후에도 유지하는 설정입니다.",["app/core/orchestration/adapters/vm_delivery.py"],{preparedBy:"guest 초기화가 UUID 기반으로 구성합니다.",requiredWhen:"PostgreSQL 데이터를 재부팅 뒤에도 같은 경로에서 사용할 때 필요합니다.",missingEffect:"재부팅 후 빈 boot Disk 경로에 데이터를 쓸 수 있습니다.",boundary:"Linux guest mount 설정입니다."}),
      node("runtime.containerBind","PostgreSQL Docker bind","runtimeConfiguration",["postgres"],"mount 아래 전용 child 디렉터리를 PostgreSQL data path에 연결합니다.",["app/core/orchestration/adapters/vm_delivery.py"],{preparedBy:"State VM의 docker run 구성이 준비합니다.",requiredWhen:"PostgreSQL 데이터를 독립 Disk에 보존할 때 필요합니다.",missingEffect:"컨테이너 교체 시 데이터가 사라질 수 있습니다.",boundary:"Docker volume 연결 설정입니다."}),
      node("state.postgresDataPath","/var/lib/postgresql/data","applicationContract",["postgres"],"현재 공식 PostgreSQL image가 실제 DB 파일을 기록하는 컨테이너 경로입니다.",["app/core/orchestration/provider_deployment.py"],{preparedBy:"지원 runtime registry가 고정합니다.",requiredWhen:"postgres:17-bookworm을 영속 Disk와 함께 실행할 때 필요합니다.",missingEffect:"영속 mount가 아닌 컨테이너 layer에 데이터가 기록됩니다.",boundary:"현재 지원 PostgreSQL workload의 구체적인 data path 계약입니다."}),
      node("state.postgresProcess","실행 중인 PostgreSQL 17","applicationRuntime",["postgres"],"별도 State VM에서 postgres:17-bookworm으로 실행되는 PostgreSQL 서버입니다.",["app/core/orchestration/provider_deployment.py"],{preparedBy:"State VM guest 구성이 image와 data path를 준비한 뒤 실행합니다.",requiredWhen:"설계에 PostgreSQL database workload가 있을 때 필요합니다.",missingEffect:"앱의 migration과 SQL 업무 기능이 실패합니다.",boundary:"EasyDep가 별도 State VM에 직접 실행하는 workload입니다."}),
      node("runtime.dbEndpoint","State VM PostgreSQL endpoint","runtimeConfiguration",["postgres"],"State VM 사설 주소, 5432와 database 이름으로 만든 접속 주소입니다.",["app/core/orchestration/provider_deployment.py"],{preparedBy:"State VM 배포 결과와 앱 runtime binding이 만듭니다.",requiredWhen:"App VM과 State VM을 분리할 때 필요합니다.",missingEffect:"앱이 PostgreSQL 서버를 찾지 못합니다.",boundary:"EasyDep가 만든 State VM에서 도출한 구체적인 endpoint 설정입니다."}),
      node("runtime.datasourceConfig","Spring datasource 설정","runtimeConfiguration",["postgres"],"State endpoint와 PostgreSQL engine에 맞춘 spring.datasource.* 설정입니다.",["app/core/orchestration/app_cloud_contracts.py"],{preparedBy:"EasyDep 앱 계약 binding이 생성합니다.",requiredWhen:"앱이 PostgreSQL DataSource를 사용할 때 필요합니다.",missingEffect:"DataSource 생성 또는 인증이 실패합니다.",boundary:"Spring Boot runtime 설정입니다."}),
      node("app.datasource","앱 PostgreSQL DataSource","applicationContract",["postgres"],"Repository와 migration이 PostgreSQL 연결을 얻는 앱 내부 계약입니다.",["app/core/orchestration/app_cloud_contracts.py"],{preparedBy:"Spring Boot가 datasource 설정으로 생성합니다.",requiredWhen:"생성 앱이 PostgreSQL을 사용할 때 필요합니다.",missingEffect:"migration과 업무 query가 실패합니다.",boundary:"애플리케이션 내부 DB 연결 인터페이스입니다."})
    ],
    edges: [
      edge("binding-build-input","build.applicationBundle","build.easyDepImageBuild","배포 번들로 1회 build","prerequisite",["build"],"배포 번들의 Dockerfile로 앱 image build가 성공한다.",["docs/logical-deployment-topology-decisions.md"]),
      edge("binding-gradle-base","external.gradleBuildImage","build.easyDepImageBuild","Gradle build stage base","prerequisite",["build"],"docker.io/library/gradle:8.14.2-jdk21을 정확히 조회한다.",["app/core/orchestration/adapters/vm_delivery.py"]),
      edge("binding-temurin-base","external.temurinRuntimeImage","build.easyDepImageBuild","Temurin runtime stage base","prerequisite",["build"],"docker.io/library/eclipse-temurin:21-jre를 정확히 조회한다.",["app/core/orchestration/adapters/vm_delivery.py"]),
      edge("binding-build-digest","build.easyDepImageBuild","artifact.appImageDigest","push 결과 digest 기록","prerequisite",["build"],"Registry가 반환한 sha256 digest를 배포 checkpoint에 기록한다.",["docs/logical-deployment-topology-decisions.md"]),
      edge("binding-digest-pull","artifact.appImageDigest","runtime.appImageReady","고정 digest pull","prerequisite",["build"],"App VM에 계획과 동일한 digest의 image가 존재한다.",["docs/logical-deployment-topology-decisions.md"]),
      edge("binding-pull-outbound","runtime.outboundPath","runtime.appImageReady","Registry 도달","prerequisite",["build"],"App VM이 선택한 CSP Registry endpoint에 도달한다.",["docs/logical-deployment-topology-decisions.md"]),
      edge("binding-image-start","runtime.appImageReady","app.process","pull image로 앱 시작","prerequisite",["build"],"앱 컨테이너가 고정 digest와 restart policy로 실행된다.",["app/core/orchestration/adapters/vm_delivery.py"]),
      edge("binding-environment","runtime.environment","app.process","runtime 설정 주입","configuration",["build"],"계약 port와 필수 일반 환경값으로 앱이 시작한다.",["app/core/orchestration/app_cloud_contracts.py"]),
      edge("binding-http-process","app.httpPort","app.process","server.port에서 수신","configuration",["requestInternal","requestDirect","requestLoadBalanced"],"실제 업무 요청이 설정 port에서 응답한다.",["app/core/orchestration/app_cloud_contracts.py"]),
      edge("binding-process-readiness","app.process","app.readiness","readiness 제공","configuration",["health"],"readiness endpoint가 2xx와 실패 상태를 구분한다.",["docs/logical-deployment-topology-decisions.md"]),
      edge("binding-process-liveness","app.process","app.liveness","liveness 제공","configuration",["health"],"liveness가 외부 DB 장애를 직접 실패로 만들지 않는다.",["docs/logical-deployment-topology-decisions.md"]),
      edge("binding-postgres-source","external.postgresImage","runtime.outboundPath","Docker Hub image 조회","prerequisite",["postgres"],"State VM에서 postgres:17-bookworm pull에 성공한다.",["evaluation/dependency_audit/inter_vm_postgres_intervention.py"]),
      edge("binding-postgres-pull","runtime.outboundPath","runtime.postgresImageReady","postgres image pull","prerequisite",["postgres"],"State VM에 선택 image ID가 존재한다.",["evaluation/dependency_audit/inter_vm_postgres_intervention.py"]),
      edge("binding-block-filesystem","runtime.blockDevice","runtime.filesystem","filesystem 확인·조건부 생성","prerequisite",["postgres"],"기존 filesystem에는 mkfs를 다시 실행하지 않는다.",["app/core/orchestration/adapters/vm_delivery.py"]),
      edge("binding-filesystem-mount","runtime.filesystem","runtime.guestMount","UUID로 mount","prerequisite",["postgres"],"State VM 재부팅 뒤 같은 filesystem이 mount된다.",["app/core/orchestration/adapters/vm_delivery.py"]),
      edge("binding-mount-bind","runtime.guestMount","runtime.containerBind","전용 child path bind","configuration",["postgres"],"filesystem root가 아니라 전용 child를 연결한다.",["app/core/orchestration/adapters/vm_delivery.py"]),
      edge("binding-bind-postgres","runtime.containerBind","state.postgresDataPath","PostgreSQL data path 연결","configuration",["postgres"],"bind target이 /var/lib/postgresql/data와 일치한다.",["app/core/orchestration/provider_deployment.py"]),
      edge("binding-postgres-image-start","runtime.postgresImageReady","state.postgresProcess","pull image로 PostgreSQL 시작","prerequisite",["postgres"],"postgres:17-bookworm 컨테이너가 실행된다.",["app/core/orchestration/provider_deployment.py"]),
      edge("binding-postgres-data-start","state.postgresDataPath","state.postgresProcess","data path 준비 후 시작","prerequisite",["postgres"],"기존 데이터를 보존한 채 PostgreSQL이 시작한다.",["app/core/orchestration/adapters/vm_delivery.py"]),
      edge("binding-app-query","app.process","app.datasource","DataSource 사용","runtimeTraffic",["postgres"],"migration과 업무 query가 성공한다.",["app/core/orchestration/app_cloud_contracts.py"]),
      edge("binding-datasource-config","runtime.datasourceConfig","app.datasource","Spring 설정으로 생성","configuration",["postgres"],"JDBC URL·driver·dialect가 PostgreSQL과 일치한다.",["app/core/orchestration/app_cloud_contracts.py"]),
      edge("binding-endpoint-config","runtime.dbEndpoint","runtime.datasourceConfig","State endpoint 설정","configuration",["postgres"],"host·5432·database가 실제 State VM과 일치한다.",["app/core/orchestration/provider_deployment.py"]),
      edge("binding-db-endpoint","app.datasource","runtime.dbEndpoint","PostgreSQL로 연결","runtimeTraffic",["postgres"],"App VM에서 State VM 5432 연결과 인증에 성공한다.",["evaluation/dependency_audit/inter_vm_postgres_intervention.py"]),
      edge("binding-db-process","runtime.dbEndpoint","state.postgresProcess","SQL 요청 전달","runtimeTraffic",["postgres"],"실제 PostgreSQL에서 create/write/read가 성공한다.",["evaluation/dependency_audit/dependency-experiment-results-20260814.md"])
    ],
    providers: {
      aws:{identityLabel:"EC2 IAM Role + AmazonEC2ContainerRegistryReadOnly",nodeRefs:[
        ["aws.ecrRepository",["build"]],
        ["aws.registryPullPolicyAttachment",["build"]],
        ["aws.ec2",["build","requestInternal","requestDirect","requestLoadBalanced","postgres"]],
        ["aws.asgInstance",["build","requestInternal","requestLoadBalanced"]],
        ["aws.primaryEni",["requestInternal","requestDirect","requestLoadBalanced"]],
        ["aws.securityGroup",["requestInternal","requestDirect","requestLoadBalanced"]],
        ["aws.internetGateway",["requestDirect","requestLoadBalanced"]],
        ["aws.eip",["build","requestDirect"]],
        ["aws.albPublicAddress",["requestLoadBalanced"]],
        ["aws.alb",["requestLoadBalanced"]],
        ["aws.listener",["requestLoadBalanced"]],
        ["aws.targetGroup",["requestLoadBalanced","health"]],
        ["aws.autoScalingGroup",["health"]],
        ["aws.ebs",["postgres"]],
        ["aws.volumeAttachment",["postgres"]],
        ["aws.natGateway",["build","postgres"]]
      ],edgeRefs:{requestInternal:["aws.traffic-eni-ec2"],requestDirect:["aws.traffic-igw-eip","aws.traffic-eip-eni","aws.traffic-eni-ec2"],requestLoadBalanced:["aws.traffic-igw-alb-address","aws.traffic-alb-address-alb","aws.traffic-alb-listener","aws.traffic-listener-target","aws.traffic-target-eni","aws.traffic-eni-ec2","aws.traffic-eni-asg-instance"],postgres:["aws.attach-ebs","aws.attach-ec2"],health:["aws.target-asg"]},edges:[
        edge("aws.registry-publish","aws.ecrRepository","artifact.appImageDigest","ECR push target·digest 게시","prerequisite",["build"],"ECR Repository에 앱 image와 동일한 digest가 존재한다.",["https://docs.aws.amazon.com/AmazonECR/latest/userguide/docker-push-ecr-image.html"]),
        edge("aws.pull-ec2","aws.ec2","runtime.appImageReady","EC2가 digest pull","prerequisite",["build"],"단일 App EC2가 ECR digest를 pull한다.",["https://docs.aws.amazon.com/AmazonECR/latest/userguide/docker-pull-ecr-image.html"],{appComputeMode:"single"}),
        edge("aws.pull-asg","aws.asgInstance","runtime.appImageReady","ASG VM이 digest pull","prerequisite",["build"],"각 App ASG instance가 같은 ECR digest를 pull한다.",["https://docs.aws.amazon.com/AmazonECR/latest/userguide/docker-pull-ecr-image.html"],{appComputeMode:"group"}),
        edge("aws.pull-authorize","aws.registryPullPolicyAttachment","runtime.appImageReady","ECR pull 권한","configuration",["build"],"EC2 instance profile의 IAM Role이 ECR read 권한을 가진다.",["https://docs.aws.amazon.com/AmazonECR/latest/userguide/ECR_on_EC2.html"]),
        edge("aws.build-eip","aws.eip","runtime.outboundPath","공인 주소 outbound","prerequisite",["build"],"직접 공개 EC2에서 ECR endpoint에 도달한다.",["docs/logical-deployment-topology-decisions.md"]),
        edge("aws.build-nat","aws.natGateway","runtime.outboundPath","NAT outbound","prerequisite",["build","postgres"],"사설 VM에서 Registry 또는 Docker Hub에 도달한다.",["app/core/orchestration/adapters/vm_delivery.py"]),
        edge("aws.internal-entry","caller.service","aws.primaryEni","사설 IP 요청","runtimeTraffic",["requestInternal"],"VPC 내부 client가 업무 API에 성공한다.",["docs/logical-deployment-topology-decisions.md"]),
        edge("aws.direct-entry","caller.service","aws.internetGateway","인터넷 직접 요청","runtimeTraffic",["requestDirect"],"EIP endpoint에서 업무 API에 성공한다.",["docs/logical-deployment-topology-decisions.md"]),
        edge("aws.lb-entry","caller.service","aws.internetGateway","인터넷 LB 요청","runtimeTraffic",["requestLoadBalanced"],"ALB endpoint에서 업무 API에 성공한다.",["docs/logical-deployment-topology-decisions.md"]),
        edge("aws.port-ec2","aws.ec2","app.httpPort","EC2 guest port 전달","runtimeTraffic",["requestInternal","requestDirect","requestLoadBalanced"],"server.port에서 응답한다.",["app/core/orchestration/app_cloud_contracts.py"]),
        edge("aws.port-asg","aws.asgInstance","app.httpPort","ASG guest port 전달","runtimeTraffic",["requestInternal","requestLoadBalanced"],"server.port에서 응답한다.",["app/core/orchestration/app_cloud_contracts.py"]),
        edge("aws.sg-port","aws.securityGroup","app.httpPort","요청 source·port 허용","configuration",["requestInternal","requestDirect","requestLoadBalanced"],"선택 endpoint source만 앱 port에 도달한다.",["docs/logical-deployment-topology-decisions.md"]),
        edge("aws.attachment-device","aws.volumeAttachment","runtime.blockDevice","EBS guest device 노출","prerequisite",["postgres"],"Volume ID로 device를 식별한다.",["app/core/orchestration/adapters/vm_delivery.py"]),
        edge("aws.state-host","aws.ec2","state.postgresProcess","State EC2에서 실행","prerequisite",["postgres"],"State allocation이 EC2와 일치한다.",["app/core/orchestration/provider_deployment.py"]),
        edge("aws.readiness","aws.targetGroup","app.readiness","Target Group health 요청","healthSignal",["health"],"실패 target이 트래픽에서 제외된다.",["docs/logical-deployment-topology-decisions.md"]),
        edge("aws.liveness","app.liveness","aws.autoScalingGroup","교체 판단","healthSignal",["health"],"지속 실패 instance만 교체된다.",["docs/logical-deployment-topology-decisions.md"])
      ]},
      azure:{identityLabel:"User-assigned Managed Identity + AcrPull",nodeRefs:[
        ["azure.containerRegistry",["build"]],
        ["azure.registryPullRoleAssignment",["build"]],
        ["azure.vm",["build","requestInternal","requestDirect","requestLoadBalanced","postgres"]],
        ["azure.nic",["requestInternal","requestDirect","requestLoadBalanced"]],
        ["azure.vmssInstance",["build","requestInternal","requestLoadBalanced","health"]],
        ["azure.vmssNic",["requestLoadBalanced"]],
        ["azure.nsg",["requestInternal","requestDirect","requestLoadBalanced"]],
        ["azure.publicIp",["build","requestDirect","requestLoadBalanced"]],
        ["azure.agwFrontendIp",["requestLoadBalanced"]],
        ["azure.agwListener",["requestLoadBalanced"]],
        ["azure.agwRoutingRule",["requestLoadBalanced"]],
        ["azure.agwBackendPool",["requestLoadBalanced"]],
        ["azure.agwBackendSettings",["requestLoadBalanced","health"]],
        ["azure.agwProbe",["health"]],
        ["azure.vmssHealthExtension",["health"]],
        ["azure.vmssRepairPolicy",["health"]],
        ["azure.disk",["postgres"]],
        ["azure.diskAttachment",["postgres"]],
        ["azure.natGateway",["build","postgres"]]
      ],edgeRefs:{requestInternal:["azure.traffic-nic-vm"],requestDirect:["azure.traffic-public-ip-nic","azure.traffic-nic-vm"],requestLoadBalanced:["azure.traffic-public-ip-frontend","azure.traffic-frontend-listener","azure.traffic-listener-rule","azure.traffic-rule-pool","azure.traffic-pool-settings","azure.traffic-settings-vmss-nic","azure.traffic-vmss-nic-instance"],postgres:["azure.attach-disk","azure.attach-vm"],health:["azure.probe-backend-settings","azure.health-repair-policy","azure.repair-instance"]},edges:[
        edge("azure.registry-publish","azure.containerRegistry","artifact.appImageDigest","ACR push target·digest 게시","prerequisite",["build"],"Container Registry에 앱 image와 동일한 digest가 존재한다.",["https://learn.microsoft.com/azure/container-registry/container-registry-get-started-azure-cli"]),
        edge("azure.pull-vm","azure.vm","runtime.appImageReady","VM이 digest pull","prerequisite",["build"],"단일 App VM이 ACR digest를 pull한다.",["https://learn.microsoft.com/azure/container-registry/container-registry-authentication-managed-identity"],{appComputeMode:"single"}),
        edge("azure.pull-vmss","azure.vmssInstance","runtime.appImageReady","VMSS VM이 digest pull","prerequisite",["build"],"각 App VMSS instance가 같은 ACR digest를 pull한다.",["https://learn.microsoft.com/azure/container-registry/container-registry-authentication-managed-identity"],{appComputeMode:"group"}),
        edge("azure.pull-authorize","azure.registryPullRoleAssignment","runtime.appImageReady","AcrPull 권한","configuration",["build"],"VM identity가 Registry 범위의 AcrPull role을 가진다.",["https://learn.microsoft.com/azure/container-registry/container-registry-authentication-managed-identity"]),
        edge("azure.build-public-ip","azure.publicIp","runtime.outboundPath","공인 주소 outbound","prerequisite",["build"],"직접 공개 VM에서 ACR endpoint에 도달한다.",["docs/logical-deployment-topology-decisions.md"]),
        edge("azure.build-nat","azure.natGateway","runtime.outboundPath","NAT outbound","prerequisite",["build","postgres"],"사설 VM에서 Registry 또는 Docker Hub에 도달한다.",["docs/provider-native-dependency-graphs.html"]),
        edge("azure.internal-entry","caller.service","azure.nic","사설 IP 요청","runtimeTraffic",["requestInternal"],"VNet 내부 client가 업무 API에 성공한다.",["docs/logical-deployment-topology-decisions.md"]),
        edge("azure.direct-entry","caller.service","azure.publicIp","인터넷 직접 요청","runtimeTraffic",["requestDirect"],"Public IP endpoint에서 업무 API에 성공한다.",["docs/logical-deployment-topology-decisions.md"]),
        edge("azure.lb-entry","caller.service","azure.publicIp","인터넷 LB 요청","runtimeTraffic",["requestLoadBalanced"],"Application Gateway endpoint에서 업무 API에 성공한다.",["docs/logical-deployment-topology-decisions.md"]),
        edge("azure.lb-standalone","azure.agwBackendSettings","azure.nic","standalone VM backend","runtimeTraffic",["requestLoadBalanced"],"Gateway에서 standalone VM private IP로 요청이 도달한다.",["docs/logical-deployment-topology-decisions.md"],{conditional:true}),
        edge("azure.port-vm","azure.vm","app.httpPort","VM guest port 전달","runtimeTraffic",["requestInternal","requestDirect","requestLoadBalanced"],"server.port에서 응답한다.",["app/core/orchestration/app_cloud_contracts.py"]),
        edge("azure.port-vmss","azure.vmssInstance","app.httpPort","VMSS guest port 전달","runtimeTraffic",["requestInternal","requestLoadBalanced"],"server.port에서 응답한다.",["app/core/orchestration/app_cloud_contracts.py"]),
        edge("azure.nsg-port","azure.nsg","app.httpPort","요청 source·port 허용","configuration",["requestInternal","requestDirect","requestLoadBalanced"],"선택 endpoint source만 앱 port에 도달한다.",["docs/logical-deployment-topology-decisions.md"]),
        edge("azure.attachment-device","azure.diskAttachment","runtime.blockDevice","Managed Disk guest device 노출","prerequisite",["postgres"],"LUN으로 대상 Disk를 식별한다.",["app/core/orchestration/adapters/vm_delivery.py"]),
        edge("azure.state-host","azure.vm","state.postgresProcess","State VM에서 실행","prerequisite",["postgres"],"State allocation이 VM과 일치한다.",["app/core/orchestration/provider_deployment.py"]),
        edge("azure.readiness","azure.agwProbe","app.readiness","Gateway probe 요청","healthSignal",["health"],"실패 backend가 트래픽에서 제외된다.",["docs/logical-deployment-topology-decisions.md"]),
        edge("azure.liveness","azure.vmssHealthExtension","app.liveness","Application Health 관찰","healthSignal",["health"],"repair policy가 지속 실패 VM만 교체한다.",["docs/logical-deployment-topology-decisions.md"])
      ]},
      gcp:{identityLabel:"Service Account + Artifact Registry Reader",nodeRefs:[
        ["gcp.artifactRegistryRepository",["build"]],
        ["gcp.registryPullIamMember",["build"]],
        ["gcp.instance",["build","requestInternal","requestDirect","postgres"]],
        ["gcp.migInstance",["build","requestInternal","requestLoadBalanced","health"]],
        ["gcp.networkInterface",["requestInternal","requestDirect","requestLoadBalanced"]],
        ["gcp.firewall",["requestInternal","requestDirect","requestLoadBalanced"]],
        ["gcp.regionalAddress",["build","requestDirect"]],
        ["gcp.accessConfig",["requestDirect"]],
        ["gcp.globalAddress",["requestLoadBalanced"]],
        ["gcp.forwardingRule",["requestLoadBalanced"]],
        ["gcp.httpProxy",["requestLoadBalanced"]],
        ["gcp.urlMap",["requestLoadBalanced"]],
        ["gcp.backendService",["requestLoadBalanced","health"]],
        ["gcp.migInstanceGroup",["requestLoadBalanced"]],
        ["gcp.healthCheck",["health"]],
        ["gcp.autoHealingPolicy",["health"]],
        ["gcp.disk",["postgres"]],
        ["gcp.diskAttachment",["postgres"]],
        ["gcp.nat",["build","postgres"]]
      ],edgeRefs:{requestInternal:["gcp.traffic-interface-instance"],requestDirect:["gcp.traffic-address-access-config","gcp.traffic-access-config-interface","gcp.traffic-interface-instance"],requestLoadBalanced:["gcp.traffic-address-forwarding","gcp.traffic-forwarding-proxy","gcp.traffic-proxy-urlmap","gcp.traffic-urlmap-backend","gcp.traffic-backend-instance-group","gcp.traffic-instance-group-interface","gcp.traffic-interface-mig-instance"],postgres:["gcp.attach-disk","gcp.attach-instance"],health:["gcp.health-backend","gcp.health-autohealing","gcp.autohealing-instance"]},edges:[
        edge("gcp.registry-publish","gcp.artifactRegistryRepository","artifact.appImageDigest","Artifact Registry push target·digest 게시","prerequisite",["build"],"Artifact Registry Repository에 앱 image와 동일한 digest가 존재한다.",["https://docs.cloud.google.com/artifact-registry/docs/docker/pushing-and-pulling"]),
        edge("gcp.pull-vm","gcp.instance","runtime.appImageReady","VM이 digest pull","prerequisite",["build"],"단일 App VM이 Artifact Registry digest를 pull한다.",["https://docs.cloud.google.com/artifact-registry/docs/access-control"],{appComputeMode:"single"}),
        edge("gcp.pull-mig","gcp.migInstance","runtime.appImageReady","MIG VM이 digest pull","prerequisite",["build"],"각 App MIG instance가 같은 Artifact Registry digest를 pull한다.",["https://docs.cloud.google.com/artifact-registry/docs/access-control"],{appComputeMode:"group"}),
        edge("gcp.pull-authorize","gcp.registryPullIamMember","runtime.appImageReady","Artifact Registry Reader 권한","configuration",["build"],"VM Service Account가 Repository 범위의 Reader role을 가진다.",["https://docs.cloud.google.com/artifact-registry/docs/access-control"]),
        edge("gcp.build-public-ip","gcp.regionalAddress","runtime.outboundPath","공인 주소 outbound","prerequisite",["build"],"직접 공개 VM에서 Artifact Registry endpoint에 도달한다.",["docs/logical-deployment-topology-decisions.md"]),
        edge("gcp.build-nat","gcp.nat","runtime.outboundPath","Cloud NAT outbound","prerequisite",["build","postgres"],"사설 VM에서 Registry 또는 Docker Hub에 도달한다.",["docs/provider-native-dependency-graphs.html"]),
        edge("gcp.internal-entry","caller.service","gcp.networkInterface","사설 IP 요청","runtimeTraffic",["requestInternal"],"VPC 내부 client가 업무 API에 성공한다.",["docs/logical-deployment-topology-decisions.md"]),
        edge("gcp.direct-entry","caller.service","gcp.regionalAddress","인터넷 직접 요청","runtimeTraffic",["requestDirect"],"External IP endpoint에서 업무 API에 성공한다.",["docs/logical-deployment-topology-decisions.md"]),
        edge("gcp.lb-entry","caller.service","gcp.globalAddress","인터넷 LB 요청","runtimeTraffic",["requestLoadBalanced"],"Global external Application LB endpoint에서 업무 API에 성공한다.",["docs/logical-deployment-topology-decisions.md"]),
        edge("gcp.port-vm","gcp.instance","app.httpPort","VM guest port 전달","runtimeTraffic",["requestInternal","requestDirect"],"server.port에서 응답한다.",["app/core/orchestration/app_cloud_contracts.py"]),
        edge("gcp.port-mig","gcp.migInstance","app.httpPort","MIG guest named port 전달","runtimeTraffic",["requestInternal","requestLoadBalanced"],"named port와 server.port가 일치한다.",["app/core/orchestration/app_cloud_contracts.py"]),
        edge("gcp.firewall-port","gcp.firewall","app.httpPort","요청 source·port 허용","configuration",["requestInternal","requestDirect","requestLoadBalanced"],"선택 endpoint source만 앱 port에 도달한다.",["docs/logical-deployment-topology-decisions.md"]),
        edge("gcp.attachment-device","gcp.diskAttachment","runtime.blockDevice","Persistent Disk guest device 노출","prerequisite",["postgres"],"stable by-id로 대상 Disk를 식별한다.",["app/core/orchestration/adapters/vm_delivery.py"]),
        edge("gcp.state-host","gcp.instance","state.postgresProcess","State VM에서 실행","prerequisite",["postgres"],"State allocation이 VM과 일치한다.",["app/core/orchestration/provider_deployment.py"]),
        edge("gcp.readiness","gcp.healthCheck","app.readiness","LB health 요청","healthSignal",["health"],"실패 backend가 트래픽에서 제외된다.",["docs/logical-deployment-topology-decisions.md"]),
        edge("gcp.liveness","app.liveness","gcp.autoHealingPolicy","autohealing 판단","healthSignal",["health"],"지속 실패 VM만 재생성된다.",["docs/logical-deployment-topology-decisions.md"])
      ]}
    },
    setupScenario:{id:"easydep-registry-deploy-postgres-v3",title:"provider-native Registry 앱 배포 + 선택적 PostgreSQL State VM",topology:"EasyDep 배포 번들 → 앱 image 1회 build·Registry push → App VM 또는 관리형 App VM 그룹 digest pull → 선택 시 별도 PostgreSQL State VM + 영속 Disk",boundary:"앱 image에는 선택 CSP의 ECR Repository, Container Registry 또는 Artifact Registry Repository 하나를 사용합니다. PostgreSQL은 Docker Hub 공식 docker.io/library/postgres:17-bookworm만 별도 State VM이 pull합니다. managed DB·Secret·외부 API는 지원 대상으로 넣지 않습니다.",bootSourceChains:{aws:["Amazon Machine Image (AMI)","EC2 또는 Launch Template","App/State EC2","Root EBS Volume"],azure:["Virtual Machine Image","VM 또는 VMSS model","App/State VM","OS Managed Disk"],gcp:["OS Image","Instance 또는 Instance Template","App/State VM","Boot Persistent Disk"]},phases:[
      {id:"decide",order:1,title:"배포 결정 고정",requires:["CSP","Endpoint 방식","State 필요 여부","배치·운영 수준"],actions:["진입 없음·내부 직접·공개 직접·공개 LB 중 하나를 선택","단일 App VM 또는 관리형 App VM Group을 선택"],produces:["정규화된 배포 결정"],gate:"LB와 VM Group을 같은 개념으로 취급하지 않는다.",bindingRefs:["caller.service","app.httpPort"]},
      {id:"bundle",order:2,title:"실행 가능한 배포 번들 준비",requires:["생성 앱 소스","application/Dockerfile","Terraform template"],actions:["doctor·plan·deploy·status·destroy entrypoint 생성","파일 checksum과 배포 ID 고정"],produces:["EasyDep 배포 번들"],gate:"새 환경에서 같은 입력으로 plan을 재현할 수 있다.",bindingRefs:["build.applicationBundle"]},
      {id:"foundation",order:3,title:"Network·Registry·pull identity 프로비저닝",requires:["정규화된 결정","VM image·사양"],actions:["Network·Subnet·보안 정책 생성","provider-native Registry 생성","App VM용 Registry read identity·binding 생성","선택한 공인 주소·LB·outbound 생성"],produces:["앱 image push target","Registry pull 권한","App compute 기반"],gate:"Registry와 최소 pull 권한이 실제 VM 또는 VM Group 시작 구성에 연결된다.",bindingRefs:["runtime.outboundPath"]},
      {id:"image",order:4,title:"앱 image 1회 build·push·digest 고정",requires:["EasyDep 배포 번들","Gradle·Temurin base image","앱 image push target"],actions:["앱 image를 한 번 build","선택 CSP Registry에 push","sha256 digest를 checkpoint에 기록"],produces:["Registry의 immutable 앱 image digest"],gate:"Registry 조회 결과와 checkpoint digest가 일치한다.",bindingRefs:["build.easyDepImageBuild","artifact.appImageDigest","binding-build-input","binding-build-digest"]},
      {id:"postgres",order:5,title:"선택적 PostgreSQL State 준비",requires:["설계의 database workload","docker.io/library/postgres:17-bookworm","State VM·Disk"],actions:["공식 image pull","Disk 식별·filesystem·mount·bind","PostgreSQL 시작"],produces:["영속 PostgreSQL 17","State endpoint"],gate:"create/write/read 후 State VM 재부팅에도 데이터가 남는다.",bindingRefs:["external.postgresImage","runtime.postgresImageReady","state.postgresProcess","binding-postgres-pull","binding-postgres-data-start"]},
      {id:"workload",order:6,title:"App VM digest pull·실행·연결",requires:["App compute 기반","Registry pull 권한","고정 앱 image digest","runtime 환경설정","선택 시 State endpoint"],actions:["App VM 또는 각 VM Group instance가 digest pull","앱 컨테이너 시작","server.port와 선택 시 datasource 반영"],produces:["같은 digest로 실행 중인 Spring Boot 앱"],gate:"실행 중 container image digest가 checkpoint와 같고 localhost API와 선택 시 migration·query가 성공한다.",bindingRefs:["runtime.appImageReady","app.process","runtime.datasourceConfig","binding-digest-pull","binding-pull-outbound","binding-image-start","binding-datasource-config"]},
      {id:"verify",order:7,title:"진입·복구 종단 검증",requires:["선택 endpoint","실행 중 앱","선택 health 정책"],actions:["내부 또는 외부 업무 요청","LB 선택 시 readiness 제외 시험","VM Group 선택 시 liveness 교체 시험"],produces:["endpoint별 runtime evidence"],gate:"선택한 endpoint 경로만 성공하고 주장한 복구 범위가 증거와 일치한다.",bindingRefs:["app.readiness","app.liveness","binding-http-process","binding-process-readiness","binding-process-liveness"]},
      {id:"deliver",order:8,title:"사용자에게 운영 정보 전달",requires:["검증된 배포","배포 checkpoint"],actions:["endpoint·resource ID·image digest 출력","status·destroy 사용법과 비용 경계 안내"],produces:["재현 가능한 운영 인수 정보"],gate:"사용자가 원본 생성 세션 없이 status 확인과 destroy를 수행할 수 있다.",bindingRefs:["build.applicationBundle","artifact.appImageDigest"]}
    ],variants:[["진입 없음","앱을 실행하지만 공유 요청 endpoint를 만들지 않습니다."],
        ["내부 직접","사설 NIC로 직접 요청하며 LB를 만들지 않습니다."],
        ["공개 직접","고정 공인 주소로 VM에 직접 요청하며 VM 장애 자동 전환은 없습니다."],
        ["공개 Load Balancer","LB를 사용하지만 AWS·Azure에서는 standalone backend도 가능하고, 현재 GCP 실현은 MIG를 사용합니다."],
        ["PostgreSQL 선택","별도 State VM이 공식 postgres:17-bookworm을 pull하고 영속 Disk에 저장합니다."]],resumePolicy:"각 gate의 배포 ID·Registry·image digest·VM·Disk ID를 checkpoint로 저장하고 실패 단계부터 재개합니다. 이미 push한 동일 digest는 다시 build하지 않고 기존 filesystem에는 mkfs를 다시 실행하지 않습니다."},
    targetChecklist:[
      ["Deployment bundle","모든 앱 배포","앱·Dockerfile·Terraform·doctor/plan/deploy/status/destroy","새 환경에서 plan 재현"],
      ["Application Registry","모든 앱 배포","선택 CSP의 ECR Repository·Container Registry·Artifact Registry Repository 중 하나","repository 생성·조회 성공"],
      ["App image build","모든 앱 배포","배포 번들 + 고정 Gradle·Temurin base → 1회 build → Registry push","Registry digest 확정"],
      ["Registry pull identity","모든 App VM","AWS IAM Role·Azure Managed Identity·GCP Service Account의 최소 read binding","VM credential로 pull 성공"],
      ["App image pull","모든 App VM","Registry image@sha256 → 단일 VM 또는 VM Group instance","실행 digest가 checkpoint와 일치"],
      ["Pull outbound","public endpoint가 없는 App·State VM","실제 NAT 또는 private Registry endpoint → Registry·Docker Hub","필요 host 조회 성공"],
      ["Internal request","내부 직접 endpoint","내부 caller → private NIC → VM → server.port","VPC/VNet 내부 업무 API 성공"],
      ["Public direct request","공개 직접 endpoint","인터넷 caller → 고정 공인 주소 → NIC → VM → server.port","공인 주소 업무 API 성공"],
      ["Load-balanced request","공개 LB endpoint","인터넷 caller → LB → backend → VM → server.port","LB endpoint 업무 API 성공"],
      ["LB backend choice","공개 LB endpoint","AWS·Azure standalone 또는 group backend, GCP 현재 MIG backend","선택 backend만 등록"],
      ["Readiness","LB 사용","앱 readiness → LB backend 포함·제외","실패 backend 트래픽 제외"],
      ["Liveness","관리형 VM 복구","앱 liveness → VM 그룹 교체 정책","지속 실패 VM만 교체"],
      ["PostgreSQL image","database workload","docker.io/library/postgres:17-bookworm → State VM","정확한 image pull·실행"],
      ["Disk attachment","PostgreSQL 영속성","State VM + Disk → provider Attachment → guest device","계획 Disk ID가 State VM에 부착"],
      ["Guest storage","PostgreSQL 영속성","device → filesystem → UUID mount → child bind → /var/lib/postgresql/data","재부팅 후 데이터 보존"],
      ["Datasource","PostgreSQL 사용 앱","State endpoint → spring.datasource.* → DataSource → PostgreSQL","migration·업무 query 성공"]
    ]
  };

  root.EASYDEP_APPLICATION_DEPLOYMENT_BINDINGS = graph;
  if (typeof module !== "undefined" && module.exports) module.exports = graph;
})(typeof globalThis !== "undefined" ? globalThis : this);
