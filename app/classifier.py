"""BERT 기반 FR/NFR 검증 분류기.

materials/BERT_FR_NFR_Classifier 노트북에서 파인튜닝한 BertForSequenceClassification
체크포인트를 로드해, LLM 분류 결과를 대조·검증하는 데 사용한다.

라벨 매핑(학습 시 고정): 0 = NFR, 1 = FR
전처리(노트북 추론 셀과 동일): max_length=128, padding/truncation, softmax confidence.

무거운 의존성(torch/transformers)과 417MB 가중치를 쓰므로:
  - 모델은 최초 호출 시 1회 지연 로딩(lazy) 후 프로세스 전역 재사용.
  - settings.enable_bert_verify=False 이면 로드 자체를 건너뛴다.
"""
from __future__ import annotations

import threading

from app.config import settings

# 0 = NFR, 1 = FR  (학습 시 {'NFR': 0, 'FR': 1} 매핑과 동일)
_ID2LABEL = {0: "NFR", 1: "FR"}
_MAX_LENGTH = 128

# 지연 로딩된 (tokenizer, model, torch) 캐시. 로드 실패 시 False로 표시해 재시도 방지.
_bundle = None
_load_lock = threading.Lock()


def _load_bundle():
    """(tokenizer, model, torch) 튜플을 1회 로드해 반환. 실패하면 None."""
    global _bundle
    if _bundle is not None:
        return _bundle or None  # False -> None

    with _load_lock:
        if _bundle is not None:
            return _bundle or None
        try:
            import torch
            from transformers import BertForSequenceClassification, BertTokenizer

            tokenizer = BertTokenizer.from_pretrained(settings.bert_model_path)
            model = BertForSequenceClassification.from_pretrained(
                settings.bert_model_path
            )
            model.eval()
            _bundle = (tokenizer, model, torch)
        except Exception as exc:  # noqa: BLE001 - 검증은 옵션 기능이라 실패해도 앱은 계속
            print(f"[classifier] BERT 로드 실패 — 검증 노드 비활성화됨: {exc}")
            _bundle = False
            return None
    return _bundle


def bert_available() -> bool:
    """BERT 검증을 실제로 수행할 수 있는지."""
    if not settings.enable_bert_verify:
        return False
    return _load_bundle() is not None


def warmup() -> bool:
    """서버 기동 시 1회 호출해 BERT 가중치를 미리 로드(+워밍업 추론 1회)한다.

    이후 모든 요청은 프로세스 전역 캐시(_bundle)를 재사용하므로 재로딩이 없다.
    (멀티 워커로 띄우면 워커 프로세스마다 1회씩 로드된다.)
    enable_bert_verify=False면 로드하지 않고 False를 반환한다.
    """
    if not bert_available():
        return False
    # 첫 추론에서 지연 초기화되는 연산 그래프/커널까지 데워 첫 요청 지연을 없앤다.
    classify_bert("The system shall respond within 2 seconds.")
    return True


def classify_bert(text: str) -> tuple[str, float] | None:
    """단일 영어 요구사항 문장을 BERT로 분류.

    Returns (label, confidence) 예: ("FR", 0.98). 사용 불가 시 None.
    """
    if not settings.enable_bert_verify:
        return None
    bundle = _load_bundle()
    if bundle is None:
        return None

    tokenizer, model, torch = bundle
    inputs = tokenizer(
        text,
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=_MAX_LENGTH,
    )
    with torch.no_grad():
        logits = model(**inputs).logits
        probs = torch.nn.functional.softmax(logits, dim=-1)
        confidence, pred = torch.max(probs, dim=-1)

    return _ID2LABEL[int(pred.item())], float(confidence.item())
