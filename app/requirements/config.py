"""애플리케이션 설정. .env 파일 또는 환경변수에서 로드한다.

Nvidia NIM 무료 엔드포인트(OpenAI 호환)를 사용하므로 OpenAI 클라이언트 규격의
API_KEY / BASE_URL 을 그대로 사용한다.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Nvidia NIM (OpenAI 호환) 자격증명
    api_key: str
    base_url: str = "https://integrate.api.nvidia.com/v1"
    # NIM에서 사용할 모델 ID. 요구사항 분석 에이전트 기본값은 gpt-oss-120b.
    model: str = "openai/gpt-oss-120b"
    # 분류·구조화 작업이라 표본을 넓힐 이유가 없다. 0으로 둔다(2026-07-26까지 0.2였다).
    temperature: float = 0.0
    # 같은 입력에 같은 표본을 **요청**한다. None이면 파라미터를 보내지 않는다.
    #
    # ⚠ 이 둘로 출력이 고정되지는 않는다. 이유는 서버가 seed를 무시할 수 있다는 정도가
    # 아니라 구조적이다(MoE 라우팅이 배치 구성에 좌우된다) — `agent/llm.py`의
    # "재현성 — 여기서 얻을 수 없는 것"에 한 곳에만 적어 둔다.
    seed: int | None = 42
    # few-shot 예시 코사인 샘플링용 NIM 임베딩 모델(OpenAI 호환 embeddings 엔드포인트).
    # base_url/api_key 는 위 자격증명을 재사용한다. example_sampler 의 backend="nim" 참고.
    embed_model: str = "nvidia/llama-nemotron-embed-1b-v2"
    # step1 구체화 프롬프트의 few-shot 예시 추출 방식.
    #  - "random"  : 무작위(baseline, 오프라인)
    #  - "mmr+nim" : NIM 임베딩으로 요구사항과 관련되면서 다양한 예시 선별(네트워크 필요)
    example_sampling_method: str = "random"

    # --- 요구사항 분석 에이전트 설정 ---
    # materials의 파인튜닝 BERT(FR/NFR) 모델 디렉토리. (0=NFR, 1=FR)
    bert_model_path: str = "materials/BERT_FR_NFR_Classifier/bert_model"
    # BERT 검증 노드 사용 여부. False면 torch 로드를 건너뛰고 LLM 분류만 사용
    # (경량 배포/AKS 메모리 제약 시 유용).
    enable_bert_verify: bool = True
    # (2~4단계는 항상 실행한다. 예전 enable_pipeline_stubs 게이트는 제거됨.)
    # step3 명세 생성·semantic 커버리지 채점의 동시 LLM 호출 상한(UC별 병렬).
    # hosted NIM(integrate.api)에서 8 동시까지 429 없이 견딤을 실측(콜당 ~3s, 웨이브 수 = ceil(UC/이 값)).
    spec_concurrency: int = 8
    # step3 명세 반성(reflection) 루프: 검증 실패 시 수술적 지시로 재생성하는 최대 횟수.
    max_repair_iters: int = 2
    # step2 커버리지 강제-수리 루프: 고아 FR을 재프롬프트로 보충하는 최대 횟수.
    max_coverage_iters: int = 2
    # 파이프라인 되돌아가기(supervisor) 최대 횟수. 결함을 낸 단계로 되돌리는 일은
    # 되돌리기를 또 부를 수 있어서, 상한이 없으면 끝나지 않는다.
    # 1회로 시작한다 — 되돌릴 때마다 그 아래 단계 전부가 다시 도므로 비용이 크다.
    max_redo_rounds: int = 1
    # 대화형 모드 스위치. True면 모든 interrupt 기반 상호작용을 켠다:
    #  - step1 clarify(요구사항이 추상적일 때 질문) 루프
    #  - 각 스텝(1~4) 말미의 피드백 게이트(피드백 주면 재생성·루프, 빈 값이면 다음 단계)
    # False면 비대화형(배치/자율) — 질문도 게이트도 없이 파이프라인을 끝까지 진행한다.
    enable_feedback_gates: bool = False
    # 정적 체크에 더해 LLM 의미 검증(hidden branching·scope creep 등)을 병합할지.
    enable_semantic_validator: bool = True
    # 서빙 경로에서 대화형 세션(체크포인트+게이트 모드)을 MySQL에 저장할지.
    # True면 서버가 재시작해도 진행 중인 분석이 이어지고, 파드를 여럿 띄울 수 있다.
    # server.py가 기동 시 init_db()를 부르므로 서빙 경로는 어차피 MySQL을 요구한다.
    # CLI·배치 러너는 이 값과 무관하게 항상 메모리를 쓴다(프로세스와 수명이 같다).
    enable_session_persistence: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


# api_key 등 필수 필드는 .env/환경변수에서 런타임에 주입된다. 타입 체커는 이를 모르고
# "인자 누락"으로 오판하므로 무시한다. (값이 없으면 여기서 ValidationError로 즉시 실패)
settings = Settings()  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]
