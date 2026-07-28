"""cb-spider 지원 매트릭스 (kbcommon/cbspider.py).

여기서 지켜야 하는 것:
- **메서드 이름이 핸들러마다 다르다.** VM은 `StartVM`, 이미지는 `SnapshotVM`이다.
  `Create`로만 찾으면 그 둘이 전부 미구현으로 잡힌다(처음에 그 상태를 만들었다).
- **스텁을 구현으로 세지 않는다.** 파일이 있다고 되는 게 아니다.
- **과잉 판정도 막는다.** 긴 본문 속 "not supported"는 일부 기능 제한일 수 있어
  스텁으로 보지 않는다 — 되는 것을 안 된다고 답하면 그것도 오답이다.
- **도구의 커버리지이지 클라우드의 사실이 아니다.** "드라이버가 없다"를
  "CSP에 그 기능이 없다"로 옮겨 말하면 거짓이 된다 — 우리는 배포기가 아니라
  가이드라인 지식베이스를 만든다.
"""

from __future__ import annotations

import io
import json
import tarfile

import pytest

from app.deployment.envkb import cbspider
from app.deployment.tests._helpers import flat

REAL = """
package resources

func (h *KtVPCHandler) CreateVPC(req irs.VPCReqInfo) (irs.VPCInfo, error) {
	client, err := getClient()
	if err != nil {
		return irs.VPCInfo{}, err
	}
	result, err := client.CreateVpc(req.IId.NameId)
	if err != nil {
		return irs.VPCInfo{}, err
	}
	return toVPCInfo(result), nil
}
"""

STUB = """
package resources

func (h *KtClusterHandler) CreateCluster(req irs.ClusterInfo) (irs.ClusterInfo, error) {
	return irs.ClusterInfo{}, fmt.Errorf("CreateCluster is not supported!")
}
"""

# 긴 본문 안의 미지원 문구 — 일부 기능 제한이지 핸들러 전체가 스텁인 게 아니다
PARTIAL = """
package resources

func (h *NhnNLBHandler) CreateNLB(req irs.NLBInfo) (irs.NLBInfo, error) {
	// UDP health check is not supported by this CSP; fall back to TCP.
	client, err := getClient()
	if err != nil {
		return irs.NLBInfo{}, err
	}
	if req.HealthChecker.Protocol == "UDP" {
		return irs.NLBInfo{}, fmt.Errorf("UDP health check is not supported")
	}
	lb, err := client.CreateLoadBalancer(req)
	if err != nil {
		return irs.NLBInfo{}, err
	}
	listener, err := client.CreateListener(lb.ID, req.Listener)
	if err != nil {
		return irs.NLBInfo{}, err
	}
	return toNLBInfo(lb, listener), nil
}
"""


def test_vm_and_image_use_non_create_method_names() -> None:
    """cb-spider는 VM 생성을 `StartVM`, 이미지를 `SnapshotVM`이라 부른다."""
    assert cbspider.HANDLERS["VMHandler"][1] == "StartVM"
    assert cbspider.HANDLERS["MyImageHandler"][1] == "SnapshotVM"


def test_real_implementation_is_recognised() -> None:
    ok, reason = cbspider.classify(REAL, "CreateVPC")
    assert ok and reason is None


def test_stub_is_not_counted_as_supported() -> None:
    ok, reason = cbspider.classify(STUB, "CreateCluster")
    assert not ok
    assert "only returns an unsupported error (a stub)" in flat(reason)


def test_partial_limitation_is_not_a_stub() -> None:
    """본문이 길면 미지원 문구가 있어도 구현으로 본다 — 과잉 판정 방지."""
    ok, _ = cbspider.classify(PARTIAL, "CreateNLB")
    assert ok


def test_missing_method_is_reported() -> None:
    ok, reason = cbspider.classify(REAL, "CreateCluster")
    assert not ok
    assert "the CreateCluster method is missing" in flat(reason)


def _tar(tmp_path, files: dict[tuple[str, str], str]):
    tar = tmp_path / "spider.tar.gz"
    with tarfile.open(tar, "w:gz") as archive:
        for (csp, handler), text in files.items():
            member = (
                f"cb-spider-0.0/cloud-control-manager/cloud-driver/drivers/"
                f"{csp}/resources/{handler}.go"
            )
            raw = text.encode()
            info = tarfile.TarInfo(member)
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    return tar


def test_matrix_separates_missing_from_stub(tmp_path) -> None:
    tar = _tar(tmp_path, {
        ("kt", "VPCHandler"): REAL,
        ("kt", "ClusterHandler"): STUB,
        # NLBHandler 파일 자체가 없다
    })
    records, stats = cbspider.parse_tarball(tar)
    rows = {r["core"]: r for r in records[0]["resources"]}
    assert rows["vNet"]["supported"] is True
    assert rows["k8sCluster"]["supported"] is False
    assert "only returns an unsupported error (a stub)" in flat(
        rows["k8sCluster"]["reason"]
    )
    assert rows["nlb"]["supported"] is False
    assert "the handler file is missing" in flat(rows["nlb"]["reason"])
    assert stats["stub"] == 2 and stats["missing"] >= 1


@pytest.fixture
def built(tmp_path):
    records, _ = cbspider.parse_tarball(
        _tar(tmp_path, {("kt", "VPCHandler"): REAL, ("kt", "ClusterHandler"): STUB})
    )
    (tmp_path / "cbspider-support.json").write_text(
        json.dumps({"csps": records, "_source": []}), encoding="utf-8"
    )
    cbspider._load.cache_clear()
    yield tmp_path
    cbspider._load.cache_clear()


def test_answer_is_tool_coverage_not_a_cloud_fact(built) -> None:
    """**가이드라인 KB의 핵심 계약.** 이건 도구의 커버리지이지 클라우드의 사실이 아니다.

    "KT Cloud에 쿠버네티스가 없다"로 읽히면 거짓을 말한 것이다. 우리는 배포기가
    아니라 가이드라인 지식베이스를 만든다.
    """
    text = flat(cbspider.describe("kt", "k8sCluster", output_dir=str(built)))
    assert "**This does not mean that CSP lacks the feature**" in text
    assert (
        "Whether that CSP offers this service is not what this data answers" in text
    )


def test_unknown_csp_is_rejected_clearly(built) -> None:
    """모르는 CSP도 '없다'가 아니라 '커버리지 밖'이다."""
    text = flat(cbspider.describe("vultr", output_dir=str(built)))
    assert "**outside this tool's coverage**" in text
    assert "that does not mean no such CSP exists" in text
