# Mobile API contract design

**Status: Implemented; mobile handover published.**

The maintained mobile contract now starts at the [FIKRAH mobile integration handbook](../../mobile/README.md). This feature page records design scope and points to one detailed reference rather than duplicating endpoint tables that can drift.

## Mobile handover

| Need | Reference |
| --- | --- |
| Product responsibilities and user stories | [Product and user journeys](../../mobile/product-and-user-journeys.md) |
| Every operation's purpose, timing, request, response and failures | [42-operation endpoint reference](../../mobile/api-reference.md) |
| DTO fields, nullability, constraints, examples and error recovery | [Data models and errors](../../mobile/data-models-and-errors.md) |
| Direct Flutter auth, refresh, polling, idempotency, SSE and audio | [Flutter integration](../../mobile/flutter-integration.md) |
| Integration scenarios and unresolved release decisions | [Acceptance checklist](../../mobile/integration-acceptance.md) |
| Importable current schemas | [OpenAPI snapshot](../../mobile/openapi.json) |

## Contract design boundaries

- Flutter is the primary production client and talks directly to the JSON/Bearer API.
- The server derives ownership from the authenticated session; mobile does not submit an owner ID.
- Project-scoped creation, search, graph and conversation navigation use the selected project ID.
- General is protected from deletion and is identified by `is_general`.
- Note creation is idempotent by required `Idempotency-Key`; background processing is read through note polling.
- Rewrite proposals are separate from content until explicit acceptance.
- Search and factual chat answers are scoped to current authorized project knowledge.
- Chat supports both complete JSON responses and optional POST/SSE streaming; previews become authoritative only at completion.
- Audio transcription accepts a recording and returns text only. PDF originals stay on-device and there is no backend PDF-extraction API.
- Lists remain unpaginated. Counts, sort parameters, cursor envelopes and summary fields must not be inferred from earlier proposals.
- Deletion is authoritative in PostgreSQL and schedules durable vector cleanup.

The handbook separates source behavior from user requirements still needing reconciliation. In particular, password length, email callback routing, proposal editing and language support must not be silently finalized by a mobile client.

## Maintenance

Update the endpoint/DTO reference and generated schema together when public behavior changes. Runtime failures not expressed by OpenAPI must remain explicit in the prose reference.

Related architecture: [layered modular backend](../../architecture/layered-modular-architecture.md).
