# Feature: FastAPI Foundation and Health Endpoint

## Purpose

Provide a minimal FastAPI application entry point and a health endpoint for checking whether the Backend process is running.

## Run Locally

```bash
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

FastAPI's interactive API documentation is available at `/docs` while the local server is running.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

## Endpoint Contract

### `GET /health`

- **What it does:** Reports that the API process is running.
- **Why the mobile application or operations tooling needs it:** It provides a lightweight way to check API availability before diagnosing product requests.
- **When it is called:** During local development or by operational health checks.
- **Request information:** No request body or parameters.
- **Response information:** HTTP 200 with `{ "status": "ok" }`.
- **Authentication:** None for this minimal operational endpoint.

## Decisions Kept Open

This document records the original health-endpoint foundation. PostgreSQL, Qdrant, authentication, and LLM provider integrations have since been implemented; refer to the current feature and architecture documentation for their status.

## Dependency readiness

`GET /ready` is an unauthenticated operational probe for PostgreSQL, Redis, and
Qdrant. It runs `SELECT 1`, Redis `PING`, and Qdrant `GET /readyz` concurrently
under one three-second execution budget. It does not create collections, modify
data, send email, or call paid model APIs. Unconfigured dependencies are unavailable.

- HTTP `200`: all three checks succeeded; `status` is `ready`.
- HTTP `503`: at least one check failed or timed out; `status` is `unavailable`.
- `checks` maps `postgresql`, `redis`, and `qdrant` to `ready` or `unavailable`.
- Responses use `Cache-Control: no-store` and never include credentials,
  connection strings, or raw dependency errors.

Example degraded response:

```json
{"status":"unavailable","checks":{"postgresql":"ready","redis":"ready","qdrant":"unavailable"}}
```

Use `/ready` for traffic readiness and `/health` for process liveness. `/health`
keeps its existing path, HTTP `200`, and `{"status":"ok"}` response even when a
dependency is down. Compose health checks are unchanged. This probe does not
prove worker availability, schema migration currency, collection configuration,
or SMTP/model provider health. The endpoint tester discovers `/ready` directly
from OpenAPI; mobile authentication and content APIs do not depend on this probe.
