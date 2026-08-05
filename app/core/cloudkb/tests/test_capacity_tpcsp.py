"""alicloud·tencent Terraform provider (tpcsp.py).

여기서 지켜야 하는 것:
- **두 저장소의 등록 표기가 다르다.** alicloud는 함수를 그대로, tencent는 패키지를
  앞에 붙인다. 점을 허용 안 하면 tencent가 **0건**이 된다(실측으로 겪었다).
- **데이터소스는 리소스가 아니다.** 만들 수 있는 것이 아니라 조회다.
- **응답 전용 칸은 제약이 아니다.**
"""

from __future__ import annotations

from app.core.cloudkb.capacitykb.model import CapacitySet
from app.core.cloudkb.capacitykb.parsers.tpcsp import (
    PROVIDERS,
    Report,
    _emit,
    _is_output_only,
    _registration,
)


def test_alicloud_registration_form() -> None:
    text = '"alicloud_vpc": resourceAliCloudVpc(),'
    assert _registration("alicloud_").findall(text) == [
        ("alicloud_vpc", "resourceAliCloudVpc")
    ]


def test_tencent_registration_needs_package_prefix() -> None:
    """`vpc.ResourceTencentCloudVpc()` — 점을 허용 안 하면 0건이 된다."""
    text = '"tencentcloud_vpc": vpc.ResourceTencentCloudVpc(),'
    found = _registration("tencentcloud_").findall(text)
    assert found == [("tencentcloud_vpc", "vpc.ResourceTencentCloudVpc")]
    # 함수 정의는 패키지 없이 쓰이므로 마지막 마디를 쓴다
    assert found[0][1].rsplit(".", 1)[-1] == "ResourceTencentCloudVpc"


def test_output_only_property_is_not_a_constraint() -> None:
    """`Computed`만 있으면 응답 전용이라 사용자가 넣는 칸이 아니다."""
    assert _is_output_only("Type: schema.TypeString,\nComputed: true,")
    assert not _is_output_only("Computed: true,\nOptional: true,")
    assert not _is_output_only("Required: true,")


def test_emit_reads_the_signals() -> None:
    props = {
        "instance_type": "Type: schema.TypeString,\nRequired: true,\nForceNew: true,",
        "size": "Optional: true,\nValidateFunc: validation.IntBetween(20, 500),",
        "mode": 'Optional: true,\nValidateFunc: validation.StringInSlice([]string{"PostPaid", "PrePaid"}, false),',
        "disks": "Optional: true,\nMaxItems: 16,",
        "status": "Computed: true,",          # 응답 전용 → 빠져야 한다
        "id": "Computed: true,",              # Terraform 칸 → 빠져야 한다
    }
    capacity, report = CapacitySet(), Report()
    assert _emit(props, "alibaba::alicloud_instance", capacity, report)
    by_prop: dict[str, set[str]] = {}
    for c in capacity.constraints:
        by_prop.setdefault(c.property, set()).add(c.kind)

    assert by_prop["instance_type"] == {"required", "mutability"}
    assert by_prop["size"] == {"min", "max"}
    assert by_prop["mode"] == {"enum"}
    assert by_prop["disks"] == {"max_items"}
    assert "status" not in by_prop and "id" not in by_prop
    assert report.output_only == 1 and report.tf_only == 1


def test_evidence_marks_the_single_source() -> None:
    """대조할 짝이 없는 단일 소스임이 근거 라벨로 구분돼야 한다."""
    props = {"x": "Required: true,"}
    capacity = CapacitySet()
    _emit(props, "tencent::tencentcloud_vpc", capacity, Report())
    assert {c.evidence for c in capacity.constraints} == {"tpcsp-schema"}


def test_type_id_keeps_the_terraform_name() -> None:
    """id에 `alicloud_`가 그대로 남는 것이 의도다 — Terraform 이름임을 드러낸다."""
    assert PROVIDERS["alicloud"]["provider"] == "alibaba"
    assert PROVIDERS["alicloud"]["prefix"] == "alicloud_"
    assert PROVIDERS["tencent"]["provider"] == "tencent"
