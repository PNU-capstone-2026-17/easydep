"""terraform-provider-aws → AWS 제약 (CFN이 표현 못 하는 것)."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

from capacitykb.parsers.tpaws import (
    parse_provider, read_service_namespaces, resolve_type, tf_path_to_cfn,
)

HCL = '''
service "amp" {
  sdk { id = "amp"
    arn_namespace = "aps" }
}
service "ec2" {
  sdk { id = "EC2"
    arn_namespace = "ec2" }
}
'''

GO = '''
// @SDKResource("aws_prometheus_scraper", name="Scraper")
func resourceScraper() *schema.Resource {
	return &schema.Resource{
		CustomizeDiff: customdiff.ForceNewIfChange("alias", isShrink),
		Schema: map[string]*schema.Schema{
			"alias": {
				Type:     schema.TypeString,
				Optional: true,
			},
			"scrape_configuration": {
				Type:          schema.TypeString,
				Required:      true,
				ForceNew:      true,
				ConflictsWith: []string{"source", "destination"},
				ValidateFunc:  validation.IntBetween(1, 100),
			},
			"created_at": {
				Type:     schema.TypeString,
				Computed: true,
			},
		},
	}
}

// @SDKResource("aws_instance", name="Instance")
func resourceInstance() *schema.Resource {
	return &schema.Resource{
		Schema: map[string]*schema.Schema{
			"instance_type": {
				Type:     schema.TypeString,
				Required: true,
			},
		},
	}
}
'''


def _tar(tmp_path: Path) -> Path:
    path = tmp_path / "p.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, body in (
            ("p/names/data/names_data.hcl", HCL),
            ("p/internal/service/amp/scraper.go", GO),
        ):
            data = body.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


CFN = {"aws::AWS::APS::Scraper", "aws::AWS::EC2::Instance"}


def test_service_is_joined_by_the_providers_own_table() -> None:
    """TF 디렉터리(amp)·접두사(prometheus)·CFN 서비스(APS)가 셋 다 다르다.

    손으로 표를 만들지 않고 프로바이더가 가진 names_data.hcl의 arn_namespace를 쓴다.
    """
    ns = read_service_namespaces(HCL)
    assert ns["amp"] == "aps"
    from capacitykb.parsers.tpaws import index_cfn
    got = resolve_type("amp", "aws_prometheus_scraper",
                       namespaces=ns, cfn=index_cfn(CFN))
    assert got == "aws::AWS::APS::Scraper"


def test_path_becomes_pascal_case() -> None:
    assert tf_path_to_cfn("root_block_device.0.volume_size") == "RootBlockDevice.VolumeSize"


def test_harvests_what_cloudformation_cannot_express(tmp_path: Path) -> None:
    """교차 필드 조건과 조건부 불변 — CFN에는 표현할 방법이 없는 것들."""
    got, _ = parse_provider(_tar(tmp_path), cfn_types=CFN)
    kinds = {(c.property, c.kind) for c in got.constraints}
    assert ("ScrapeConfiguration", "conflicts_with") in kinds
    assert ("Alias", "mutability") in kinds  # ForceNewIfChange
    restricted = [c for c in got.constraints if c.value == "update_restricted"]
    assert restricted and "isShrink" in restricted[0].note


def test_each_resource_gets_only_its_own_schema(tmp_path: Path) -> None:
    """한 파일에 리소스가 여럿이면 서로 오염되면 안 된다.

    처음 구현은 파일 단위로 첫 스키마를 찾아서, 두 번째 리소스가 첫 번째의 제약을
    통째로 물려받았다. 수확이 1/6로 줄고 값이 엉뚱한 타입에 붙었다.
    """
    got, _ = parse_provider(_tar(tmp_path), cfn_types=CFN)
    aps = {c.property for c in got.constraints if "APS" in c.type_id}
    ec2 = {c.property for c in got.constraints if "EC2" in c.type_id}
    assert "ScrapeConfiguration" in aps
    assert "ScrapeConfiguration" not in ec2, "다른 리소스의 스키마가 새어 들어왔다"


def test_output_only_fields_are_skipped(tmp_path: Path) -> None:
    got, report = parse_provider(_tar(tmp_path), cfn_types=CFN)
    assert "CreatedAt" not in {c.property for c in got.constraints}
    assert report.output_only >= 1


def test_unmapped_resources_are_counted_not_silently_dropped(tmp_path: Path) -> None:
    """CFN에 없는 리소스는 담지 않되 **센다.**"""
    got, report = parse_provider(_tar(tmp_path), cfn_types={"aws::AWS::EC2::Instance"})
    assert ("amp", "aws_prometheus_scraper") in report.unmapped
