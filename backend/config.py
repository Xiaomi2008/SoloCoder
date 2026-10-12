from pydantic import ConfigDict
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env")

    api_prefix: str = "/api/v1"
    fastapi_host: str = "0.0.0.0"
    fastapi_port: int = 8000
    openai_api_key: Optional[str] = ""
    openai_compatible_url: str = "http://172.16.0.217:12345/v1"
    max_context_tokens: int = 128000
    compact_threshold: float = 0.8


settings = Settings()
