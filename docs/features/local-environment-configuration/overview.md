# Local Environment Configuration

## Purpose

Load developer-specific settings from a root `.env` file for both the FastAPI
application and Celery worker. The file is ignored by Git, and process-level
environment variables take precedence over values in the file.

## Setup

Copy `.env.example` to `.env` in the repository root. Set local PostgreSQL,
Redis, and Qdrant URLs, then add provider credentials when LLM-backed processing
is needed. `.env.example` contains placeholders only; the workspace `.env` may
contain private values and must not be committed.

## Loading behavior

Importing the `app` package loads `<repository-root>/.env` without replacing
environment variables already configured by the host process. This covers both
`uvicorn app.main:app` and `celery -A app.workers.celery_app:celery_app worker`
entry points.

## Local defaults and required integrations

- PostgreSQL, Redis, and Qdrant URLs point to local services by default.
- `FIKRAH_JWT_SECRET` must be a private key of at least 32 characters.
- LLM text generation defaults to OpenRouter when configured; DeepSeek and Gemini adapters are also available. Gemini is required for embeddings. Keep provider secrets in the ignored local `.env` file and use `.env.example` only for variable names and safe defaults.
- Google OAuth and SMTP settings are optional until those flows are exercised.
- Email action links must use an HTTPS URL accepted by the mobile application.


## Typed settings snapshots

`Settings` is immutable. Worker counts/durations, SMTP port, embedding dimensions,
and SMTP flags are parsed to integers/booleans at configuration construction.
Worker cross-field deadline checks remain in `get_worker_settings()`. Invalid
numeric deployment configuration now fails when the shared settings are first
constructed, rather than waiting for the corresponding provider call.

`get_settings()` reuses a bounded cache of snapshots keyed by configured environment
values. An unchanged process environment reuses one snapshot; tests/tools changing
an existing variable receive a new snapshot. Explicit mappings stay isolated from
process values. `.env` loading and Compose precedence are unchanged.

Every existing variable name and default is retained. The following table lists
**declared defaults only**, never credentials loaded from a local environment.
The regression fixture in `tests/fixtures/settings-defaults.json` records the
previous defaults for comparison.

| Environment variable | Default | Parsed type |
| --- | --- | --- |
| `DATABASE_URL` | `unset` | `NoneType` |
| `REDIS_URL` | `redis://localhost:6379/0` | `str` |
| `FIKRAH_LOGIN_LIMIT_PER_MINUTE` | `5` | `int` |
| `FIKRAH_GOOGLE_LIMIT_PER_MINUTE` | `20` | `int` |
| `FIKRAH_GOOGLE_TIMEOUT_SECONDS` | `10` | `float` |
| `FIKRAH_GOOGLE_EXECUTOR_WORKERS` | `8` | `int` |
| `FIKRAH_LLM_LIMIT_PER_MINUTE` | `10` | `int` |
| `FIKRAH_NOTE_MAX_CHARACTERS` | `100000` | `int` |
| `FIKRAH_REQUEST_MAX_BYTES` | `416384` | `int` |
| `FIKRAH_TRANSCRIPTION_MAX_BYTES` | `10485760` | `int` |
| `FIKRAH_SEARCH_TIMEOUT_SECONDS` | `100` | `int` |
| `FIKRAH_CHAT_TIMEOUT_SECONDS` | `110` | `int` |
| `FIKRAH_SEARCH_EXECUTOR_WORKERS` | `8` | `int` |
| `FIKRAH_LLM_EXECUTOR_WORKERS` | `8` | `int` |
| `FIKRAH_CELERY_MAX_RETRIES` | `3` | `int` |
| `FIKRAH_NOTE_WORKER_CONCURRENCY` | `4` | `int` |
| `FIKRAH_DISPATCH_WORKER_CONCURRENCY` | `1` | `int` |
| `FIKRAH_EMAIL_WORKER_CONCURRENCY` | `1` | `int` |
| `FIKRAH_VECTOR_WORKER_CONCURRENCY` | `1` | `int` |
| `FIKRAH_CELERY_RETRY_BACKOFF_SECONDS` | `5` | `int` |
| `FIKRAH_CELERY_VISIBILITY_TIMEOUT_SECONDS` | `3600` | `int` |
| `FIKRAH_NOTE_SOFT_TIME_LIMIT_SECONDS` | `240` | `int` |
| `FIKRAH_NOTE_HARD_TIME_LIMIT_SECONDS` | `270` | `int` |
| `FIKRAH_DISPATCH_STALE_AFTER_SECONDS` | `300` | `int` |
| `FIKRAH_PROCESSING_STALE_AFTER_SECONDS` | `900` | `int` |
| `FIKRAH_DISPATCH_INTERVAL_SECONDS` | `30` | `int` |
| `FIKRAH_DISPATCH_BATCH_SIZE` | `100` | `int` |
| `FIKRAH_DISPATCH_PUBLISH_BUDGET_SECONDS` | `10` | `int` |
| `QDRANT_URL` | `unset` | `NoneType` |
| `QDRANT_API_KEY` | `unset` | `NoneType` |
| `QDRANT_NOTES_COLLECTION` | `fikrah-notes` | `str` |
| `LLM_TEXT_PROVIDER` | `empty` | `str` |
| `DEEPSEEK_API_KEY` | `unset` | `NoneType` |
| `DEEPSEEK_TEXT_MODEL` | `deepseek-flash` | `str` |
| `GEMINI_API_KEY` | `unset` | `NoneType` |
| `GEMINI_TEXT_MODEL` | `gemini-3.8-flash` | `str` |
| `GEMINI_EMBEDDING_MODEL` | `gemini-embedding-001` | `str` |
| `GEMINI_EMBEDDING_DIMENSIONS` | `768` | `int` |
| `GEMINI_TRANSCRIPTION_MODEL` | `gemini-3.5-transcribe` | `str` |
| `OPENROUTER_API_KEY` | `unset` | `NoneType` |
| `OPENROUTER_TEXT_MODEL` | `deepseek/deepseek-v4.1-flash` | `str` |
| `OPENROUTER_CHAT_REASONING_ENABLED` | `false` | `bool` |
| `FIKRAH_JWT_SECRET` | `empty` | `str` |
| `GOOGLE_CLIENT_ID` | `unset` | `NoneType` |
| `EMAIL_ACTION_BASE_URL` | `unset` | `NoneType` |
| `SMTP_HOST` | `unset` | `NoneType` |
| `SMTP_PORT` | `587` | `int` |
| `SMTP_FROM` | `unset` | `NoneType` |
| `SMTP_USERNAME` | `unset` | `NoneType` |
| `SMTP_PASSWORD` | `unset` | `NoneType` |
| `SMTP_USE_TLS` | `false` | `bool` |
| `SMTP_START_TLS` | `true` | `bool` |
