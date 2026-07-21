"""빌드 산출물 쓰기·읽기 (지식베이스 패키지 공유).

**왜 공용인가**: costkb와 perfkb가 같은 실수를 같은 모양으로 하고 있었다 —
`build_dataset()` 결과를 검증 없이 바로 `write_text`하고, 로드 측은 `exists()`만
확인한 뒤 `json.loads` + `validate`를 무방비로 불렀다. 결과:

- 상위 스키마가 드리프트하면(새 프로바이더 `oracle` 등) 빌드가 ⚠️ 한 줄만 찍고
  **exit 0**으로 끝나며 깨진 파일을 남긴다. 이후 모든 로드가 `ValidationError`를
  던져 **`output/`를 손으로 지우기 전까지 복구 불가**였다(결함 C2).
- 빌드가 쓰기 도중 끊기면 잘린 JSON이 남아, 성능 도구가 아니라 **비용 추천 전체가
  죽었다**(결함 (마)).

그래서 두 가지를 여기서 강제한다:

1. **쓰기 전에 검증하고, 원자적으로 바꿔친다.** 검증에 실패하면 파일을 아예 만들지
   않는다 — 나쁜 산출물이 존재하지 않는 것이 존재하는 것보다 낫다. 기존 산출물이
   있으면 그대로 살아남는다.
2. **읽기는 실패를 값으로 돌려준다.** 예외를 던지면 호출자가 폴백할 기회를 잃는다.
   "파일 없음"과 "파일이 깨짐"은 다른 사건이라 구분해서 알려준다.

`fetch.py`의 `.part` + `os.replace` 패턴을 그대로 따른다.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

import jsonschema

from kbcommon.invariants import Invariant, Result, run


class ArtifactInvalid(Exception):
    """산출물이 스키마를 위반해 쓰기를 거부했다."""


def write_dataset(
    path: Path,
    dataset: dict,
    schema: dict,
    invariants: Sequence[Invariant] = (),
) -> Result:
    """검증에 통과한 데이터셋만 원자적으로 쓴다.

    두 단계로 본다. **스키마**는 레코드 하나의 형태를, **불변식**은 레코드 사이의
    정합성을 본다(`kbcommon/invariants.py`). 후자가 없어서 `default=0, min=10` 같은
    모순이 그대로 산출물이 됐다.

    Returns:
        불변식 결과. `report` 등급 위반이 담겨 있으니 **호출자가 반드시 알려야 한다.**
        조용히 버리면 침묵이 "문제 없음"으로 읽힌다.

    Raises:
        ArtifactInvalid: 스키마 위반 또는 `error` 등급 불변식 위반.
            **이때 파일은 만들어지지 않는다.**
    """
    try:
        jsonschema.validate(dataset, schema)
    except jsonschema.ValidationError as exc:
        # 어디가 틀렸는지 알려주지 않으면 빌드 로그만 보고는 고칠 수 없다.
        location = "/".join(str(p) for p in exc.absolute_path) or "(최상위)"
        raise ArtifactInvalid(f"{location}: {exc.message}") from exc

    result = run(dataset, invariants)
    if not result.ok:
        raise ArtifactInvalid("레코드 간 모순으로 쓰기를 거부했습니다\n" + result.summary())

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(
        json.dumps(dataset, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)
    return result


def load_json(path: Path) -> dict:
    """`.gz`든 아니든 읽는다."""
    if path.suffix == ".gz":
        import gzip

        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(path.read_text(encoding="utf-8"))


def read_dataset(path: Path, schema: dict) -> tuple[dict | None, str | None]:
    """산출물을 읽어 검증한다. **예외를 던지지 않는다.**

    Returns:
        (데이터, 오류설명). 셋 중 하나다:
        - `(data, None)`  — 정상.
        - `(None, None)`  — 파일 없음. 아직 빌드 안 한 것이지 오류가 아니다.
        - `(None, "…")`   — 파일이 깨졌다. 호출자는 폴백하되 **이 사실을 밝혀야 한다.**
    """
    if not path.exists():
        return None, None
    try:
        # `.gz`(저장소에 커밋한 산출물)도 그대로 읽는다. 여기서 안 받으면 폴백이
        # 조용히 "빌드 안 됨"으로 떨어진다 — 실제로 그렇게 한 번 새어 나갔다.
        data = load_json(path)
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"{path}를 읽을 수 없습니다: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"{path}가 온전한 JSON이 아닙니다(쓰다 만 파일일 수 있음): {exc}"
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        location = "/".join(str(p) for p in exc.absolute_path) or "(최상위)"
        return None, f"{path}가 스키마를 위반합니다 — {location}: {exc.message}"
    return data, None


REBUILD_HINT = "다시 빌드하거나 파일을 지우세요"


# --- 저장소에 커밋된 산출물 ------------------------------------------------
#
# 클론 직후 빌드 없이 동작하게 하려고 gzip한 산출물을 `data/`에 커밋한다.
# `output/`(새로 빌드한 것)이 있으면 그쪽이 이기고, 없을 때만 `data/`에서 꺼낸다.
#
# **폴백은 기본 위치에서만 한다.** 호출자가 다른 디렉터리를 명시하면 그 말을
# 그대로 지킨다 — 테스트가 빈 tmp 디렉터리를 묶어 "미빌드 상태"나 "번들 36건
# 폴백"을 검사하는데, 거기서 커밋된 데이터가 슬쩍 끼어들면 그 테스트들이
# 검사하려던 상황 자체가 사라진다.
#
# 커밋한 파일에는 `_source` 블록(핀·sha256)이 그대로 들어 있어 어느 소스 어느
# 커밋에서 나왔는지가 파일 안에 남는다. 핀 규약이 약해지는 게 아니라 검증
# 가능해지는 쪽이다.

REPO_ROOT = Path(__file__).resolve().parent.parent
BUNDLED_DIR = REPO_ROOT / "data"
DEFAULT_OUTPUT = REPO_ROOT / "output"


def resolve(output_dir: Path | str, name: str) -> Path | None:
    """산출물 하나의 실제 경로. 없으면 None.

    `output/<name>` → `data/<name>.gz` 순으로 본다. `output_dir`가 기본 위치가
    아니면 폴백하지 않는다(위 주석 참고).
    """
    fresh = Path(output_dir) / name
    if fresh.exists():
        return fresh
    try:
        is_default = Path(output_dir).resolve() == DEFAULT_OUTPUT
    except OSError:
        is_default = False
    if not is_default:
        return None
    packed = BUNDLED_DIR / f"{name}.gz"
    return packed if packed.exists() else None


