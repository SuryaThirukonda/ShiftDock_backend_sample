from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: str = "sqlite:///./shiftdock.db"

    SECRET_KEY: str = "local-dev-secret-change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480


settings = Settings()


if not settings.DATABASE_URL.startswith("sqlite"):
    raise RuntimeError(
        "This backend sample is configured for local SQLite only. "
        "Set DATABASE_URL to a sqlite URL such as sqlite:///./shiftdock.db."
    )
