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
    # 파이프라인 되돌아가기(supervisor) 최대 횟수. 0이면 끈다.
    #
    # **기본값이 0인 이유는 측정 결과다**(2026-07-26, toystore, 조건별 2회):
    # 결함 합이 기저 7~8 vs 처리 6~9 — 처리 범위가 기저 범위를 감쌌고, 결정론 재검증도
    # 기저가 0·3으로 갈려 조건 간 차이를 읽을 수 없었다. 반면 비용은 LLM 호출 1.9배로
    # 확실했다. 개선이 안 보이는데 비용이 두 배인 것을 기본으로 둘 수는 없다.
    #
    # 기계장치는 그대로 두고 끈다 — 왜 안 먹었는지에 대한 가설(에스컬레이션이 문장 수준
    # 지시를 상위 단계로 올려 실행 불가능한 지시가 된다)은 아직 시험하지 않았다.
    # `docs/requirements-agent-improvements.md` §6 참고. 켜려면 `MAX_REDO_ROUNDS=1`.
    max_redo_rounds: int = 0
    # 대화형 모드 스위치. True면 모든 interrupt 기반 상호작용을 켠다:
    #  - step1 clarify(요구사항이 추상적일 때 질문) 루프
    #  - 각 스텝(1~4) 말미의 피드백 게이트(피드백 주면 재생성·루프, 빈 값이면 다음 단계)
    # False면 비대화형(배치/자율) — 질문도 게이트도 없이 파이프라인을 끝까지 진행한다.
    enable_feedback_gates: bool = False
    # 정적 체크에 더해 LLM 의미 검증(hidden branching·scope creep 등)을 병합할지.
    enable_semantic_validator: bool = True
    # 의미 판정을 몇 번 물어 **과반으로 확정할지**. 1이면 한 번 묻는다(다수결 없음).
    #
    # **왜 필요한가는 측정 결과다**(2026-07-26, toystore 명세 11개 × 5회): 같은 명세를 같은
    # 검증자에게 5번 물었을 때 (명세×규칙) 판정 24건 중 **안정된 것이 4건**이었다
    # (흔들림 83%). 두 규칙만의 문제가 아니라 이 층 전체가 그랬다.
    #
    # 판정이 동전 던지기면 그 위에 쌓은 모든 수가 무의미하다 — 반성 루프는 깜빡이는 결함을
    # 쫓고(`no_improvement`가 11개 중 5~7개였다), 실행 비교는 같은 동전을 다시 던지는 일이
    # 된다. 비용은 검증 호출 × 이 값이다.
    validator_votes: int = 1
    # 의미 판정을 **규칙마다 따로** 물을지. False면 한 번에 다 묻는다.
    #
    # 다수결(위)이 흔들림을 90%→75%로만 줄인 뒤 남은 갈래다. 다수결은 한 번 판정의 확률이
    # 0.5에서 떨어져 있을 때만 날카로워지는데, 규칙 6개를 한 응답에 판정하게 하는 지금
    # 구조에서는 그 확률 자체가 0.5 근처다. 과제를 쪼개면 달라지는지 보려는 것이다.
    # 비용은 검증 호출 × 규칙 수(대신 응답이 짧아 호출당 시간은 줄어든다).
    validator_per_rule: bool = False
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
