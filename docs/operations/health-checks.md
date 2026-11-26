# Health checks

`GET /health` reports process liveness with HTTP 200 and `{"status":"ok"}`. Use `/ready` for dependency readiness.

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
