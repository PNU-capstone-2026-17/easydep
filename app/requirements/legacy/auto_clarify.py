from app.requirements.legacy.example_sampler import (
    DEFAULT_DATASET,
    format_examples,
    sample_examples,
)

_DEFAULT_EXAMPLES = """[Reference Examples]
        - The system must provide user authentication through a valid verification process.
        - The system must provide a password reset functionality.
        - The search response time must be within 2 seconds.
        - The system must ensure 24/7 continuous availability without interruption.
        """

# few-shot 예시 추출 방법 → (strategy, backend) 매핑.
# 실험(docs/research/fewshot-sampling-experiment-results.md) 결과 채택한 두 방법:
#   - "random"  : 무작위 baseline. 쿼리 불필요, 오프라인.
#   - "mmr+nim" : NIM 임베딩으로 관련성+다양성 균형(top-k 중복 억제). 쿼리 필요.
_METHODS = {
    "random": ("random", "tfidf"),
    "mmr+nim": ("mmr", "nim"),
}


def extract_examples_from_xlsx(
    file_path: str = DEFAULT_DATASET,
    sample_size: int = 5,
    query: str = "",
    method: str = "random",
) -> str:
    """데이터셋에서 few-shot 예시를 뽑아 [Reference Examples] 블록으로 반환.

    method="random" 은 기존 baseline(무작위). method="mmr+nim" 은 query(추상 요구사항)와
    의미가 가까우면서 서로 다양한 예시를 NIM 임베딩으로 선별한다. 샘플링 로직은
    example_sampler 모듈 참고.
    """
    try:
        strategy, backend = _METHODS.get(method, _METHODS["random"])
        items = sample_examples(
            query=query,
            dataset_path=file_path,
            sample_size=sample_size,
            strategy=strategy,
            backend=backend,
        )
        if not items:
            raise ValueError("샘플링 결과가 비었습니다.")
        return format_examples(items)
    except Exception as e:
        print(f"⚠️ 데이터셋 파일을 읽는 중 오류가 발생했습니다: {e}")
        print("기본 예시를 사용합니다.\n")
        return _DEFAULT_EXAMPLES


def refine_requirements_prompt(
    abstract_text: str,
    dataset_path: str = DEFAULT_DATASET,
    method: str = "random",
):
    """
    데이터셋을 참고하여 추상적 요구사항을 분석하고 JSON 딕셔너리로 반환합니다.

    method="mmr+nim" 이면 abstract_text 를 쿼리로 써서 관련 예시를 선별한다.
    (기본값은 기존 동작 유지를 위해 random.)
    """

    # 데이터셋에서 예시 추출
    dataset_examples = extract_examples_from_xlsx(
        dataset_path, query=abstract_text, method=method
    )

    # 2. 동적 시스템 프롬프트 정의 (FR/NFR 분류 제거)
    SYSTEM_PROMPT = f"""
    You are a System Architect and an expert in Requirements Engineering.
    You are given a set of user requirement statements. You must decompose and refine them into specific, testable requirement statements.
    You must strictly mimic the tone, level of detail, and writing style of the [Reference Examples] provided below.

    [Singularity — one need per statement]
    - Each output statement MUST express exactly ONE need (IEEE 29148 singularity/atomicity).
    - Separate every non-functional / quality constraint into its OWN statement. NEVER fuse a
      performance ("within 1 second", "within 500 ms"), load ("under 200 concurrent sessions"),
      security ("encrypted at rest"), reliability/atomicity ("recorded atomically"), or availability
      constraint into a functional sentence.
    - A separated constraint MUST carry its subject so it stands alone. Example:
        Compound:  "The system shall let a registered user log in and return a response within 1 second."
        Split ->   "The system shall authenticate a registered user by email and password."
                   "The login response shall be returned within 1 second of request receipt."
    - Keep the functional actions that form a single operation together; only split OUT quality
      constraints — do not shatter one function into meaningless fragments.
    - Whenever you split a constraint out, RECORD it in constraint_links: map the constraint
      sentence to the functional sentence it qualifies (both written verbatim as they appear in
      refined_requirements). This preserves the FR<-NFR traceability link.

    [Reference Examples]
    {dataset_examples}
    """

    return SYSTEM_PROMPT
