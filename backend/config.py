from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    api_prefix: str = "/api/v1"
    fastapi_host: str = "0.0.0.0"
    fastapi_port: int = 8000
    openai_api_key: Optional[str] = None
    max_context_tokens: int = 128000
    compact_threshold: float = 0.8

    class Config:
        env_file = ".env"


settings = Settings()
