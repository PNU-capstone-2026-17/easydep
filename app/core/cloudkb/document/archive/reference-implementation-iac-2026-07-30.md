# 참조 구현의 IaC 전수 수집 — 2026-07-30

> **불변 기록이다.** 갱신하지 않는다. 이 날 무엇을 보았는지의 스냅샷이고,
> 판정이 바뀌면 새 문서를 만든다(`CLAUDE.md` 문서 정책 2).

## 0. 무엇을 왜 모았나

우리 자원 의존 census(`graphkb/parsers/tumblebug_resources.json`)는 **cb-tumblebug의
코드에서만** 나왔다. 그래서 `validate:"required"`가 *클라우드의 필연*인지 *tumblebug의
설계 선택*인지 가릴 수 없었다. TOSCA 2.0 대조는 이 물음에 답하지 못했다 —
요구 정의의 `count_range` 기본값이 `[0, UNBOUNDED]`라서 커뮤니티 프로파일들이
**필연을 아예 표현하지 않기 때문이다**.

그래서 남이 실제로 배포한 인프라 코드를 정답지로 삼는다. 외부 참조 구현이
만드는 자원과 그 사이 의존을 전수로 모았다.

## 1. 방법

**수집** — `git clone --filter=blob:none --no-checkout --depth 1`로 파일 목록을 먼저
보고(GitHub API 한도를 쓰지 않는다), 대상만 얕은 클론으로 내용까지 받았다.
검증: `instana/robot-shop`의 파일 수가 GitHub 트리 API와 같은 171로 일치했다.

**의존의 관측 형태는 둘뿐이다.** 둘 다 코드에 적힌 참조다.

| 형태 | terraform | bicep |
|---|---|---|
| 명시 의존 | `depends_on = [...]` | `dependsOn: [...]` |
| 속성 참조 | `aws_vpc.main.id` | `vnet.id` · `parent: vnet` |

**순서·주석·문서로는 간선을 만들지 않았다** — 우리 census와 같은 규율
(`test_ordering_alone_never_makes_an_edge`).

**제외** — 모듈 저장소의 `examples/` · `tests/` · `.github/` · `docs/`는 모듈 자신의
자원이 아니라 사용 예시·CI 발판이므로 뺐다. 실제로 `boutique`에서 이 규칙이
`.github/terraform/`(terraform 상태 버킷 · CI 서비스 계정 · PR용 클러스터)을 걸러 냈다.

## 2. 대상 29건 — 커밋 고정

| 라벨 | 저장소 | 커밋 | 커밋일 | 자원 | 간선(타입쌍) |
|---|---|---|---|---:|---:|
| `aksstore` | Azure-Samples/aks-store-demo | `7ce10c5` | 2026-07-13 | 35 | 31 |
| `anthos` | GoogleCloudPlatform/bank-of-anthos | `1e40564` | 2026-07-13 | 44 | 38 |
| `boutique` | GoogleCloudPlatform/microservices-demo | `9a4616e` | 2026-07-13 | 2 | 0 |
| `iaasbase` | mspnp/iaas-baseline | `7f1cb4a` | 2025-10-15 | 95 | 38 |
| `retail` | aws-containers/retail-store-sample-app | `1a28474` | 2026-07-28 | 81 | 66 |
| `yelb` | mreferre/yelb | `55ee1b8` | 2025-01-23 | 39 | 40 |
| `m-aks` | Azure/terraform-azurerm-avm-res-containerservice-managedcluster | `663d90a` | 2026-07-29 | 6 | 1 |
| `m-awsalb` | terraform-aws-modules/terraform-aws-alb | `87f1c9c` | 2026-01-08 | 15 | 15 |
| `m-awsapprun` | terraform-aws-modules/terraform-aws-app-runner | `fd6601a` | 2025-10-21 | 16 | 8 |
| `m-awsddb` | terraform-aws-modules/terraform-aws-dynamodb-table | `45c9cb1` | 2026-01-08 | 12 | 2 |
| `m-awseks` | terraform-aws-modules/terraform-aws-eks | `7f8acb3` | 2026-07-28 | 55 | 43 |
| `m-awsiam` | terraform-aws-modules/terraform-aws-iam | `d6e381c` | 2026-07-28 | 21 | 16 |
| `m-awsos` | terraform-aws-modules/terraform-aws-opensearch | `067dfed` | 2026-07-10 | 17 | 17 |
| `m-awsrds` | terraform-aws-modules/terraform-aws-rds-aurora | `3cd7842` | 2026-07-10 | 20 | 23 |
| `m-awsredis` | cloudposse/terraform-aws-elasticache-redis | `911c1b1` | 2026-06-15 | 6 | 5 |
| `m-awssg` | terraform-aws-modules/terraform-aws-security-group | `58d8e89` | 2026-06-03 | 5 | 6 |
| `m-awsvpc` | terraform-aws-modules/terraform-aws-vpc | `3ffbd46` | 2026-04-02 | 86 | 32 |
| `m-azacr` | Azure/terraform-azurerm-avm-res-containerregistry-registry | `4b55628` | 2026-07-28 | 10 | 10 |
| `m-azaoai` | Azure/terraform-azurerm-avm-res-cognitiveservices-account | `012b906` | 2026-07-20 | 4 | 2 |
| `m-azdocdb` | Azure/terraform-azurerm-avm-res-documentdb-databaseaccount | `6e6fd2a` | 2026-07-20 | 17 | 25 |
| `m-azsb` | Azure/terraform-azurerm-avm-res-servicebus-namespace | `30ac0fb` | 2026-07-20 | 15 | 25 |
| `m-ekbaddon` | aws-ia/terraform-aws-eks-blueprints-addons | `d78fb74` | 2026-07-07 | 21 | 39 |
| `m-gcloud` | terraform-google-modules/terraform-google-gcloud | `bd2a705` | 2026-06-30 | 0 | 0 |
| `m-gcpiam` | terraform-google-modules/terraform-google-iam | `af3ee87` | 2026-03-04 | 46 | 0 |
| `m-gcpnat` | terraform-google-modules/terraform-google-cloud-nat | `8fcc03d` | 2026-02-23 | 2 | 0 |
| `m-gcpnet` | terraform-google-modules/terraform-google-network | `66532db` | 2026-06-10 | 45 | 17 |
| `m-gcppf` | terraform-google-modules/terraform-google-project-factory | `d3cd617` | 2026-06-24 | 53 | 24 |
| `m-gcpsql` | GoogleCloudPlatform/terraform-google-sql-db | `a77dac9` | 2026-07-23 | 26 | 11 |
| `m-gke` | terraform-google-modules/terraform-google-kubernetes-engine | `8091076` | 2026-07-27 | 45 | 15 |
| | | | **합** | **839** | |

앞 6건은 **앱과 인프라를 한 저장소에 가진 것**(+ 인프라 전용 1건),
`m-` 접두 23건은 그 6건이 실제로 쓰는 **레지스트리 모듈**이다. 벤더 샘플은
인프라를 자원이 아니라 모듈 수준으로 쓰기 때문에, 모듈을 읽지 않으면 자원 의존이
보이지 않는다.

## 3. 자원 타입 전수 — 어느 관심사에 속하나

**분류는 우리가 얹은 것이다.** IaC 자원 타입 이름과 우리 census의 자원 id는 다른
체계이므로, 대조하려면 짝을 지어야 한다. 그 짝이 틀릴 수 있다.

| 구분 | 인스턴스 | 비율 | 타입 수 |
|---|---:|---:|---:|
| 밖:미분류 | 269 | 32.1% | 158 |
| 밖:신원·권한 | 252 | 30.0% | 71 |
| 밖:관측 | 73 | 8.7% | 19 |
| 우리:securityGroup | 55 | 6.6% | 4 |
| 밖:경로 | 36 | 4.3% | 10 |
| 밖:컨테이너 실행 | 33 | 3.9% | 14 |
| 밖:메시징·캐시 | 16 | 1.9% | 13 |
| 밖:비밀·키 | 13 | 1.5% | 6 |
| 밖:정책·가드레일 | 12 | 1.4% | 4 |
| 우리:subnet | 11 | 1.3% | 2 |
| 우리:publicIp | 10 | 1.2% | 4 |
| 밖:이미지 저장소 | 9 | 1.1% | 3 |
| 밖:사설 연결 | 9 | 1.1% | 6 |
| 우리:vNet | 8 | 1.0% | 3 |
| 우리:nlb | 7 | 0.8% | 4 |
| 우리:objectStorage | 6 | 0.7% | 1 |
| 밖:게이트웨이·WAF | 5 | 0.6% | 4 |
| 우리:nodeGroup | 5 | 0.6% | 3 |
| 우리:k8sCluster | 4 | 0.5% | 2 |
| 우리:node | 3 | 0.4% | 2 |
| 우리:sqlDb | 3 | 0.4% | 2 |
| **우리 어휘 안 합** | **112** | **13.3%** | |
| **경계 밖 합** | **727** | **86.7%** | |

<details><summary><b>밖:미분류</b> — 타입 158종 / 인스턴스 269</summary>

- `aws_network_acl_rule` ×14
- `helm_release` ×9
- `kubernetes_namespace_v1` ×9
- `azurerm_private_endpoint` ×8
- `aws_appautoscaling_policy` ×7
- `aws_network_acl` ×7
- `Microsoft.Network/applicationSecurityGroups` ×6
- `aws_appautoscaling_target` ×6
- `azurerm_management_lock` ×5
- `aws_vpc_security_group_ingress_rule` ×5
- `aws_vpc_security_group_egress_rule` ×5
- `Microsoft.Resources/resourceGroups` ×4
- `azurerm_private_endpoint_application_security_group_association` ×4
- `aws_eks_addon` ×4
- `kubernetes_config_map_v1_data` ×4
- `google_network_connectivity_spoke` ×4
- `aws_dynamodb_table` ×3
- `aws_vpn_gateway_route_propagation` ×3
- `google_project_service` ×3
- `google_project_service_identity` ×3
- `google_workflows_workflow` ×3
- `google_sql_user` ×3
- `Microsoft.AlertsManagement/prometheusRuleGroups` ×2
- `google_cloudbuild_trigger` ×2
- `google_clouddeploy_target` ×2
- `aws_lb_target_group_attachment` ×2
- `aws_eks_access_entry` ×2
- `aws_db_subnet_group` ×2
- `google_compute_network_peering` ×2
- `google_compute_shared_vpc_service_project` ×2
- `google_service_networking_connection` ×2
- `google_compute_shared_vpc_host_project` ×2
- `google_project` ×2
- `google_cloud_scheduler_job` ×2
- `google_monitoring_alert_policy` ×2
- `google_sql_database` ×2
- `azurerm_resource_group` ×1
- `azurerm_user_assigned_identity` ×1
- `azurerm_cosmosdb_sql_role_definition` ×1
- `Microsoft.ContainerService/managedClusters` ×1
- `Microsoft.Monitor/accounts` ×1
- `google_clouddeploy_delivery_pipeline` ×1
- `google_storage_bucket_object` ×1
- `aws_lb_listener` ×1
- `aws_lb_listener_rule` ×1
- `aws_lb_listener_certificate` ×1
- `aws_lb_target_group` ×1
- `aws_lambda_permission` ×1
- `aws_wafv2_web_acl_association` ×1
- `aws_lb_trust_store` ×1
- `aws_lb_trust_store_revocation` ×1
- `aws_dynamodb_resource_policy` ×1
- `aws_ec2_tag` ×1
- `aws_eks_access_policy_association` ×1
- `aws_eks_identity_provider_config` ×1
- `aws_eks_capability` ×1
- `aws_placement_group` ×1
- `aws_eks_fargate_profile` ×1
- `aws_rolesanywhere_profile` ×1
- `aws_rolesanywhere_trust_anchor` ×1
- `aws_eks_pod_identity_association` ×1
- `aws_sqs_queue` ×1
- `aws_sqs_queue_policy` ×1
- `aws_autoscaling_group` ×1
- `aws_opensearch_domain` ×1
- `aws_opensearch_package_association` ×1
- `aws_opensearch_vpc_endpoint` ×1
- `aws_opensearch_domain_policy` ×1
- `aws_opensearch_domain_saml_options` ×1
- `aws_opensearch_outbound_connection` ×1
- `aws_opensearchserverless_collection_group` ×1
- `aws_opensearchserverless_collection` ×1
- `aws_opensearchserverless_access_policy` ×1
- `aws_opensearchserverless_lifecycle_policy` ×1
- `aws_rds_cluster_instance` ×1
- `aws_rds_cluster_endpoint` ×1
- `aws_rds_cluster_role_association` ×1
- `aws_rds_cluster_parameter_group` ×1
- `aws_db_parameter_group` ×1
- `aws_rds_cluster_activity_stream` ×1
- `aws_rds_shard_group` ×1
- `aws_dsql_cluster` ×1
- `aws_dsql_cluster_peering` ×1
- `aws_vpc_security_group_rules_exclusive` ×1
- `aws_vpc_security_group_vpc_association` ×1
- `aws_vpc_ipv4_cidr_block_association` ×1
- `aws_vpc_block_public_access_options` ×1
- `aws_vpc_block_public_access_exclusion` ×1
- `aws_vpc_dhcp_options` ×1
- `aws_vpc_dhcp_options_association` ×1
- `aws_redshift_subnet_group` ×1
- `aws_customer_gateway` ×1
- `aws_vpn_gateway` ×1
- `aws_vpn_gateway_attachment` ×1
- `aws_default_vpc` ×1
- `aws_default_security_group` ×1
- `aws_default_network_acl` ×1
- `aws_flow_log` ×1
- `aws_vpc_endpoint` ×1
- `azurerm_container_registry` ×1
- `azurerm_container_registry_token` ×1
- `azurerm_container_registry_token_password` ×1
- `azurerm_container_registry_scope_map` ×1
- `azurerm_cognitive_account_customer_managed_key` ×1
- `azurerm_cosmosdb_gremlin_database` ×1
- `azurerm_cosmosdb_gremlin_graph` ×1
- `azurerm_cosmosdb_mongo_database` ×1
- `azurerm_cosmosdb_mongo_collection` ×1
- `azurerm_cosmosdb_sql_database` ×1
- `azurerm_cosmosdb_sql_container` ×1
- `azurerm_cosmosdb_sql_function` ×1
- `azurerm_cosmosdb_sql_stored_procedure` ×1
- `azurerm_cosmosdb_sql_trigger` ×1
- `azurerm_cosmosdb_sql_dedicated_gateway` ×1
- `azurerm_cosmosdb_account` ×1
- `aws_autoscaling_lifecycle_hook` ×1
- `aws_autoscaling_group_tag` ×1
- `kubernetes_config_map_v1` ×1
- `aws_cloudformation_stack` ×1
- `google_compute_firewall_policy` ×1
- `google_compute_firewall_policy_association` ×1
- `google_compute_firewall_policy_rule` ×1
- `google_network_connectivity_hub` ×1
- `google_network_connectivity_group` ×1
- `google_compute_network_firewall_policy` ×1
- `google_compute_network_firewall_policy_association` ×1
- `google_compute_network_firewall_policy_rule` ×1
- `google_compute_network_firewall_policy_packet_mirroring_rule` ×1
- `google_compute_region_network_firewall_policy` ×1
- `google_compute_region_network_firewall_policy_association` ×1
- `google_compute_region_network_firewall_policy_rule` ×1
- `google_compute_global_forwarding_rule` ×1
- `google_compute_service_attachment` ×1
- `google_compute_route` ×1
- `google_vpc_access_connector` ×1
- `google_app_engine_application` ×1
- `google_billing_budget` ×1
- `google_resource_manager_lien` ×1
- `google_project_usage_export_bucket` ×1
- `google_access_context_manager_service_perimeter_resource` ×1
- `google_access_context_manager_service_perimeter_dry_run_resource` ×1
- `google_compute_project_default_network_tier` ×1
- `google_tags_tag_binding` ×1
- `google_compute_project_cloud_armor_tier` ×1
- `google_essential_contacts_contact` ×1
- `google_compute_project_metadata_item` ×1
- `google_service_usage_consumer_quota_override` ×1
- `google_monitoring_notification_channel` ×1
- `kubernetes_config_map` ×1
- `google_binary_authorization_attestor` ×1
- `google_container_analysis_note` ×1
- `google_kms_crypto_key` ×1
- `kubernetes_role_v1` ×1
- `kubernetes_role_binding_v1` ×1
- `kubernetes_cluster_role_v1` ×1
- `kubernetes_cluster_role_binding_v1` ×1
- `aws_alb_target_group` ×1
- `aws_alb_listener` ×1

</details>

<details><summary><b>밖:신원·권한</b> — 타입 71종 / 인스턴스 252</summary>

- `google_project_iam_member` ×42
- `aws_iam_role_policy_attachment` ×33
- `aws_iam_policy` ×20
- `aws_iam_role` ×18
- `azurerm_role_assignment` ×11
- `google_service_account` ×11
- `google_storage_bucket_iam_member` ×10
- `google_service_account_iam_member` ×8
- `Microsoft.Authorization/roleAssignments` ×8
- `Microsoft.Authorization/roleDefinitions` ×7
- `google_compute_subnetwork_iam_member` ×6
- `aws_iam_role_policy` ×5
- `azurerm_federated_identity_credential` ×3
- `Microsoft.ManagedIdentity/userAssignedIdentities` ×3
- `aws_iam_instance_profile` ×3
- `google_project_iam_binding` ×3
- `google_artifact_registry_repository_iam_member` ×2
- `aws_iam_openid_connect_provider` ×2
- `aws_iam_group_policy_attachment` ×2
- `google_organization_iam_member` ×2
- `google_project_iam_custom_role` ×2
- `google_compute_subnetwork_iam_binding` ×2
- `azurerm_cosmosdb_sql_role_assignment` ×1
- `aws_iam_account_alias` ×1
- `aws_iam_account_password_policy` ×1
- `aws_iam_group` ×1
- `aws_iam_group_membership` ×1
- `aws_iam_user` ×1
- `aws_iam_user_policy_attachment` ×1
- `aws_iam_user_policy` ×1
- `aws_iam_user_login_profile` ×1
- `aws_iam_access_key` ×1
- `aws_iam_user_ssh_key` ×1
- `google_artifact_registry_repository_iam_binding` ×1
- `google_project_iam_audit_config` ×1
- `google_bigquery_dataset_iam_binding` ×1
- `google_bigquery_dataset_iam_member` ×1
- `google_billing_account_iam_binding` ×1
- `google_billing_account_iam_member` ×1
- `google_cloud_run_service_iam_binding` ×1
- `google_cloud_run_service_iam_member` ×1
- `google_organization_iam_custom_role` ×1
- `google_dns_managed_zone_iam_binding` ×1
- `google_dns_managed_zone_iam_member` ×1
- `google_folder_iam_binding` ×1
- `google_folder_iam_member` ×1
- `google_kms_crypto_key_iam_binding` ×1
- `google_kms_crypto_key_iam_member` ×1
- `google_kms_key_ring_iam_binding` ×1
- `google_kms_key_ring_iam_member` ×1
- `google_organization_iam_binding` ×1
- `google_pubsub_subscription_iam_binding` ×1
- `google_pubsub_subscription_iam_member` ×1
- `google_pubsub_topic_iam_binding` ×1
- `google_pubsub_topic_iam_member` ×1
- `google_secret_manager_secret_iam_binding` ×1
- `google_secret_manager_secret_iam_member` ×1
- `google_secure_source_manager_instance_iam_binding` ×1
- `google_secure_source_manager_instance_iam_member` ×1
- `google_secure_source_manager_repository_iam_binding` ×1
- `google_secure_source_manager_repository_iam_member` ×1
- `google_service_account_iam_binding` ×1
- `google_storage_bucket_iam_binding` ×1
- `google_tags_tag_key_iam_binding` ×1
- `google_tags_tag_key_iam_member` ×1
- `google_tags_tag_value_iam_binding` ×1
- `google_tags_tag_value_iam_member` ×1
- `google_project_default_service_accounts` ×1
- `google_gke_hub_scope_iam_member` ×1
- `google_service_account_key` ×1
- `kubernetes_service_account_v1` ×1

</details>

<details><summary><b>밖:관측</b> — 타입 19종 / 인스턴스 73</summary>

- `Microsoft.Insights/diagnosticSettings` ×14
- `aws_cloudwatch_log_group` ×10
- `Microsoft.OperationalInsights/workspaces` ×7
- `Microsoft.Insights/dataCollectionRules` ×5
- `azurerm_monitor_diagnostic_setting` ×5
- `aws_cloudwatch_event_rule` ×4
- `aws_cloudwatch_event_target` ×4
- `aws_cloudwatch_metric_alarm` ×4
- `azurerm_monitor_data_collection_rule_association` ×3
- `Microsoft.Insights/dataCollectionRuleAssociations` ×3
- `azurerm_monitor_data_collection_rule` ×2
- `azurerm_monitor_alert_prometheus_rule_group` ×2
- `Microsoft.Insights/dataCollectionEndpoints` ×2
- `Microsoft.OperationsManagement/solutions` ×2
- `aws_cloudwatch_log_resource_policy` ×2
- `azurerm_log_analytics_workspace` ×1
- `azurerm_monitor_workspace` ×1
- `azurerm_monitor_data_collection_endpoint` ×1
- `aws_cloudwatch_log_stream` ×1

</details>

<details><summary><b>우리:securityGroup</b> — 타입 4종 / 인스턴스 55</summary>

- `aws_security_group` ×22
- `google_compute_firewall` ×17
- `aws_security_group_rule` ×9
- `Microsoft.Network/networkSecurityGroups` ×7

</details>

<details><summary><b>밖:경로</b> — 타입 10종 / 인스턴스 36</summary>

- `aws_route` ×10
- `aws_route_table_association` ×9
- `aws_route_table` ×7
- `google_compute_router` ×2
- `aws_internet_gateway` ×2
- `aws_nat_gateway` ×2
- `aws_route53_record` ×1
- `aws_egress_only_internet_gateway` ×1
- `aws_default_route_table` ×1
- `google_compute_router_nat` ×1

</details>

<details><summary><b>밖:컨테이너 실행</b> — 타입 14종 / 인스턴스 33</summary>

- `google_gke_hub_feature_membership` ×6
- `google_gke_hub_membership` ×4
- `aws_apprunner_service` ×3
- `aws_apprunner_vpc_ingress_connection` ×3
- `aws_apprunner_vpc_connector` ×3
- `google_gke_hub_feature` ×2
- `google_gke_hub_scope_rbac_role_binding` ×2
- `aws_ecs_cluster` ×2
- `aws_ecs_task_definition` ×2
- `aws_ecs_service` ×2
- `aws_apprunner_custom_domain_association` ×1
- `aws_apprunner_connection` ×1
- `aws_apprunner_auto_scaling_configuration_version` ×1
- `aws_apprunner_observability_configuration` ×1

</details>

<details><summary><b>밖:메시징·캐시</b> — 타입 13종 / 인스턴스 16</summary>

- `aws_elasticache_subnet_group` ×2
- `azurerm_servicebus_queue` ×2
- `azurerm_servicebus_subscription` ×2
- `google_redis_instance` ×1
- `aws_elasticache_parameter_group` ×1
- `aws_elasticache_replication_group` ×1
- `aws_elasticache_serverless_cache` ×1
- `azurerm_servicebus_queue_authorization_rule` ×1
- `azurerm_servicebus_namespace` ×1
- `azurerm_servicebus_namespace_authorization_rule` ×1
- `azurerm_servicebus_topic` ×1
- `azurerm_servicebus_topic_authorization_rule` ×1
- `aws_mq_broker` ×1

</details>

<details><summary><b>밖:비밀·키</b> — 타입 6종 / 인스턴스 13</summary>

- `aws_secretsmanager_secret` ×4
- `aws_secretsmanager_secret_version` ×4
- `Microsoft.KeyVault/vaults` ×2
- `aws_secretsmanager_secret_rotation` ×1
- `google_kms_key_handle` ×1
- `aws_kms_key` ×1

</details>

<details><summary><b>밖:정책·가드레일</b> — 타입 4종 / 인스턴스 12</summary>

- `Microsoft.Authorization/policyAssignments` ×5
- `Microsoft.Authorization/policyDefinitions` ×4
- `aws_opensearchserverless_security_policy` ×2
- `google_compute_security_policy` ×1

</details>

<details><summary><b>우리:subnet</b> — 타입 2종 / 인스턴스 11</summary>

- `aws_subnet` ×9
- `google_compute_subnetwork` ×2

</details>

<details><summary><b>우리:publicIp</b> — 타입 4종 / 인스턴스 10</summary>

- `google_compute_global_address` ×4
- `Microsoft.Network/publicIPAddresses` ×3
- `aws_eip` ×2
- `google_compute_address` ×1

</details>

<details><summary><b>밖:이미지 저장소</b> — 타입 3종 / 인스턴스 9</summary>

- `aws_ecr_repository` ×4
- `aws_ecr_repository_policy` ×4
- `google_artifact_registry_repository` ×1

</details>

<details><summary><b>밖:사설 연결</b> — 타입 6종 / 인스턴스 9</summary>

- `Microsoft.Network/privateDnsZones` ×3
- `aws_service_discovery_private_dns_namespace` ×2
- `Microsoft.Network/privateEndpoints` ×1
- `Microsoft.Network/privateDnsZones/virtualNetworkLinks` ×1
- `Microsoft.Network/privateEndpoints/privateDnsZoneGroups` ×1
- `aws_service_discovery_service` ×1

</details>

<details><summary><b>우리:vNet</b> — 타입 3종 / 인스턴스 8</summary>

- `Microsoft.Network/virtualNetworks` ×5
- `aws_vpc` ×2
- `google_compute_network` ×1

</details>

<details><summary><b>우리:nlb</b> — 타입 4종 / 인스턴스 7</summary>

- `Microsoft.Network/loadBalancers` ×4
- `aws_lb` ×1
- `google_compute_forwarding_rule` ×1
- `aws_alb` ×1

</details>

<details><summary><b>우리:objectStorage</b> — 타입 1종 / 인스턴스 6</summary>

- `google_storage_bucket` ×6

</details>

<details><summary><b>밖:게이트웨이·WAF</b> — 타입 4종 / 인스턴스 5</summary>

- `Microsoft.Network/applicationGateways` ×2
- `google_compute_ssl_policy` ×1
- `Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies` ×1
- `Microsoft.Network/bastionHosts` ×1

</details>

<details><summary><b>우리:nodeGroup</b> — 타입 3종 / 인스턴스 5</summary>

- `google_container_node_pool` ×3
- `aws_launch_template` ×1
- `aws_eks_node_group` ×1

</details>

<details><summary><b>우리:k8sCluster</b> — 타입 2종 / 인스턴스 4</summary>

- `google_container_cluster` ×3
- `aws_eks_cluster` ×1

</details>

<details><summary><b>우리:node</b> — 타입 2종 / 인스턴스 3</summary>

- `Microsoft.Compute/virtualMachineScaleSets` ×2
- `google_compute_instance` ×1

</details>

<details><summary><b>우리:sqlDb</b> — 타입 2종 / 인스턴스 3</summary>

- `google_sql_database_instance` ×2
- `aws_rds_cluster` ×1

</details>

## 4. 우리 어휘 안에서 관측된 간선 전수

| 간선 | 소스 수 | 참조 횟수 | 관측 형태 | 우리 census |
|---|---:|---:|---|---|
| `securityGroup → securityGroup` | 3 | 5 | 속성 참조 | **없음** |
| `subnet → vNet` | 2 | 11 | 속성 참조 | 있음 |
| `securityGroup → subnet` | 2 | 3 | 속성 참조 | **없음** |
| `nlb → securityGroup` | 2 | 2 | 속성 참조 | **없음** |
| `nodeGroup → k8sCluster` | 1 | 28 | 속성 참조 | **없음** |
| `securityGroup → k8sCluster` | 1 | 21 | 명시 depends_on · 속성 참조 | **방향이 반대** |
| `nodeGroup → securityGroup` | 1 | 14 | 명시 depends_on | **없음** |
| `vNet → securityGroup` | 1 | 7 | 속성 참조 | **방향이 반대** |
| `node → nlb` | 1 | 6 | 명시 dependsOn · 속성 참조 | **없음** |
| `securityGroup → vNet` | 1 | 5 | 속성 참조 | 있음 |
| `k8sCluster → securityGroup` | 1 | 2 | 명시 depends_on | 있음 |
| `sqlDb → securityGroup` | 1 | 1 | 속성 참조 | **없음** |
| `nlb → publicIp` | 1 | 1 | 속성 참조 | **없음** |
| `publicIp → vNet` | 1 | 1 | 속성 참조 | **없음** |
| `nlb → subnet` | 1 | 1 | 속성 참조 | **없음** |

소스별로 어디서 나왔는지:

- `securityGroup → securityGroup` — `m-awseks`, `m-awsvpc`, `retail`
- `nlb → securityGroup` — `m-awsalb`, `yelb`
- `securityGroup → subnet` — `m-awseks`, `m-awsos`
- `subnet → vNet` — `m-awsvpc`, `yelb`
- `vNet → securityGroup` — `iaasbase`
- `node → nlb` — `iaasbase`
- `k8sCluster → securityGroup` — `m-awseks`
- `sqlDb → securityGroup` — `m-awsrds`
- `nlb → publicIp` — `m-gcpnet`
- `publicIp → vNet` — `m-gcpsql`
- `nodeGroup → k8sCluster` — `m-gke`
- `securityGroup → k8sCluster` — `m-gke`
- `nodeGroup → securityGroup` — `m-gke`
- `securityGroup → vNet` — `yelb`
- `nlb → subnet` — `yelb`

### 4.1 모듈의 `examples/`를 포함하면 — 두 번째 관점

모듈 저장소의 `examples/`는 모듈 자신의 자원이 아니지만, **그 모듈을 실제로 어떻게
조합하는지**를 보여 준다. 그리고 모듈 루트에는 VM이 없다 — VPC·EKS·ALB 모듈은
네트워크와 클러스터만 만들고, **노드를 만드는 것은 모듈을 쓰는 쪽**이다. 그래서
`node → *` 간선은 `examples/`에서만 나타난다.

| | 모듈 루트만 | `examples/` 포함 |
|---|---:|---:|
| 자원 인스턴스 | 839 | 1168 |
| 우리 어휘 안 | 112 (13.3%) | 184 (15.8%) |
| 우리 어휘 안 간선 | 15 | 21 |
| 그중 우리 census와 일치 | 3 | 6 |

`examples/`에서만 나오는 간선 6종:

- `nlb → node` — `m-gcpnet` — **없음**
- `nlb → vNet` — `m-gcpnet` — 우리 census에 있음
- `node → publicIp` — `m-gcpnet` — **방향이 반대**
- `node → securityGroup` — `m-awseks` — 우리 census에 있음
- `node → subnet` — `m-gcpnet` — 우리 census에 있음
- `publicIp → subnet` — `m-gcpnet` — **없음**

**이 관점에서 우리 census와 일치하는 간선은 6개로 늘어난다** — 아래 5장의 미관측 목록은 "모듈 루트만" 기준이고, 이 관점에서는 그만큼 줄어든다.

## 5. 우리 census 39 간선과의 대조

- 관측됨: **3** / 39
- 미관측: **36** / 39
- 참조 구현에 있으나 우리 census에 없음: **12**
- (`examples/` 포함 기준으로는 관측 **6** · 미관측 **33**)

### 5.1 미관측 간선의 갈래 — **우리 판단이다**

**범주 불일치(IaC에서 속성이지 자원이 아니다)** (8)

- `customImage → node`
- `k8sCluster → image`
- `k8sCluster → spec`
- `k8sNodeGroup → image`
- `k8sNodeGroup → spec`
- `node → customImage`
- `node → image`
- `node → spec`

**cb-tumblebug 고유 추상(대응 자원이 없다)** (4)

- `globalDns → infra`
- `nlb → infra`
- `node → infra`
- `vpn → infra`

**우리 매핑이 갈라놓아 미관측으로 잡힌 것(거짓 미관측)** (1)

- `k8sNodeGroup → k8sCluster`

**참조 구현이 실제로 만들지 않는 것** (17)

- `dataDisk → node`
- `fileSystem → vNet`
- `k8sNodeGroup → node`
- `k8sNodeGroup → sshKey`
- `nlb → nodeGroup`
- `node → dataDisk`
- `node → sshKey`
- `publicIp → node`
- `publicIp → vNic`
- `sqlDb → subnet`
- `sqlDb → vNet`
- `vNic → node`
- `vNic → securityGroup`
- `vNic → subnet`
- `vNic → vNet`
- `vpn → subnet`
- `vpn → vNet`

**모듈 경계에서 끊긴 것(참조가 변수·문자열이다)** (2)

- `k8sCluster → subnet`
- `k8sCluster → vNet`

**전이적으로만 성립(직접 참조가 없다)** (1)

- `node → vNet`

**`examples/` 관점에서는 관측된 것** (3)

- `nlb → vNet`
- `node → securityGroup`
- `node → subnet`

갈래마다 근거를 원문으로 남긴다.

- **모듈 경계에서 끊긴 것** — 클러스터는 서브넷·네트워크를 *자원 참조*가 아니라
  *변수*로 받는다. 그래서 자원 사이 간선으로 나타날 수가 없다.

  ```hcl
  # terraform-aws-modules/terraform-aws-eks · main.tf:81
  subnet_ids = coalescelist(var.control_plane_subnet_ids, var.subnet_ids)

  # terraform-google-modules/terraform-google-kubernetes-engine · cluster.tf:33
  network = "projects/${local.network_project_id}/global/networks/${var.network}"
  ```

  두 번째는 **문자열 보간**이라 어떤 정규식으로도 자원 참조로 잡히지 않는다.
  즉 이 둘의 미관측은 **의존이 없다는 뜻이 아니라 이 방법으로는 보이지 않는다**는
  뜻이다.

- **전이적으로만 성립** — IaC에서 인스턴스는 서브넷에 붙고, vNet은 서브넷을 통해
  간접적으로만 걸린다. `node → vNet`의 직접 참조는 어디에도 없다. 우리 census가
  이 간선을 세운 것은 cb-tumblebug의 요청 스키마가 `VNetId`를 직접 받기 때문이다
  (`core/model/infra.go:265`).

### 5.2 참조 구현에 있으나 우리에게 없는 것

- `securityGroup → securityGroup` (3개 소스)
- `securityGroup → subnet` (2개 소스)
- `nlb → securityGroup` (2개 소스)
- `node → nlb` (1개 소스)
- `sqlDb → securityGroup` (1개 소스)
- `securityGroup → k8sCluster` (1개 소스) — 우리는 방향이 반대다
- `nodeGroup → k8sCluster` (1개 소스)
- `nodeGroup → securityGroup` (1개 소스)
- `nlb → publicIp` (1개 소스)
- `publicIp → vNet` (1개 소스)
- `vNet → securityGroup` (1개 소스) — 우리는 방향이 반대다
- `nlb → subnet` (1개 소스)

## 6. 자원 타입별 원본 간선 — 요약 없이

타입쌍 477종. 참조 횟수 2회 이상만 싣는다(1회는 아래 접힌 목록).

| from | to | 횟수 |
|---|---|---:|
| `google_project_iam_member` | `google_service_account` | 86 |
| `module:aws_efs_csi_driver` | `aws_efs_csi_driver` | 45 |
| `module:aws_for_fluentbit` | `aws_for_fluentbit` | 45 |
| `module:aws_fsx_csi_driver` | `aws_fsx_csi_driver` | 45 |
| `module:aws_load_balancer_controller` | `aws_load_balancer_controller` | 45 |
| `module:aws_node_termination_handler` | `aws_node_termination_handler` | 45 |
| `module:aws_privateca_issuer` | `aws_privateca_issuer` | 45 |
| `module:aws_gateway_api_controller` | `aws_gateway_api_controller` | 45 |
| `module:aws_cloudwatch_metrics` | `aws_cloudwatch_metrics` | 43 |
| `aws_iam_role_policy_attachment` | `aws_iam_role` | 42 |
| `google_container_node_pool` | `google_container_cluster` | 28 |
| `module:project-iam-bindings` | `google_service_account` | 22 |
| `aws_iam_role` | `aws_iam_policy_document` | 21 |
| `kubernetes_config_map_v1_data` | `google_container_cluster` | 21 |
| `kubernetes_config_map_v1_data` | `google_container_node_pool` | 21 |
| `google_compute_firewall` | `google_container_cluster` | 21 |
| `aws_appautoscaling_policy` | `aws_appautoscaling_target` | 19 |
| `google_sql_user` | `google_sql_database_instance` | 18 |
| `Microsoft.Authorization/policyAssignments` | `Microsoft.Authorization/policyDefinitions` | 17 |
| `Microsoft.Insights/dataCollectionRules` | `Microsoft.OperationalInsights/workspaces` | 17 |
| `aws_iam_policy` | `aws_iam_policy_document` | 17 |
| `aws_iam_role_policy_attachment` | `aws_iam_policy` | 16 |
| `Microsoft.Insights/diagnosticSettings` | `Microsoft.OperationalInsights/workspaces` | 15 |
| `Microsoft.Authorization/roleAssignments` | `Microsoft.Authorization/roleDefinitions` | 14 |
| `aws_network_acl_rule` | `aws_network_acl` | 14 |
| `google_container_node_pool` | `google_compute_firewall` | 14 |
| `aws_route_table_association` | `aws_route_table` | 13 |
| `google_sql_database` | `google_sql_database_instance` | 12 |
| `aws_apprunner_service` | `aws_secretsmanager_secret_version` | 12 |
| `aws_subnet` | `aws_vpc` | 11 |
| `Microsoft.ManagedIdentity/userAssignedIdentities` | `Microsoft.Authorization/roleDefinitions` | 10 |
| `Microsoft.Network/networkSecurityGroups` | `Microsoft.Network/applicationSecurityGroups` | 10 |
| `aws_vpc_security_group_ingress_rule` | `aws_security_group` | 10 |
| `aws_vpc_security_group_egress_rule` | `aws_security_group` | 10 |
| `google_project_iam_member` | `google_project_service_identity` | 10 |
| `google_service_account_iam_member` | `google_service_account` | 9 |
| `google_storage_bucket_iam_member` | `google_storage_bucket` | 9 |
| `aws_route` | `aws_route_table` | 9 |
| `aws_route_table_association` | `aws_subnet` | 9 |
| `google_project_iam_member` | `google_project` | 9 |
| `aws_appautoscaling_target` | `aws_dynamodb_table` | 8 |
| `aws_iam_role_policy` | `aws_iam_policy_document` | 8 |
| `aws_iam_role_policy` | `aws_iam_role` | 8 |
| `azurerm_management_lock` | `azurerm_private_endpoint` | 8 |
| `aws_cloudwatch_log_group` | `aws_for_fluentbit_cw_log_group` | 8 |
| `kubernetes_config_map_v1_data` | `aws_for_fluentbit` | 8 |
| `helm_release` | `kubernetes_namespace_v1` | 8 |
| `Microsoft.Network/virtualNetworks` | `Microsoft.Network/networkSecurityGroups` | 7 |
| `azurerm_private_endpoint_application_security_group_association` | `azurerm_private_endpoint` | 7 |
| `aws_vpc_block_public_access_exclusion` | `aws_subnet` | 7 |
| `aws_network_acl` | `aws_subnet` | 7 |
| `kubernetes_config_map` | `google_container_cluster` | 7 |
| `kubernetes_config_map` | `google_container_node_pool` | 7 |
| `aws_secretsmanager_secret_version` | `aws_secretsmanager_secret` | 7 |
| `azurerm_monitor_data_collection_rule` | `azurerm_resource_group` | 6 |
| `google_gke_hub_feature_membership` | `google_gke_hub_feature` | 6 |
| `google_gke_hub_feature_membership` | `google_gke_hub_membership` | 6 |
| `Microsoft.Authorization/roleAssignments` | `Microsoft.Authorization/policyAssignments` | 6 |
| `Microsoft.OperationsManagement/solutions` | `Microsoft.OperationalInsights/workspaces` | 6 |
| `Microsoft.Compute/virtualMachineScaleSets` | `Microsoft.Network/loadBalancers` | 6 |
| `Microsoft.Compute/virtualMachineScaleSets` | `Microsoft.Network/privateDnsZones` | 6 |
| `module:aws_node_termination_handler_sqs` | `aws_node_termination_handler_sqs` | 6 |
| `google_container_cluster` | `google_project_iam_member` | 6 |
| `module:orders_service` | `aws_secretsmanager_secret_version` | 6 |
| `google_storage_bucket_iam_member` | `google_service_account` | 5 |
| `google_storage_bucket` | `google_project` | 5 |
| `aws_security_group_rule` | `aws_security_group` | 5 |
| `aws_iam_policy` | `aws_load_balancer_controller` | 5 |
| `kubernetes_namespace_v1` | `kubernetes_nodes` | 5 |
| `module:catalog_service` | `aws_secretsmanager_secret_version` | 5 |
| `aws_iam_policy` | `aws_region` | 5 |
| `aws_iam_policy` | `aws_caller_identity` | 5 |
| `aws_security_group` | `aws_vpc` | 5 |
| `azurerm_monitor_alert_prometheus_rule_group` | `azurerm_resource_group` | 4 |
| `module:project-iam-bindings` | `google_project` | 4 |
| `Microsoft.Compute/virtualMachineScaleSets` | `Microsoft.ManagedIdentity/userAssignedIdentities` | 4 |
| `aws_launch_template` | `aws_iam_role_policy_attachment` | 4 |
| `aws_iam_instance_profile` | `aws_iam_role` | 4 |
| `aws_cloudwatch_event_target` | `aws_cloudwatch_event_rule` | 4 |
| `aws_route` | `aws_nat_gateway` | 4 |
| `aws_route` | `aws_internet_gateway` | 4 |
| `azurerm_role_assignment` | `azurerm_private_endpoint` | 4 |
| `google_network_connectivity_spoke` | `google_network_connectivity_hub` | 4 |
| `google_sql_database` | `google_sql_user` | 4 |
| `aws_ecr_repository_policy` | `aws_ecr_repository` | 4 |
| `azurerm_monitor_data_collection_endpoint` | `azurerm_resource_group` | 3 |
| `azurerm_monitor_data_collection_rule` | `azurerm_monitor_workspace` | 3 |
| `azurerm_monitor_data_collection_rule` | `azurerm_log_analytics_workspace` | 3 |
| `azurerm_federated_identity_credential` | `azurerm_user_assigned_identity` | 3 |
| `azurerm_role_assignment` | `azurerm_user_assigned_identity` | 3 |
| `module:gke` | `google_project` | 3 |
| `google_cloudbuild_trigger` | `google_storage_bucket` | 3 |
| `google_cloudbuild_trigger` | `google_artifact_registry_repository` | 3 |
| `Microsoft.Authorization/policyAssignments` | `Microsoft.Insights/dataCollectionRules` | 3 |
| `Microsoft.Insights/diagnosticSettings` | `Microsoft.Network/applicationSecurityGroups` | 3 |
| `aws_apprunner_vpc_ingress_connection` | `aws_apprunner_service` | 3 |
| `aws_eks_addon` | `aws_eks_addon_version` | 3 |
| `aws_security_group` | `aws_subnet` | 3 |
| `aws_vpn_gateway_route_propagation` | `aws_route_table` | 3 |
| `aws_vpn_gateway_route_propagation` | `aws_vpn_gateway` | 3 |
| `aws_vpn_gateway_route_propagation` | `aws_vpn_gateway_attachment` | 3 |
| `google_binary_authorization_attestor` | `google_kms_crypto_key_version` | 3 |
| `helm_release` | `aws_security_group` | 3 |
| `module:dependencies` | `aws_security_group` | 3 |
| `aws_secretsmanager_secret` | `aws_kms_key` | 3 |
| `aws_ecs_task_definition` | `aws_iam_role` | 3 |
| `module:db` | `azurerm_resource_group` | 2 |
| `module:acr` | `azurerm_resource_group` | 2 |
| `module:aks` | `azurerm_resource_group` | 2 |
| `module:aks` | `azurerm_client_config` | 2 |
| `azurerm_log_analytics_workspace` | `azurerm_resource_group` | 2 |
| `azurerm_monitor_workspace` | `azurerm_resource_group` | 2 |
| `azurerm_monitor_data_collection_rule_association` | `azurerm_monitor_data_collection_rule` | 2 |
| `azurerm_monitor_alert_prometheus_rule_group` | `azurerm_monitor_workspace` | 2 |
| `azurerm_role_assignment` | `azurerm_client_config` | 2 |
| `module:sb` | `azurerm_resource_group` | 2 |
| `Microsoft.Insights/dataCollectionRuleAssociations` | `Microsoft.Insights/dataCollectionRules` | 2 |
| `Microsoft.Insights/dataCollectionRules` | `Microsoft.Insights/dataCollectionEndpoints` | 2 |
| `module:artifact-registry-repository-iam-bindings` | `google_service_account` | 2 |
| `module:gke_development` | `google_gke_hub_feature` | 2 |
| `module:gke_production` | `google_gke_hub_feature` | 2 |
| `module:gke_staging` | `google_gke_hub_feature` | 2 |
| `google_cloudbuild_trigger` | `google_storage_bucket_object` | 2 |
| `google_cloudbuild_trigger` | `google_service_account` | 2 |
| `google_storage_bucket_object` | `google_storage_bucket` | 2 |
| `module:ci-cd-pipeline` | `google_clouddeploy_target` | 2 |
| `google_clouddeploy_target` | `google_gke_hub_membership` | 2 |
| `google_clouddeploy_target` | `google_storage_bucket` | 2 |
| `google_clouddeploy_target` | `google_service_account` | 2 |
| `Microsoft.Insights/dataCollectionRules` | `Microsoft.OperationsManagement/solutions` | 2 |
| `Microsoft.Authorization/policyAssignments` | `Microsoft.OperationalInsights/workspaces` | 2 |
| `Microsoft.Insights/diagnosticSettings` | `Microsoft.Network/virtualNetworks` | 2 |
| `Microsoft.Network/privateEndpoints/privateDnsZoneGroups` | `Microsoft.Network/privateDnsZones` | 2 |
| `Microsoft.Network/privateDnsZones` | `Microsoft.Network/virtualNetworks` | 2 |
| `Microsoft.Compute/virtualMachineScaleSets` | `Microsoft.Network/applicationGateways` | 2 |
| `Microsoft.Compute/virtualMachineScaleSets` | `Microsoft.OperationsManagement/solutions` | 2 |
| `Microsoft.Compute/virtualMachineScaleSets` | `Microsoft.Authorization/roleAssignments` | 2 |
| `Microsoft.Compute/virtualMachineScaleSets` | `Microsoft.Network/applicationSecurityGroups` | 2 |
| `aws_lb_listener` | `aws_lb_target_group` | 2 |
| `aws_lb_listener_rule` | `aws_lb_target_group` | 2 |
| `aws_lb_target_group_attachment` | `aws_lambda_permission` | 2 |
| `aws_lb_target_group_attachment` | `aws_lb_target_group` | 2 |
| `aws_route53_record` | `aws_lb` | 2 |
| `aws_iam_role_policy_attachment` | `aws_partition` | 2 |
| `aws_eks_cluster` | `aws_security_group_rule` | 2 |
| `aws_eks_addon` | `aws_eks_cluster` | 2 |
| `aws_eks_capability` | `aws_iam_role_policy_attachment` | 2 |
| `aws_eks_capability` | `aws_idc` | 2 |
| `aws_launch_template` | `aws_placement_group` | 2 |
| `aws_eks_access_entry` | `aws_iam_role` | 2 |
| `aws_iam_group_policy_attachment` | `aws_iam_group` | 2 |
| `aws_opensearchserverless_collection` | `aws_opensearchserverless_security_policy` | 2 |
| `aws_cloudwatch_metric_alarm` | `aws_elasticache_replication_group` | 2 |
| `aws_route` | `aws_egress_only_internet_gateway` | 2 |
| `aws_eip` | `aws_internet_gateway` | 2 |
| `aws_nat_gateway` | `aws_subnet` | 2 |
| `azurerm_private_endpoint` | `azurerm_container_registry` | 2 |
| `module:scope_maps` | `azurerm_container_registry` | 2 |
| `azurerm_container_registry` | `azurerm_key_vault_key` | 2 |
| `azurerm_cosmosdb_gremlin_database` | `azurerm_cosmosdb_account` | 2 |
| `azurerm_cosmosdb_gremlin_graph` | `azurerm_cosmosdb_account` | 2 |
| `azurerm_management_lock` | `azurerm_cosmosdb_account` | 2 |
| `azurerm_cosmosdb_mongo_database` | `azurerm_cosmosdb_account` | 2 |
| `azurerm_cosmosdb_mongo_collection` | `azurerm_cosmosdb_account` | 2 |
| `azurerm_private_endpoint` | `azurerm_cosmosdb_account` | 2 |
| `azurerm_cosmosdb_sql_database` | `azurerm_cosmosdb_account` | 2 |
| `azurerm_cosmosdb_sql_container` | `azurerm_cosmosdb_account` | 2 |
| `azurerm_cosmosdb_sql_stored_procedure` | `azurerm_cosmosdb_account` | 2 |
| `azurerm_management_lock` | `azurerm_monitor_diagnostic_setting` | 2 |
| `azurerm_management_lock` | `azurerm_role_assignment` | 2 |
| `azurerm_management_lock` | `azurerm_private_endpoint_application_security_group_association` | 2 |
| `azurerm_management_lock` | `azurerm_servicebus_namespace` | 2 |
| `azurerm_management_lock` | `azurerm_servicebus_queue` | 2 |
| `azurerm_management_lock` | `azurerm_servicebus_subscription` | 2 |
| `azurerm_private_endpoint` | `azurerm_servicebus_namespace` | 2 |
| `azurerm_servicebus_queue` | `azurerm_servicebus_namespace` | 2 |
| `azurerm_servicebus_queue_authorization_rule` | `azurerm_servicebus_queue` | 2 |
| `azurerm_role_assignment` | `azurerm_servicebus_queue` | 2 |
| `azurerm_servicebus_subscription` | `azurerm_servicebus_topic` | 2 |
| `kubernetes_config_map_v1` | `aws_cloudwatch_log_group` | 2 |
| `google_compute_instance` | `google_project_service` | 2 |
| `google_service_networking_connection` | `google_compute_global_address` | 2 |
| `google_resource_manager_lien` | `google_project` | 2 |
| `google_project_usage_export_bucket` | `google_project` | 2 |
| `google_project_service` | `google_project` | 2 |
| `google_cloud_scheduler_job` | `google_workflows_workflow` | 2 |
| `google_workflows_workflow` | `google_sql_database_instance` | 2 |
| `google_storage_bucket_iam_member` | `google_sql_database_instance` | 2 |
| `google_project_iam_member` | `google_sql_database_instance` | 2 |
| `google_gke_hub_membership` | `google_container_cluster` | 2 |
| `aws_apprunner_service` | `aws_apprunner_vpc_connector` | 2 |
| `aws_apprunner_service` | `aws_iam_role` | 2 |
| `aws_apprunner_vpc_connector` | `aws_security_group` | 2 |
| `module:app_runner_ui` | `aws_apprunner_vpc_ingress_connection` | 2 |
| `aws_ecs_service` | `aws_ecs_task_definition` | 2 |
| `aws_ecs_service` | `aws_security_group` | 2 |
| `aws_appautoscaling_policy` | `aws_ecs_cluster` | 2 |
| `aws_appautoscaling_policy` | `aws_ecs_service` | 2 |
| `aws_cloudwatch_metric_alarm` | `aws_ecs_cluster` | 2 |
| `aws_cloudwatch_metric_alarm` | `aws_ecs_service` | 2 |
| `aws_cloudwatch_metric_alarm` | `aws_appautoscaling_policy` | 2 |
| `aws_subnet` | `aws_availability_zones` | 2 |

<details><summary>1회만 관측된 타입쌍 275종</summary>

- `Microsoft.Authorization/policyAssignments` → `Microsoft.OperationsManagement/solutions`
- `Microsoft.Insights/dataCollectionRuleAssociations` → `Microsoft.Insights/dataCollectionEndpoints`
- `Microsoft.Insights/diagnosticSettings` → `Microsoft.Network/applicationGateways`
- `Microsoft.Insights/diagnosticSettings` → `Microsoft.Network/bastionHosts`
- `Microsoft.Insights/diagnosticSettings` → `Microsoft.Network/loadBalancers`
- `Microsoft.Insights/diagnosticSettings` → `Microsoft.Network/publicIPAddresses`
- `Microsoft.Monitor/accounts` → `Microsoft.OperationalInsights/workspaces`
- `Microsoft.Network/applicationGateways` → `Microsoft.ManagedIdentity/userAssignedIdentities`
- `Microsoft.Network/applicationGateways` → `Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies`
- `Microsoft.Network/bastionHosts` → `Microsoft.Network/publicIPAddresses`
- `Microsoft.Network/privateDnsZones/virtualNetworkLinks` → `Microsoft.Network/privateDnsZones`
- `Microsoft.Network/privateDnsZones/virtualNetworkLinks` → `Microsoft.Network/virtualNetworks`
- `Microsoft.Network/privateEndpoints` → `Microsoft.KeyVault/vaults`
- `Microsoft.Network/privateEndpoints` → `Microsoft.Network/applicationSecurityGroups`
- `Microsoft.Network/privateEndpoints/privateDnsZoneGroups` → `Microsoft.KeyVault/vaults`
- `Microsoft.Network/privateEndpoints/privateDnsZoneGroups` → `Microsoft.Network/privateEndpoints`
- `aws_alb` → `aws_security_group`
- `aws_alb` → `aws_subnet`
- `aws_alb_listener` → `aws_alb`
- `aws_alb_listener` → `aws_alb_target_group`
- `aws_alb_target_group` → `aws_vpc`
- `aws_appautoscaling_policy` → `aws_rds_cluster`
- `aws_appautoscaling_target` → `aws_ecs_cluster`
- `aws_appautoscaling_target` → `aws_ecs_service`
- `aws_appautoscaling_target` → `aws_rds_cluster`
- `aws_apprunner_custom_domain_association` → `aws_apprunner_service`
- `aws_apprunner_service` → `aws_apprunner_observability_configuration`
- `aws_cloudwatch_event_rule` → `aws_caller_identity`
- `aws_cloudwatch_event_rule` → `aws_region`
- `aws_cloudwatch_event_target` → `aws_cloudwatch_log_group`
- `aws_cloudwatch_event_target` → `aws_node_termination_handler_sqs`
- `aws_cloudwatch_event_target` → `aws_sqs_queue`
- `aws_cloudwatch_log_resource_policy` → `aws_cloudwatch_log_group`
- `aws_cloudwatch_log_resource_policy` → `aws_iam_policy_document`
- `aws_cloudwatch_log_stream` → `aws_cloudwatch_log_group`
- `aws_db_subnet_group` → `aws_subnet`
- `aws_default_network_acl` → `aws_vpc`
- `aws_default_route_table` → `aws_vpc`
- `aws_default_security_group` → `aws_vpc`
- `aws_dsql_cluster_peering` → `aws_dsql_cluster`
- `aws_ec2_tag` → `aws_eks_cluster`
- `aws_ecs_service` → `aws_alb_listener`
- `aws_ecs_service` → `aws_alb_target_group`
- `aws_ecs_service` → `aws_ecs_cluster`
- `aws_ecs_service` → `aws_iam_role_policy_attachment`
- `aws_ecs_service` → `aws_subnet`
- `aws_eks_access_entry` → `aws_eks_cluster`
- `aws_eks_access_entry` → `aws_sqs_queue_policy`
- `aws_eks_access_policy_association` → `aws_eks_access_entry`
- `aws_eks_access_policy_association` → `aws_eks_cluster`
- `aws_eks_addon` → `kubernetes_cluster_role_v1`
- `aws_eks_addon` → `kubernetes_role_v1`
- `aws_eks_cluster` → `aws_cloudwatch_log_group`
- `aws_eks_cluster` → `aws_iam_policy`
- `aws_eks_cluster` → `aws_iam_role`
- `aws_eks_cluster` → `aws_iam_role_policy_attachment`
- `aws_eks_fargate_profile` → `aws_iam_role`
- `aws_eks_identity_provider_config` → `aws_eks_cluster`
- `aws_eks_node_group` → `aws_iam_role`
- `aws_eks_pod_identity_association` → `aws_iam_role`
- `aws_elasticache_replication_group` → `aws_elasticache_parameter_group`
- `aws_elasticache_replication_group` → `aws_security_group`
- `aws_elasticache_serverless_cache` → `aws_elasticache_parameter_group`
- `aws_elasticache_serverless_cache` → `aws_security_group`
- `aws_elasticache_subnet_group` → `aws_subnet`
- `aws_flow_log` → `aws_cloudwatch_log_group`
- `aws_flow_log` → `aws_iam_role`
- `aws_iam_access_key` → `aws_iam_user`
- `aws_iam_group_membership` → `aws_iam_group`
- `aws_iam_group_policy_attachment` → `aws_iam_policy`
- `aws_iam_openid_connect_provider` → `aws_eks_cluster`
- `aws_iam_role_policy_attachment` → `aws_load_balancer_controller`
- `aws_iam_user_login_profile` → `aws_iam_user`
- `aws_iam_user_policy` → `aws_iam_policy_document`
- `aws_iam_user_policy` → `aws_iam_user`
- `aws_iam_user_policy_attachment` → `aws_iam_user`
- `aws_iam_user_ssh_key` → `aws_iam_user`
- `aws_internet_gateway` → `aws_vpc`
- `aws_lambda_permission` → `aws_lb_target_group`
- `aws_lambda_permission` → `aws_partition`
- `aws_launch_template` → `aws_iam_instance_profile`
- `aws_launch_template` → `aws_ssm_parameter`
- `aws_lb` → `aws_security_group`
- `aws_lb_listener` → `aws_lb`
- `aws_lb_listener_certificate` → `aws_lb_listener`
- `aws_lb_listener_rule` → `aws_lb_listener`
- `aws_lb_trust_store_revocation` → `aws_lb_trust_store`
- `aws_mq_broker` → `aws_security_group`
- `aws_nat_gateway` → `aws_eip`
- `aws_nat_gateway` → `aws_internet_gateway`
- `aws_opensearch_domain` → `aws_cloudwatch_log_group`
- `aws_opensearch_domain` → `aws_iam_session_context`
- `aws_opensearch_domain` → `aws_security_group`
- `aws_opensearch_domain_policy` → `aws_iam_policy_document`
- `aws_opensearch_domain_policy` → `aws_opensearch_domain`
- `aws_opensearch_domain_saml_options` → `aws_opensearch_domain`
- `aws_opensearch_outbound_connection` → `aws_opensearch_domain`
- `aws_opensearch_package_association` → `aws_opensearch_domain`
- `aws_opensearch_vpc_endpoint` → `aws_opensearch_domain`
- `aws_opensearchserverless_access_policy` → `aws_caller_identity`
- `aws_opensearchserverless_collection` → `aws_opensearchserverless_access_policy`
- `aws_opensearchserverless_collection` → `aws_opensearchserverless_collection_group`
- `aws_rds_cluster` → `aws_cloudwatch_log_group`
- `aws_rds_cluster` → `aws_iam_role`
- `aws_rds_cluster` → `aws_rds_cluster_parameter_group`
- `aws_rds_cluster` → `aws_security_group`
- `aws_rds_cluster_activity_stream` → `aws_rds_cluster`
- `aws_rds_cluster_activity_stream` → `aws_rds_cluster_instance`
- `aws_rds_cluster_endpoint` → `aws_rds_cluster`
- `aws_rds_cluster_endpoint` → `aws_rds_cluster_instance`
- `aws_rds_cluster_instance` → `aws_db_parameter_group`
- `aws_rds_cluster_instance` → `aws_iam_role`
- `aws_rds_cluster_instance` → `aws_rds_cluster`
- `aws_rds_cluster_role_association` → `aws_rds_cluster`
- `aws_rds_shard_group` → `aws_rds_cluster`
- `aws_redshift_subnet_group` → `aws_subnet`
- `aws_rolesanywhere_profile` → `aws_iam_role`
- `aws_route` → `aws_vpc`
- `aws_route_table` → `aws_nat_gateway`
- `aws_route_table` → `aws_vpc`
- `aws_secretsmanager_secret_rotation` → `aws_rds_cluster`
- `aws_service_discovery_private_dns_namespace` → `aws_vpc`
- `aws_service_discovery_service` → `aws_service_discovery_private_dns_namespace`
- `aws_sqs_queue_policy` → `aws_iam_policy_document`
- `aws_sqs_queue_policy` → `aws_sqs_queue`
- `aws_vpc_dhcp_options_association` → `aws_vpc_dhcp_options`
- `aws_vpc_endpoint` → `aws_vpc_endpoint_service`
- `aws_vpc_ipv4_cidr_block_association` → `aws_vpc`
- `aws_vpc_security_group_rules_exclusive` → `aws_security_group`
- `aws_vpc_security_group_rules_exclusive` → `aws_vpc_security_group_egress_rule`
- `aws_vpc_security_group_rules_exclusive` → `aws_vpc_security_group_ingress_rule`
- `aws_vpc_security_group_vpc_association` → `aws_security_group`
- `aws_wafv2_web_acl_association` → `aws_lb`
- `azurerm_cognitive_account_customer_managed_key` → `azurerm_key_vault_key`
- `azurerm_container_registry` → `azurerm_user_assigned_identity`
- `azurerm_container_registry_token` → `azurerm_container_registry_scope_map`
- `azurerm_container_registry_token_password` → `azurerm_container_registry_token`
- `azurerm_cosmosdb_gremlin_graph` → `azurerm_cosmosdb_gremlin_database`
- `azurerm_cosmosdb_mongo_collection` → `azurerm_cosmosdb_mongo_database`
- `azurerm_cosmosdb_sql_container` → `azurerm_cosmosdb_sql_database`
- `azurerm_cosmosdb_sql_dedicated_gateway` → `azurerm_cosmosdb_account`
- `azurerm_cosmosdb_sql_function` → `azurerm_cosmosdb_sql_container`
- `azurerm_cosmosdb_sql_role_assignment` → `azurerm_cosmosdb_sql_role_definition`
- `azurerm_cosmosdb_sql_role_assignment` → `azurerm_resource_group`
- `azurerm_cosmosdb_sql_role_assignment` → `azurerm_user_assigned_identity`
- `azurerm_cosmosdb_sql_role_definition` → `azurerm_resource_group`
- `azurerm_cosmosdb_sql_role_definition` → `azurerm_user_assigned_identity`
- `azurerm_cosmosdb_sql_stored_procedure` → `azurerm_cosmosdb_sql_container`
- `azurerm_cosmosdb_sql_stored_procedure` → `azurerm_cosmosdb_sql_database`
- `azurerm_cosmosdb_sql_trigger` → `azurerm_cosmosdb_sql_container`
- `azurerm_management_lock` → `azurerm_container_registry`
- `azurerm_management_lock` → `azurerm_servicebus_namespace_authorization_rule`
- `azurerm_management_lock` → `azurerm_servicebus_queue_authorization_rule`
- `azurerm_management_lock` → `azurerm_servicebus_topic`
- `azurerm_management_lock` → `azurerm_servicebus_topic_authorization_rule`
- `azurerm_monitor_data_collection_rule` → `azurerm_monitor_data_collection_endpoint`
- `azurerm_monitor_data_collection_rule_association` → `azurerm_monitor_data_collection_endpoint`
- `azurerm_monitor_diagnostic_setting` → `azurerm_container_registry`
- `azurerm_monitor_diagnostic_setting` → `azurerm_cosmosdb_account`
- `azurerm_monitor_diagnostic_setting` → `azurerm_servicebus_namespace`
- `azurerm_role_assignment` → `azurerm_container_registry`
- `azurerm_role_assignment` → `azurerm_cosmosdb_account`
- `azurerm_role_assignment` → `azurerm_servicebus_namespace`
- `azurerm_role_assignment` → `azurerm_servicebus_topic`
- `azurerm_servicebus_namespace_authorization_rule` → `azurerm_servicebus_namespace`
- `azurerm_servicebus_subscription` → `azurerm_servicebus_queue`
- `azurerm_servicebus_topic` → `azurerm_servicebus_namespace`
- `azurerm_servicebus_topic_authorization_rule` → `azurerm_servicebus_topic`
- `azurerm_user_assigned_identity` → `azurerm_resource_group`
- `google_access_context_manager_service_perimeter_dry_run_resource` → `google_project`
- `google_access_context_manager_service_perimeter_dry_run_resource` → `google_service_account`
- `google_access_context_manager_service_perimeter_resource` → `google_project`
- `google_access_context_manager_service_perimeter_resource` → `google_service_account`
- `google_artifact_registry_repository_iam_member` → `google_service_account`
- `google_binary_authorization_attestor` → `google_container_analysis_note`
- `google_compute_firewall` → `google_project_service`
- `google_compute_firewall_policy_association` → `google_compute_firewall_policy`
- `google_compute_firewall_policy_rule` → `google_compute_firewall_policy`
- `google_compute_forwarding_rule` → `google_compute_address`
- `google_compute_global_address` → `google_compute_network`
- `google_compute_global_forwarding_rule` → `google_compute_global_address`
- `google_compute_network_firewall_policy_association` → `google_compute_network_firewall_policy`
- `google_compute_network_firewall_policy_packet_mirroring_rule` → `google_compute_network_firewall_policy`
- `google_compute_network_firewall_policy_rule` → `google_compute_network_firewall_policy`
- `google_compute_project_default_network_tier` → `google_project`
- `google_compute_project_metadata_item` → `google_project`
- `google_compute_project_metadata_item` → `google_project_service`
- `google_compute_region_network_firewall_policy_association` → `google_compute_region_network_firewall_policy`
- `google_compute_region_network_firewall_policy_rule` → `google_compute_region_network_firewall_policy`
- `google_compute_service_attachment` → `google_compute_subnetwork`
- `google_compute_shared_vpc_host_project` → `google_compute_network`
- `google_compute_shared_vpc_host_project` → `google_project`
- `google_compute_shared_vpc_service_project` → `google_project`
- `google_network_connectivity_group` → `google_network_connectivity_hub`
- `google_project_default_service_accounts` → `google_project`
- `google_project_iam_binding` → `google_project`
- `google_project_iam_binding` → `google_project_iam_custom_role`
- `google_project_iam_custom_role` → `google_project`
- `google_service_account` → `google_project`
- `google_service_account_iam_member` → `google_project`
- `google_service_account_key` → `google_service_account`
- `google_service_networking_connection` → `google_compute_network`
- `google_tags_tag_binding` → `google_project`
- `helm_release` → `aws_eks_addon`
- `kubernetes_cluster_role_v1` → `kubernetes_namespace_v1`
- `kubernetes_config_map_v1` → `kubernetes_namespace_v1`
- `kubernetes_role_binding_v1` → `kubernetes_namespace_v1`
- `kubernetes_role_v1` → `kubernetes_namespace_v1`
- `module:acm` → `google_project`
- `module:aks` → `azurerm_log_analytics_workspace`
- `module:aoai` → `azurerm_resource_group`
- `module:app_runner_carts` → `aws_security_group`
- `module:app_runner_checkout` → `aws_apprunner_vpc_ingress_connection`
- `module:app_runner_checkout` → `aws_security_group`
- `module:app_runner_ui` → `aws_security_group`
- `module:artifact-registry-repository-iam-bindings` → `google_artifact_registry_repository`
- `module:artifact-registry-repository-iam-bindings` → `google_project`
- `module:asm` → `google_project`
- `module:aws_efs_csi_driver` → `aws_iam_policy_document`
- `module:aws_for_fluentbit` → `aws_cloudwatch_log_group`
- `module:aws_for_fluentbit` → `aws_iam_policy_document`
- `module:aws_fsx_csi_driver` → `aws_iam_policy_document`
- `module:aws_gateway_api_controller` → `aws_iam_policy_document`
- `module:aws_load_balancer_controller` → `aws_iam_policy_document`
- `module:aws_node_termination_handler` → `aws_iam_policy_document`
- `module:aws_node_termination_handler` → `aws_node_termination_handler_sqs`
- `module:aws_privateca_issuer` → `aws_iam_policy_document`
- `module:boa-istio` → `google_project`
- `module:boa-secret` → `google_project`
- `module:carts_service` → `aws_cloudwatch_log_group`
- `module:carts_service` → `aws_ecs_cluster`
- `module:carts_service` → `aws_service_discovery_private_dns_namespace`
- `module:catalog_opensearch` → `aws_caller_identity`
- `module:catalog_opensearch` → `aws_region`
- `module:catalog_opensearch` → `aws_security_group`
- `module:catalog_service` → `aws_cloudwatch_log_group`
- `module:catalog_service` → `aws_ecs_cluster`
- `module:catalog_service` → `aws_iam_policy`
- `module:catalog_service` → `aws_service_discovery_private_dns_namespace`
- `module:cert_manager` → `aws_iam_policy_document`
- `module:checkout_service` → `aws_cloudwatch_log_group`
- `module:checkout_service` → `aws_ecs_cluster`
- `module:checkout_service` → `aws_service_discovery_private_dns_namespace`
- `module:ci-cd-pipeline` → `google_artifact_registry_repository`
- `module:ci-cd-pipeline` → `google_service_account`
- `module:cluster_autoscaler` → `aws_iam_policy_document`
- `module:deployment` → `azurerm_cognitive_account_customer_managed_key`
- `module:eks_managed_node_group` → `aws_eks_cluster`
- `module:enabled_google_apis` → `google_project`
- `module:external_dns` → `aws_iam_policy_document`
- `module:external_secrets` → `aws_iam_policy_document`
- `module:gke-nat` → `google_compute_router`
- `module:gke_development` → `google_project`
- `module:gke_production` → `google_project`
- `module:gke_staging` → `google_project`
- `module:iam_assumable_role_adot_amp` → `aws_partition`
- `module:iam_assumable_role_adot_amp` → `kubernetes_namespace_v1`
- `module:iam_assumable_role_adot_logs` → `aws_partition`
- `module:iam_assumable_role_adot_logs` → `kubernetes_namespace_v1`
- `module:istio-annotation` → `google_project`
- `module:istio-injection-label` → `google_project`
- `module:karpenter` → `aws_iam_policy_document`
- `module:kms` → `aws_iam_session_context`
- `module:orders_service` → `aws_cloudwatch_log_group`
- `module:orders_service` → `aws_ecs_cluster`
- `module:orders_service` → `aws_iam_policy`
- `module:orders_service` → `aws_service_discovery_private_dns_namespace`
- `module:project_services` → `google_project`
- `module:self_managed_node_group` → `aws_eks_cluster`
- `module:ui_service` → `aws_cloudwatch_log_group`
- `module:ui_service` → `aws_ecs_cluster`
- `module:ui_service` → `aws_service_discovery_private_dns_namespace`
- `module:velero` → `aws_iam_policy_document`
- `module:vpc` → `google_project_service`
- `module:vpc_endpoints` → `aws_security_group`

</details>

## 7. 한계 — 이 수집을 어디까지 믿을 수 있나

1. **어휘 매핑과 관심사 분류는 우리가 얹은 것이다.** 근거가 아니다.
2. **모듈의 모듈은 안 읽었다.** 23개 레지스트리 모듈을 읽었지만 그 모듈들이 또
   부르는 하위·외부 모듈은 따라가지 않았다.
3. **모듈 경계를 넘는 의존(module → module)은 세지 않았다.** 벤더 샘플의 실제
   의존 상당 부분이 그 층에 있다.
4. **정규식 추출이다.** HCL·bicep 파서가 아니라 블록 경계를 중괄호로 세고 참조를
   정규식으로 잡았다. `for_each`·동적 블록·`try()` 안의 참조는 놓칠 수 있다.
5. **`count_range`에 해당하는 필연 정보가 IaC에는 없다.** terraform·bicep은
   "이 자원이 저 자원을 **반드시** 요구한다"를 타입 수준에서 적지 않는다. 여기서
   모은 것은 *어떤 배포에서 실제로 참조했다*이지 *필수다*가 아니다.
6. **못 찾은 것과 없는 것을 구별한다.** 위 미관측 목록은 "이 29개 소스에서
   관측되지 않았다"는 뜻이고, 클라우드에 그 의존이 없다는 뜻이 아니다.

## 8. 이 수집이 실제로 바꾼 판단

- **`node → sshKey`는 클라우드의 필연이 아니다.** 29개 소스 전체에서 SSH 키
  자원이 0건이다. 관리형 컨테이너 플랫폼(ECS/Fargate·EKS·AKS·GKE)은 SSH를 쓰지
  않고, `iaas-baseline`조차 키를 VMSS에 인라인으로 넣는다. 우리 census가 이 간선을
  다섯 형태로 관측해 필수로 둔 것은 **cb-tumblebug이 VM에 SSH로 붙는 운영 방식을
  택한 결과**다.
- **spec·image는 자원이 아니라 속성이다.** IaC에서는 `instance_type = "t3.medium"`
  처럼 값이다. cb-tumblebug이 카탈로그를 ID 가진 REST 자원으로 노출하기 때문에
  우리가 그것을 그래프 간선으로 승격했다. `RESOURCE_SPEC`이 이미 `minVCpu`·
  `minMemoryGiB`를 **필드**로 갖고 있는 것과 어긋난다 — 필드가 맞았다.
- **Azure에서 보안 그룹과 네트워크의 방향이 뒤집힌다.** `iaas-baseline`에서
  `virtualNetworks → networkSecurityGroups`다(서브넷을 vNet 안에 인라인 선언하고
  그 서브넷이 NSG를 참조한다). 우리 census의 `securityGroup->vNet.required`가
  `{aws: true, azure: false, gcp: true}`인 것과, 커뮤니티 TOSCA 프로파일에서
  `Nsg → Region`(VNet 아님)인 것과 **같은 현상의 세 번째 독립 확인**이다.
- **자기 참조 간선 개념이 우리에게 없다.** `securityGroup → securityGroup`이
  3개 소스에서 나온다(규칙이 다른 SG를 `source_security_group_id`로 가리킨다).
- **가장 큰 공백은 신원·권한과 관측이다.** 경계 밖 인스턴스의 최다 범주다. 그리고
  이것은 `cloud_concerns.json`이 이미 말하고 있었다 — `cn.identity-access` ·
  `cn.operational-signal` · `cn.event-record` 셋 다 소비자 칸이 없다고 적혀 있다.
  위에서 내려온 조사와 아래에서 올라온 조사가 같은 자리를 짚었다.

