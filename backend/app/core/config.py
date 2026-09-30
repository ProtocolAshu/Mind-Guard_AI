"""Application settings.

All configuration comes from environment variables (optionally a `.env` file).
Secrets are held as `SecretStr` so they never render in logs or reprs.
"""

from __future__ import annotations

import json
import secrets
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent

ProviderName = Literal["mock", "anthropic", "openai_compatible", "disabled"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- application -------------------------------------------------------
    environment: Literal["development", "test", "production"] = "development"
    app_name: str = "MindGuard"
    api_version: str = "1.0.0"
    log_level: str = "INFO"
    log_json: bool = True

    # --- storage -----------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./mindguard.db"
    database_echo: bool = False
    redis_url: str | None = None

    # --- auth / security ---------------------------------------------------
    jwt_secret: SecretStr = SecretStr("")
    jwt_issuer: str = "mindguard"
    jwt_audience: str = "mindguard-clients"
    access_token_ttl_minutes: int = Field(default=15, ge=1, le=120)
    refresh_token_ttl_days: int = Field(default=14, ge=1, le=90)
    allow_registration: bool = True
    admin_emails: str = ""  # comma-separated; these accounts receive the admin role at registration
    cors_origins: str = "http://localhost:3000"
    max_request_bytes: int = 2_000_000
    rate_limit_default_per_minute: int = 240
    rate_limit_auth_per_minute: int = 10
    rate_limit_llm_per_minute: int = 20
    event_max_clock_skew_seconds: int = 900
    event_max_age_days: int = 7
    evaluation_min_interval_seconds: int = 45
    metrics_token: SecretStr = SecretStr("")

    # --- model roles (section 17): A reasoning LLM, B vision, C embeddings -----
    llm_provider: ProviderName = "mock"
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str | None = None
    llm_reasoning_model: str = "claude-sonnet-5"
    llm_fast_model: str = "claude-haiku-4-5-20251001"
    llm_fallback_model: str | None = None
    llm_timeout_seconds: float = 12.0
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_user_daily_token_budget: int = 40_000
    llm_monthly_budget_usd: float = 25.0
    llm_cache_ttl_seconds: int = 3600
    model_pricing_json: str = "{}"

    vision_provider: ProviderName = "disabled"
    vision_api_key: SecretStr = SecretStr("")
    vision_base_url: str | None = None
    vision_model: str = "claude-sonnet-5"

    embedding_provider: Literal["hashing", "openai_compatible"] = "hashing"
    embedding_api_key: SecretStr = SecretStr("")
    embedding_base_url: str | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 384

    # --- observability -----------------------------------------------------
    otel_endpoint: str | None = None
    otel_service_name: str = "mindguard-backend"

    # --- ML / knowledge ----------------------------------------------------
    model_dir: Path = REPO_ROOT / "ml" / "models"
    knowledge_dir: Path = BACKEND_ROOT / "app" / "rag" / "corpus"
    risk_ml_weight: float = Field(default=0.6, ge=0.0, le=1.0)
    bandit_algorithm: Literal["linucb", "epsilon_greedy", "thompson"] = "linucb"

    # --- decision thresholds ----------------------------------------------
    min_confidence_hard_action: float = 0.6
    llm_ambiguity_low: float = 0.45
    llm_ambiguity_high: float = 0.72
    default_retention_days: int = 90

    @model_validator(mode="after")
    def _validate_secrets(self) -> Settings:
        secret = self.jwt_secret.get_secret_value()
        if self.environment == "production":
            if len(secret) < 32:
                raise ValueError("JWT_SECRET must be set to at least 32 characters in production")
            if self.llm_provider == "mock":
                warnings.warn(
                    "LLM_PROVIDER=mock in production: agent reasoning uses deterministic heuristics",
                    stacklevel=2,
                )
        elif not secret:
            # Ephemeral per-process secret for local development/tests only.
            self.jwt_secret = SecretStr(secrets.token_urlsafe(48))
        return self

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def model_pricing(self) -> dict[str, dict[str, float]]:
        try:
            data = json.loads(self.model_pricing_json or "{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
