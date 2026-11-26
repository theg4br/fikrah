# FIKRAH Backend

FIKRAH turns captured thoughts into private, connected knowledge through note processing, semantic search and grounded project conversations.

**Capture → Understand → Organize → Connect → Retrieve → Discover**

This repository owns the FastAPI backend and LLM workflows. Flutter is the primary production client; the local website is a development/testing tool.

## Mobile team: start here

Read the [mobile integration handbook](docs/mobile/README.md).

It covers product/user journeys, all 42 application operations, request/response examples, field validation, errors, session refresh, note polling, idempotency, POST/SSE chat, voice transcription, acceptance scenarios and open release decisions.

- [Endpoint reference](docs/mobile/api-reference.md)
- [Data models and errors](docs/mobile/data-models-and-errors.md)
- [Flutter integration patterns](docs/mobile/flutter-integration.md)
- [Importable OpenAPI snapshot](docs/mobile/openapi.json)

The handbook is a source-version contract, not a promise that a running environment has been updated or a production domain has been assigned.

## Backend team

- [Local stack, configuration and safe upgrades](docs/features/local-development-stack/overview.md)
- [Layered modular architecture](docs/architecture/layered-modular-architecture.md)
- [Product scope](docs/product/llm-backend-scope.md)
- [Worker queues and rollout](docs/features/celery-work-isolation/queues-and-rollout.md)
- [Dependency locks and CI](docs/features/reproducible-backend-checks/dependency-locks-and-ci.md)
- [Endpoint testing tools](docs/features/api-endpoint-smoke-script/api-endpoint-testing-tools.md)

## Runtime components

FastAPI serves the JSON/Bearer mobile API. PostgreSQL is authoritative for accounts, projects, notes and conversations. Redis supports request budgets and Celery; dedicated workers handle processing, dispatch, email and vector cleanup. Qdrant stores note vectors. Configured LLM providers and SMTP remain backend integrations.

PDF originals remain on the device. An optional recording-transcription endpoint returns text for user review before ordinary note creation.

Local interactive API documentation is available at `/docs` after the API is running; `/health` is liveness and `/ready` checks PostgreSQL, Redis and Qdrant. See the setup guide before starting or upgrading an existing environment.
