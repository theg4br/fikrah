# FIKRAH mobile integration handbook

This is the starting point for the Flutter team. It describes the **implemented backend contract**, the user journeys it supports, and the decisions still needed before release.

**Contract baseline:** email OTP activation/recovery and saved note favorites, reviewed on 26 November 2026; requires migration `20261126_02`. Documentation changes do not imply that an existing running server has been upgraded to this revision.

## Read in this order

| Document | What you get |
| --- | --- |
| [Product and user journeys](product-and-user-journeys.md) | What FIKRAH does, ownership boundaries, each user story and its call sequence. |
| [Data models and errors](data-models-and-errors.md) | Exact field names, nullability, limits, timestamps, status codes and client recovery behavior. |
| [Endpoint reference](api-reference.md) | Every one of the 43 application operations: purpose, trigger, request, success result and additional failure cases. |
| [Flutter integration](flutter-integration.md) | Base URL setup, session/refresh coordination, idempotency, polling, streaming, audio upload and local state. |
| [Integration acceptance and open decisions](integration-acceptance.md) | A practical handover checklist, known gaps and decisions that must not be guessed. |
| [OpenAPI snapshot](openapi.json) | Importable schema generated from this source revision; useful for API clients and model generation. |

Field definitions and example response objects live in the data-model document. Endpoint entries refer to those objects rather than repeating their definitions. The endpoint reference owns the operation-by-operation call contract; feature documents explain current processing rules and operational behavior.

## First successful session

1. Obtain a reachable backend base URL from the backend team. The current demo base URL is `https://fikrah-api-23107c84.austriaeast.cloudapp.azure.com`; see the [operator runbook](../features/azure-vm-deployment/operations-runbook.md) for deployment limits.
2. Register using `/v1/auth/register/otp`, enter the emailed six-digit code at `/v1/auth/verify-email/otp`, then log in; or obtain a Google ID token and exchange it.
3. Store the returned FIKRAH access/refresh pair securely.
4. Fetch `GET /v1/auth/me` and `GET /v1/projects`.
5. Select the project with `is_general: true`; save its ID, not its name.
6. Create a text note in that project with a stable `Idempotency-Key`.
7. Poll that note until `completed` or `failed`.
8. Open its refinement proposal, related notes, search or project conversation.

Saving is immediate; LLM processing is asynchronous. A successful save response is not a promise that processing has finished.

## Rules the mobile client must preserve

- Email OTP is the preferred activation/recovery flow; password/Google login remains unchanged. Legacy email-link routes are retained temporarily.
- Native Flutter calls the backend directly using JSON and `Authorization: Bearer <access_token>`. The local web tester's cookies/proxy are not part of the mobile protocol.
- The backend determines the user from the token. Do not send `user_id`, database credentials or model-provider keys.
- Every note belongs to one selected project. Start in General, identified by `is_general`.
- IDs and timestamps come from the backend. A title can temporarily be null while processing.
- A proposal is not the saved note content. Only acceptance replaces content.
- Keep the latest `processing_generation` when taking proposal actions.
- Favorites use an explicit true/false PUT; server-filter the project list. These metadata writes do not trigger processing or change content revisions/timestamps.
- Serialize refresh requests. Reusing an already rotated refresh token revokes its session family.
- Chat deltas are previews. Only the final `complete` event is the saved authoritative exchange.
- The graph API provides actual relationships. Mobile owns layout, colors, clusters and gestures.
- PDF binaries stay on-device; there is no PDF upload/extraction endpoint. Optional audio transcription is a separate endpoint returning text.
- Lists are arrays without pagination envelopes. No invented cursor, total count, note-moving route, profile route or account-deletion route exists.

## Important current integration gaps

The handbook documents what the source does, including behavior that differs from earlier requirements. See the [release decisions](integration-acceptance.md#release-decisions-and-known-gaps) before implementing final validation or legacy email callbacks.

Passwords accept **5–128 characters** consistently across registration, login and recovery, as requested. The local HTTP email callback exception is absent for legacy links. OTP flows need no callback URL; production app-link destinations for legacy links and platform OAuth identifiers remain undecided. This documentation does not silently change any of those behaviors.

## Sources and maintenance

Runtime contract sources: [routes](../../app/api/v1/), [HTTP schemas](../../app/schemas/), [services](../../app/services/), and [middleware](../../app/api/body_limits.py). The runtime `/openapi.json` is authoritative for a particular deployed version's schema; compare its behavior/version with the backend team if it differs from this snapshot.

Some service/middleware error statuses are not declared in generated OpenAPI. The prose error tables document those existing runtime branches; code generation alone is insufficient.

When an API changes, update the affected handbook section and regenerate the snapshot with the same dependency/runtime baseline. Do not add proposed fields or endpoints to the implemented reference.

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
from app.main import app

Path("docs/mobile/openapi.json").write_text(
    json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n"
)
PY
```

Schema generation imports the application without starting its lifespan, workers or migrations. It is not an end-to-end test.
