"""Runtime configuration for Memoe."""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[3]
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql://root@localhost:26257/memoe?sslmode=disable"
    observation_provider: str = "ollama"

    ollama_base_url: str | None = "https://ollama.com"
    ollama_api_key: SecretStr | None = None
    ollama_model: str | None = "gpt-oss:20b"

    aws_region: str | None = None
    aws_profile: str | None = None
    bedrock_model_id: str | None = None
    bedrock_max_tokens: int = 1200
    bedrock_temperature: float = 0
