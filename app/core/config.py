from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    # Core LLM settings
    api_key: Optional[str] = None
    nvidia_api_key: Optional[str] = None
    nvidia_nim_api_key: Optional[str] = None
    llm_api_key: Optional[str] = None
    
    base_url: Optional[str] = None
    model: str = "openai/gpt-oss-120b"
    temperature: float = 0.0
    seed: int = 42
    
    # Specific Agent Models
    design_agent_model: str = "openai/gpt-oss-120b"
    openhands_model: Optional[str] = None
    llm_model: Optional[str] = None
    
    # LLM Options
    llm_max_completion_tokens: Optional[int] = None
    llm_timeout_seconds: float = 300.0
    llm_wall_timeout_seconds: float = 330.0
    llm_max_retries: int = 0
    llm_failure_response_sample_chars: int = 0
    
    # OpenHands / Implementation Provider Settings
    openhands_max_output_tokens: Optional[int] = None
    openhands_provider_retry_base_seconds: float = 1.0
    openhands_provider_retry_max_seconds: float = 30.0
    
    # Database Settings
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "easydep"
    
    # Experiments / Debug
    easydep_experiment_session: Optional[str] = None
    easydep_log_level: str = "INFO"
    enable_bert_verify: bool = True
    enable_feedback_gates: bool = False
    
    # Stall probe
    easydep_llm_stall_probe_after_seconds: Optional[float] = None
    easydep_llm_stall_probe_timeout_seconds: float = 60.0
    
    # Tools / External
    plantuml_jar_path: str = "plantuml.jar"
    easydep_terraform_path: Optional[str] = None
    
    # Implementation Limits & Config
    implementation_max_cross_phase_repairs: int = 3
    implementation_max_conformance_repairs: int = 3
    design_max_repair_iters: int = 2
    implementation_max_workers: int = 1
    implementation_agent_model: str = "nvidia_nim/openai/gpt-oss-120b"
    implementation_agent_base_url: str = "https://integrate.api.nvidia.com/v1"
    implementation_command_timeout_seconds: int = 3600
    implementation_startup_warmup: bool = True
    implementation_default_container_port: int = 8000
    implementation_docker_gradle_image: str = "gradle:8.14.2-jdk21"
    implementation_docker_jre_image: str = "eclipse-temurin:21-jre-alpine"
    implementation_aws_log_retention_days: int = 30
    implementation_azure_mysql_retention_days: int = 7
    
    # Docker path mapping
    easydep_docker_command_workspace: Optional[str] = None
    easydep_docker_host_workspace: Optional[str] = None
    easydep_docker_windows_workspace: Optional[str] = None
    easydep_fixed_linux_runner: Optional[str] = None
    easydep_terraform_path: Optional[str] = None
    easydep_tofu_plugin_cache: Optional[str] = None
    easydep_member_runner_image: Optional[str] = None
    
    # Cloud KB
    cloudkb_cache_dir: Optional[str] = None
    graphkb_cache_dir: Optional[str] = None
    aws_region: str = "ap-northeast-2"
    google_cloud_project: Optional[str] = None
    
    # Workflow approval
    easydep_approve_member_implementation: str = "0"
    
    # Testing
    dynamic_test_max_retries: int = 3
    
settings = Settings()
