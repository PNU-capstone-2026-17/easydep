"""application/runtime 의도와 cloud capability/binding의 공개 계약을 검증한다."""

from pathlib import Path

from app.requirements.resources.application_cloud import (
    ApplicationRuntimeContract,
    BindingEndpoint,
    CloudCapabilityContract,
    ContractBinding,
    ContractFact,
    DeploymentBindingContract,
    application_intent_contract_from_requirements,
    cloud_capability_contract_from_requirements,
    dependency_declarations,
    derive_deployment_bindings,
    infer_application_contract,
    merge_application_contracts,
    validate_application_consistency,
    validate_binding_consistency,
)
from app.requirements.resources.application_cloud import (
    test_environment as contract_test_environment,
)


def test_contract_core_accepts_new_fact_kinds_without_schema_change():
    contract = ApplicationRuntimeContract(
        facts=[
            ContractFact(
                id="future-runtime",
                kind="runtime.vendor.example",
                attributes={"newField": {"nested": [1, 2, 3]}},
                extensions={"team.example/option": True},
            )
        ]
    )

    assert contract.facts[0].attributes["newField"]["nested"] == [1, 2, 3]
    assert contract.model_provenance.standards_compliant == []


def test_persistent_capability_uses_semantics_not_case_specific_need_id():
    application = ApplicationRuntimeContract(
        facts=[
            ContractFact(
                id="intent.storage",
                kind="runtime.storage.intent",
                attributes={
                    "durability": "persistent",
                    "accessScope": "node-filesystem",
                    "accessPath": "/srv/catalog-data",
                },
            ),
            ContractFact(
                id="observed.storage",
                kind="runtime.storage",
                attributes={"accessPath": "/srv"},
            ),
        ]
    )
    cloud = CloudCapabilityContract(
        facts=[
            ContractFact(
                id="capability.arbitrary_storage_name",
                kind="cloud.capability.arbitrary_storage_name",
                attributes={
                    "required": True,
                    "applicationState": {
                        "durability": "persistent",
                        "accessScope": "node-filesystem",
                        "accessPath": "/srv/catalog-data",
                    },
                },
            )
        ]
    )

    planned_cloud, bindings = derive_deployment_bindings(
        application, cloud, DeploymentBindingContract()
    )

    mount = next(fact for fact in planned_cloud.facts if fact.kind == "cloud.storage.mount")
    storage_binding = next(item for item in bindings.bindings if item.kind == "storage")
    assert mount.attributes["mountPath"] == "/srv/catalog-data"
    assert storage_binding.consumes.fact_id == "intent.storage"


def test_dependency_renderer_rejects_contract_code_injection():
    contract = ApplicationRuntimeContract(
        facts=[
            ContractFact(
                id="unsafe",
                kind="build.dependency",
                attributes={
                    "declarations": [
                        {
                            "configuration": "implementation",
                            "coordinate": "example:lib:1.0'\nprintln('unsafe')",
                        }
                    ]
                },
            )
        ]
    )

    try:
        dependency_declarations(contract)
    except ValueError as error:
        assert "Unsafe dependency coordinate" in str(error)
    else:
        raise AssertionError("Unsafe coordinate was accepted")


def test_generated_openapi_and_spring_configuration_derive_build_dependencies(tmp_path):
    source = tmp_path / "src/main/java/example/Api.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import io.swagger.v3.oas.annotations.media.Schema;\n"
        "import org.openapitools.jackson.nullable.JsonNullable;\n"
        "class Api {}\n",
        encoding="utf-8",
    )
    configuration = tmp_path / "src/main/resources/application.yml"
    configuration.parent.mkdir(parents=True)
    configuration.write_text(
        "management:\n  endpoints:\n    web:\n      exposure:\n        include: health\n"
        "spring:\n  flyway:\n    enabled: true\n",
        encoding="utf-8",
    )

    declarations = dependency_declarations(infer_application_contract(tmp_path))

    assert (
        "implementation",
        "org.springdoc:springdoc-openapi-starter-webmvc-ui:2.6.0",
    ) in declarations
    assert (
        "implementation",
        "org.openapitools:jackson-databind-nullable:0.2.10",
    ) in declarations
    assert (
        "implementation",
        "org.springframework.boot:spring-boot-starter-actuator",
    ) in declarations
    assert ("implementation", "org.flywaydb:flyway-core") in declarations


def test_flyway_postgresql_derives_database_specific_runtime_module(tmp_path):
    configuration = tmp_path / "src/main/resources/application.yml"
    configuration.parent.mkdir(parents=True)
    configuration.write_text(
        "spring:\n"
        "  datasource:\n"
        "    url: ${DATABASE_URL:jdbc:postgresql://state:5432/app}\n"
        "  flyway:\n"
        "    enabled: true\n",
        encoding="utf-8",
    )

    contract = infer_application_contract(tmp_path)
    declarations = dependency_declarations(contract)

    assert ("implementation", "org.flywaydb:flyway-core") in declarations
    assert (
        "runtimeOnly",
        "org.flywaydb:flyway-database-postgresql",
    ) in declarations
    database_url = next(
        fact
        for fact in contract.facts
        if fact.id == "observed.environment.database_url"
    )
    assert database_url.attributes["valuePrefix"] == "jdbc:postgresql://"


def test_test_environment_cannot_override_process_control_variables(tmp_path: Path):
    contract = ApplicationRuntimeContract(
        facts=[
            ContractFact(
                id="unsafe-env",
                kind="runtime.environment",
                attributes={"name": "JAVA_TOOL_OPTIONS", "testValueTemplate": "-javaagent:bad"},
            ),
            ContractFact(
                id="owned-env",
                kind="runtime.environment",
                attributes={"name": "EASYDEP_DATA_URL", "testValueTemplate": "file:{temp}/data"},
            ),
        ]
    )

    environment = contract_test_environment(contract, tmp_path)

    assert "JAVA_TOOL_OPTIONS" not in environment
    assert environment["EASYDEP_DATA_URL"].endswith("/data")


def test_legacy_cloud_adapter_preserves_unknown_accepted_capability():
    contract = cloud_capability_contract_from_requirements(
        {
            "deployment_needs": {
                "future_accelerator_pool": {
                    "required": True,
                    "decision": "accepted",
                    "metadata": {"vendorShape": "example"},
                    "requirementIds": ["R-9"],
                }
            }
        }
    )

    assert contract.facts[0].kind == "cloud.capability.future_accelerator_pool"
    assert contract.facts[0].attributes["vendorShape"] == "example"
    assert contract.facts[0].source_refs == ["R-9"]


def test_open_need_projects_only_explicit_application_state_intent():
    contract = application_intent_contract_from_requirements(
        {
            "deployment_needs": {
                "arbitrary_state_need": {
                    "required": True,
                    "decision": "accepted",
                    "requirementIds": ["R-1"],
                    "evidenceSpans": ["state on the VM filesystem"],
                    "metadata": {
                        "applicationState": {
                            "durability": "persistent",
                            "accessScope": "node-filesystem",
                        },
                        "vendorSpecificFutureValue": 7,
                    },
                },
                "unrelated_open_need": {
                    "required": True,
                    "decision": "accepted",
                    "requirementIds": ["R-2"],
                    "metadata": {"future": "preserved elsewhere"},
                },
            }
        }
    )

    assert len(contract.facts) == 1
    fact = contract.facts[0]
    assert fact.id == "intent.arbitrary_state_need.state"
    assert fact.kind == "runtime.storage.intent"
    assert fact.attributes == {
        "required": True,
        "durability": "persistent",
        "accessScope": "node-filesystem",
    }
    assert fact.source_refs == ["R-1"]
    assert fact.evidence_refs == ["state on the VM filesystem"]
    assert fact.provenance_class == "adapted"


def _external_postgresql_requirements() -> dict:
    return {
        "deployment_needs": {
            "state": {
                "required": True,
                "decision": "accepted",
                "requirementIds": ["NFR-STATE"],
                "metadata": {
                    "applicationState": {"durability": "persistent"},
                },
            },
            "database": {
                "required": True,
                "decision": "accepted",
                "requirementIds": ["NFR-DB"],
                "metadata": {
                    "databaseEngine": "Postgres",
                    "databaseVersion": "16",
                    "deploymentMode": "self-hosted container",
                    "embedded": False,
                    "managedServiceAllowed": False,
                },
            },
            "configuration": {
                "required": True,
                "decision": "accepted",
                "requirementIds": ["FR-CONFIG"],
                "metadata": {
                    "environment_variables": [
                        "DATABASE_URL",
                        "DATABASE_USER",
                        "DATABASE_PASSWORD",
                    ]
                },
            },
        }
    }


def test_application_validator_rejects_embedded_database_against_external_intent(
    tmp_path: Path,
):
    resources = tmp_path / "src/main/resources/application.yml"
    resources.parent.mkdir(parents=True)
    resources.write_text(
        "spring:\n  datasource:\n    url: jdbc:h2:mem:generated\n"
        "    driver-class-name: org.h2.Driver\n",
        encoding="utf-8",
    )
    (tmp_path / "build.gradle").write_text(
        "dependencies { runtimeOnly 'com.h2database:h2' }",
        encoding="utf-8",
    )
    intent = application_intent_contract_from_requirements(
        _external_postgresql_requirements()
    )
    declared = merge_application_contracts(intent, None)
    contract = infer_application_contract(
        tmp_path, declared.model_dump(mode="json", by_alias=True)
    )

    diagnostics = validate_application_consistency(tmp_path, contract)
    codes = {item.code for item in diagnostics}

    assert {
        "APP-DB-ENGINE-001",
        "APP-DB-MODE-001",
        "APP-STORAGE-001",
        "APP-CONFIG-001",
    } <= codes


def test_application_validator_accepts_external_postgresql_contract(tmp_path: Path):
    resources = tmp_path / "src/main/resources/application.yml"
    resources.parent.mkdir(parents=True)
    resources.write_text(
        "spring:\n  datasource:\n"
        "    url: ${DATABASE_URL}\n"
        "    username: ${DATABASE_USER}\n"
        "    password: ${DATABASE_PASSWORD}\n"
        "    driver-class-name: org.postgresql.Driver\n",
        encoding="utf-8",
    )
    (tmp_path / "build.gradle").write_text(
        "dependencies { runtimeOnly 'org.postgresql:postgresql' }",
        encoding="utf-8",
    )
    intent = application_intent_contract_from_requirements(
        _external_postgresql_requirements()
    )
    declared = merge_application_contracts(intent, None)
    contract = infer_application_contract(
        tmp_path, declared.model_dump(mode="json", by_alias=True)
    )

    diagnostics = validate_application_consistency(tmp_path, contract)

    assert diagnostics == []


def test_application_validator_rejects_source_controlled_secret_defaults(tmp_path: Path):
    resources = tmp_path / "src/main/resources/application.yml"
    resources.parent.mkdir(parents=True)
    resources.write_text(
        "spring:\n  datasource:\n"
        "    url: ${DATABASE_URL}\n"
        "    username: ${DATABASE_USER}\n"
        "    password: ${DATABASE_PASSWORD:postgres}\n"
        "    driver-class-name: org.postgresql.Driver\n",
        encoding="utf-8",
    )
    (tmp_path / "build.gradle").write_text(
        "dependencies { runtimeOnly 'org.postgresql:postgresql' }",
        encoding="utf-8",
    )
    intent = application_intent_contract_from_requirements(
        _external_postgresql_requirements()
    )
    contract = infer_application_contract(
        tmp_path,
        merge_application_contracts(intent, None).model_dump(mode="json", by_alias=True),
    )

    diagnostics = validate_application_consistency(tmp_path, contract)
    secret = next(item for item in diagnostics if item.code == "APP-CONFIG-SECRET-001")

    assert secret.details["keysWithDefaults"] == ["DATABASE_PASSWORD"]


def test_application_validator_reports_missing_observed_dependency(tmp_path: Path):
    source = tmp_path / "src/main/java/Note.java"
    source.parent.mkdir(parents=True)
    source.write_text("import jakarta.persistence.Entity;", encoding="utf-8")
    (tmp_path / "build.gradle").write_text("dependencies {}", encoding="utf-8")
    contract = infer_application_contract(tmp_path)

    diagnostics = validate_application_consistency(tmp_path, contract)

    assert {item.code for item in diagnostics} == {"APP-DEP-001"}


def test_application_contract_observes_placeholder_yaml_server_port(tmp_path: Path):
    resources = tmp_path / "src/main/resources/application.yml"
    resources.parent.mkdir(parents=True)
    resources.write_text(
        "server:\n  port: ${SERVER_PORT:9090}\n\nspring:\n  main:\n    banner-mode: off\n",
        encoding="utf-8",
    )

    contract = infer_application_contract(tmp_path)

    port = next(fact for fact in contract.facts if fact.kind == "runtime.port")
    assert port.attributes["port"] == 9090


def test_application_contract_handles_large_nonmatching_yaml_without_backtracking(
    tmp_path: Path,
):
    resources = tmp_path / "src/main/resources/application.yml"
    resources.parent.mkdir(parents=True)
    resources.write_text(
        "spring:\n" + "".join(f"  setting{index}: value\n" for index in range(5000)),
        encoding="utf-8",
    )

    contract = infer_application_contract(tmp_path)

    port = next(fact for fact in contract.facts if fact.kind == "runtime.port")
    assert port.attributes["port"] == 8080


def test_directly_referenced_database_drivers_are_compile_dependencies(tmp_path: Path):
    source = tmp_path / "src/main/java/DataSources.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import org.sqlite.SQLiteDataSource;\nimport org.h2.jdbcx.JdbcDataSource;",
        encoding="utf-8",
    )

    declarations = dependency_declarations(infer_application_contract(tmp_path))

    assert ("implementation", "org.xerial:sqlite-jdbc:3.46.1.0") in declarations
    assert ("implementation", "com.h2database:h2") in declarations


def test_application_validator_reports_conflicting_database_markers(tmp_path: Path):
    resources = tmp_path / "src/main/resources/application.properties"
    resources.parent.mkdir(parents=True)
    resources.write_text(
        "spring.datasource.url=jdbc:sqlite:/data/app.db\n"
        "spring.datasource.driver-class-name=org.h2.Driver\n",
        encoding="utf-8",
    )
    (tmp_path / "build.gradle").write_text(
        "dependencies { runtimeOnly 'org.xerial:sqlite-jdbc:3.46.1.0'; "
        "runtimeOnly 'com.h2database:h2' }",
        encoding="utf-8",
    )
    contract = infer_application_contract(tmp_path)

    diagnostics = validate_application_consistency(tmp_path, contract)

    assert "APP-DB-001" in {item.code for item in diagnostics}


def test_application_validator_requires_database_orm_integration(tmp_path: Path):
    source = tmp_path / "src/main/java/Note.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import jakarta.persistence.Entity; import org.sqlite.SQLiteDataSource;",
        encoding="utf-8",
    )
    resources = tmp_path / "src/main/resources/application.properties"
    resources.parent.mkdir(parents=True)
    resources.write_text("spring.datasource.url=jdbc:sqlite:notes.db", encoding="utf-8")
    (tmp_path / "build.gradle").write_text(
        "dependencies { implementation 'org.springframework.boot:spring-boot-starter-data-jpa'; "
        "implementation 'org.xerial:sqlite-jdbc:3.46.1.0' }",
        encoding="utf-8",
    )

    diagnostics = validate_application_consistency(tmp_path, infer_application_contract(tmp_path))

    assert "APP-DB-002" in {item.code for item in diagnostics}


def test_application_validator_accepts_explicit_custom_database_dialect(tmp_path: Path):
    source = tmp_path / "src/main/java/Note.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import jakarta.persistence.Entity; import org.sqlite.SQLiteDataSource;",
        encoding="utf-8",
    )
    resources = tmp_path / "src/main/resources/application.properties"
    resources.parent.mkdir(parents=True)
    resources.write_text(
        "spring.datasource.url=jdbc:sqlite:notes.db\n"
        "spring.jpa.properties.hibernate.dialect=com.example.CustomDialect\n",
        encoding="utf-8",
    )
    (tmp_path / "build.gradle").write_text(
        "dependencies { implementation 'org.springframework.boot:spring-boot-starter-data-jpa'; "
        "implementation 'org.xerial:sqlite-jdbc:3.46.1.0' }",
        encoding="utf-8",
    )

    diagnostics = validate_application_consistency(tmp_path, infer_application_contract(tmp_path))

    assert "APP-DB-002" not in {item.code for item in diagnostics}


def test_application_validator_rejects_dialect_class_from_wrong_module(tmp_path: Path):
    source = tmp_path / "src/main/java/Note.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import jakarta.persistence.Entity; import org.sqlite.SQLiteDataSource;",
        encoding="utf-8",
    )
    resources = tmp_path / "src/main/resources/application.properties"
    resources.parent.mkdir(parents=True)
    resources.write_text(
        "spring.datasource.url=jdbc:sqlite:notes.db\n"
        "spring.jpa.database-platform=org.hibernate.dialect.SQLiteDialect\n",
        encoding="utf-8",
    )
    (tmp_path / "build.gradle").write_text(
        "dependencies { implementation 'org.springframework.boot:spring-boot-starter-data-jpa'; "
        "implementation 'org.xerial:sqlite-jdbc:3.46.1.0' }",
        encoding="utf-8",
    )

    diagnostics = validate_application_consistency(tmp_path, infer_application_contract(tmp_path))

    mismatch = next(item for item in diagnostics if item.code == "APP-DB-003")
    assert mismatch.details["supportedClass"] == ("org.hibernate.community.dialect.SQLiteDialect")
    assert mismatch.details["requiredCoordinate"] == (
        "org.hibernate.orm:hibernate-community-dialects"
    )


def test_application_validator_requires_module_for_community_dialect(tmp_path: Path):
    resources = tmp_path / "src/main/resources/application.properties"
    resources.parent.mkdir(parents=True)
    resources.write_text(
        "spring.datasource.url=jdbc:sqlite:notes.db\n"
        "spring.jpa.database-platform=org.hibernate.community.dialect.SQLiteDialect\n",
        encoding="utf-8",
    )
    (tmp_path / "build.gradle").write_text("dependencies {}", encoding="utf-8")

    diagnostics = validate_application_consistency(tmp_path, infer_application_contract(tmp_path))

    missing = next(
        item
        for item in diagnostics
        if item.code == "APP-DEP-001" and item.details.get("rule") == "hibernate-community.sqlite"
    )
    assert missing.details["missingCoordinates"] == [
        "org.hibernate.orm:hibernate-community-dialects"
    ]


def test_application_observation_ignores_stale_build_outputs(tmp_path: Path):
    resources = tmp_path / "src/main/resources/application.yml"
    resources.parent.mkdir(parents=True)
    resources.write_text(
        "spring:\n  datasource:\n    url: jdbc:sqlite:notes.db\n"
        "  jpa:\n    database-platform: com.example.CustomDialect\n",
        encoding="utf-8",
    )
    custom = tmp_path / "src/main/java/com/example/CustomDialect.java"
    custom.parent.mkdir(parents=True)
    custom.write_text("package com.example; class CustomDialect {}", encoding="utf-8")
    stale = tmp_path / "build/resources/main/application.yml"
    stale.parent.mkdir(parents=True)
    stale.write_text(
        "spring:\n  jpa:\n    database-platform: org.hibernate.dialect.SQLiteDialect\n",
        encoding="utf-8",
    )
    (tmp_path / "build.gradle").write_text("dependencies {}", encoding="utf-8")

    diagnostics = validate_application_consistency(tmp_path, infer_application_contract(tmp_path))

    assert "APP-DB-003" not in {item.code for item in diagnostics}
    observed_files = {location for item in diagnostics for location in item.locations}
    assert all(not location.startswith("build/") for location in observed_files)


def test_application_observes_general_file_io_without_database_names(tmp_path: Path):
    source = tmp_path / "src/main/java/example/Store.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import java.nio.file.Files; class Store { void save(java.nio.file.Path p) "
        "throws Exception { Files.newOutputStream(p); } }",
        encoding="utf-8",
    )
    configuration = tmp_path / "src/main/resources/application.yml"
    configuration.parent.mkdir(parents=True)
    configuration.write_text(
        "file:\n  path: ${STATE_FILE:/srv/state/records.bin}\n",
        encoding="utf-8",
    )

    contract = infer_application_contract(tmp_path)
    storage = next(fact for fact in contract.facts if fact.kind == "runtime.storage")

    assert storage.attributes == {
        "accessPath": "/srv/state",
        "durability": "persistent",
        "accessScope": "node-filesystem",
    }
    assert storage.extensions["detector"] == "file-io.external-path"


def test_application_observes_convenience_file_io_methods(tmp_path: Path):
    source = tmp_path / "src/main/java/example/Store.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import java.nio.file.Files; class Store { void save(java.nio.file.Path p, String v) "
        "throws Exception { Files.writeString(p, v); } }",
        encoding="utf-8",
    )
    configuration = tmp_path / "src/main/resources/application.yml"
    configuration.parent.mkdir(parents=True)
    configuration.write_text(
        "state:\n  path: ${STATE_FILE_PATH:/srv/state/records.json}\n",
        encoding="utf-8",
    )

    contract = infer_application_contract(tmp_path)

    storage = next(fact for fact in contract.facts if fact.kind == "runtime.storage")
    assert storage.attributes["accessScope"] == "node-filesystem"
    assert storage.attributes["accessPath"] == "/srv/state"


def test_log_path_with_file_io_is_not_called_application_state(
    tmp_path: Path,
):
    source = tmp_path / "src/main/java/example/Logger.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import java.nio.file.Files; class Logger { void write(java.nio.file.Path p) "
        "throws Exception { Files.newOutputStream(p); } }",
        encoding="utf-8",
    )
    configuration = tmp_path / "src/main/resources/application.yml"
    configuration.parent.mkdir(parents=True)
    configuration.write_text(
        "logging:\n  file:\n    name: ${LOG_PATH:/var/log/app.log}\n",
        encoding="utf-8",
    )

    contract = infer_application_contract(tmp_path)

    assert all(fact.kind != "runtime.storage" for fact in contract.facts)


def test_binding_validator_supports_generic_port_and_storage_facts():
    application = ApplicationRuntimeContract(
        facts=[
            ContractFact(id="http", kind="runtime.port", attributes={"value": 8080}),
            ContractFact(id="data", kind="runtime.storage", attributes={"path": "/data"}),
        ]
    )
    cloud = CloudCapabilityContract(
        facts=[
            ContractFact(id="backend", kind="cloud.network.backend", attributes={"value": 9090}),
            ContractFact(id="mount", kind="cloud.storage.mount", attributes={"path": "/mnt"}),
        ]
    )
    bindings = DeploymentBindingContract(
        bindings=[
            ContractBinding(
                id="http-binding",
                kind="network",
                consumes=BindingEndpoint(contract="application", factId="http", attribute="value"),
                provides=BindingEndpoint(contract="cloud", factId="backend", attribute="value"),
                invariants=[{"operator": "equals"}],
            ),
            ContractBinding(
                id="data-binding",
                kind="storage",
                consumes=BindingEndpoint(contract="application", factId="data", attribute="path"),
                provides=BindingEndpoint(contract="cloud", factId="mount", attribute="path"),
                invariants=[{"operator": "equals"}],
            ),
        ]
    )

    diagnostics = validate_binding_consistency(application, cloud, bindings)

    assert {item.code for item in diagnostics} == {
        "BIND-PORT-001",
        "BIND-STORAGE-001",
    }


def test_binding_planner_uses_app_port_and_storage_path_without_database_assumption():
    application = ApplicationRuntimeContract(
        facts=[
            ContractFact(
                id="app-http",
                kind="runtime.port",
                attributes={"port": 8181, "protocol": "http"},
            ),
            ContractFact(
                id="app-data",
                kind="runtime.storage",
                attributes={"accessPath": "/srv/state", "durability": "persistent"},
            ),
        ]
    )
    cloud = CloudCapabilityContract(
        facts=[
            ContractFact(
                id="persistent",
                kind="cloud.capability.persistent_storage",
                attributes={"required": True},
            )
        ]
    )

    planned_cloud, bindings = derive_deployment_bindings(application, cloud)

    facts = {fact.kind: fact for fact in planned_cloud.facts}
    assert facts["cloud.network.backend"].attributes["port"] == 8181
    assert facts["cloud.storage.mount"].attributes["mountPath"] == "/srv/state"
    assert {item.kind for item in bindings.bindings} == {"network", "storage"}
    assert validate_binding_consistency(application, planned_cloud, bindings) == []
    assert all("sqlite" not in str(item).lower() for item in planned_cloud.facts)

    stateless_app = ApplicationRuntimeContract(facts=[application.facts[0]])
    replanned_cloud, replanned_bindings = derive_deployment_bindings(
        stateless_app,
        planned_cloud,
        bindings.model_dump(mode="json", by_alias=True),
    )
    assert all(fact.kind != "cloud.storage.mount" for fact in replanned_cloud.facts)
    assert all(item.kind != "storage" for item in replanned_bindings.bindings)


def test_cloud_contract_does_not_invent_deployment_topology():
    contract = cloud_capability_contract_from_requirements(
        {"deployment_needs": {}, "resource_spec": {"provider": "aws"}}
    )

    assert all(fact.kind != "cloud.deploymentTopology" for fact in contract.facts)


def test_high_availability_requirement_remains_a_capability_fact():
    contract = cloud_capability_contract_from_requirements(
        {
            "deployment_needs": {
                "availability_requirement": {
                    "decision": "accepted",
                    "requirementIds": ["NFR-HA"],
                    "metadata": {"high_availability": True},
                }
            },
            "resource_spec": {"provider": "aws"},
        }
    )

    assert all(fact.kind != "cloud.deploymentTopology" for fact in contract.facts)
    assert any(
        fact.kind == "cloud.capability.availability_requirement"
        for fact in contract.facts
    )
