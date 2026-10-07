from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://techpm:techpm@localhost:5432/techpm"
    redis_url: str = "redis://localhost:6379/0"
    # Fernet key (urlsafe base64, 32 bytes). Generate: python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
    credential_encryption_key: str = ""
    session_ttl_hours: int = 12
    cookie_secure: bool = False
    cors_origins: str = "http://localhost:3000"
    demo_mode: bool = True
    # LLM
    llm_provider: str = "demo"  # demo | anthropic
    llm_model: str = "claude-sonnet-5-5"
    anthropic_api_key: str = ""
    llm_timeout_seconds: float = 30.0
    # Workflow limits
    workflow_max_tool_calls: int = 12
    workflow_timeout_seconds: float = 60.0
    # Auth rate limit
    login_max_attempts: int = 8
    login_window_seconds: int = 300
    action_expiry_hours: int = 48
    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
