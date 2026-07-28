"""Deterministic Kubernetes rendering from a capability-based deployment intent."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

import yaml
from jsonschema import Draft202012Validator

SCHEMA_VERSION = "easydep-deployment-intent/v1alpha1"
WORKLOAD_KINDS = {"Deployment", "StatefulSet", "Job", "CronJob"}
CAPABILITIES = {
    "service", "ingress", "hpa", "pdb", "networkPolicy",
    "serviceAccount", "externalSecret", "configMap", "pvc", "serviceMonitor",
}


def render_deployment(run_root: Path, spec: Any) -> dict[str, object]:
    cloud_path = spec.inputs.get("cloud")
    deployment_path = spec.inputs.get("deployment")
    intent_path = spec.inputs.get("deploymentIntent")
    has_intent = bool(intent_path and intent_path.is_file())
    if not has_intent and (cloud_path is None or not cloud_path.is_file()):
        raise ValueError(
            "Deployment intent inference requires a cloud resource specification"
        )
    cloud = (
        json.loads(cloud_path.read_text(encoding="utf-8"))
        if cloud_path and cloud_path.is_file()
        else {}
    )
    deployment = (
        deployment_path.read_text(encoding="utf-8")
        if deployment_path and deployment_path.is_file()
        else ""
    )
    intent = (
        json.loads(intent_path.read_text(encoding="utf-8"))
        if has_intent
        else infer_intent(spec.name, cloud, deployment)
    )
    validate_intent(intent)
    application = run_root / "application"
    removed = remove_previous_render(application, run_root / "reports")
    rendered: list[str] = []

    def write(relative: str, content: str) -> None:
        path = application / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        rendered.append(f"application/{relative}")

    namespace = str(intent.get("namespace", "default"))
    write(
        "Dockerfile",
        r"""FROM gradle:8.14.2-jdk21 AS build
WORKDIR /app
COPY . .
RUN gradle bootJar --no-daemon \
    && jar="$(find build/libs -maxdepth 1 -type f -name '*.jar' ! -name '*-plain.jar' -print | sort | head -n 1)" \
    && test -n "$jar" \
    && cp "$jar" /tmp/app.jar

FROM eclipse-temurin:21-jre-alpine
WORKDIR /app
RUN addgroup -S app && adduser -S app -G app
COPY --from=build /tmp/app.jar app.jar
USER app
EXPOSE 8000
ENTRYPOINT ["java", "-jar", "app.jar"]""",
    )
    write(
        ".dockerignore",
        """.git
.gradle
build
reports
.env
.env.*
*.pem
*.key""",
    )
    if any("__EASYDEP_REGISTRY_" in str(workload.get("image", "")) for workload in intent["workloads"]):
        write(
            "k8s/render-images.sh",
            r'''#!/bin/sh
set -eu

# Render a deployable manifest tree without mutating the deterministic source manifests.
terraform_dir=${1:?usage: render-images.sh <terraform-dir> <output-dir>}
output_dir=${2:?usage: render-images.sh <terraform-dir> <output-dir>}
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
registry_outputs=$(terraform -chdir="$terraform_dir" output -json registry_image_bases)
rm -rf "$output_dir"
mkdir -p "$output_dir"
REGISTRY_OUTPUTS="$registry_outputs" SOURCE_DIR="$source_dir" OUTPUT_DIR="$output_dir" python3 - <<'PY'
import json
import os
from pathlib import Path

registries = json.loads(os.environ["REGISTRY_OUTPUTS"])
source_dir = Path(os.environ["SOURCE_DIR"])
output_dir = Path(os.environ["OUTPUT_DIR"])
for source in source_dir.rglob("*.y*ml"):
    target = output_dir / source.relative_to(source_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = source.read_text(encoding="utf-8")
    for registry_ref, image_base in registries.items():
        content = content.replace(f"__EASYDEP_REGISTRY_{registry_ref}__", image_base)
    if "__EASYDEP_REGISTRY_" in content:
        raise SystemExit(f"unresolved registry marker in {source}")
    target.write_text(content, encoding="utf-8")
PY
''',
        )
        write(
            "k8s/deploy.sh",
            r'''#!/bin/sh
set -eu

# Apply IaC, resolve registry addresses from Terraform outputs, then apply Kubernetes manifests.
terraform_dir=${1:?usage: deploy.sh <terraform-dir> [terraform apply options...]}
shift
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
output_dir=${EASYDEP_MANIFEST_DIR:-"$script_dir/resolved"}
terraform -chdir="$terraform_dir" init -input=false
terraform -chdir="$terraform_dir" apply "$@"
sh "$script_dir/render-images.sh" "$terraform_dir" "$output_dir"
find "$output_dir" -type f \( -name '*.yaml' -o -name '*.yml' \) -print | sort | while IFS= read -r manifest; do
  kubectl apply -f "$manifest"
done
''',
        )
    if intent.get("createNamespace", True):
        write("k8s/namespace.yaml", resource("v1", "Namespace", namespace))
    for workload in intent["workloads"]:
        render_workload(write, namespace, workload)
    validation = validate_rendered_manifests(application, rendered)
    source_conformance = validate_source_conformance(
        application, rendered, intent, cloud, deployment
    )
    report = {
        "schemaVersion": "easydep-deployment-render/v1alpha1",
        "intent": intent,
        "renderedFiles": sorted(rendered),
        "removedFiles": sorted(removed),
        "renderer": "deterministic",
        "validation": validation,
        "sourceConformance": source_conformance,
        "sourceEvidence": {
            "deploymentDiagram": bool(deployment),
            "cloudResourceSpecification": bool(cloud),
            "explicitIntent": has_intent,
        },
        "intentSource": "explicit-input" if has_intent else "implementation-agent-inference",
        "externalPrerequisites": external_prerequisites(intent),
    }
    reports = run_root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "deployment-intent.json").write_text(
        json.dumps(intent, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (reports / "deployment-render.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if source_conformance["status"] == "FAILED":
        raise ValueError(
            "Deployment source conformance failed:\n- "
            + "\n- ".join(source_conformance["errors"])
        )
    return report


def render_workload(write: Callable[[str, str], None], namespace: str, workload: dict[str, Any]) -> None:
    name, kind = workload["name"], workload["kind"]
    cap = workload["capabilities"]
    port = int(workload.get("port", 8000))
    pod = pod_spec(workload, port)
    if kind in {"Deployment", "StatefulSet"}:
        service_name = f"\n  serviceName: {name}" if kind == "StatefulSet" else ""
        body = (
            f"{resource('apps/v1', kind, name, namespace)}\nspec:{service_name}\n"
            f"  replicas: {workload.get('replicas', {}).get('min', 1)}\n"
            f"  selector:\n    matchLabels: {{ app.kubernetes.io/name: {name} }}\n"
            f"  template:\n    metadata:\n      labels: {{ app.kubernetes.io/name: {name} }}\n"
            f"    spec:\n{pod}"
        )
    elif kind == "Job":
        body = (
            f"{resource('batch/v1', kind, name, namespace)}\nspec:\n"
            f"  template:\n    metadata:\n      labels: {{ app.kubernetes.io/name: {name} }}\n"
            f"    spec:\n{pod}"
        )
    else:
        body = (
            f"{resource('batch/v1', kind, name, namespace)}\nspec:\n"
            f"  schedule: \"{workload['schedule']}\"\n  jobTemplate:\n    spec:\n"
            f"      template:\n        metadata:\n          labels: {{ app.kubernetes.io/name: {name} }}\n"
            f"        spec:\n{indent(pod, 4)}"
        )
    write(f"k8s/{name}/{kind.lower()}.yaml", body)
    renderers = {
        "service": lambda: service(namespace, name, port, workload),
        "ingress": lambda: ingress(namespace, name, workload),
        "hpa": lambda: hpa(namespace, name, kind, workload),
        "pdb": lambda: pdb(namespace, name),
        "networkPolicy": lambda: network_policy(namespace, name, port),
        "serviceAccount": lambda: resource("v1", "ServiceAccount", name, namespace),
        "configMap": lambda: config_map(namespace, name, workload),
        "externalSecret": lambda: external_secret(namespace, name, workload),
        "pvc": lambda: pvc(namespace, name, workload),
        "serviceMonitor": lambda: service_monitor(namespace, name, workload),
    }
    filenames = {
        "networkPolicy": "network-policy", "serviceAccount": "service-account",
        "externalSecret": "external-secret", "configMap": "config-map",
        "serviceMonitor": "service-monitor",
    }
    for capability, renderer in renderers.items():
        if cap.get(capability):
            write(f"k8s/{name}/{filenames.get(capability, capability)}.yaml", renderer())


def pod_spec(workload: dict[str, Any], port: int) -> str:
    name = workload["name"]
    values = workload.get("resources", {})
    requests = values.get("requests", {"cpu": "250m", "memory": "512Mi"})
    limits = values.get("limits", {"cpu": "1", "memory": "1Gi"})
    restart = "Never" if workload["kind"] in {"Job", "CronJob"} else "Always"
    account = f"\n      serviceAccountName: {name}" if workload["capabilities"].get("serviceAccount") else ""
    env_from = ""
    if workload["capabilities"].get("configMap"):
        env_from += f"\n          envFrom:\n            - configMapRef: {{ name: {name} }}"
    if workload["capabilities"].get("externalSecret"):
        prefix = "\n          envFrom:" if not env_from else ""
        env_from += f"{prefix}\n            - secretRef: {{ name: {name}-runtime }}"
    storage = ""
    if workload["capabilities"].get("pvc"):
        mount_path = workload.get("storage", {}).get("mountPath", "/data")
        env_from += f"\n          volumeMounts:\n            - {{ name: data, mountPath: {mount_path} }}"
        storage = f"\n      volumes:\n        - name: data\n          persistentVolumeClaim: {{ claimName: {name} }}"
    probes = ""
    if workload.get("health") and workload["kind"] in {"Deployment", "StatefulSet"}:
        health = workload["health"]
        readiness_path = health.get("readinessPath", health.get("path", "/healthz"))
        liveness_path = health.get("livenessPath", health.get("path", readiness_path))
        probes = (
            f"\n          readinessProbe:\n            httpGet: {{ path: {readiness_path}, port: {port} }}"
            f"\n          livenessProbe:\n            httpGet: {{ path: {liveness_path}, port: {port} }}"
        )
    return (
        f"      restartPolicy: {restart}{account}\n      containers:\n"
        f"        - name: {name}\n          image: {workload['image']}\n"
        f"          ports: [{{ containerPort: {port} }}]\n"
        f"          resources:\n            requests: {{ cpu: \"{requests['cpu']}\", memory: \"{requests['memory']}\" }}\n"
        f"            limits: {{ cpu: \"{limits['cpu']}\", memory: \"{limits['memory']}\" }}"
        f"{env_from}{probes}{storage}"
    )


def infer_intent(
    name: str, cloud: dict[str, Any], deployment_diagram: str = ""
) -> dict[str, Any]:
    resources = cloud.get("resources", [])
    provider = cloud_provider(cloud)
    cluster = next((item for item in resources if cloud_role(provider, item) == "cluster"), {})
    registries = [item for item in resources if cloud_role(provider, item) == "registry"]
    networking = cluster.get("networking", {})
    exposed_aliases = deployment_exposed_aliases(deployment_diagram)
    workloads = []
    for source in cluster.get("workloads", []) or [{"name": name}]:
        workload_name = dns_name(str(source.get("name", name)))
        replicas = source.get("replicas", {"min": 1, "max": 1})
        if not isinstance(replicas, dict):
            replicas = {"min": max(as_int(replicas, 1), 0), "max": max(as_int(replicas, 1), 1)}
        else:
            replicas = {
                "min": max(as_int(replicas.get("min"), 1), 0),
                "max": max(as_int(replicas.get("max"), 1), 1),
            }
        diagram_alias = dns_name(str(source.get("diagramAlias", "")))
        diagram_exposed = bool(
            diagram_alias and diagram_alias in exposed_aliases
        )
        role = str(source.get("role", "")).lower()
        exposure = str(source.get("exposure", "")).lower()
        if exposure in {"none", "disabled"}:
            api_like = False
        elif exposure in {"public", "external"}:
            api_like = True
        elif role in {"worker", "consumer", "batch", "scheduler"}:
            api_like = diagram_exposed
        else:
            api_like = (
                diagram_exposed
                or role in {"api", "web", "frontend", "gateway"}
                or bool(source.get("probes"))
                or "api" in workload_name
                or "service" in workload_name
            )
        use_ingress = api_like and networking.get("ingressProtocol") == "HTTPS"
        metrics_path = source.get("monitoring", {}).get("metricsPath")
        external_secret = source.get("externalSecret")
        registry = infer_workload_registry(source, cluster, registries)
        workloads.append({
            "name": workload_name, "kind": "Deployment",
            "image": registry_image(provider, registry, workload_name),
            **({"registryRef": resource_reference(registry)} if registry else {}),
            "port": int(networking.get("containerPort", 8000)),
            "replicas": replicas, "resources": source.get("resources", {}),
            "health": {
                "readinessPath": source.get("probes", {}).get("readiness", "/healthz"),
                "livenessPath": source.get(
                    "probes", {}
                ).get("liveness", source.get("probes", {}).get("readiness", "/healthz")),
            },
            "service": {
                "type": "ClusterIP" if use_ingress else networking.get("serviceExposure", "ClusterIP")
            },
            "ingress": {
                "className": networking.get("ingressClassName", "")
            } if use_ingress and networking.get("ingressClassName") else {},
            "monitoring": {"metricsPath": metrics_path} if metrics_path else {},
            "capabilities": {
                "service": api_like,
                "ingress": use_ingress,
                "hpa": replicas.get("max", 1) > replicas.get("min", 1),
                "pdb": replicas.get("min", 1) > 1,
                "networkPolicy": True, "serviceAccount": True,
                "externalSecret": bool(external_secret), "configMap": False,
                "pvc": bool(source.get("persistentVolume")),
                "serviceMonitor": bool(metrics_path),
            },
            **({"externalSecret": external_secret} if external_secret else {}),
            **(
                {"storage": source["persistentVolume"]}
                if source.get("persistentVolume")
                else {}
            ),
        })
    return {
        "schemaVersion": SCHEMA_VERSION, "namespace": dns_name(name),
        "createNamespace": True, "workloads": workloads,
    }


def validate_intent(intent: dict[str, Any]) -> None:
    schema_path = Path(__file__).with_name("deployment_intent.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = [
        f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in Draft202012Validator(schema).iter_errors(intent)
    ]
    if intent.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"schemaVersion must be {SCHEMA_VERSION}")
    workloads = intent.get("workloads")
    namespace = intent.get("namespace", "default")
    if not isinstance(namespace, str) or not is_dns1123_name(namespace):
        errors.append("namespace must be a DNS-1123 name")
    if not isinstance(workloads, list) or not workloads:
        errors.append("workloads must be a non-empty array")
        workloads = []
    names: set[str] = set()
    for index, workload in enumerate(workloads):
        at = f"workloads[{index}]"
        name, kind = workload.get("name"), workload.get("kind")
        if not isinstance(name, str) or dns_name(name) != name:
            errors.append(f"{at}.name must be a DNS-1123 name")
        elif name in names:
            errors.append(f"{at}.name is duplicated: {name}")
        else:
            names.add(name)
        if kind not in WORKLOAD_KINDS:
            errors.append(f"{at}.kind is unsupported: {kind}")
        cap = workload.get("capabilities")
        if not isinstance(cap, dict):
            errors.append(f"{at}.capabilities must be an object")
            continue
        unknown = set(cap) - CAPABILITIES
        if unknown:
            errors.append(f"{at}.capabilities contains unsupported values: {', '.join(sorted(unknown))}")
        if kind in {"Job", "CronJob"} and any(cap.get(key) for key in ("service", "ingress", "hpa", "pdb", "serviceMonitor")):
            errors.append(f"{at}: Job/CronJob cannot enable service, ingress, hpa, pdb, or serviceMonitor")
        if cap.get("ingress") and not cap.get("service"):
            errors.append(f"{at}: ingress requires service")
        if cap.get("serviceMonitor") and not cap.get("service"):
            errors.append(f"{at}: serviceMonitor requires service")
        if cap.get("hpa"):
            replicas = workload.get("replicas", {})
            if kind not in {"Deployment", "StatefulSet"}:
                errors.append(f"{at}: hpa requires Deployment or StatefulSet")
            elif as_int(replicas.get("max"), 0) <= as_int(
                replicas.get("min"), 0
            ):
                errors.append(f"{at}: hpa requires replicas.max > replicas.min")
        if kind == "StatefulSet" and not cap.get("service"):
            errors.append(f"{at}: StatefulSet requires a governing service")
        if cap.get("pvc"):
            access_modes = workload.get("storage", {}).get(
                "accessModes", ["ReadWriteOnce"]
            )
            minimum = as_int(workload.get("replicas", {}).get("min"), 1)
            if minimum > 1 and "ReadWriteMany" not in access_modes:
                errors.append(
                    f"{at}: pvc with multiple replicas requires ReadWriteMany"
                )
        if cap.get("externalSecret"):
            secret = workload.get("externalSecret")
            if not isinstance(secret, dict):
                errors.append(f"{at}: externalSecret capability requires configuration")
            else:
                for key in ("storeName", "remoteKey"):
                    value = secret.get(key)
                    if (
                        not isinstance(value, str)
                        or not value.strip()
                        or "<" in value
                        or ">" in value
                    ):
                        errors.append(
                            f"{at}.externalSecret.{key} must be an explicit value"
                        )
        if kind == "CronJob" and not workload.get("schedule"):
            errors.append(f"{at}: CronJob requires schedule")
        elif kind == "CronJob" and not valid_cron(str(workload.get("schedule"))):
            errors.append(f"{at}.schedule must contain five cron fields")
        for group in ("requests", "limits"):
            quantities = workload.get("resources", {}).get(group, {})
            for key, value in quantities.items():
                if not valid_quantity(str(value)):
                    errors.append(
                        f"{at}.resources.{group}.{key} is not a Kubernetes quantity"
                    )
        storage_size = workload.get("storage", {}).get("size")
        if storage_size is not None and not valid_quantity(str(storage_size)):
            errors.append(f"{at}.storage.size is not a Kubernetes quantity")
    if errors:
        raise ValueError("Invalid deploymentIntent:\n- " + "\n- ".join(errors))


def validate_rendered_manifests(
    application: Path, rendered: list[str]
) -> dict[str, object]:
    """Validate YAML shape and cross-resource references before promotion."""
    objects: list[dict[str, Any]] = []
    errors: list[str] = []
    for relative in rendered:
        if not relative.endswith((".yaml", ".yml")):
            continue
        path = application.parent / relative
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            errors.append(f"{relative}: invalid YAML: {error}")
            continue
        if not isinstance(document, dict):
            errors.append(f"{relative}: manifest must be a YAML object")
            continue
        for key in ("apiVersion", "kind", "metadata"):
            if key not in document:
                errors.append(f"{relative}: missing {key}")
        if not isinstance(document.get("metadata"), dict) or not document.get("metadata", {}).get("name"):
            errors.append(f"{relative}: metadata.name is required")
        else:
            metadata = document["metadata"]
            if not is_dns1123_name(str(metadata["name"])):
                errors.append(f"{relative}: metadata.name is not DNS-1123")
            manifest_namespace = metadata.get("namespace")
            if manifest_namespace and not is_dns1123_name(str(manifest_namespace)):
                errors.append(f"{relative}: metadata.namespace is not DNS-1123")
        document["_file"] = relative
        objects.append(document)
    index = {
        (str(item.get("kind")), str(item.get("metadata", {}).get("name"))): item
        for item in objects
    }
    workload_kinds = {"Deployment", "StatefulSet", "Job", "CronJob"}
    workloads = {
        str(item["metadata"]["name"])
        for item in objects if item.get("kind") in workload_kinds
    }
    services = {
        str(item["metadata"]["name"])
        for item in objects if item.get("kind") == "Service"
    }
    config_maps = {
        str(item["metadata"]["name"])
        for item in objects if item.get("kind") == "ConfigMap"
    }
    service_accounts = {
        str(item["metadata"]["name"])
        for item in objects if item.get("kind") == "ServiceAccount"
    }
    claims = {
        str(item["metadata"]["name"])
        for item in objects if item.get("kind") == "PersistentVolumeClaim"
    }
    generated_secrets = {
        str(item.get("spec", {}).get("target", {}).get("name"))
        for item in objects if item.get("kind") == "ExternalSecret"
    }
    for item in objects:
        kind, name, spec = item.get("kind"), item.get("metadata", {}).get("name"), item.get("spec", {})
        if kind == "HorizontalPodAutoscaler":
            target = spec.get("scaleTargetRef", {})
            if (str(target.get("kind")), str(target.get("name"))) not in index:
                errors.append(f"{item['_file']}: scaleTargetRef does not resolve")
        if kind == "Ingress":
            backends = [
                path.get("backend", {}).get("service", {}).get("name")
                for rule in spec.get("rules", [])
                for path in rule.get("http", {}).get("paths", [])
            ]
            for backend in backends:
                if backend not in services:
                    errors.append(f"{item['_file']}: backend Service does not resolve: {backend}")
        if kind in {"PodDisruptionBudget", "NetworkPolicy", "ServiceMonitor"} and name not in workloads:
            errors.append(f"{item['_file']}: selected workload does not resolve: {name}")
        if kind in workload_kinds:
            pod = workload_pod_spec(item)
            account = pod.get("serviceAccountName")
            if account and account not in service_accounts:
                errors.append(
                    f"{item['_file']}: serviceAccountName does not resolve: {account}"
                )
            for container in pod.get("containers", []):
                for source in container.get("envFrom", []):
                    config_name = source.get("configMapRef", {}).get("name")
                    secret_name = source.get("secretRef", {}).get("name")
                    if config_name and config_name not in config_maps:
                        errors.append(
                            f"{item['_file']}: ConfigMap does not resolve: {config_name}"
                        )
                    if secret_name and secret_name not in generated_secrets:
                        errors.append(
                            f"{item['_file']}: generated Secret does not resolve: {secret_name}"
                        )
            for volume in pod.get("volumes", []):
                claim = volume.get("persistentVolumeClaim", {}).get("claimName")
                if claim and claim not in claims:
                    errors.append(
                        f"{item['_file']}: PersistentVolumeClaim does not resolve: {claim}"
                    )
    if errors:
        raise ValueError("Invalid rendered Kubernetes manifests:\n- " + "\n- ".join(errors))
    warnings = rendered_warnings(objects)
    return {
        "status": "SUCCEEDED" if not warnings else "SUCCEEDED_WITH_WARNINGS",
        "manifestCount": len(objects),
        "warnings": warnings,
        "validationScope": [
            "YAML decoding",
            "required Kubernetes object fields",
            "DNS-1123 metadata names",
            "cross-resource references",
        ],
        "notValidated": [
            "cluster-installed CRDs and admission policies",
            "container image build and runtime health",
        ],
    }


def validate_source_conformance(
    application: Path,
    rendered: list[str],
    intent: dict[str, Any],
    cloud: dict[str, Any],
    deployment_diagram: str,
) -> dict[str, object]:
    """Prove that intent and generated manifests retain their source constraints."""
    errors: list[str] = []
    warnings: list[str] = []
    objects = rendered_objects(application, rendered)
    intent_workloads = {
        str(workload["name"]): workload for workload in intent["workloads"]
    }
    manifest_index = {
        (str(item.get("kind")), str(item.get("metadata", {}).get("name"))): item
        for item in objects
    }

    # Intent-to-manifest traceability is always available, including manual intent input.
    for name, workload in intent_workloads.items():
        kind = str(workload["kind"])
        manifest = manifest_index.get((kind, name))
        if manifest is None:
            errors.append(f"intent workload {name} is missing {kind} manifest")
            continue
        verify_manifest_workload(manifest, workload, errors)
        for capability, resource_kind in capability_resource_kinds(workload).items():
            exists = (resource_kind, name) in manifest_index
            if workload["capabilities"].get(capability) and not exists:
                errors.append(
                    f"intent workload {name} enables {capability} but no {resource_kind} was rendered"
                )
            if not workload["capabilities"].get(capability) and exists:
                errors.append(
                    f"intent workload {name} disables {capability} but {resource_kind} was rendered"
                )

    if cloud:
        verify_cloud_resource_spec(intent_workloads, cloud, deployment_diagram, errors, warnings)
    else:
        warnings.append(
            "Cloud resource specification was not supplied; source-to-intent validation was skipped"
        )

    return {
        "status": (
            "FAILED" if errors else "SUCCEEDED" if not warnings else "SUCCEEDED_WITH_WARNINGS"
        ),
        "checked": ["intent-to-manifest", "cloud-resource-spec"] if cloud else ["intent-to-manifest"],
        "errors": errors,
        "warnings": warnings,
    }


def rendered_objects(application: Path, rendered: list[str]) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for relative in rendered:
        if relative.endswith((".yaml", ".yml")):
            document = yaml.safe_load((application.parent / relative).read_text(encoding="utf-8"))
            if isinstance(document, dict):
                objects.append(document)
    return objects


def verify_manifest_workload(
    manifest: dict[str, Any], workload: dict[str, Any], errors: list[str]
) -> None:
    kind = str(workload["kind"])
    name = str(workload["name"])
    spec = manifest.get("spec", {})
    pod = workload_pod_spec(manifest)
    containers = pod.get("containers", [])
    container = containers[0] if containers else {}
    if str(container.get("image")) != str(workload["image"]):
        errors.append(f"{kind}/{name} image does not match deployment intent")
    ports = container.get("ports", [])
    expected_port = int(workload.get("port", 8000))
    if not any(port.get("containerPort") == expected_port for port in ports):
        errors.append(f"{kind}/{name} container port does not match deployment intent")
    if kind in {"Deployment", "StatefulSet"}:
        expected_replicas = int(workload.get("replicas", {}).get("min", 1))
        if spec.get("replicas") != expected_replicas:
            errors.append(f"{kind}/{name} replicas do not match deployment intent")
    expected_resources = workload.get("resources", {})
    actual_resources = container.get("resources", {})
    for group in ("requests", "limits"):
        for key, value in expected_resources.get(group, {}).items():
            if str(actual_resources.get(group, {}).get(key)) != str(value):
                errors.append(
                    f"{kind}/{name} {group}.{key} does not match deployment intent"
                )
    if workload.get("health") and kind in {"Deployment", "StatefulSet"}:
        health = workload["health"]
        expected_paths = {
            "readinessProbe": health.get("readinessPath", health.get("path", "/healthz")),
            "livenessProbe": health.get(
                "livenessPath", health.get("path", health.get("readinessPath", "/healthz"))
            ),
        }
        for probe, expected_path in expected_paths.items():
            actual_path = container.get(probe, {}).get("httpGet", {}).get("path")
            if actual_path != expected_path:
                errors.append(f"{kind}/{name} {probe} does not match deployment intent")


def capability_resource_kinds(workload: dict[str, Any]) -> dict[str, str]:
    result = {
        "service": "Service",
        "ingress": "Ingress",
        "hpa": "HorizontalPodAutoscaler",
        "pdb": "PodDisruptionBudget",
        "networkPolicy": "NetworkPolicy",
        "serviceAccount": "ServiceAccount",
        "configMap": "ConfigMap",
        "externalSecret": "ExternalSecret",
        "pvc": "PersistentVolumeClaim",
        "serviceMonitor": "ServiceMonitor",
    }
    # StatefulSet uses the required governing Service and is validated identically.
    return result


def verify_cloud_resource_spec(
    intent_workloads: dict[str, dict[str, Any]],
    cloud: dict[str, Any],
    deployment_diagram: str,
    errors: list[str],
    warnings: list[str],
) -> None:
    resources = cloud.get("resources", [])
    provider = cloud_provider(cloud)
    cluster = next(
        (
            item for item in resources
            if cloud_role(provider, item) == "cluster"
        ),
        None,
    )
    if not isinstance(cluster, dict):
        errors.append("cloud resource spec has no managed Kubernetes cluster")
        return
    networking = cluster.get("networking", {})
    exposed_aliases = deployment_exposed_aliases(deployment_diagram)
    for source in cluster.get("workloads", []):
        name = dns_name(str(source.get("name", "")))
        workload = intent_workloads.get(name)
        if workload is None:
            errors.append(f"cloud workload {name} is missing from deployment intent")
            continue
        source_replicas = source.get("replicas")
        if isinstance(source_replicas, dict):
            actual = workload.get("replicas", {})
            for field in ("min", "max"):
                if field in source_replicas and actual.get(field) != source_replicas[field]:
                    errors.append(
                        f"cloud workload {name} replicas.{field} does not match deployment intent"
                    )
        for group in ("requests", "limits"):
            for key, value in source.get("resources", {}).get(group, {}).items():
                actual = workload.get("resources", {}).get(group, {}).get(key)
                if str(actual) != str(value):
                    errors.append(
                        f"cloud workload {name} resources.{group}.{key} does not match deployment intent"
                    )
        expected_port = networking.get("containerPort")
        if expected_port is not None and int(workload.get("port", 8000)) != int(expected_port):
            errors.append(f"cloud networking containerPort does not match workload {name}")
        health = workload.get("health", {})
        readiness = source.get("probes", {}).get("readiness")
        actual_readiness = health.get("readinessPath", health.get("path"))
        if readiness and actual_readiness != readiness:
            errors.append(f"cloud workload {name} readiness probe does not match deployment intent")
        liveness = source.get("probes", {}).get("liveness")
        actual_liveness = health.get(
            "livenessPath", health.get("path", actual_readiness)
        )
        if liveness and actual_liveness != liveness:
            errors.append(f"cloud workload {name} liveness probe does not match deployment intent")
        if source.get("persistentVolume") and not workload["capabilities"].get("pvc"):
            errors.append(f"cloud workload {name} requires persistent storage")
        secret = source.get("externalSecret")
        if secret:
            if not workload["capabilities"].get("externalSecret"):
                errors.append(f"cloud workload {name} requires ExternalSecret")
            elif workload.get("externalSecret") != secret:
                errors.append(f"cloud workload {name} ExternalSecret config does not match")
        alias = dns_name(str(source.get("diagramAlias", "")))
        if deployment_diagram and not alias:
            warnings.append(
                f"cloud workload {name} has no diagramAlias; diagram exposure cannot be verified"
            )
        if alias and alias in exposed_aliases:
            capabilities = workload["capabilities"]
            if not capabilities.get("service"):
                errors.append(f"diagram-exposed workload {name} requires Service")
            if networking.get("ingressProtocol") == "HTTPS" and not capabilities.get("ingress"):
                errors.append(f"diagram-exposed workload {name} requires Ingress for HTTPS")


def cloud_provider(cloud: dict[str, Any]) -> str:
    provider = str(cloud.get("provider", "azure")).lower()
    return provider if provider in {"azure", "aws", "gcp"} else "azure"


def cloud_role(provider: str, item: dict[str, Any]) -> str | None:
    kind = str(item.get("type", "")).lower()
    if provider == "azure":
        if kind == "microsoft.containerservice/managedclusters":
            return "cluster"
        if kind == "microsoft.containerregistry/registries":
            return "registry"
    elif provider == "aws":
        if "eks" in kind and "cluster" in kind:
            return "cluster"
        if "ecr" in kind and "repository" in kind:
            return "registry"
    elif provider == "gcp":
        if "container" in kind and "cluster" in kind:
            return "cluster"
        if "artifact" in kind and "repository" in kind:
            return "registry"
    return None


def resource_reference(resource: dict[str, Any]) -> str:
    explicit = resource.get("id")
    return str(explicit) if isinstance(explicit, str) and explicit.strip() else f"{resource.get('type')}:{resource.get('name')}"


def infer_workload_registry(
    workload: dict[str, Any], cluster: dict[str, Any], registries: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Choose a registry explicitly; ambiguous cloud input must not invent a target."""
    requested = workload.get("registryRef")
    if isinstance(requested, str) and requested:
        matches = [item for item in registries if requested in {str(item.get("name")), resource_reference(item)}]
        if len(matches) != 1:
            raise ValueError(f"workload {workload.get('name')} registryRef does not identify one registry: {requested}")
        return matches[0]
    cluster_dependencies = set(cluster.get("dependsOn", [])) if isinstance(cluster.get("dependsOn"), list) else set()
    connected = [item for item in registries if resource_reference(item) in cluster_dependencies or item.get("name") in cluster_dependencies]
    candidates = connected or registries
    if len(candidates) > 1:
        raise ValueError(f"workload {workload.get('name')} requires registryRef because multiple registries are available")
    return candidates[0] if candidates else None


def registry_image(provider: str, registry: dict[str, Any] | None, workload: str) -> str:
    if registry is None:
        return f"{workload}:<tag>"
    marker = f"__EASYDEP_REGISTRY_{resource_reference(registry)}__"
    if provider == "aws":
        return f"{marker}:<tag>"
    return f"{marker}/{workload}:<tag>"


def resource(api: str, kind: str, name: str, namespace: str | None = None) -> str:
    suffix = f"\n  namespace: {namespace}" if namespace else ""
    return f"apiVersion: {api}\nkind: {kind}\nmetadata:\n  name: {name}{suffix}"


def service(ns: str, name: str, port: int, workload: dict[str, Any]) -> str:
    if workload["kind"] == "StatefulSet":
        return f"{resource('v1', 'Service', name, ns)}\n  labels: {{ app.kubernetes.io/name: {name} }}\nspec:\n  clusterIP: None\n  publishNotReadyAddresses: true\n  selector: {{ app.kubernetes.io/name: {name} }}\n  ports: [{{ name: http, port: {port}, targetPort: {port} }}]"
    service_type = workload.get("service", {}).get("type", "ClusterIP")
    return f"{resource('v1', 'Service', name, ns)}\n  labels: {{ app.kubernetes.io/name: {name} }}\nspec:\n  type: {service_type}\n  selector: {{ app.kubernetes.io/name: {name} }}\n  ports: [{{ name: http, port: 80, targetPort: {port} }}]"


def ingress(ns: str, name: str, workload: dict[str, Any]) -> str:
    host = workload.get("ingress", {}).get("host", f"{name}.example.invalid")
    tls_name = workload.get("ingress", {}).get("tlsSecretName", f"{name}-tls")
    class_name = workload.get("ingress", {}).get("className")
    class_line = f"\n  ingressClassName: {class_name}" if class_name else ""
    return f"{resource('networking.k8s.io/v1', 'Ingress', name, ns)}\nspec:{class_line}\n  tls:\n    - hosts: [{host}]\n      secretName: {tls_name}\n  rules:\n    - host: {host}\n      http:\n        paths:\n          - path: /\n            pathType: Prefix\n            backend:\n              service:\n                name: {name}\n                port: {{ number: 80 }}"


def hpa(ns: str, name: str, kind: str, workload: dict[str, Any]) -> str:
    replicas = workload["replicas"]
    target = workload.get("autoscaling", {}).get("cpuTarget", 80)
    return f"{resource('autoscaling/v2', 'HorizontalPodAutoscaler', name, ns)}\nspec:\n  scaleTargetRef: {{ apiVersion: apps/v1, kind: {kind}, name: {name} }}\n  minReplicas: {replicas['min']}\n  maxReplicas: {replicas['max']}\n  metrics: [{{ type: Resource, resource: {{ name: cpu, target: {{ type: Utilization, averageUtilization: {target} }} }} }}]"


def pdb(ns: str, name: str) -> str:
    return f"{resource('policy/v1', 'PodDisruptionBudget', name, ns)}\nspec:\n  minAvailable: 1\n  selector:\n    matchLabels: {{ app.kubernetes.io/name: {name} }}"


def network_policy(ns: str, name: str, port: int) -> str:
    return f"{resource('networking.k8s.io/v1', 'NetworkPolicy', name, ns)}\nspec:\n  podSelector:\n    matchLabels: {{ app.kubernetes.io/name: {name} }}\n  policyTypes: [Ingress, Egress]\n  ingress: [{{ ports: [{{ protocol: TCP, port: {port} }}] }}]\n  egress: [{{}}]"


def config_map(ns: str, name: str, workload: dict[str, Any]) -> str:
    values = workload.get("config", {}) or {"LOG_LEVEL": "INFO"}
    data = "\n".join(f"  {key}: {json.dumps(str(value))}" for key, value in sorted(values.items()))
    return f"{resource('v1', 'ConfigMap', name, ns)}\ndata:\n{data}"


def external_secret(ns: str, name: str, workload: dict[str, Any]) -> str:
    store = workload.get("externalSecret", {}).get("storeName", "cluster-secret-store")
    remote_key = workload.get("externalSecret", {}).get("remoteKey", "<set-at-deploy>")
    return f"{resource('external-secrets.io/v1beta1', 'ExternalSecret', name, ns)}\nspec:\n  refreshInterval: 1h\n  secretStoreRef: {{ name: {store}, kind: ClusterSecretStore }}\n  target: {{ name: {name}-runtime }}\n  dataFrom:\n    - extract: {{ key: \"{remote_key}\" }}"


def pvc(ns: str, name: str, workload: dict[str, Any]) -> str:
    size = workload.get("storage", {}).get("size", "10Gi")
    modes = ", ".join(
        workload.get("storage", {}).get("accessModes", ["ReadWriteOnce"])
    )
    return f"{resource('v1', 'PersistentVolumeClaim', name, ns)}\nspec:\n  accessModes: [{modes}]\n  resources:\n    requests: {{ storage: {size} }}"


def service_monitor(ns: str, name: str, workload: dict[str, Any]) -> str:
    path = workload.get("monitoring", {}).get("metricsPath", "/actuator/prometheus")
    return f"{resource('monitoring.coreos.com/v1', 'ServiceMonitor', name, ns)}\nspec:\n  selector:\n    matchLabels: {{ app.kubernetes.io/name: {name} }}\n  endpoints: [{{ port: http, path: {path} }}]"


def dns_name(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", value.lower().replace("_", "-")).strip("-")[:63].rstrip("-")


def is_dns1123_name(value: str) -> bool:
    return bool(
        len(value) <= 63
        and re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", value)
    )


def valid_cron(value: str) -> bool:
    return len(value.split()) == 5


def valid_quantity(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?"
            r"(?:n|u|m|k|K|M|G|T|P|E|Ki|Mi|Gi|Ti|Pi|Ei)?",
            value,
        )
    )


def as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def deployment_exposed_aliases(diagram: str) -> set[str]:
    """Return aliases that receive traffic from a load balancer or an actor."""
    exposed: set[str] = set()
    external_sources = {"lb", "loadbalancer", "gateway", "ingress"}
    actors = {
        match.group(1)
        for match in re.finditer(r"(?im)^\s*actor\s+(?:\"[^\"]+\"\s+as\s+)?(\w+)", diagram)
    }
    external_sources.update(item.lower() for item in actors)
    for match in re.finditer(r"(?im)^\s*(\w+)\s*(?:-+>|=+>)\s*(\w+)", diagram):
        source, target = match.group(1).lower(), match.group(2).lower()
        if source in external_sources:
            exposed.add(dns_name(target))
    return exposed


def remove_previous_render(application: Path, reports: Path) -> list[str]:
    """Remove only files recorded as managed by the previous renderer run."""
    report_path = reports / "deployment-render.json"
    if not report_path.is_file():
        return []
    try:
        previous = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    removed: list[str] = []
    root = application.parent.resolve()
    for relative in previous.get("renderedFiles", []):
        if not isinstance(relative, str) or not relative.startswith("application/"):
            continue
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(application.resolve())
        except ValueError:
            continue
        if candidate.is_file():
            candidate.unlink()
            removed.append(relative)
    for directory in sorted((application / "k8s").glob("*"), reverse=True):
        if directory.is_dir():
            try:
                directory.rmdir()
            except OSError:
                pass
    return removed


def rendered_warnings(objects: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    for item in objects:
        kind = str(item.get("kind"))
        name = str(item.get("metadata", {}).get("name"))
        if kind in WORKLOAD_KINDS:
            spec = item.get("spec", {})
            if kind == "CronJob":
                pod_specification = (
                    spec.get("jobTemplate", {})
                    .get("spec", {})
                    .get("template", {})
                    .get("spec", {})
                )
            else:
                pod_specification = spec.get("template", {}).get("spec", {})
            for container in pod_specification.get("containers", []):
                image = str(container.get("image", ""))
                if "<" in image or ">" in image:
                    warnings.append(
                        f"{kind}/{name} uses an unresolved image placeholder"
                    )
        if kind == "Ingress" and not item.get("spec", {}).get("ingressClassName"):
            warnings.append(
                f"Ingress/{name} requires a cluster default ingress class"
            )
        if kind == "Ingress":
            warnings.append(
                f"Ingress/{name} requires its TLS Secret to be provisioned"
            )
        if kind == "NetworkPolicy" and item.get("spec", {}).get("egress") == [{}]:
            warnings.append(
                f"NetworkPolicy/{name} allows all egress until destinations are specified"
            )
    return sorted(set(warnings))


def external_prerequisites(intent: dict[str, Any]) -> list[str]:
    required: set[str] = set()
    for workload in intent.get("workloads", []):
        capabilities = workload.get("capabilities", {})
        if capabilities.get("externalSecret"):
            required.update(
                {
                    "External Secrets Operator CRDs",
                    "Configured ClusterSecretStore and cloud workload identity",
                }
            )
        if capabilities.get("serviceMonitor"):
            required.add("Prometheus Operator ServiceMonitor CRD")
        if capabilities.get("ingress"):
            required.update(
                {
                    "Ingress controller",
                    "TLS Secret or certificate automation",
                }
            )
    return sorted(required)


def workload_pod_spec(item: dict[str, Any]) -> dict[str, Any]:
    spec = item.get("spec", {})
    if item.get("kind") == "CronJob":
        return (
            spec.get("jobTemplate", {})
            .get("spec", {})
            .get("template", {})
            .get("spec", {})
        )
    return spec.get("template", {}).get("spec", {})


def indent(text: str, levels: int) -> str:
    prefix = "  " * levels
    return "\n".join(prefix + line if line else line for line in text.splitlines())
