"""Validated application settings loaded from the existing environment."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

from pydantic import BeforeValidator, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

DATABASE_URL_ENV = "DATABASE_URL"


def _positive_integer(value: object) -> int:
    try:
        parsed = int(value) if isinstance(value, str) else value
    except ValueError as error:
        raise ValueError("must be a positive integer") from error
    if isinstance(parsed, bool) or not isinstance(parsed, int) or parsed < 1:
        raise ValueError("must be a positive integer")
    return parsed


def _smtp_boolean(value: object) -> bool:
    # Preserve the existing SMTP convention: only a case-insensitive 'true'.
    return value if isinstance(value, bool) else str(value).lower() == "true"


PositiveInteger = Annotated[int, BeforeValidator(_positive_integer)]
SMTPBoolean = Annotated[bool, BeforeValidator(_smtp_boolean)]


class Settings(BaseSettings):
    """Environment configuration with the repository's existing names/defaults."""

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore", frozen=True)

    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")
    redis_url: str = Field(
        default="redis://localhost:6379/0", validation_alias="REDIS_URL"
    )
    fikrah_login_limit_per_minute: int = Field(
        default=5, ge=1, validation_alias="FIKRAH_LOGIN_LIMIT_PER_MINUTE"
    )
    fikrah_google_limit_per_minute: int = Field(
        default=20, ge=1, validation_alias="FIKRAH_GOOGLE_LIMIT_PER_MINUTE"
    )
    fikrah_google_timeout_seconds: float = Field(
        default=10, gt=0, allow_inf_nan=False,
        validation_alias="FIKRAH_GOOGLE_TIMEOUT_SECONDS",
    )
    fikrah_google_executor_workers: int = Field(
        default=8, ge=1, validation_alias="FIKRAH_GOOGLE_EXECUTOR_WORKERS"
    )
    fikrah_llm_limit_per_minute: int = Field(
        default=10, ge=1, validation_alias="FIKRAH_LLM_LIMIT_PER_MINUTE"
    )
    fikrah_note_max_characters: int = Field(
        default=100_000, ge=1, validation_alias="FIKRAH_NOTE_MAX_CHARACTERS"
    )
    fikrah_request_max_bytes: int = Field(
        default=416_384, ge=1, validation_alias="FIKRAH_REQUEST_MAX_BYTES"
    )
    fikrah_transcription_max_bytes: int = Field(
        default=10_485_760,
        ge=1,
        le=10_485_760,
        validation_alias="FIKRAH_TRANSCRIPTION_MAX_BYTES",
    )
    fikrah_search_timeout_seconds: float = Field(
        default=100,
        gt=0,
        allow_inf_nan=False,
        validation_alias="FIKRAH_SEARCH_TIMEOUT_SECONDS",
    )
    fikrah_chat_timeout_seconds: float = Field(
        default=110,
        gt=0,
        allow_inf_nan=False,
        validation_alias="FIKRAH_CHAT_TIMEOUT_SECONDS",
    )
    fikrah_search_executor_workers: int = Field(
        default=8, ge=1, validation_alias="FIKRAH_SEARCH_EXECUTOR_WORKERS"
    )
    fikrah_llm_executor_workers: int = Field(
        default=8, ge=1, validation_alias="FIKRAH_LLM_EXECUTOR_WORKERS"
    )

    fikrah_celery_max_retries: PositiveInteger = Field(
        default=3, validation_alias="FIKRAH_CELERY_MAX_RETRIES"
    )
    fikrah_note_worker_concurrency: int = Field(
        default=4, ge=1, validation_alias="FIKRAH_NOTE_WORKER_CONCURRENCY"
    )
    fikrah_dispatch_worker_concurrency: int = Field(
        default=1, ge=1, validation_alias="FIKRAH_DISPATCH_WORKER_CONCURRENCY"
    )
    fikrah_email_worker_concurrency: int = Field(
        default=1, ge=1, validation_alias="FIKRAH_EMAIL_WORKER_CONCURRENCY"
    )
    fikrah_vector_worker_concurrency: int = Field(
        default=1, ge=1, validation_alias="FIKRAH_VECTOR_WORKER_CONCURRENCY"
    )
    fikrah_celery_retry_backoff_seconds: PositiveInteger = Field(
        default=5, validation_alias="FIKRAH_CELERY_RETRY_BACKOFF_SECONDS"
    )
    fikrah_celery_visibility_timeout_seconds: PositiveInteger = Field(
        default=3600, validation_alias="FIKRAH_CELERY_VISIBILITY_TIMEOUT_SECONDS"
    )
    fikrah_note_soft_time_limit_seconds: PositiveInteger = Field(
        default=240, validation_alias="FIKRAH_NOTE_SOFT_TIME_LIMIT_SECONDS"
    )
    fikrah_note_hard_time_limit_seconds: PositiveInteger = Field(
        default=270, validation_alias="FIKRAH_NOTE_HARD_TIME_LIMIT_SECONDS"
    )
    fikrah_dispatch_stale_after_seconds: PositiveInteger = Field(
        default=300, validation_alias="FIKRAH_DISPATCH_STALE_AFTER_SECONDS"
    )
    fikrah_processing_stale_after_seconds: PositiveInteger = Field(
        default=900, validation_alias="FIKRAH_PROCESSING_STALE_AFTER_SECONDS"
    )
    fikrah_dispatch_interval_seconds: PositiveInteger = Field(
        default=30, validation_alias="FIKRAH_DISPATCH_INTERVAL_SECONDS"
    )
    fikrah_dispatch_batch_size: PositiveInteger = Field(
        default=100, validation_alias="FIKRAH_DISPATCH_BATCH_SIZE"
    )
    fikrah_dispatch_publish_budget_seconds: PositiveInteger = Field(
        default=10, validation_alias="FIKRAH_DISPATCH_PUBLISH_BUDGET_SECONDS"
    )

    qdrant_url: str | None = Field(default=None, validation_alias="QDRANT_URL")
    qdrant_api_key: str | None = Field(default=None, validation_alias="QDRANT_API_KEY")
    qdrant_notes_collection: str = Field(
        default="fikrah-notes", validation_alias="QDRANT_NOTES_COLLECTION"
    )

    llm_text_provider: str = Field(default="", validation_alias="LLM_TEXT_PROVIDER")
    deepseek_api_key: str | None = Field(
        default=None, validation_alias="DEEPSEEK_API_KEY"
    )
    deepseek_text_model: str = Field(
        default="deepseek-flash", validation_alias="DEEPSEEK_TEXT_MODEL"
    )
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    gemini_text_model: str = Field(
        default="gemini-3.8-flash", validation_alias="GEMINI_TEXT_MODEL"
    )
    gemini_embedding_model: str = Field(
        default="gemini-embedding-001", validation_alias="GEMINI_EMBEDDING_MODEL"
    )
    gemini_embedding_dimensions: PositiveInteger = Field(
        default=768, validation_alias="GEMINI_EMBEDDING_DIMENSIONS"
    )
    gemini_transcription_model: str = Field(
        default="gemini-3.5-transcribe",
        validation_alias="GEMINI_TRANSCRIPTION_MODEL",
    )
    openrouter_api_key: str | None = Field(
        default=None, validation_alias="OPENROUTER_API_KEY"
    )
    openrouter_text_model: str = Field(
        default="deepseek/deepseek-v4.1-flash",
        validation_alias="OPENROUTER_TEXT_MODEL",
    )
    openrouter_chat_reasoning_enabled: bool = Field(
        default=False, validation_alias="OPENROUTER_CHAT_REASONING_ENABLED"
    )

    fikrah_jwt_secret: str = Field(default="", validation_alias="FIKRAH_JWT_SECRET")
    google_client_id: str | None = Field(
        default=None, validation_alias="GOOGLE_CLIENT_ID"
    )

    email_action_base_url: str | None = Field(
        default=None, validation_alias="EMAIL_ACTION_BASE_URL"
    )
    smtp_host: str | None = Field(default=None, validation_alias="SMTP_HOST")
    smtp_port: PositiveInteger = Field(default=587, validation_alias="SMTP_PORT")
    smtp_from: str | None = Field(default=None, validation_alias="SMTP_FROM")
    smtp_username: str | None = Field(default=None, validation_alias="SMTP_USERNAME")
    smtp_password: str | None = Field(default=None, validation_alias="SMTP_PASSWORD")
    smtp_use_tls: SMTPBoolean = Field(default=False, validation_alias="SMTP_USE_TLS")
    smtp_start_tls: SMTPBoolean = Field(default=True, validation_alias="SMTP_START_TLS")


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    max_retries: int
    retry_backoff_seconds: int
    stale_dispatch_after_seconds: int
    stale_processing_after_seconds: int
    dispatch_interval_seconds: int
    dispatch_batch_size: int
    broker_visibility_timeout_seconds: int
    note_soft_time_limit_seconds: int
    note_hard_time_limit_seconds: int
    dispatch_publish_budget_seconds: int


_WORKER_FIELDS = {
    "max_retries": "fikrah_celery_max_retries",
    "retry_backoff_seconds": "fikrah_celery_retry_backoff_seconds",
    "stale_dispatch_after_seconds": "fikrah_dispatch_stale_after_seconds",
    "stale_processing_after_seconds": "fikrah_processing_stale_after_seconds",
    "dispatch_interval_seconds": "fikrah_dispatch_interval_seconds",
    "dispatch_batch_size": "fikrah_dispatch_batch_size",
    "broker_visibility_timeout_seconds": "fikrah_celery_visibility_timeout_seconds",
    "note_soft_time_limit_seconds": "fikrah_note_soft_time_limit_seconds",
    "note_hard_time_limit_seconds": "fikrah_note_hard_time_limit_seconds",
    "dispatch_publish_budget_seconds": "fikrah_dispatch_publish_budget_seconds",
}


def _settings_from_values(environ: Mapping[str, str]) -> Settings:
    values = {
        str(field.validation_alias): field.default
        for field in Settings.model_fields.values()
    }
    values.update(environ)
    return Settings(**values)


@lru_cache(maxsize=8)
def _cached_settings(environment: tuple[tuple[str, str], ...]) -> Settings:
    return _settings_from_values(dict(environment))


def get_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Reuse immutable settings for unchanged environment values.

    Explicit mappings remain isolated from the process environment. A changed
    configured variable selects a new snapshot, including in tests and tools.
    """
    if environ is not None:
        return _settings_from_values(environ)
    environment = tuple(
        (alias, os.environ[alias])
        for field in Settings.model_fields.values()
        if (alias := str(field.validation_alias)) in os.environ
    )
    return _cached_settings(environment)


def get_worker_settings(environ: Mapping[str, str] | None = None) -> WorkerSettings:
    """Apply cross-field safety checks to already typed worker configuration."""
    try:
        settings = get_settings(environ)
    except ValidationError as error:
        worker_aliases = {
            str(Settings.model_fields[name].validation_alias)
            for name in _WORKER_FIELDS.values()
        }
        for detail in error.errors():
            name = str(detail["loc"][0])
            if name in worker_aliases:
                raise ValueError(f"{name} must be a positive integer") from error
        raise
    worker = WorkerSettings(
        **{name: getattr(settings, setting) for name, setting in _WORKER_FIELDS.items()}
    )
    if worker.note_soft_time_limit_seconds >= worker.note_hard_time_limit_seconds:
        raise ValueError(
            "FIKRAH_NOTE_SOFT_TIME_LIMIT_SECONDS must be less than "
            "FIKRAH_NOTE_HARD_TIME_LIMIT_SECONDS"
        )
    if worker.note_hard_time_limit_seconds >= min(
        worker.stale_processing_after_seconds, worker.broker_visibility_timeout_seconds
    ):
        raise ValueError(
            "FIKRAH_NOTE_HARD_TIME_LIMIT_SECONDS must be less than "
            "FIKRAH_PROCESSING_STALE_AFTER_SECONDS and "
            "FIKRAH_CELERY_VISIBILITY_TIMEOUT_SECONDS"
        )
    return worker
