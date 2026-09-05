"""공개 단일 VM 배포 패키지를 실제 클라우드에서 일회성으로 검증한다.

이 스크립트는 EasyDep이 사용자에게 제공하는 PowerShell 스크립트를 그대로 호출한다.
테스트용 애플리케이션과 OpenTofu 상태는 시스템 임시 폴더에만 만들며, 검증이 끝나면
클라우드 자원과 이 실행이 만든 로컬 이미지 태그를 정리한다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.cloudkb.depkb.provider_cache import (  # noqa: E402
    provider_cache_environment,
    provider_mirror_configuration,
)
from app.design.services.deployment_diagram.bundle import (  # noqa: E402
    build_deployment_diagram_bundle,
)
from app.implementation.delivery.iac_renderer import render_open_tofu  # noqa: E402
from app.implementation.delivery.package import render_deployment_package  # noqa: E402
from scripts.generate_deployment_diagram_examples import (  # noqa: E402
    deployment_case_graph,
    deployment_resource_spec,
    semantic_case_id,
)
from scripts.validate_deployment_iac_examples import (  # noqa: E402
    DEFAULT_TWO_WORKLOADS_ONE_PERSISTENT,
    PUBLIC_SINGLE,
    ZONE_SPREAD,
)

PLACEHOLDER_DIGEST = "sha256:" + "0" * 64
SMOKE_CONFIG_VALUE = "easydep-live-smoke-secret"
COLOCATED_TWO = semantic_case_id(
    compute_kind="standaloneVm",
    compute_units=1,
    replicas=1,
    zones=1,
    workload_count=2,
    persistent_workload_count=0,
    colocate_relation_count=0,
    separate_relation_count=0,
    ingress_kind="directPublicIp",
)
SEPARATED_TWO = semantic_case_id(
    compute_kind="standaloneVm",
    compute_units=2,
    replicas=1,
    zones=1,
    workload_count=2,
    persistent_workload_count=0,
    colocate_relation_count=0,
    separate_relation_count=1,
    ingress_kind="directPublicIp",
)
PERSISTENT_SEPARATED = semantic_case_id(
    compute_kind="standaloneVm",
    compute_units=2,
    replicas=1,
    zones=1,
    workload_count=2,
    persistent_workload_count=1,
    colocate_relation_count=0,
    separate_relation_count=1,
    ingress_kind="directPublicIp",
)
PER_REPLICA_STORAGE = semantic_case_id(
    compute_kind="standaloneVm",
    compute_units=2,
    replicas=1,
    zones=1,
    workload_count=2,
    persistent_workload_count=1,
    colocate_relation_count=0,
    separate_relation_count=0,
    ingress_kind="directPublicIp",
    per_replica_storage_count=1,
)
SINGLE_ZONE_MANAGED = semantic_case_id(
    compute_kind="managedVmGroup",
    compute_units=1,
    replicas=2,
    zones=1,
    workload_count=1,
    persistent_workload_count=0,
    colocate_relation_count=0,
    separate_relation_count=0,
    ingress_kind="loadBalancer",
)
PRIVATE_SINGLE = semantic_case_id(
    compute_kind="standaloneVm",
    compute_units=1,
    replicas=1,
    zones=1,
    workload_count=1,
    persistent_workload_count=0,
    colocate_relation_count=0,
    separate_relation_count=0,
    ingress_kind="privateEgressOnly",
)
CASES = {
    "public-single": PUBLIC_SINGLE,
    "zone-spread": ZONE_SPREAD,
    "colocated-two": COLOCATED_TWO,
    "separated-two": SEPARATED_TWO,
    "persistent-colocated": DEFAULT_TWO_WORKLOADS_ONE_PERSISTENT,
    "persistent-separated": PERSISTENT_SEPARATED,
    "multi-persistent-separated": PERSISTENT_SEPARATED,
    "per-replica-storage": PER_REPLICA_STORAGE,
    "single-zone-managed": SINGLE_ZONE_MANAGED,
    "private-single": PRIVATE_SINGLE,
    "secret-binding": PUBLIC_SINGLE,
    "course-registration-app": PUBLIC_SINGLE,
}


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    environment: dict[str, str] | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """명령을 UTF-8로 실행하고 실패한 명령을 즉시 드러낸다."""

    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=check,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _output(command: list[str], environment: dict[str, str]) -> str:
    return _run(command, environment=environment, capture=True).stdout.strip()


def _cli(name: str, *arguments: str) -> list[str]:
    """Windows의 ``.cmd`` 실행 파일을 포함한 실제 CLI 경로를 찾는다."""

    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"필요한 CLI를 찾지 못했습니다: {name}")
    return [executable, *arguments]


def _write_smoke_application(application: Path, *, require_secret: bool = False) -> None:
    """외부 패키지가 필요 없는 작은 HTTP 애플리케이션을 만든다."""

    application.mkdir()
    (application / "Dockerfile").write_text(
        """FROM python:3.12-alpine
WORKDIR /app
COPY app.py .
USER 10001:10001
EXPOSE 8000
CMD ["python", "app.py"]
""",
        encoding="utf-8",
        newline="\n",
    )
    application_source = """import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.request import urlopen


EXPECTED_API_TOKEN = __EXPECTED_API_TOKEN__
data_directory = Path("/var/lib/easydep/state")
if data_directory.is_dir():
    (data_directory / "live-smoke.txt").write_text("mounted", encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        healthy = self.path in ("/healthz", "/actuator/health")
        if healthy and EXPECTED_API_TOKEN:
            healthy = os.getenv("API_TOKEN") == EXPECTED_API_TOKEN
        if healthy and os.getenv("EASYDEP_EXPECT_STORAGE") == "1":
            healthy = (data_directory / "live-smoke.txt").is_file()
        state_url = os.getenv("STATE_SERVICE_URL", "").rstrip("/")
        if healthy and state_url:
            try:
                with urlopen(state_url + "/healthz", timeout=2) as response:
                    healthy = response.status == 200
            except OSError:
                healthy = False
        self.send_response(200 if healthy else 404)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        return


HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
"""
    (application / "app.py").write_text(
        application_source.replace(
            "__EXPECTED_API_TOKEN__",
            json.dumps(SMOKE_CONFIG_VALUE if require_secret else ""),
        ),
        encoding="utf-8",
        newline="\n",
    )


def _live_graph(case_name: str) -> dict[str, Any]:
    """실배포에 쓸 graph를 만들고 테스트 불가능한 예시 placeholder만 치환한다."""

    graph = deployment_case_graph(CASES[case_name])
    if case_name in {
        "persistent-colocated",
        "persistent-separated",
        "multi-persistent-separated",
        "per-replica-storage",
    }:
        state = next(item for item in graph["workloads"] if item["id"] == "state")
        state["artifact"] = {"kind": "generatedApplication"}
        # smoke 실행은 비용과 데이터가 남지 않아야 하므로 예시의 retain 정책만 바꾼다.
        state["storage"][0]["deletionPolicy"] = "delete"
    if case_name == "multi-persistent-separated":
        web = next(item for item in graph["workloads"] if item["id"] == "web")
        web["storage"] = [
            {
                "id": "web-volume",
                "persistence": "persistent",
                "capacityGiB": 10,
                "mountPath": "/var/lib/easydep/state",
                "deletionPolicy": "delete",
                "replicaSemantics": "singleAttachment",
                "sourceRefs": ["requirement:WEB-DATA"],
            }
        ]
        for workload in graph["workloads"]:
            workload["configuration"].append(
                {
                    "id": "expect-storage",
                    "name": "EASYDEP_EXPECT_STORAGE",
                    "kind": "value",
                    "value": "1",
                    "sourceRefs": ["requirement:LIVE-STORAGE"],
                }
            )
    if case_name == "secret-binding":
        graph["workloads"][0]["configuration"] = [
            {
                "id": "api-token",
                "name": "API_TOKEN",
                "kind": "secretBinding",
                "sensitive": True,
                "sourceRefs": ["requirement:LIVE-SECRET"],
            }
        ]
    if case_name == "course-registration-app":
        web = graph["workloads"][0]
        web["interfaces"][0]["healthPath"] = "/healthz"
        web["storage"] = [
            {
                "id": "application-data",
                "persistence": "persistent",
                "capacityGiB": 10,
                "mountPath": "/var/lib/easydep/state",
                "deletionPolicy": "delete",
                "replicaSemantics": "singleAttachment",
                "sourceRefs": ["requirement:RR15"],
            }
        ]
        web["configuration"] = [
            {
                "id": "datasource-url",
                "name": "SPRING_DATASOURCE_URL",
                "kind": "value",
                "value": (
                    "jdbc:h2:file:/var/lib/easydep/state/course-registration;"
                    "MODE=MySQL;DATABASE_TO_LOWER=TRUE;DB_CLOSE_ON_EXIT=FALSE"
                ),
                "sourceRefs": ["requirement:RR15"],
            },
            {
                "id": "datasource-username",
                "name": "SPRING_DATASOURCE_USERNAME",
                "kind": "value",
                "value": "sa",
                "sourceRefs": ["requirement:RR15"],
            },
            {
                "id": "datasource-password",
                "name": "SPRING_DATASOURCE_PASSWORD",
                "kind": "value",
                "value": "easydep-live-smoke",
                "sourceRefs": ["requirement:RR15"],
            },
            {
                "id": "security-username",
                "name": "SPRING_SECURITY_USER_NAME",
                "kind": "value",
                "value": "easydep",
                "sourceRefs": ["requirement:RR1"],
            },
            {
                "id": "security-password",
                "name": "SPRING_SECURITY_USER_PASSWORD",
                "kind": "value",
                "value": "easydep-live-smoke",
                "sourceRefs": ["requirement:RR1"],
            },
        ]
    return graph


def _create_aws_smoke_secret(prefix: str, environment: dict[str, str]) -> str:
    """실행 중에만 존재하는 AWS Secret을 만들고 ARN을 돌려준다."""

    return _output(
        _cli(
            "aws",
            "secretsmanager",
            "create-secret",
            "--region",
            "ap-northeast-2",
            "--name",
            f"{prefix}-api-token",
            "--secret-string",
            SMOKE_CONFIG_VALUE,
            "--query",
            "ARN",
            "--output",
            "text",
        ),
        environment,
    )


def _delete_aws_smoke_secret(secret_ref: str, environment: dict[str, str]) -> None:
    """실배포 검증이 만든 AWS Secret만 복구 대기 없이 제거한다."""

    result = _run(
        _cli(
            "aws",
            "secretsmanager",
            "delete-secret",
            "--region",
            "ap-northeast-2",
            "--secret-id",
            secret_ref,
            "--force-delete-without-recovery",
        ),
        environment=environment,
        capture=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"AWS 테스트 Secret 정리에 실패했습니다: {result.stderr.strip()}")


def _create_gcp_smoke_secret(
    prefix: str, environment: dict[str, str], workspace: Path
) -> str:
    """GCP 검증용 Secret과 첫 버전을 만들고 짧은 Secret 이름을 돌려준다."""

    secret_name = f"{prefix}-api-token"
    _run(
        _cli(
            "gcloud",
            "secrets",
            "create",
            secret_name,
            "--replication-policy=automatic",
            "--quiet",
        ),
        environment=environment,
    )
    try:
        secret_file = workspace / "gcp-smoke-secret.txt"
        secret_file.write_text(SMOKE_CONFIG_VALUE, encoding="utf-8", newline="")
        _run(
            _cli(
                "gcloud",
                "secrets",
                "versions",
                "add",
                secret_name,
                f"--data-file={secret_file}",
                "--quiet",
            ),
            environment=environment,
        )
    except Exception:
        _delete_gcp_smoke_secret(secret_name, environment)
        raise
    return secret_name


def _delete_gcp_smoke_secret(secret_name: str, environment: dict[str, str]) -> None:
    """이번 실행에서 만든 GCP Secret만 삭제한다."""

    result = _run(
        _cli("gcloud", "secrets", "delete", secret_name, "--quiet"),
        environment=environment,
        capture=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"GCP 테스트 Secret 정리에 실패했습니다: {result.stderr.strip()}")


def _azure_secret_names(prefix: str) -> tuple[str, str]:
    """Azure의 길이 제한 안에서 실행별 Resource Group과 vault 이름을 만든다."""

    unique_part = prefix.rsplit("-", 1)[-1]
    return f"{prefix}-secret-rg", f"edlive{unique_part}kv"


def _create_azure_smoke_secret(prefix: str, environment: dict[str, str]) -> str:
    """검증용 Key Vault Secret을 만들고 권한 범위에 쓸 리소스 ID를 돌려준다."""

    resource_group, vault_name = _azure_secret_names(prefix)
    _run(
        _cli(
            "az",
            "group",
            "create",
            "--name",
            resource_group,
            "--location",
            "koreacentral",
            "--output",
            "none",
        ),
        environment=environment,
    )
    try:
        # 먼저 생성자 access policy로 테스트 값을 넣은 뒤 RBAC 방식으로 전환한다.
        # 배포 템플릿이 만드는 VM 역할은 RBAC의 Key Vault Secrets User를 사용한다.
        _run(
            _cli(
                "az",
                "keyvault",
                "create",
                "--name",
                vault_name,
                "--resource-group",
                resource_group,
                "--location",
                "koreacentral",
                "--enable-rbac-authorization",
                "false",
                "--output",
                "none",
            ),
            environment=environment,
        )
        _run(
            _cli(
                "az",
                "keyvault",
                "secret",
                "set",
                "--vault-name",
                vault_name,
                "--name",
                "api-token",
                "--value",
                SMOKE_CONFIG_VALUE,
                "--output",
                "none",
            ),
            environment=environment,
        )
        _run(
            _cli(
                "az",
                "keyvault",
                "update",
                "--name",
                vault_name,
                "--resource-group",
                resource_group,
                "--enable-rbac-authorization",
                "true",
                "--output",
                "none",
            ),
            environment=environment,
        )
        subscription_id = _output(
            _cli("az", "account", "show", "--query", "id", "--output", "tsv"),
            environment,
        )
    except Exception:
        _delete_azure_smoke_secret(prefix, environment)
        raise
    return (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.KeyVault/vaults/{vault_name}/secrets/api-token"
    )


def _delete_azure_smoke_secret(prefix: str, environment: dict[str, str]) -> None:
    """이번 실행에서 만든 Azure Resource Group과 soft-deleted vault를 정리한다."""

    resource_group, vault_name = _azure_secret_names(prefix)
    group_result = _run(
        _cli("az", "group", "delete", "--name", resource_group, "--yes"),
        environment=environment,
        capture=True,
        check=False,
    )
    if group_result.returncode != 0:
        print(f"Azure 테스트 Secret Resource Group 정리에 실패했습니다: {group_result.stderr.strip()}")
        return
    purge_result = _run(
        _cli(
            "az",
            "keyvault",
            "purge",
            "--name",
            vault_name,
            "--location",
            "koreacentral",
            "--no-wait",
        ),
        environment=environment,
        capture=True,
        check=False,
    )
    if purge_result.returncode != 0:
        print(f"Azure 테스트 vault purge에 실패했습니다: {purge_result.stderr.strip()}")


def _copy_application(source: Path, destination: Path) -> None:
    """빌드 캐시처럼 배포 입력이 아닌 큰 디렉터리를 제외하고 앱을 복사한다."""

    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("deployment", "build", "node_modules", ".gradle"),
    )
    dockerignore = destination / ".dockerignore"
    content = dockerignore.read_text(encoding="utf-8") if dockerignore.is_file() else ""
    patterns = content.splitlines()
    if "/deployment" not in patterns:
        existing = content.rstrip()
        dockerignore.write_text(
            (existing + "\n" if existing else "") + "/deployment\n",
            encoding="utf-8",
            newline="\n",
        )


def _provider_inputs(
    provider: str, environment: dict[str, str], workspace: Path
) -> dict[str, str]:
    """로그인된 CLI에서 공개 식별자만 읽어 terraform 변수로 돌려준다."""

    if provider == "aws":
        region = "ap-northeast-2"
        image_id = _output(
            _cli(
                "aws",
                "ssm",
                "get-parameter",
                "--region",
                region,
                "--name",
                "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id",
                "--query",
                "Parameter.Value",
                "--output",
                "text",
            ),
            environment,
        )
        return {'boot_image_id = "ami-REPLACE_ME"': f'boot_image_id = "{image_id}"'}
    if provider == "azure":
        subscription_id = _output(
            _cli("az", "account", "show", "--query", "id", "--output", "tsv"),
            environment,
        )
        private_key = workspace / "azure-smoke-key"
        _run(
            _cli(
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                "easydep-live-smoke",
                "-f",
                str(private_key),
            ),
            environment=environment,
        )
        public_key = private_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
        # Korea Central에서 기본 B2s는 현재 용량 부족이고 DASv5 계열 쿼터는 0이다.
        # 실배포 검증에서는 이 구독에 쿼터가 남아 있는 Basv2 계열을 사용한다.
        environment["TF_VAR_vm_sku"] = "Standard_B2als_v2"
        return {
            'subscription_id = ""': f'subscription_id = "{subscription_id}"',
            'ssh_public_key = ""': f'ssh_public_key = "{public_key}"',
        }
    project_id = _output(
        _cli("gcloud", "config", "get-value", "project", "--quiet"), environment
    )
    if not project_id or project_id == "(unset)":
        raise RuntimeError("gcloud 기본 project가 설정되어 있지 않습니다.")
    environment["GOOGLE_OAUTH_ACCESS_TOKEN"] = _output(
        _cli("gcloud", "auth", "print-access-token", "--quiet"), environment
    )
    return {'project_id = ""': f'project_id = "{project_id}"'}


def _new_image_tags(environment: dict[str, str], before: set[str], prefix: str) -> set[str]:
    output = _output(
        ["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"], environment
    )
    # Azure Container Registry 이름은 하이픈을 제거한다. 사람이 지정한 prefix를 그대로
    # 찾으면 Azure에서 만든 로컬 태그만 남으므로 두 문자열을 같은 규칙으로 비교한다.
    normalized_prefix = re.sub(r"[^a-z0-9]", "", prefix.lower())
    return {
        tag
        for tag in output.splitlines()
        if tag not in before
        and normalized_prefix in re.sub(r"[^a-z0-9]", "", tag.lower())
    }


def _load_image_digests(path: Path, environment: dict[str, str]) -> None:
    """생성 스크립트가 기록한 digest 변수를 후속 OpenTofu 명령에 다시 넣는다."""

    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        name, separator, value = line.partition("=")
        if separator and name.startswith("TF_VAR_image_digest_"):
            environment[name] = value


def _aws_instance_ids(tofu: Path, environment: dict[str, str]) -> list[str]:
    """OpenTofu output의 단일 VM과 Auto Scaling Group을 실제 instance ID로 푼다."""

    outputs = json.loads(
        _run(
            ["tofu", "output", "-json"],
            cwd=tofu,
            environment=environment,
            capture=True,
        ).stdout
    )
    ids: list[str] = []
    for name, output in outputs.items():
        if not name.startswith("resource_id_") or not isinstance(output, dict):
            continue
        value = str(output.get("value") or "")
        if value.startswith("i-"):
            ids.append(value)
            continue
        if value:
            members = _output(
                _cli(
                    "aws",
                    "autoscaling",
                    "describe-auto-scaling-groups",
                    "--region",
                    "ap-northeast-2",
                    "--auto-scaling-group-names",
                    value,
                    "--query",
                    "AutoScalingGroups[0].Instances[].InstanceId",
                    "--output",
                    "text",
                ),
                environment,
            )
            ids.extend(item for item in members.split() if item.startswith("i-"))
    return list(dict.fromkeys(ids))


def _wait_for_aws_bootstrap(tofu: Path, environment: dict[str, str]) -> None:
    """공개 주소가 없는 VM을 포함해 모든 AWS cloud-init 완료 표식을 기다린다."""

    instance_ids = _aws_instance_ids(tofu, environment)
    if not instance_ids:
        raise RuntimeError("AWS 배포에서 확인할 EC2 instance를 찾지 못했습니다.")
    waiting = set(instance_ids)
    deadline = time.monotonic() + 600
    failure_markers = (
        "Failed to run module scripts-user",
        "scripts-user' failed",
        "Failed to run module scripts_user",
        "scripts_user' failed",
        "did not attach",
        "EASYDEP_BOOTSTRAP_FAILED",
    )
    while waiting and time.monotonic() < deadline:
        for instance_id in tuple(waiting):
            result = _run(
                _cli(
                    "aws",
                    "ec2",
                    "get-console-output",
                    "--region",
                    "ap-northeast-2",
                    "--instance-id",
                    instance_id,
                    "--latest",
                    "--query",
                    "Output",
                    "--output",
                    "text",
                ),
                environment=environment,
                capture=True,
                check=False,
            )
            output = result.stdout or ""
            if any(marker in output for marker in failure_markers):
                raise RuntimeError(f"EC2 {instance_id}의 cloud-init이 실패했습니다.")
            if "EASYDEP_BOOTSTRAP_OK" in output:
                waiting.remove(instance_id)
                print(f"AWS_BOOTSTRAP_OK={instance_id}")
        if waiting:
            time.sleep(15)
    if waiting:
        raise RuntimeError(
            "EC2 cloud-init 완료 표식을 기다리다 시간 초과했습니다: "
            + ", ".join(sorted(waiting))
        )


def run_smoke(provider: str, application_source: Path | None, case_name: str) -> None:
    """한 공급자의 생성·배포·검증·정리 전 과정을 실행한다."""

    if case_name == "course-registration-app" and application_source is None:
        raise ValueError("course-registration-app에는 --application-root가 필요합니다.")

    prefix = f"easydep-live-{provider}-{uuid.uuid4().hex[:8]}"
    workspace = Path(tempfile.mkdtemp(prefix=f"{prefix}-"))
    application = workspace / "application"
    environment = provider_cache_environment()
    environment.update(os.environ)
    tofu_config = workspace / "tofurc"
    tofu_config.write_text(
        provider_mirror_configuration(), encoding="utf-8", newline="\n"
    )
    # filesystem mirror가 설치 원본이므로 같은 경로를 plugin cache로도 지정하면
    # OpenTofu가 provider 디렉터리를 자기 자신에게 복사하려고 한다.
    environment.pop("TF_PLUGIN_CACHE_DIR", None)
    environment["TF_CLI_CONFIG_FILE"] = str(tofu_config)
    environment["TF_VAR_image_digest_web"] = PLACEHOLDER_DIGEST
    before_images = set(
        _output(
            ["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"],
            environment,
        ).splitlines()
    )
    state_is_empty = False
    secret_ref = ""
    try:
        if case_name == "secret-binding":
            if provider == "aws":
                secret_ref = _create_aws_smoke_secret(prefix, environment)
            elif provider == "azure":
                secret_ref = _create_azure_smoke_secret(prefix, environment)
            elif provider == "gcp":
                secret_ref = _create_gcp_smoke_secret(prefix, environment, workspace)
        if application_source is None:
            _write_smoke_application(
                application, require_secret=case_name == "secret-binding"
            )
        else:
            _copy_application(application_source.resolve(), application)

        bundle = build_deployment_diagram_bundle(
            _live_graph(case_name), deployment_resource_spec(provider)
        )
        resource_plan = bundle["projections"][0]["resourcePlan"]
        rendered = render_open_tofu(resource_plan)
        package = render_deployment_package(application, resource_plan, rendered)

        tfvars = package / "tofu" / "terraform.tfvars.example"
        content = tfvars.read_text(encoding="utf-8").replace(
            'resource_prefix = "easydep"', f'resource_prefix = "{prefix}"'
        )
        for old, new in _provider_inputs(provider, environment, workspace).items():
            content = content.replace(old, new)
        if secret_ref:
            # terraform.tfvars는 TF_VAR_* 환경 변수보다 우선한다. 예제 파일의 빈
            # secret 값까지 그대로 복사하면 외부에서 준 참조가 가려지므로,
            # 이번 smoke 실행에서 만든 정확한 Secret 참조를 로컬 tfvars에 기록한다.
            content = content.replace(
                'secret_reference_web_api_token = ""',
                f'secret_reference_web_api_token = {json.dumps(secret_ref)}',
            )
        (package / "tofu" / "terraform.tfvars").write_text(
            content, encoding="utf-8", newline="\n"
        )

        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell is None:
            raise RuntimeError("PowerShell 실행 파일을 찾지 못했습니다.")

        def run_script(name: str, *arguments: str) -> None:
            _run(
                [
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(package / "scripts" / name),
                    *arguments,
                ],
                cwd=application,
                environment=environment,
            )

        # 생성된 smoke script의 중간 cleanup을 잠시 미뤄 모든 사설 VM까지 확인한다.
        # 이 함수의 finally가 같은 state를 사용해 항상 정리를 수행한다.
        environment["KEEP_RESOURCES"] = "1"
        if provider == "aws":
            # 공개 health 요청은 cloud-init 실패도 10분 동안 기다릴 수 있다. AWS
            # 콘솔 표식을 먼저 확인하면 사설 VM까지 보고 실패도 더 빨리 드러난다.
            run_script("doctor.ps1")
            run_script("prepare-images.ps1")
            _load_image_digests(
                package / "runtime" / "image-digests.env", environment
            )
            run_script("plan.ps1", "-input=false")
            run_script("deploy.ps1", "-auto-approve")
            _wait_for_aws_bootstrap(package / "tofu", environment)
            if case_name != "private-single":
                run_script("verify.ps1")
        else:
            run_script("smoke-test.ps1")
    finally:
        tofu = application / "deployment" / "tofu"
        _load_image_digests(
            application / "deployment" / "runtime" / "image-digests.env",
            environment,
        )
        state_file = tofu / "terraform.tfstate"
        if not state_file.is_file():
            # init이나 첫 apply 전에 실패했다면 정리할 클라우드 자원이 없다.
            state_is_empty = True
        elif tofu.is_dir():
            state = _run(
                ["tofu", "state", "list"],
                cwd=tofu,
                environment=environment,
                capture=True,
                check=False,
            )
            state_is_empty = state.returncode == 0 and not state.stdout.strip()
            if not state_is_empty:
                _run(
                    ["tofu", "destroy", "-auto-approve", "-input=false"],
                    cwd=tofu,
                    environment=environment,
                    check=False,
                )
                state = _run(
                    ["tofu", "state", "list"],
                    cwd=tofu,
                    environment=environment,
                    capture=True,
                    check=False,
                )
                state_is_empty = state.returncode == 0 and not state.stdout.strip()
        for tag in sorted(_new_image_tags(environment, before_images, prefix)):
            _run(["docker", "image", "rm", tag], environment=environment, check=False)
        if secret_ref and provider == "aws":
            _delete_aws_smoke_secret(secret_ref, environment)
        elif secret_ref and provider == "azure":
            _delete_azure_smoke_secret(prefix, environment)
        elif secret_ref and provider == "gcp":
            _delete_gcp_smoke_secret(secret_ref, environment)
        if state_is_empty:
            shutil.rmtree(workspace, ignore_errors=True)
            print(f"{provider.upper()}_LIVE_SMOKE=CLEANED")
        else:
            print(f"자원이 남아 있어 복구용 작업 공간을 보존했습니다: {workspace}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True, choices=("aws", "azure", "gcp"))
    parser.add_argument("--case", choices=tuple(CASES), default="public-single")
    parser.add_argument("--application-root", type=Path)
    arguments = parser.parse_args()
    run_smoke(arguments.provider, arguments.application_root, arguments.case)


if __name__ == "__main__":
    main()
