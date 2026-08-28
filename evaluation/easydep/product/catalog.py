"""평가 입력 목록과 빠른 실행·안정성 실행 구성을 읽는다.

기존 요구사항 JSON은 이미 분류된 항목을 담고 있다. 평가에서는 이 구조를 내부 함수에
바로 넘기지 않고, 각 항목을 하나의 사용자 메시지로 합친 뒤 공개 Workspace API에 보낸다.
따라서 요구사항 분류와 확인 질문을 건너뛰지 않으면서 기존 입력을 다시 사용할 수 있다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Partition = Literal["development", "holdout"]
TargetStage = Literal["design", "testing"]

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_CATALOG_PATH = Path(__file__).with_name("catalog.json")

_DEVELOPMENT_IDS = (
    "dev_stateless_conversion",
    "dev_checkout_gateway",
    "dev_notification_delivery",
    "dev_iot_monitoring",
    "note_taking",
    "shopping_mall",
    "ride_hailing",
    "online_boutique",
)
_FULL_IDS = (
    "dev_stateless_conversion",
    "dev_checkout_gateway",
    "dev_iot_monitoring",
    "online_boutique",
)
_HOLDOUT_IDS = (
    "holdout_logistics",
    "holdout_partner_reporting",
    "holdout_telehealth",
)


class HoldoutAccessError(ValueError):
    """설정을 고정했다는 확인 없이 holdout을 열려고 했음을 나타낸다."""


@dataclass(frozen=True)
class DatasetCase:
    """공개 Workspace API에 그대로 보낼 요구사항 한 세트다."""

    dataset_id: str
    partition: Partition
    domain: str
    source: str
    message: str
    question_answer: str | None

    @property
    def input_digest(self) -> str:
        """실제로 전송할 UTF-8 메시지의 SHA-256을 계산한다."""
        return hashlib.sha256(self.message.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvaluationProfile:
    """어떤 입력을 몇 번, 어느 단계까지 실행할지 정한 실행 묶음이다."""

    name: str
    dataset_ids: tuple[str, ...]
    repetitions: int
    target_stage: TargetStage
    partition: Partition

    @property
    def planned_run_count(self) -> int:
        """중간 재개를 제외한 최초 실행 수를 반환한다."""
        return len(self.dataset_ids) * self.repetitions


def _raw_message(source: dict[str, object]) -> str:
    """분류된 기존 입력을 사람이 한 번에 입력할 수 있는 원문 메시지로 합친다."""
    description = str(source.get("description") or "새 애플리케이션")
    classified = source.get("classified")
    if not isinstance(classified, list) or not classified:
        raise ValueError("평가 입력에는 classified 요구사항이 한 개 이상 필요합니다.")
    # Workspace는 비어 있지 않은 각 줄을 서로 다른 RAW 요구사항으로 취급한다. 따라서
    # "다음 요구사항을 만족해 주세요" 같은 안내 문장을 끼우면 그것도 요구사항이 되어
    # 출처 추적 검사에서 매핑되지 않은 RAW 항목으로 남는다. 제품 설명과 실제 요구사항만
    # 한 줄씩 보낸다.
    lines: list[str] = [description]
    for item in classified:
        if not isinstance(item, dict) or not str(item.get("text") or "").strip():
            raise ValueError("classified의 각 항목에는 비어 있지 않은 text가 필요합니다.")
        lines.append(str(item["text"]).strip())
    constraints = str(source.get("resource_constraints_text") or "").strip()
    if constraints:
        lines.append(f"배포 조건: {constraints}")
    return "\n".join(lines)


def _load_catalog_cases(
    path: Path | None,
    *,
    selected_ids: Iterable[str] | None,
    expected_partition: Partition | None,
) -> dict[str, DatasetCase]:
    """카탈로그를 검증하고 선택한 요구사항 원문만 읽는다.

    catalog.json에는 development와 holdout의 경로가 모두 있다. 그러나
    경로를 안다고 해서 원문 파일까지 읽어야 하는 것은 아니다. selected_ids가
    주어지면 해당 ID의 원문만 열어, 개발 중 holdout 내용이 미리
    노출되지 않게 한다.
    """
    catalog_path = path or _CATALOG_PATH
    document = json.loads(catalog_path.read_text(encoding="utf-8"))
    raw_entries = document.get("datasets")
    if not isinstance(raw_entries, list):
        raise TypeError("catalog의 datasets는 목록이어야 합니다.")
    wanted = set(selected_ids) if selected_ids is not None else None
    found_ids: set[str] = set()
    cases: dict[str, DatasetCase] = {}
    for entry in raw_entries:
        if not isinstance(entry, dict):
            raise TypeError("catalog의 각 dataset은 JSON 객체여야 합니다.")
        dataset_id = str(entry.get("id") or "").strip()
        partition = str(entry.get("partition") or "")
        source_path = str(entry.get("source") or "").strip()
        if not dataset_id or partition not in {"development", "holdout"}:
            raise ValueError("dataset에는 id와 올바른 partition이 필요합니다.")
        if dataset_id in found_ids:
            raise ValueError(f"중복된 dataset ID입니다: {dataset_id}")
        found_ids.add(dataset_id)
        if wanted is not None and dataset_id not in wanted:
            # 선택하지 않은 항목은 메타데이터만 검증한다. 아래의 원문
            # read_text를 실행하지 않는 것이 holdout 보호의 핵심이다.
            continue
        if expected_partition is not None and partition != expected_partition:
            raise ValueError(
                f"profile과 dataset의 development/holdout 구분이 다릅니다: "
                f"{dataset_id}"
            )
        source = json.loads((_REPOSITORY_ROOT / source_path).read_text(encoding="utf-8"))
        cases[dataset_id] = DatasetCase(
            dataset_id=dataset_id,
            partition=partition,  # type: ignore[arg-type]
            domain=str(entry.get("domain") or ""),
            source=source_path,
            message=_raw_message(source),
            question_answer=(
                str(entry["questionAnswer"]).strip()
                if entry.get("questionAnswer")
                else None
            ),
        )
    if wanted is not None:
        missing = sorted(wanted - found_ids)
        if missing:
            raise ValueError(f"catalog에 요청한 dataset이 없습니다: {', '.join(missing)}")
    return cases


def load_catalog(path: Path | None = None) -> dict[str, DatasetCase]:
    """catalog와 연결된 모든 입력 파일을 읽어 ID별 평가 사례를 만든다.

    기존 Python 사용처와의 호환을 위해 인자 없이 호출하면 전체 카탈로그를
    반환한다. 실제 profile 실행에서는 holdout 원문을 불필요하게 읽지 않도록
    :func:`load_profile_catalog`를 사용해야 한다.
    """
    cases = _load_catalog_cases(
        path,
        selected_ids=None,
        expected_partition=None,
    )
    if len(cases) < 8:
        raise ValueError("제품 평가는 서로 다른 요구사항을 최소 8개 포함해야 합니다.")
    return cases


def load_profile_catalog(
    profile: EvaluationProfile,
    path: Path | None = None,
    *,
    allow_holdout_after_settings_lock: bool = False,
) -> dict[str, DatasetCase]:
    """profile 검사가 끝난 뒤 그 profile에 필요한 원문만 읽는다.

    holdout은 함수 인자로 확인을 다시 받는다. EvaluationProfile을 직접
    만들어 보호 절차를 우회하는 실수까지 막기 위해서다.
    """
    if profile.partition == "holdout" and not allow_holdout_after_settings_lock:
        raise HoldoutAccessError(
            "holdout은 설정과 알고리즘을 확정한 뒤 명시적으로 잠금을 확인해야 열 수 있습니다."
        )
    return _load_catalog_cases(
        path,
        selected_ids=profile.dataset_ids,
        expected_partition=profile.partition,
    )


def load_profile(
    name: str,
    *,
    allow_holdout_after_settings_lock: bool = False,
) -> EvaluationProfile:
    """이름으로 실행 구성을 고르고 holdout의 우발적 실행을 막는다."""
    profiles = {
        "quick": EvaluationProfile(
            "quick", _DEVELOPMENT_IDS, 1, "design", "development"
        ),
        "stability": EvaluationProfile(
            "stability", _DEVELOPMENT_IDS, 3, "design", "development"
        ),
        "full": EvaluationProfile(
            "full", _FULL_IDS, 1, "testing", "development"
        ),
        "holdout": EvaluationProfile(
            "holdout", _HOLDOUT_IDS, 1, "testing", "holdout"
        ),
    }
    try:
        profile = profiles[name]
    except KeyError as error:
        raise ValueError(f"알 수 없는 평가 profile입니다: {name}") from error
    if profile.partition == "holdout" and not allow_holdout_after_settings_lock:
        raise HoldoutAccessError(
            "holdout은 설정과 알고리즘을 확정한 뒤 명시적으로 잠금을 확인해야 실행됩니다."
        )
    return profile
