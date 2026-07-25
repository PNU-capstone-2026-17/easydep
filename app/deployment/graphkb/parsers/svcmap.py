"""svcmap — 앱 개념(app::) ↔ 벤더 관리형 서비스 타입의 동치 그래프.

## 왜 필요한가

core 층 13개는 전부 인프라(vm·vNet·subnet…)라 **앱 설계도가 말하는 것들 —
DB·큐·캐시·객체 스토리지 — 에 대응이 없었다.** 이 파서가 `app::` 개념 13종(P1의
9종 + 보강 2의 관리형 k8s·검색·스트림·API 게이트웨이)을 만들고 벤더 타입에 잇는다. `kb_equivalent_types`가 병합 그래프를 타므로, 이 파일이 생기는
순간 "DynamoDB는 Azure에서 뭐야?"가 관리형 서비스에서도 답이 된다.

## 근거 구조 — 다리를 건너면 등급이 떨어진다

기존 mapping-graph 82엣지는 전부 단일 출처(cb-spider) 짐작이었다. 여기는 독립
소스가 둘이다:

    MS 비교표(CC-BY-4.0)    "Amazon RDS ↔ Azure SQL"을 **문서가 명시**
    diagrams 분류(MIT)      프로바이더 모듈에 RDS 클래스가 **존재**

그러나 basis는 넷 다 **inferred(검수됨)**다. 표가 명시하는 것은 서비스 **이름**
수준이고, 그걸 타입 id(`AWS::RDS::DBInstance`)에 붙이는 마지막 걸음은 언제나 우리
손 검수이기 때문이다. 라벨이 가르는 것은 등급이 아니라 **어떤 독립 근거가 뒤에
있는가**다: 둘 다 → `svcmap-cross-checked`, MS만 → `ms-learn-comparison`,
diagrams만 → `mingrammer-taxonomy`, 손 검수만 → `svcmap-reviewed`.

## 실행 경계

이 대응은 **안내이지 배포 가능이 아니다.** cb-tumblebug 실행 경로는 VM·k8s까지라
관리형 서비스를 만들지 못한다 — `agent_api.equivalent_types`가 app:: 층을 지나면
그 사실을 답에 붙인다(`cap_csp_supports`와 같은 구분).

## 담지 않은 것 — 지우지 말 것

- **tencent** — MS 표에도 diagrams에도 없다(실측: mingrammer에 tencentcloud 모듈
  없음). 손 검수만으로 넣으면 기존 82엣지와 같은 처지라 P1에선 뺐다.
- **GCP CDN** — Cloud CDN은 백엔드 서비스에 붙는 **플래그**라 1:1 타입이 없다.
  억지로 ComputeBackendBucket을 대면 "CDN을 만들었다"로 읽힌다.
- **kt·ncp·nhn DNS 외 대부분** — 소스도 타입도 없는 조합은 만들지 않는다.
  nhn 객체 스토리지·DNS처럼 **타입 이름이 스스로 용도를 말하는 것만** 손 검수로 담았다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from graphkb.model import Edge, Graph, Node
from kbcommon.fetch import describe_source_set, fetch_cached
from kbcommon.invariants import announce
from kbcommon.sources import SOURCES

EVIDENCE_CROSS = "svcmap-cross-checked"
EVIDENCE_MS = "ms-learn-comparison"
EVIDENCE_DIAGRAMS = "mingrammer-taxonomy"
EVIDENCE_REVIEWED = "svcmap-reviewed"

#: MS 비교표 파일. **한 파일이 아니다** — services.md 하나였던 것이 카테고리별로
#: 쪼개져 있었다(404를 직접 겪음). 행수가 급감하면 재편이 또 일어난 것이다.
MS_AWS_FILES = (
    "docs/aws-professional/index.md",
    "docs/aws-professional/compute.md",
    "docs/aws-professional/databases.md",
    "docs/aws-professional/messaging.md",
    "docs/aws-professional/storage.md",
    "docs/aws-professional/networking.md",
)
MS_GCP_FILES = ("docs/gcp-professional/services.md",)

#: diagrams 모듈에서 클래스 어휘를 모을 파일들. 없는 파일은 조용히 건너뛴다 —
#: 프로바이더마다 분류가 다르다(ibm은 database.py가 없고 data.py다).
_DIAGRAM_MODULES = (
    "analytics.py", "compute.py", "database.py", "devtools.py", "integration.py",
    "network.py", "security.py", "storage.py", "web.py", "application.py",
    "data.py", "applications.py", "general.py",
)

#: 우리 프로바이더명 → diagrams 디렉터리명.
_DIAGRAM_DIR = {
    "aws": "aws", "azure": "azure", "gcp": "gcp", "ibm": "ibm",
    "alibaba": "alibabacloud", "oracle": "oci", "openstack": "openstack",
}


def _b(type_id: str, ms: tuple[str, ...] = (), dg: tuple[str, ...] = (),
       note: str = "") -> dict:
    """바인딩 하나: 타입 id + 그 프로바이더에서 찾을 소스 키워드."""
    return {"type_id": type_id, "ms": ms, "dg": dg, "note": note}


#: 개념 → 프로바이더 → 바인딩 목록. **type_id는 빌드가 병합 그래프에 대조한다** —
#: 실재하지 않으면 빌드가 죽는다(core 매핑의 "후보 전부 대조, 0건 실패" 선례).
CONCEPTS: dict[str, dict] = {
    "relationalDatabase": {
        "display": "relational database",
        "bindings": {
            "aws": [_b("aws::AWS::RDS::DBInstance", ("Amazon RDS",), ("RDS",))],
            "azure": [
                _b("azure::Microsoft.Sql/servers/databases", ("Azure SQL",), ("SQLDatabases",)),
                _b("azure::Microsoft.DBforPostgreSQL/flexibleServers",
                   ("Azure Database for PostgreSQL",), ("DatabaseForPostgresqlServers",)),
                _b("azure::Microsoft.DBforMySQL/flexibleServers",
                   ("Azure Database for MySQL",), ("DatabaseForMysqlServers",)),
            ],
            "gcp": [_b("gcp::SQLInstance", ("Cloud SQL",), ("SQL",))],
            "alibaba": [_b("alibaba::alicloud_db_instance", (),
                          ("RelationalDatabaseService", "ApsaradbPostgresql"))],
            "ibm": [_b("ibm::ibm_database", (), ("Cloudant",),
                       "ICD (Databases for …) — PostgreSQL/MySQL are split by plan")],
            "openstack": [_b("openstack::openstack_db_instance_v1", (), (),
                             "Trove. The type name states what it is for")],
            "oracle": [_b("oracle::oci_database_db_system", (), ("DatabaseService", "Dbcs"))],
        },
    },
    "nosqlDatabase": {
        "display": "NoSQL database",
        "bindings": {
            # MS 표의 링크 텍스트가 "Amazon" 없이 `[DynamoDB]`다(실측).
            "aws": [_b("aws::AWS::DynamoDB::Table", ("Amazon DynamoDB", "DynamoDB"), ("Dynamodb",))],
            "azure": [_b("azure::Microsoft.DocumentDB/databaseAccounts",
                         ("Azure Cosmos DB", "Cosmos DB"), ("CosmosDb",))],
            "gcp": [_b("gcp::FirestoreDatabase", ("Firestore",), ("Firestore",))],
            "alibaba": [_b("alibaba::alicloud_mongodb_instance", (), ("ApsaradbMongodb",))],
        },
    },
    "keyValueCache": {
        "display": "in-memory cache",
        "bindings": {
            "aws": [_b("aws::AWS::ElastiCache::CacheCluster",
                       ("Amazon ElastiCache", "ElastiCache"), ("Elasticache",))],
            "azure": [_b("azure::Microsoft.Cache/redis",
                         ("Azure Cache for Redis",), ("CacheForRedis",))],
            "gcp": [_b("gcp::RedisInstance", ("Memorystore",), ("Memorystore",))],
            "alibaba": [_b("alibaba::alicloud_kvstore_instance", (), ("ApsaradbRedis",),
                           "KVStore (Redis family)")],
        },
    },
    "messageQueue": {
        "display": "message queue",
        "bindings": {
            "aws": [_b("aws::AWS::SQS::Queue",
                       ("Simple Queue Service", "Amazon SQS", "(SQS)"), ("SQS",))],
            "azure": [_b("azure::Microsoft.ServiceBus/namespaces",
                         ("Service Bus",), ("ServiceBus",))],
            "gcp": [_b("gcp::PubSubTopic", ("Pub/Sub",), ("PubSub", "Pubsub"))],
            "alibaba": [_b("alibaba::alicloud_mns_queue", (), (),
                           "MNS. The type name states what it is for")],
        },
    },
    "objectStorage": {
        "display": "object storage",
        "bindings": {
            "aws": [_b("aws::AWS::S3::Bucket", ("Amazon S3", "(S3)"), ("S3",))],
            "azure": [_b("azure::Microsoft.Storage/storageAccounts",
                         ("Blob Storage", "Blob storage"), ("BlobStorage",),
                         "Blob is a sub-service of storageAccounts — no dedicated top-level type")],
            "gcp": [_b("gcp::StorageBucket", ("Cloud Storage",), ("Storage", "GCS"))],
            "alibaba": [_b("alibaba::alicloud_oss_bucket", (), ("ObjectStorageService", "Oss"))],
            "ibm": [_b("ibm::ibm_cos_bucket", (), ("ObjectStorage",))],
            "oracle": [_b("oracle::oci_objectstorage_bucket", (), ("ObjectStorage", "Buckets"))],
            "openstack": [_b("openstack::openstack_objectstorage_container_v1", (),
                             ("ObjectStore", "Swift"))],
            "nhn": [_b("nhn::nhncloud_objectstorage_container_v1", (), (),
                       "the type name states what it is for")],
        },
    },
    "serverlessFunction": {
        "display": "serverless function",
        "bindings": {
            "aws": [_b("aws::AWS::Lambda::Function", ("AWS Lambda", "Lambda"), ("Lambda",))],
            "azure": [_b("azure::Microsoft.Web/sites",
                         ("Azure Functions",), ("FunctionApps",),
                         "no dedicated Functions type — it deploys as Web/sites (kind=functionapp)")],
            "gcp": [_b("gcp::CloudFunctions2Function", ("Cloud Functions",), ("Functions",))],
            "alibaba": [_b("alibaba::alicloud_fc_function", (), (),
                           "Function Compute. The type name states what it is for")],
        },
    },
    "cdn": {
        "display": "CDN",
        "bindings": {
            "aws": [_b("aws::AWS::CloudFront::Distribution",
                       ("Amazon CloudFront", "CloudFront"), ("CloudFront",))],
            "azure": [_b("azure::Microsoft.Cdn/profiles",
                         ("Azure CDN", "Content Delivery Network", "Front Door"), ("CDNProfiles",))],
            "alibaba": [_b("alibaba::alicloud_cdn_domain_new", (), ("CDN",))],
        },
    },
    "dnsZone": {
        "display": "DNS zone",
        "bindings": {
            "aws": [_b("aws::AWS::Route53::HostedZone",
                       ("Amazon Route 53", "Route 53"), ("Route53",))],
            "azure": [_b("azure::Microsoft.Network/dnsZones", ("Azure DNS",), ("DNSZones",))],
            "gcp": [_b("gcp::DNSManagedZone", ("Cloud DNS",), ("DNS",))],
            "ibm": [_b("ibm::ibm_dns_zone", (), (), "the type name states what it is for")],
            "oracle": [_b("oracle::oci_dns_zone", (), (),
                       "the diagrams oci module has no DNS class (measured) — "
                       "the type name states what it is for")],
            "openstack": [_b("openstack::openstack_dns_zone_v2", (), (), "Designate")],
            "nhn": [_b("nhn::nhncloud_dns_zone_v2", (), (), "the type name states what it is for")],
        },
    },
    "secretStore": {
        "display": "secret store",
        "bindings": {
            "aws": [_b("aws::AWS::SecretsManager::Secret",
                       ("Secrets Manager",), ("SecretsManager",))],
            "azure": [_b("azure::Microsoft.KeyVault/vaults", ("Key Vault",), ("KeyVaults",))],
            "gcp": [_b("gcp::SecretManagerSecret", ("Secret Manager",), ("KeyManagementService",))],
            "alibaba": [_b("alibaba::alicloud_kms_secret", (), (), "KMS Secret")],
            "oracle": [_b("oracle::oci_vault_secret", (), ("Vault",))],
        },
    },
    # --- 이하 4종은 확장(보강 2, 2026-07-24) — 같은 근거 규율이다 ---
    "containerService": {
        "display": "managed Kubernetes",
        "bindings": {
            "aws": [_b("aws::AWS::EKS::Cluster",
                       ("Elastic Kubernetes Service", "Amazon EKS"), ("EKS",))],
            "azure": [_b("azure::Microsoft.ContainerService/managedClusters",
                         ("Azure Kubernetes Service",), ("AKS", "KubernetesServices"))],
            "gcp": [_b("gcp::ContainerCluster",
                       ("Google Kubernetes Engine", "GKE"), ("GKE", "KubernetesEngine"))],
            "alibaba": [_b("alibaba::alicloud_cs_managed_kubernetes", (),
                           ("ContainerService", "ACK"),
                           "the type name states what it is for (cs = Container Service)")],
            "oracle": [_b("oracle::oci_containerengine_cluster", (),
                          ("ContainerEngine", "OKE"),
                          "the type name states what it is for (OKE)")],
            "ibm": [_b("ibm::ibm_container_vpc_cluster", (), ("IKS",),
                       "IBM Kubernetes Service (VPC). ibm_container_cluster for classic "
                       "is the older generation, so only one is included")],
            "openstack": [_b("openstack::openstack_containerinfra_cluster_v1", (), (),
                             "Magnum. The type name states what it is for")],
        },
    },
    "searchIndex": {
        "display": "managed search index",
        "bindings": {
            "aws": [_b("aws::AWS::OpenSearchService::Domain",
                       ("OpenSearch",), ("ElasticsearchService",))],
            "azure": [_b("azure::Microsoft.Search/searchServices",
                         ("Azure AI Search", "Cognitive Search", "Azure Search"),
                         ("SearchServices",))],
            "alibaba": [_b("alibaba::alicloud_elasticsearch_instance", (),
                           ("ElasticSearch", "OpenSearch"),
                           "the type name states what it is for")],
            "oracle": [_b("oracle::oci_opensearch_opensearch_cluster", (), (),
                          "the type name states what it is for")],
        },
    },
    "eventStream": {
        "display": "event stream",
        "bindings": {
            "aws": [_b("aws::AWS::Kinesis::Stream", ("Kinesis",),
                       ("KinesisDataStreams", "Kinesis"))],
            "azure": [_b("azure::Microsoft.EventHub/namespaces",
                         ("Event Hubs",), ("EventHubs",))],
            # Pub/Sub는 큐·스트림 축을 하나가 겸한다(GCP의 설계) — messageQueue에도
            # 대응돼 있다. 한쪽만 담으면 "GCP엔 스트림이 없다"로 읽혀 둘 다 담고
            # 겸한다는 사실을 노트로 남긴다.
            "gcp": [_b("gcp::PubSubTopic", ("Pub/Sub",), ("PubSub", "Pubsub"),
                       "Pub/Sub alone serves as both queue and stream — "
                       "also mapped to app::messageQueue")],
            "alibaba": [_b("alibaba::alicloud_alikafka_instance", (), (),
                           "AliKafka. The type name states what it is for")],
            "oracle": [_b("oracle::oci_streaming_stream", (), (),
                          "OCI Streaming. The type name states what it is for (measured)")],
        },
    },
    "apiGateway": {
        "display": "API gateway",
        "bindings": {
            "aws": [_b("aws::AWS::ApiGateway::RestApi",
                       ("API Gateway", "Amazon API Gateway"), ("APIGateway",))],
            "azure": [_b("azure::Microsoft.ApiManagement/service",
                         ("API Management",), ("APIManagement",))],
            "gcp": [_b("gcp::APIGatewayGateway", ("API Gateway", "Apigee"),
                       ("APIGateway",),
                       "the gateway resource type itself. Apigee is a separate "
                       "product, so it is not included")],
            "alibaba": [_b("alibaba::alicloud_api_gateway_group", (), (),
                           "the API group is the gateway's deployment unit "
                           "(domains attach to the group)")],
            "oracle": [_b("oracle::oci_apigateway_gateway", (), (),
                          "the type name states what it is for")],
        },
    },
}


def _fetch_text(base_key: str, rel_path: str) -> str | None:
    """핀 박힌 소스에서 파일 하나. 404는 None — 프로바이더마다 모듈 구성이 다르다."""
    source = SOURCES[base_key]
    cache_name = f"{base_key}-{rel_path.replace('/', '-')}"
    try:
        path = fetch_cached(f"{source.url}/{rel_path}", cache_name)
    except Exception:
        return None
    return path.read_text(encoding="utf-8")


def _ms_rows(files: tuple[str, ...]) -> list[str]:
    """비교표의 데이터 행(구분선·헤더 제외). 행 전체를 문자열로 둔다 —
    파일마다 열 구성이 달라(3열/4열/야짐) 셀 위치에 기대면 깨진다."""
    rows: list[str] = []
    for rel in files:
        text = _fetch_text("ms-architecture-center", rel)
        if text is None:
            continue
        for line in text.splitlines():
            if line.startswith("|") and not set(line) <= {"|", "-", " ", ":"}:
                rows.append(line)
    return rows


_CLASS = re.compile(r"^class\s+([A-Za-z0-9_]+)\s*[(:]", re.M)

_LINK_TEXT = re.compile(r"\[([^\]]+)\]")


def _row_matches(row: str, keywords: tuple[str, ...]) -> bool:
    """키워드가 **서비스 이름 칸(링크 텍스트)**에 있는가.

    행 전체에서 찾으면 설명 칸의 언급에 걸린다 — 실측에서 'Amazon RDS'가
    Compute Optimizer 행(설명에 RDS가 지나가듯 나온다)에 먼저 걸렸다. 표의
    서비스 이름은 전부 마크다운 링크라, 링크 텍스트만 보면 그 오염이 사라진다.
    """
    texts = _LINK_TEXT.findall(row)
    return any(any(k in text for text in texts) for k in keywords)


def _diagram_vocab(provider: str) -> set[str]:
    """diagrams 모듈의 클래스 이름 어휘(소문자)."""
    directory = _DIAGRAM_DIR.get(provider)
    if directory is None:
        return set()
    vocab: set[str] = set()
    for module in _DIAGRAM_MODULES:
        text = _fetch_text("mingrammer-diagrams", f"diagrams/{directory}/{module}")
        if text is None:
            continue
        vocab.update(name.lower() for name in _CLASS.findall(text))
    return vocab


def _existing_node_ids(output_dir: Path) -> set[str]:
    """다른 그래프 산출물의 노드 id 전부 — 바인딩 검증 대상."""
    from graphkb.agent_api import GRAPH_FILES
    from kbcommon import artifact

    ids: set[str] = set()
    for name in GRAPH_FILES:
        if name == "svcmap-graph.json":
            continue  # 자기 자신에 대조하면 검증이 헛돈다
        path = artifact.resolve(output_dir, name)
        if path is None:
            continue
        for node in artifact.load_json(path).get("nodes", []):
            ids.add(node["id"])
    return ids


def _short(type_id: str) -> str:
    return type_id.split("::", 1)[-1]


def build_graph(output_dir: Path) -> tuple[Graph, list[dict], list[str]]:
    aws_rows = _ms_rows(MS_AWS_FILES)
    gcp_rows = _ms_rows(MS_GCP_FILES)
    # 표 재편 감지 — services.md가 카테고리 파일들로 쪼개졌던 그 사고를 다음엔
    # 행수 급감으로 잡는다. 실측 기준 aws 191 · gcp 199행이었다.
    if len(aws_rows) < 50 or len(gcp_rows) < 50:
        raise RuntimeError(
            f"MS 비교표 행수가 급감했다(aws {len(aws_rows)} · gcp {len(gcp_rows)}). "
            "문서가 재편됐는지 확인할 것 — 파일 목록이 낡았을 수 있다."
        )

    vocabs = {p: _diagram_vocab(p) for p in _DIAGRAM_DIR}
    known = _existing_node_ids(output_dir)

    graph = Graph()
    coverage: list[dict] = []
    warnings: list[str] = []
    unknown: list[str] = []

    for concept, spec in CONCEPTS.items():
        app_id = f"app::{concept}"
        graph.add_node(Node(
            id=app_id, layer="app", provider="app",
            display_name=spec["display"], source="svcmap", kind="app_concept",
        ))
        rows_out: list[dict] = []
        for provider, bindings in spec["bindings"].items():
            for binding in bindings:
                type_id = binding["type_id"]
                if type_id not in known:
                    unknown.append(type_id)
                    continue

                ms_hit = None
                search_rows = (
                    gcp_rows if provider == "gcp"
                    else aws_rows if provider == "aws"
                    else aws_rows + gcp_rows  # azure는 양쪽 표 모두의 상대편이다
                )
                for row in search_rows:
                    if _row_matches(row, binding["ms"]):
                        ms_hit = row.strip()
                        break
                dg_hit = any(k.lower() in vocabs.get(provider, set())
                             for k in binding["dg"])

                if ms_hit and dg_hit:
                    evidence = EVIDENCE_CROSS
                elif ms_hit:
                    evidence = EVIDENCE_MS
                elif dg_hit:
                    evidence = EVIDENCE_DIAGRAMS
                else:
                    evidence = EVIDENCE_REVIEWED
                    if not binding["note"]:
                        warnings.append(
                            f"{app_id} → {type_id}: 독립 근거가 없는데 이유(note)도 없다"
                        )

                graph.add_node(Node(
                    id=type_id, layer="vendor", provider=provider,
                    display_name=_short(type_id), source="svcmap",
                ))
                graph.add_edge(Edge(
                    from_id=app_id, to_id=type_id, type="equivalent_to",
                    via_property="", required=False, cardinality="one",
                    evidence=evidence, reviewed=True,
                ))
                rows_out.append({
                    "provider": provider,
                    "typeId": type_id,
                    "evidence": evidence,
                    # 매칭된 원문을 남긴다 — 엣지에 note 칸이 없으므로 여기가
                    # "표가 실제로 뭐라고 했는지"를 확인할 유일한 자리다.
                    "msRow": (ms_hit[:220] if ms_hit else None),
                    "note": binding["note"] or None,
                })
        coverage.append({
            "concept": app_id,
            "display": spec["display"],
            "bindings": rows_out,
            "skipped": _SKIPPED.get(concept),
        })

    if unknown:
        raise RuntimeError(
            "바인딩 타입이 그래프에 없다(손 표가 낡았거나 오타): "
            + ", ".join(sorted(unknown))
        )
    return graph, coverage, warnings


#: 일부러 안 담은 조합과 이유. 지우면 다음 사람이 다시 넣는다.
_SKIPPED = {
    "relationalDatabase": (
        "tencent (no independent source — mingrammer has no tencentcloud module)"
    ),
    "nosqlDatabase": (
        "tencent (same reason) · gcp Bigtable (wide-column, so not grouped with "
        "document DBs)"
    ),
    "cdn": "gcp (Cloud CDN is a flag on a backend service, so there is no 1:1 type)",
    "messageQueue": (
        "azure Storage Queue (a separate service from ServiceBus — naming only one "
        "misleads)"
    ),
    "containerService": (
        "nhn (the containerinfra type is not in our graph — measured) · tencent "
        "(same rule)"
    ),
    "searchIndex": (
        "gcp (KCC has no 1:1 managed search type — Vertex AI Search is not a "
        "resource type) · ibm (ibm_resource_instance is a generic type, so naming "
        "it misleads)"
    ),
    "apiGateway": "tencent (same rule)",
}


def build(output: Path, *, output_dir: Path | None = None) -> Graph:
    base = output_dir or output.parent
    graph, coverage, warnings = build_graph(base)

    ms_paths = []
    for rel in MS_AWS_FILES + MS_GCP_FILES:
        cache = f"ms-architecture-center-{rel.replace('/', '-')}"
        from kbcommon.fetch import cache_dir

        candidate = cache_dir() / cache
        if candidate.exists():
            ms_paths.append(candidate)
    graph.provenance = [describe_source_set(ms_paths, "ms-architecture-center")]
    # mingrammer는 파일이 많아 대표로 하나만 해시에 넣지 않고 소스 핀만 남긴다 —
    # 태그 고정이라 핀 자체가 재현 정보다.
    graph.provenance.append({
        "source": "mingrammer-diagrams",
        "pin_kind": "tag",
        "pin": SOURCES["mingrammer-diagrams"].pin,
        "url": SOURCES["mingrammer-diagrams"].url,
        "sha256": "0" * 64,
        "bytes": None,
        "fetched_at": None,
        "note": (
            "Class vocabulary used for cross-checking. Pinned to a tag, so the "
            "pin itself is the reproduction info."
        ),
    })
    payload = graph.to_dict()
    payload["_coverage"] = coverage
    announce(graph.save(output), "graphkb/svcmap")

    # save가 _coverage를 모르면 다시 쓴다 — 확인 후 결정되는 부분이라 방어적으로.
    import json as _json

    saved = _json.loads(output.read_text(encoding="utf-8"))
    if "_coverage" not in saved:
        saved["_coverage"] = coverage
        output.write_text(
            _json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    edges = len(graph.edges)
    cross = sum(1 for e in graph.edges if e.evidence == EVIDENCE_CROSS)
    ms_only = sum(1 for e in graph.edges if e.evidence == EVIDENCE_MS)
    dg_only = sum(1 for e in graph.edges if e.evidence == EVIDENCE_DIAGRAMS)
    hand = sum(1 for e in graph.edges if e.evidence == EVIDENCE_REVIEWED)
    print(
        f"svcmap: 개념 {len(CONCEPTS)}개 · 엣지 {edges}개 "
        f"(교차 {cross} · MS만 {ms_only} · diagrams만 {dg_only} · 손검수 {hand}) → {output}"
    )
    for warning in warnings:
        print(f"  ⚠ {warning}", file=sys.stderr)
    return graph
