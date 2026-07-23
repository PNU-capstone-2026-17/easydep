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
    intent = (
        json.loads(intent_path.read_text(encoding="utf-8"))
        if has_intent
        else infer_intent(spec.name, cloud)
    )
    validate_intent(intent)
    application = run_root / "application"
    rendered: list[str] = []

    def write(relative: str, content: str) -> None:
        path = application / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        rendered.append(f"application/{relative}")

    namespace = str(intent.get("namespace", "default"))
    write(
        "Dockerfile",
        """FROM gradle:8.14.2-jdk21 AS build
WORKDIR /app
COPY . .
RUN gradle bootJar --no-daemon

FROM eclipse-temurin:21-jre-alpine
WORKDIR /app
RUN addgroup -S app && adduser -S app -G app
COPY --from=build /app/build/libs/*.jar app.jar
USER app
EXPOSE 8000
ENTRYPOINT ["java", "-jar", "app.jar"]""",
    )
    if intent.get("createNamespace", True):
        write("k8s/namespace.yaml", resource("v1", "Namespace", namespace))
    for workload in intent["workloads"]:
        render_workload(write, namespace, workload)
    validation = validate_rendered_manifests(application, rendered)
    report = {
        "schemaVersion": "easydep-deployment-render/v1alpha1",
        "intent": intent,
        "renderedFiles": sorted(rendered),
        "renderer": "deterministic",
        "validation": validation,
    }
    reports = run_root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "deployment-render.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
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
        path = workload["health"].get("path", "/healthz")
        probes = (
            f"\n          readinessProbe:\n            httpGet: {{ path: {path}, port: {port} }}"
            f"\n          livenessProbe:\n            httpGet: {{ path: {path}, port: {port} }}"
        )
    return (
        f"      restartPolicy: {restart}{account}\n      containers:\n"
        f"        - name: {name}\n          image: {workload['image']}\n"
        f"          ports: [{{ containerPort: {port} }}]\n"
        f"          resources:\n            requests: {{ cpu: \"{requests['cpu']}\", memory: \"{requests['memory']}\" }}\n"
        f"            limits: {{ cpu: \"{limits['cpu']}\", memory: \"{limits['memory']}\" }}"
        f"{env_from}{probes}{storage}"
    )


def infer_intent(name: str, cloud: dict[str, Any]) -> dict[str, Any]:
    resources = cloud.get("resources", [])
    cluster = next((item for item in resources if item.get("type") == "Microsoft.ContainerService/managedClusters"), {})
    registry = next((item.get("name") for item in resources if item.get("type") == "Microsoft.ContainerRegistry/registries"), "<acr-name>")
    has_secret_store = any(
        item.get("type") == "Microsoft.KeyVault/vaults" for item in resources
    )
    networking = cluster.get("networking", {})
    workloads = []
    for source in cluster.get("workloads", []) or [{"name": name}]:
        workload_name = dns_name(str(source.get("name", name)))
        replicas = source.get("replicas", {"min": 1, "max": 1})
        api_like = bool(source.get("probes")) or "api" in workload_name or "service" in workload_name
        use_ingress = api_like and networking.get("ingressProtocol") == "HTTPS"
        metrics_path = source.get("monitoring", {}).get("metricsPath")
        workloads.append({
            "name": workload_name, "kind": "Deployment",
            "image": f"{registry}.azurecr.io/{workload_name}:<tag>",
            "port": int(networking.get("containerPort", 8000)),
            "replicas": replicas, "resources": source.get("resources", {}),
            "health": {"path": source.get("probes", {}).get("readiness", "/healthz")},
            "service": {
                "type": "ClusterIP" if use_ingress else networking.get("serviceExposure", "ClusterIP")
            },
            "monitoring": {"metricsPath": metrics_path} if metrics_path else {},
            "capabilities": {
                "service": api_like,
                "ingress": use_ingress,
                "hpa": replicas.get("max", 1) > replicas.get("min", 1),
                "pdb": replicas.get("min", 1) > 1,
                "networkPolicy": True, "serviceAccount": True,
                "externalSecret": has_secret_store, "configMap": False,
                "pvc": bool(source.get("persistentVolume")),
                "serviceMonitor": bool(metrics_path),
            },
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
            elif int(replicas.get("max", 0)) <= int(replicas.get("min", 0)):
                errors.append(f"{at}: hpa requires replicas.max > replicas.min")
        if kind == "StatefulSet" and not cap.get("service"):
            errors.append(f"{at}: StatefulSet requires a governing service")
        if cap.get("pvc"):
            access_modes = workload.get("storage", {}).get(
                "accessModes", ["ReadWriteOnce"]
            )
            minimum = int(workload.get("replicas", {}).get("min", 1))
            if minimum > 1 and "ReadWriteMany" not in access_modes:
                errors.append(
                    f"{at}: pvc with multiple replicas requires ReadWriteMany"
                )
        if kind == "CronJob" and not workload.get("schedule"):
            errors.append(f"{at}: CronJob requires schedule")
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
    if errors:
        raise ValueError("Invalid rendered Kubernetes manifests:\n- " + "\n- ".join(errors))
    return {"status": "SUCCEEDED", "manifestCount": len(objects)}


def resource(api: str, kind: str, name: str, namespace: str | None = None) -> str:
    suffix = f"\n  namespace: {namespace}" if namespace else ""
    return f"apiVersion: {api}\nkind: {kind}\nmetadata:\n  name: {name}{suffix}"


def service(ns: str, name: str, port: int, workload: dict[str, Any]) -> str:
    service_type = workload.get("service", {}).get("type", "ClusterIP")
    return f"{resource('v1', 'Service', name, ns)}\n  labels: {{ app.kubernetes.io/name: {name} }}\nspec:\n  type: {service_type}\n  selector: {{ app.kubernetes.io/name: {name} }}\n  ports: [{{ name: http, port: 80, targetPort: {port} }}]"


def ingress(ns: str, name: str, workload: dict[str, Any]) -> str:
    host = workload.get("ingress", {}).get("host", f"{name}.example.invalid")
    tls_name = workload.get("ingress", {}).get("tlsSecretName", f"{name}-tls")
    return f"{resource('networking.k8s.io/v1', 'Ingress', name, ns)}\nspec:\n  tls:\n    - hosts: [{host}]\n      secretName: {tls_name}\n  rules:\n    - host: {host}\n      http:\n        paths:\n          - path: /\n            pathType: Prefix\n            backend:\n              service:\n                name: {name}\n                port: {{ number: 80 }}"


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


def indent(text: str, levels: int) -> str:
    prefix = "  " * levels
    return "\n".join(prefix + line if line else line for line in text.splitlines())
