
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    # Core LLM settings
    api_key: str | None = None
    nvidia_api_key: str | None = None
    nvidia_nim_api_key: str | None = None
    llm_api_key: str | None = None

    base_url: str | None = None
    model: str = "openai/gpt-oss-120b"
    temperature: float = 0.2
    seed: int = 42

    # Specific Agent Models
    openhands_model: str | None = None
    llm_model: str | None = None

    # LLM Options
    # Structured design models can exceed provider defaults once reasoning
    # tokens are included. Keep a bounded, explicit allowance instead of
    # inheriting an undocumented OpenAI-compatible gateway default.
    llm_max_completion_tokens: int | None = 16384
    design_reasoning_effort: str = "medium"
    design_selector_reasoning_effort: str = "low"
    # Class authoring stages keep the former medium policy by default. Separate
    # settings let the frozen E1 experiment lower one stage without changing the
    # inventory, selectors, or repair scope.
    design_class_inventory_reasoning_effort: str = "medium"
    design_class_operation_reasoning_effort: str = "medium"
    design_class_call_plan_reasoning_effort: str = "medium"
    design_class_compact_operation_payload: bool = False
    llm_timeout_seconds: float = 300.0
    llm_wall_timeout_seconds: float = 330.0
    llm_max_retries: int = 0
    # 개발 중에는 실제 응답을 봐야 schema 오류와 불필요하게 긴 출력을 구분할 수 있다.
    # timing event와 Workspace event에 JSON 응답·reasoning·검증 오류를 함께 남긴다.
    # 운영 환경에서 저장량을 줄이고 싶으면 false로 끌 수 있다.
    llm_capture_response_content: bool = True
    llm_failure_response_sample_chars: int = 0

    # OpenHands / Implementation Provider Settings
    openhands_max_output_tokens: int | None = None
    openhands_provider_retry_base_seconds: float = 1.0
    openhands_provider_retry_max_seconds: float = 30.0

    # Database Settings
    db_host: str = "127.0.0.1"
    # 개발 스크립트가 MySQL 컨테이너의 3306을 호스트 33060으로 공개한다. 백엔드는
    # 호스트에서 실행되므로 스크립트를 거치지 않아도 같은 공개 포트를 기본으로 쓴다.
    db_port: int = 33060
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "easydep"
    # 개발 DB의 기존 구조와 데이터를 모두 버리고 현재 ORM schema로 다시 만든다.
    # 운영 환경에서 우발적으로 실행되지 않도록 기본값은 반드시 false다.
    db_schema_reset_on_start: bool = False

    # Experiments / Debug
    easydep_experiment_session: str | None = None
    easydep_log_level: str = "INFO"
    enable_bert_verify: bool = True
    enable_feedback_gates: bool = False

    # Stall probe
    easydep_llm_stall_probe_after_seconds: float | None = None
    easydep_llm_stall_probe_timeout_seconds: float = 60.0

    # Implementation and design execution config. Semantic repair attempts are
    # governed by progress/history, not numeric settings.
    design_sequence_parallelism: int = 2
    # E1에서 8개 동시 요청에도 429, 연결 오류, timeout이 없었다. 한 번의 전체 시간은
    # LLM 수리 편차가 크므로 이후 여러 실행의 중앙값과 완주율로 다시 조정한다.
    design_class_behavior_parallelism: int = 8
    # Stage-specific caps retain the former broad defaults until the frozen E1
    # experiment justifies a lower 2K/4K/8K/16K tier.
    design_class_inventory_max_completion_tokens: int = 16384
    # E1의 결합 operation 수리 응답이 8,192 토큰에서 실제로 잘렸으므로 다음
    # provider tier인 16,384를 사용한다. 짧은 call plan은 기존 상한이면 충분하다.
    design_class_operation_max_completion_tokens: int = 16384
    design_class_call_plan_max_completion_tokens: int = 8192
    # The global inventory needs enough combined reasoning/output budget to
    # finish strict JSON.  Choice-space reduction happens in its compact input,
    # not by truncating the response.
    design_class_structure_max_completion_tokens: int = 16384
    design_class_collaboration_max_completion_tokens: int = 8192
    implementation_max_workers: int = 1
    implementation_task_parallelism: int = 2
    implementation_agent_model: str = "nvidia_nim/openai/gpt-oss-120b"
    implementation_agent_base_url: str = "https://integrate.api.nvidia.com/v1"
    implementation_agent_temperature: float = 0.2
    implementation_agent_max_output_tokens: int = 16384
    implementation_reasoning_effort: str = "medium"
    implementation_command_timeout_seconds: int = 3600
    # 서버 시작 때 별도 Gradle compile을 실행하지 않는다. 첫 구현 요청 지연보다 시작 시간과
    # 디스크 사용량이 중요한 환경에서 기본 동작이 가벼워야 하며, 필요할 때만 env로 켠다.
    implementation_startup_warmup: bool = False
    # 기본 scaffold는 곧바로 OpenHands가 채우므로 구현 전 Gradle compile은 생략한다.
    # 생성기 자체를 점검할 때만 환경변수로 켜며, 작업별·최종 compile/test는 항상 별개로 실행한다.
    implementation_verify_initial_compile: bool = False
    implementation_default_container_port: int = 8000
    implementation_docker_gradle_image: str = "gradle:8.14.2-jdk21"
    implementation_docker_jre_image: str = "eclipse-temurin:21-jre-alpine"
    implementation_aws_log_retention_days: int = 30
    implementation_azure_mysql_retention_days: int = 7

    # Docker path mapping
    easydep_docker_command_workspace: str | None = None
    easydep_docker_host_workspace: str | None = None
    easydep_docker_windows_workspace: str | None = None
    easydep_fixed_linux_runner: str | None = None
    easydep_opentofu_path: str | None = None
    easydep_tofu_plugin_cache: str | None = None
    easydep_toolchain_image: str | None = None

    # Cloud KB
    cloudkb_cache_dir: str | None = None
    graphkb_cache_dir: str | None = None
    aws_region: str = "ap-northeast-2"
    google_cloud_project: str | None = None

    # Workflow approval
    easydep_approve_member_implementation: str = "0"

settings = Settings()
