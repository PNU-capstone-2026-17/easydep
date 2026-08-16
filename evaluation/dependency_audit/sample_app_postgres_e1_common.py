"""도메인 중립 E1 앱 실험에서 공급자와 무관한 빌드·기능 오라클을 제공한다."""

from __future__ import annotations

import base64
from pathlib import Path

APP_IMAGE = "easydep/dependency-sample:postgres-e1"
SAMPLE_ROOT = Path(__file__).with_name("sample_app")


def _encoded(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def app_build_script(install_docker: str) -> str:
    """같은 소스와 Dockerfile로 공급자 VM 안에서 시험 이미지를 만든다."""
    service = _encoded(SAMPLE_ROOT / "service.py")
    dockerfile = _encoded(SAMPLE_ROOT / "Dockerfile")
    return f"""{install_docker}
sudo mkdir -p /opt/easydep-sample
echo '{service}' | base64 -d | sudo tee /opt/easydep-sample/service.py >/dev/null
echo '{dockerfile}' | base64 -d | sudo tee /opt/easydep-sample/Dockerfile >/dev/null
sudo docker build --tag {APP_IMAGE} /opt/easydep-sample
touch /tmp/easydep-ready
"""


def baseline_script() -> str:
    """앱 준비 상태와 일반 key-value 쓰기·읽기를 함께 검사한다."""
    return r"""
set -eu
test "$(curl -sS -o /tmp/health.json -w '%{http_code}' http://127.0.0.1:8080/health/ready)" = 200
test "$(curl -sS -o /tmp/write.json -w '%{http_code}' -X PUT -H 'Content-Type: application/json' -d '{"value":{"message":"kept"}}' http://127.0.0.1:8080/records/evidence)" = 200
test "$(curl -sS -o /tmp/read.json -w '%{http_code}' http://127.0.0.1:8080/records/evidence)" = 200
grep -q '"message": "kept"' /tmp/read.json
""".strip()


def blocked_script() -> str:
    """상태 연결이 끊기면 readiness와 업무 요청이 각각 503/502인지 검사한다."""
    return r"""
set -eu
for i in $(seq 1 36); do
  health=$(curl -sS -o /tmp/health.json -w '%{http_code}' http://127.0.0.1:8080/health/ready || true)
  business=$(curl -sS -o /tmp/read.json -w '%{http_code}' http://127.0.0.1:8080/records/evidence || true)
  if [ "$health" = 503 ] && [ "$business" = 502 ]; then exit 0; fi
  sleep 5
done
exit 1
""".strip()


def restored_script() -> str:
    """연결 복구 또는 VM 재부팅 뒤 기존 업무 값이 남았는지 검사한다."""
    return r"""
set -eu
for i in $(seq 1 60); do
  status=$(curl -sS -o /tmp/read.json -w '%{http_code}' http://127.0.0.1:8080/records/evidence || true)
  if [ "$status" = 200 ] && grep -q '"message": "kept"' /tmp/read.json; then exit 0; fi
  sleep 5
done
exit 1
""".strip()
