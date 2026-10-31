"""Application settings, read once at import time.

Every value has a working default so `uvicorn app.main:app` runs with no .env at
all -- except SECRET_KEY, which is generated per-process if unset and warns
loudly, because a silent random secret would break tokens across restarts in a
way that looks like a bug rather than the misconfiguration it is.
"""

from __future__ import annotations

import logging
import secrets
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("markaz.config")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core -------------------------------------------------------------
    app_name: str = "Markaz"
    environment: str = "development"
    debug: bool = False

    # --- Security ---------------------------------------------------------
    # Left as None on purpose: a generated fallback is safer than a checked-in
    # default, and the warning below makes the trade-off visible.
    secret_key: str | None = None
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24
    bcrypt_rounds: int = 12

    # --- Database ---------------------------------------------------------
    database_url: str = "sqlite:///./markaz.db"

    # --- CORS -------------------------------------------------------------
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"

    # --- Rate limiting ----------------------------------------------------
    rate_limit_auth_per_minute: int = 30
    rate_limit_write_per_minute: int = 120

    # --- Server -----------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    if not s.secret_key:
        s.secret_key = secrets.token_urlsafe(48)
        logger.warning(
            "SECRET_KEY is not set; generated an ephemeral key for this process. "
            "Tokens will be invalidated on restart. Set SECRET_KEY in the environment "
            "before running this anywhere real."
        )
    return s


settings = get_settings()
