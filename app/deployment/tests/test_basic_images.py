"""기본 OS 이미지 — 번들의 `required: image` 공백을 메운다.

**17만 건 중 6천 건만 담는다.** `is_gpu_image`는 45.6%가 켜져 있고 79,478건이 AWS
하나라 큐레이션 신호가 아니다 — 전량을 담으면 카탈로그이지 가이드라인이 아니다.
"""

from __future__ import annotations

import json

import pytest

from app.deployment.envkb.images import describe, project


def flat(text: str) -> str:
    """줄바꿈·들여쓰기를 공백 하나로 눌러 문구 대조를 줄나눔에서 독립시킨다."""
    return " ".join(text.split())


ROWS = [
    {
        "provider_name": "AWS", "region_list": '["ap-northeast-2"]',
        "csp_image_name": "ami-arm", "os_type": "Ubuntu 24.04",
        "os_architecture": "arm64", "os_distribution": "ubuntu",
        "is_basic_image": "t", "is_gpu_image": "t",
        "is_kubernetes_image": "f", "is_basic_gpu_image": "f",
    },
    {
        "provider_name": "AWS", "region_list": '["ap-northeast-2"]',
        "csp_image_name": "ami-x86", "os_type": "Ubuntu 22.04",
        "os_architecture": "x86_64", "os_distribution": "ubuntu",
        "is_basic_image": "t", "is_gpu_image": "f",
        "is_kubernetes_image": "f", "is_basic_gpu_image": "f",
    },
    # 큐레이션 플래그가 GPU뿐 — 담지 않는다
    {
        "provider_name": "AWS", "region_list": '["ap-northeast-2"]',
        "csp_image_name": "ami-gpu-only", "os_type": "AL2",
        "os_architecture": "x86_64", "os_distribution": "amazon",
        "is_basic_image": "f", "is_gpu_image": "t",
        "is_kubernetes_image": "f", "is_basic_gpu_image": "t",
    },
    # 리전이 비었다 — 어디서 쓸지 모르므로 담지 않는다
    {
        "provider_name": "AWS", "region_list": "",
        "csp_image_name": "ami-nowhere", "os_type": "X",
        "os_architecture": "x86_64", "os_distribution": "x",
        "is_basic_image": "t", "is_gpu_image": "f",
        "is_kubernetes_image": "f", "is_basic_gpu_image": "f",
    },
]


def test_only_curated_flags_are_kept() -> None:
    """**`is_gpu_image`는 45.6%가 켜져 있어 신호가 아니다.**"""
    images, stats = project(ROWS)
    ids = {i["imageId"] for i in images}
    assert ids == {"ami-arm", "ami-x86"}
    assert "ami-gpu-only" not in ids


def test_missing_region_is_dropped_and_counted() -> None:
    """어디서 쓸지 모르는 이미지는 답에 쓸 수 없다 — 버리되 센다."""
    images, stats = project(ROWS)
    assert stats["no_region"] == 1
    assert "ami-nowhere" not in {i["imageId"] for i in images}


def test_region_list_is_json() -> None:
    images, _ = project(ROWS)
    assert images[0]["regions"] == ["ap-northeast-2"]


def test_provider_is_normalized() -> None:
    """원본은 `AWS`, 우리 색인 키는 `aws`."""
    images, _ = project(ROWS)
    assert {i["provider"] for i in images} == {"aws"}


@pytest.fixture
def built(tmp_path):
    images, _ = project(ROWS)
    (tmp_path / "basic-images.json").write_text(
        json.dumps({"_note": "테스트", "images": images}, ensure_ascii=False),
        encoding="utf-8",
    )
    return tmp_path


def test_architecture_narrows(built) -> None:
    """**arm64 스펙에 x86_64 이미지를 주면 안 뜬다** — 이게 이 도구의 존재 이유다."""
    text = describe("aws", "ap-northeast-2", "arm64", output_dir=built)
    assert "ami-arm" in text and "ami-x86" not in text


def test_missing_architecture_lists_what_exists(built) -> None:
    text = flat(describe("aws", "ap-northeast-2", "riscv64", output_dir=built))
    assert "Architectures present" in text and "arm64" in text


def test_answer_says_it_is_a_default_not_the_best(built) -> None:
    text = flat(describe("aws", "ap-northeast-2", output_dir=built))
    assert "**Not the best image — the one cb-tumblebug picked as its default.**" in text


def test_unknown_provider_says_not_flagged(built) -> None:
    """'없다'가 아니라 '이 표시가 안 붙어 있다'."""
    text = flat(describe("oracle", output_dir=built))
    assert "That does not mean none exists" in text and "this flag is not set" in text


def test_unknown_region_lists_known(built) -> None:
    text = flat(describe("aws", "us-east-1", output_dir=built))
    assert "Regions included, for example" in text


def test_unbuilt_says_how_to_build(tmp_path) -> None:
    assert "build-images" in describe("aws", output_dir=tmp_path)
