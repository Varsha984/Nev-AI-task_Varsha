"""Application configuration.

Single source of truth for environment variables. Every other module imports
`settings` from here — no module reads `os.environ` directly.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed configuration loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Postgres
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "nevup"
    postgres_user: str = "nevup"
    postgres_password: str = "nevup"

    # JWT — the deck mandates this exact secret across all three tracks.
    jwt_secret: str = (
        "97791d4db2aa5f689c3cc39356ce35762f0a73aa70923039d8ef72a2840a1b02"
    )
    jwt_algorithm: str = "HS256"

    # Optional — when unset, the coach uses a deterministic fallback.
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-5"

    # Local CPU embedding model. 384-dim, ~80MB, no API calls.
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dim: int = 384

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    # Path to the canonical seed dataset baked into the image.
    seed_data_path: Path = Path("/app/seed_data/nevup_seed_dataset.json")

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
