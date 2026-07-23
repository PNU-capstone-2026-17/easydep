"""BERT 가중치를 GitHub 파일 크기 한도 아래로 쪼개 저장소에 넣는 개발용 도구.

원본 `model.safetensors`(417MiB)는 GitHub 파일당 100MiB 한도를 넘어 커밋할 수 없다.
이 스크립트는 원본을 `bert_model/weights/` 아래 두 단계로 쪼갠다.

  1) **텐서 단위 샤딩** — HuggingFace 네이티브 포맷(`model-0000N-of-0000M.safetensors`
     + `model.safetensors.index.json`)으로 나눈다. transformers가 그대로 읽으므로
     로딩 시 합칠 필요가 없고, 샤드별로 mmap 되어 메모리도 덜 쓴다.
  2) **바이트 단위 분할** — 텐서 하나가 한도보다 큰 경우(BERT-base의
     `bert.embeddings.word_embeddings.weight` = 89.4MiB)는 샤딩으로 더 못 줄인다.
     이런 샤드만 `.partNNN` 조각으로 잘라 두고, 로딩 시점에 다시 붙인다.

즉 전체를 붙였다 떼는 게 아니라 **한도를 넘는 샤드만** 재조립하면 되므로,
기동 시 쓰기량이 417MiB → 89MiB로 줄고 나머지 78%는 그대로 mmap 된다.

재조립·검증은 `app/requirements/model_assets.py`가 담당한다(런타임/이미지 빌드 공용).

safetensors 바이너리를 직접 다뤄 텐서 바이트를 **무손실 복사**한다. torch로 로드했다
다시 저장하지 않으므로 값이 바뀔 여지가 없다.

사용:
    python scripts/shard_bert_model.py <원본_model.safetensors 경로>
    python scripts/shard_bert_model.py --verify        # 재조립 결과가 원본과 같은지 확인
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = REPO_ROOT / "materials" / "BERT_FR_NFR_Classifier" / "bert_model"
WEIGHTS_DIR = MODEL_DIR / "weights"

# GitHub은 파일당 100MiB에서 push를 거부하고 50MiB에서 경고한다.
# 45MiB로 잡으면 경고 없이 들어가고, 조각 수도 10개 남짓으로 유지된다.
LIMIT_BYTES = 45 * 1024 * 1024

# 모델 디렉터리에서 가중치와 함께 런타임으로 복사되는 파일들.
# training_args.bin은 학습 시 하이퍼파라미터 기록일 뿐 추론에 쓰이지 않아 제외한다.
COMPANION_FILES = ("config.json", "tokenizer.json", "tokenizer_config.json")

_HEADER_ALIGN = 8


# --------------------------------------------------------------------------
# safetensors 바이너리 읽기/쓰기
# --------------------------------------------------------------------------
def read_header(path: Path) -> tuple[dict, int]:
    """safetensors 헤더(JSON)와 데이터 버퍼 시작 오프셋을 돌려준다."""
    with path.open("rb") as fp:
        (header_len,) = struct.unpack("<Q", fp.read(8))
        header = json.loads(fp.read(header_len))
    return header, 8 + header_len


def _pack_header(header: dict) -> bytes:
    """헤더를 직렬화하고 데이터 버퍼가 8바이트 경계에서 시작하도록 공백 패딩한다."""
    blob = json.dumps(header, separators=(",", ":")).encode("utf-8")
    padding = -(8 + len(blob)) % _HEADER_ALIGN
    blob += b" " * padding
    return struct.pack("<Q", len(blob)) + blob


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        while block := fp.read(chunk):
            digest.update(block)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# 1단계: 텐서 단위 샤딩
# --------------------------------------------------------------------------
def plan_shards(header: dict, limit: int) -> list[list[str]]:
    """텐서를 원본 저장 순서대로 훑으며 limit을 넘지 않게 샤드로 묶는다.

    limit보다 큰 텐서 하나는 쪼갤 수 없으므로 자기 혼자 한 샤드를 차지한다
    (그 샤드는 2단계에서 바이트 분할된다).
    """
    tensors = sorted(
        ((name, meta) for name, meta in header.items() if name != "__metadata__"),
        key=lambda item: item[1]["data_offsets"][0],
    )

    shards: list[list[str]] = []
    current: list[str] = []
    current_size = 0
    for name, meta in tensors:
        start, end = meta["data_offsets"]
        size = end - start
        if current and current_size + size > limit:
            shards.append(current)
            current, current_size = [], 0
        current.append(name)
        current_size += size
    if current:
        shards.append(current)
    return shards


def write_shard(
    source: Path, data_start: int, header: dict, names: list[str], dest: Path
) -> None:
    """원본에서 지정 텐서들의 바이트를 그대로 복사해 새 safetensors를 만든다."""
    new_header: dict = {"__metadata__": header.get("__metadata__", {"format": "pt"})}
    offset = 0
    for name in names:
        meta = header[name]
        start, end = meta["data_offsets"]
        size = end - start
        new_header[name] = {
            "dtype": meta["dtype"],
            "shape": meta["shape"],
            "data_offsets": [offset, offset + size],
        }
        offset += size

    with source.open("rb") as src, dest.open("wb") as out:
        out.write(_pack_header(new_header))
        for name in names:
            start, end = header[name]["data_offsets"]
            src.seek(data_start + start)
            remaining = end - start
            while remaining:
                block = src.read(min(remaining, 1 << 22))
                if not block:
                    raise RuntimeError(f"{source} 가 예상보다 짧다 ({name})")
                out.write(block)
                remaining -= len(block)


# --------------------------------------------------------------------------
# 2단계: 한도를 넘는 샤드만 바이트 분할
# --------------------------------------------------------------------------
def split_file(path: Path, limit: int) -> list[dict]:
    """path를 limit 크기 조각으로 잘라 `.partNNN` 파일들을 만들고 원본을 지운다."""
    parts: list[dict] = []
    with path.open("rb") as src:
        index = 0
        while True:
            block = src.read(limit)
            if not block:
                break
            part = path.with_name(f"{path.name}.part{index:03d}")
            part.write_bytes(block)
            parts.append(
                {
                    "name": part.name,
                    "size": len(block),
                    "sha256": sha256_of(part),
                }
            )
            index += 1
    path.unlink()
    return parts


# --------------------------------------------------------------------------
# 실행
# --------------------------------------------------------------------------
def build(source: Path, limit: int) -> dict:
    header, data_start = read_header(source)
    shards = plan_shards(header, limit)
    total = len(shards)
    print(f"원본 {source} ({source.stat().st_size / 1048576:.1f} MiB)")
    print(f"텐서 {len(header) - ('__metadata__' in header)}개 → 샤드 {total}개")

    if WEIGHTS_DIR.exists():
        shutil.rmtree(WEIGHTS_DIR)
    WEIGHTS_DIR.mkdir(parents=True)

    weight_map: dict[str, str] = {}
    files: list[dict] = []
    total_size = 0

    for position, names in enumerate(shards, start=1):
        shard_name = f"model-{position:05d}-of-{total:05d}.safetensors"
        shard_path = WEIGHTS_DIR / shard_name
        write_shard(source, data_start, header, names, shard_path)
        for name in names:
            weight_map[name] = shard_name
            start, end = header[name]["data_offsets"]
            total_size += end - start

        size = shard_path.stat().st_size
        entry: dict = {
            "name": shard_name,
            "size": size,
            "sha256": sha256_of(shard_path),
        }
        if size > limit:
            entry["parts"] = split_file(shard_path, limit)
            print(
                f"  {shard_name}  {size / 1048576:6.1f} MiB  "
                f"→ 한도 초과, {len(entry['parts'])}조각으로 분할"
            )
        else:
            print(f"  {shard_name}  {size / 1048576:6.1f} MiB")
        files.append(entry)

    index_name = "model.safetensors.index.json"
    index_path = WEIGHTS_DIR / index_name
    index_path.write_text(
        json.dumps(
            {"metadata": {"total_size": total_size}, "weight_map": weight_map},
            indent=2,
        ),
        encoding="utf-8",
    )
    files.insert(
        0,
        {
            "name": index_name,
            "size": index_path.stat().st_size,
            "sha256": sha256_of(index_path),
        },
    )

    manifest = {
        "version": 1,
        "source": source.name,
        "source_sha256": sha256_of(source),
        "source_size": source.stat().st_size,
        "limit_bytes": limit,
        "companions": list(COMPANION_FILES),
        "files": files,
    }
    (WEIGHTS_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest


def verify(source: Path | None) -> int:
    """재조립된 모델이 실제로 로드되고, 있으면 원본과 같은 출력을 내는지 확인한다."""
    sys.path.insert(0, str(REPO_ROOT))
    from app.requirements.model_assets import ensure_model_dir

    resolved = ensure_model_dir(MODEL_DIR, force=True)
    print(f"재조립 위치: {resolved}")

    import torch
    from transformers import BertForSequenceClassification, BertTokenizer

    samples = [
        "The system shall allow a user to reset their password by email.",
        "The system shall respond to any request within 2 seconds.",
    ]
    tokenizer = BertTokenizer.from_pretrained(resolved)
    model = BertForSequenceClassification.from_pretrained(resolved).eval()
    inputs = tokenizer(
        samples, return_tensors="pt", padding=True, truncation=True, max_length=128
    )
    with torch.no_grad():
        rebuilt = model(**inputs).logits

    if source is None or not source.exists():
        print("원본이 없어 로드 가능 여부만 확인했다. logits:")
        print(rebuilt)
        return 0

    # 원본을 임시 디렉터리에 모아 같은 방식으로 로드해 비교한다.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for name in COMPANION_FILES:
            shutil.copy2(MODEL_DIR / name, tmp_dir / name)
        shutil.copy2(source, tmp_dir / "model.safetensors")
        original = BertForSequenceClassification.from_pretrained(tmp_dir).eval()
        with torch.no_grad():
            expected = original(**inputs).logits

    same_weights = all(
        torch.equal(a, b)
        for (_, a), (_, b) in zip(
            sorted(model.state_dict().items()), sorted(original.state_dict().items())
        )
    )
    max_diff = (rebuilt - expected).abs().max().item()
    print(f"가중치 비트 단위 일치: {same_weights}")
    print(f"logits 최대 차이: {max_diff}")
    return 0 if same_weights and max_diff == 0.0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        help="원본 model.safetensors 경로 (생략 시 모델 디렉터리에서 찾는다)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=LIMIT_BYTES,
        help=f"조각 하나의 최대 바이트 (기본 {LIMIT_BYTES})",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="쪼개지 않고, 이미 쪼개 둔 조각을 재조립해 원본과 대조만 한다",
    )
    args = parser.parse_args()

    source = args.source or (MODEL_DIR / "model.safetensors")
    if args.verify:
        return verify(source)

    if not source.exists():
        parser.error(f"원본을 찾을 수 없다: {source}")

    manifest = build(source, args.limit)
    committed = sum(
        part["size"]
        for entry in manifest["files"]
        for part in entry.get("parts", [entry])
    )
    largest = max(
        part["size"]
        for entry in manifest["files"]
        for part in entry.get("parts", [entry])
    )
    print(
        f"\n완료: {WEIGHTS_DIR.relative_to(REPO_ROOT)} 에 "
        f"{committed / 1048576:.1f} MiB, 최대 파일 {largest / 1048576:.1f} MiB"
    )
    print("검증: python scripts/shard_bert_model.py --verify")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
