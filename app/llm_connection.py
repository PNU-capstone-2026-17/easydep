"""루트 설정을 실제 OpenAI 호환 LLM 연결 정보로 바꾼다.

NVIDIA NIM은 ``BASE_URL``과 ``API_KEY``만 있으면 되지만 Cloudflare AI Gateway는
계정 ID가 URL에 들어가고, 특정 Gateway를 고르는 헤더도 필요하다. 호출 코드마다 이
문자열을 조립하면 단계마다 서로 다른 endpoint를 쓰기 쉬우므로 이 작은 함수만 사용한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, settings


@dataclass(frozen=True, slots=True)
class LlmConnection:
    """한 OpenAI 호환 클라이언트를 만드는 데 필요한 값이다."""

    provider: str
    api_key: str
    base_url: str
    model: str
    headers: tuple[tuple[str, str], ...] = ()

    def default_headers(self) -> dict[str, str]:
        """SDK에 넘길 새 dict를 반환해 공유 설정이 변경되지 않게 한다."""

        return dict(self.headers)


def build_llm_connection(config: Settings = settings) -> LlmConnection:
    """Cloudflare 설정이 있으면 이를 우선하고, 없으면 기존 연결을 그대로 쓴다.

    계정이나 토큰 중 하나만 설정된 상태에서 조용히 NVIDIA로 요청하면 비용과 실험 결과를
    잘못 해석할 수 있다. 따라서 Cloudflare 값이 하나라도 있으면 필수값을 함께 검사한다.
    Gateway ID는 선택값이며, 없으면 Cloudflare의 기본 Gateway가 사용된다.
    """

    account_id = (config.cloudflare_account_id or "").strip()
    api_token = (config.cloudflare_api_token or "").strip()
    gateway_id = (config.cloudflare_ai_gateway_id or "").strip()
    if account_id or api_token or gateway_id:
        missing = [
            name
            for name, value in (
                ("CLOUDFLARE_ACCOUNT_ID", account_id),
                ("CLOUDFLARE_API_TOKEN", api_token),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Cloudflare LLM configuration is incomplete: {', '.join(missing)}")
        headers = (("cf-aig-gateway-id", gateway_id),) if gateway_id else ()
        return LlmConnection(
            provider="cloudflare-ai-gateway",
            api_key=api_token,
            base_url=(
                "https://api.cloudflare.com/client/v4/accounts/"
                f"{account_id}/ai/v1"
            ),
            model=config.model,
            headers=headers,
        )

    return LlmConnection(
        provider="openai-compatible",
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
    )


def llm_subprocess_environment(config: Settings = settings) -> dict[str, str]:
    """하위 Python/Docker 프로세스가 부모와 같은 LLM 연결을 쓰게 한다."""

    connection = build_llm_connection(config)
    environment = {
        "API_KEY": connection.api_key,
        "BASE_URL": connection.base_url,
        "MODEL": connection.model,
    }
    if connection.provider == "cloudflare-ai-gateway":
        environment.update(
            {
                "CLOUDFLARE_ACCOUNT_ID": (config.cloudflare_account_id or "").strip(),
                "CLOUDFLARE_API_TOKEN": (config.cloudflare_api_token or "").strip(),
            }
        )
        if config.cloudflare_ai_gateway_id:
            environment["CLOUDFLARE_AI_GATEWAY_ID"] = config.cloudflare_ai_gateway_id.strip()
    return environment
