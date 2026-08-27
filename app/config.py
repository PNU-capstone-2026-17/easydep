
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
    temperature: float = 0.0
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
    llm_failure_response_sample_chars: int = 0

    # OpenHands / Implementation Provider Settings
    openhands_max_output_tokens: int | None = None
    openhands_provider_retry_base_seconds: float = 1.0
    openhands_provider_retry_max_seconds: float = 30.0

    # Database Settings
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "easydep"

    # Experiments / Debug
    easydep_experiment_session: str | None = None
    easydep_log_level: str = "INFO"
    enable_bert_verify: bool = True
    enable_feedback_gates: bool = False

    # Stall probe
    easydep_llm_stall_probe_after_seconds: float | None = None
    easydep_llm_stall_probe_timeout_seconds: float = 60.0

    # Implementation Limits & Config
    implementation_max_cross_phase_repairs: int = 3
    implementation_max_conformance_repairs: int = 3
    design_max_repair_iters: int = 3
    design_max_sequence_repair_calls: int = 4
    design_sequence_parallelism: int = 2
    # Long design calls are independent, but hosted NIM is more stable and each
    # prompt easier to observe when no more than two are in flight.
    design_class_behavior_parallelism: int = 2
    # Stage-specific caps retain the former broad defaults until the frozen E1
    # experiment justifies a lower 2K/4K/8K/16K tier.
    design_class_inventory_max_completion_tokens: int = 16384
    design_class_operation_max_completion_tokens: int = 8192
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
    implementation_repair_reasoning_effort: str = "high"
    implementation_command_timeout_seconds: int = 3600
    implementation_startup_warmup: bool = True
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
    easydep_terraform_path: str | None = None
    easydep_tofu_plugin_cache: str | None = None
    easydep_member_runner_image: str | None = None

    # Cloud KB
    cloudkb_cache_dir: str | None = None
    graphkb_cache_dir: str | None = None
    aws_region: str = "ap-northeast-2"
    google_cloud_project: str | None = None

    # Workflow approval
    easydep_approve_member_implementation: str = "0"

    # Testing
    dynamic_test_max_retries: int = 3

settings = Settings()
