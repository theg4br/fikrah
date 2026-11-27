# Integration acceptance and release decisions

This is a handover checklist, not a claim that these scenarios were executed with this documentation change. Use a dedicated development/test account and environment. Do not use production data for destructive checks.

## 1. Information the teams exchange first

| Item | Backend/platform owner supplies | Mobile team confirms |
| --- | --- | --- |
| API access | Reachable base origin, running source revision and environment limitations. | Device/emulator reaches the API directly. |
| Account flows | Working SMTP; six-digit activation/recovery code entry. Legacy links additionally need agreed action routing. | OTP flows work without callbacks; legacy cold/warm app opens submit the token to the correct route. |
| Google | Configured backend audience and permitted Google account policy. | Native ID-token audience and platform registrations match. |
| Schemas | Current OpenAPI plus this handbook's runtime error guidance. | DTOs preserve nulls, snake_case fields and UTC instants. |
| Sessions | Rotation/replay, expiry and revocation behavior. | Secure token storage, one refresh coordinator, account-safe cache cleanup. |
| Processing | Durable note save and polling contract. | Pending/failed state is distinct from failed save; no duplicate create retries. |
| Chat | JSON/SSE contract, citations and uncertain-send recovery. | Correct final-event handling and no automatic duplicate question. |

No server secrets need to be handed to mobile. Model, SMTP and database credentials remain on the backend.

## 2. Suggested integration order

1. Environment connectivity and account bootstrap.
2. General/custom project selection and text-note CRUD.
3. Idempotent capture, processing polling and refinement.
4. Semantic search and actual graph relationships.
5. Conversation/history, grounded/social/unsupported chat and SSE.
6. Recording/transcription → reviewed draft → saved text note.
7. Lifecycle, concurrent requests, failure recovery and destructive cascades.
8. Release decisions, provider-backed validation and production environment readiness.

The text-note journey can be integrated before native audio capture or PDF-related local presentation.

## 3. Acceptance matrix

Operation IDs refer to the [endpoint reference](api-reference.md).

| Scenario | Operations | Expected result |
| --- | --- | --- |
| OTP registration/activation | A11, A12, A04, A10, P01 | Generic 202; six-digit code entry preserving leading zeroes; activation 200 without tokens; password login and General bootstrap. |
| OTP failure/replay/expiry | A12, A15 | Generic 400; five wrong guesses exhaust code; expired/used code rejected; no refresh loop. |
| OTP resend and compatibility | A13, A14 | 60-second cooldown and five sends/hour/address shared across methods/purposes; newly issued same-purpose action retires old code/link. Suppressed requests retain generic 202. |
| OTP password recovery | A14, A15, A04 | One confirmation with email/code/new password; all previous sessions revoked; explicit login after success. |
| Legacy password account | A01, A02, A04, A10, P01 | Register 202 without tokens; unverified login 403; verification 200; login token pair; one protected General project. |
| Register existing address | A01 | Generic 202; no account-existence message or automatic login assumption. |
| Resend within/outside limits | A03, A02 | Generic 202 can suppress sending; newly issued same-purpose link retires the old one. |
| Bad/used/expired action token | A02, A07 | 400 and link-renewal guidance; no access-refresh loop. |
| Wrong password | A04 | 401; draft input retained appropriately; no call to refresh. |
| Password length | A01, A04, A07, A12, A15 | Accept 5–128 characters consistently for registration, login and both reset methods; reject shorter/longer values with 422 without consuming an OTP. |
| Google new/existing account | A05, A10, P01 | FIKRAH token pair, account linking/General behavior, no Google token used as the API Bearer. |
| Google invalid/conflicting identity | A05 | 401/409; capacity/limit 429 and dependency 503 are distinguished. |
| Refresh | A08, A10 | Pair replaced; new access works. Do not deliberately reuse old refresh in an ordinary user session. |
| Concurrent protected 401s | A08 plus protected reads | One client refresh; waiting requests reuse new pair; late 401 for an old access token does not trigger unnecessary extra refresh. |
| Replay containment in an isolated test session | A08, A10 | Reusing a previously rotated token revokes that family; subsequent family access fails. |
| Logout | A09, A10 | Logout 200, private cache cleared, old family access 401. Other independent login families are not claimed revoked. |
| Password recovery | A06, A07, A04, A10 | Generic request; one-use reset; prior sessions fail; new password can log in when account verification permits. |
| Project CRUD | P01–P05 | Correct returned IDs, name updates, empty 204; General deletion 409; no dependency on name equality. |
| Empty project | N02, K02, C01 | Empty arrays/graph structure, not errors. |
| First text capture without title | N01, N03 | 201 saved identity; nullable title; eventual completed or failed state without losing content. |
| Matching create retry | N01 | Same project/body/key returns same note ID with 201 and current state. |
| Conflicting/deleted create retry | N01, N05 | Reused key with different body 409; original key after note deletion 410 when its project remains accessible. |
| Note list/read/update/delete | N02–N05 | Current project isolation, correct ordering/fields, PATCH reprocessing, deletion 204 and later read 404. |
| PATCH null semantics | N04 | Omitted title retained; title null clears it; content null/empty patch 422. |
| Favorites and filtering | N11, N02, N03, K03 | Explicit true/false, same-value retry, server project filtering, current search state, unchanged revision/generation/timestamps and no processing. Missing/foreign note 404; malformed boolean 422. Serialize intents and recover uncertain responses from detail. |
| Manual processing retry | N06, N03 | Completed/failed queues a generation; pending request does not create another active generation. |
| Regenerate/accept/reject | N07, N09, N10 | Expected-generation checks; accept changes content; reject retains content; regenerate queues processing. |
| Stale proposal | N07–N10 | 409 then reload/review; no blind replay of old generation. |
| Proposal editor, only if approved | N08, N09 | Edit changes proposal only and advances generation; later accept uses returned generation. |
| Related notes | K01 | Only completed same-project actual shared-concept connections; unfinished note returns empty array. |
| Graph display | K02 | One mobile node per returned note; only returned edges; isolated nodes and empty graph handled; no fabricated cluster IDs/coordinates. |
| Semantic search | K03 | Selected-project results in returned order; empty result is success; pending/failed notes are not treated as current indexed results. |
| Conversation CRUD | C01–C04 | Correct project scoping, nullable/automatic title, ordered history, deletion does not delete cited notes. |
| Supported chat | C05/C06, C03 | Saved exchange, supported true and correct source IDs; source opens current note. |
| Unsupported/general question | C05/C06 | Normal completed reply with supported false and no note inventory or invented outside answer. |
| Recognized greeting/identity | C05/C06 | Concise reply, supported null, no sources; the normal shared request budget still applies. |
| Summary/continue | C05/C06 | Progressive short batches; no promise of exhaustive enumeration or cursor pagination. |
| SSE fragmentation | C06 | UTF-8 split chunks, multiple events/chunk and CRLF parsed correctly; only final complete confirms saved IDs. |
| SSE final replacement | C06 | Final refusal/different text replaces preview; zero-delta completion succeeds. |
| SSE failure/disconnect/concurrent send | C06, C03 | Embedded error/EOF handled; reload history before retry, no duplicate send/JSON fallback. |
| Voice upload | V01 | One supported file → text only; then explicit N01 creates a note. |
| Invalid voice | V01 | Correct 413/415/422; cancellation/permission denial doesn't submit empty data. |
| Liveness/readiness | O01, O02 | Health 200 is not full readiness; ready returns 200 or 503 with per-dependency states. |
| Cross-account isolation | Protected resource routes | Another user's IDs return inaccessible/not-found behavior; account switch removes prior cached private views. |
| Cascade deletion | P05/N05, K01/K02, C03, K03 | Deleted notes/relationships/citations disappear; eventual vectors do not authorize deleted note retrieval. |
| Quotas and outages | Budgeted/auth routes | Respect Retry-After; distinguish 429/503 from validation/unsupported answers; no infinite retry. |
| App pause/restart | A08/A10, N03, C03 | Resume safely with account-scoped pending attempts/polls and reconciled history. |

For provider-backed scenarios, use small synthetic notes with an explicit common fact, such as “Review neural networks before batch normalization,” then ask about that exact fact. A second note with an intentionally shared concept can establish a graph relationship. LLM concept extraction is nondeterministic, so do not assume every pair of broadly similar sentences must produce an edge.

## 4. Definition of a completed integration

- All 43 application operations are mapped or intentionally excluded from UI scope with an explicit reason.
- Authentication and recovery work without the local web tester.
- The client handles all documented nullable fields and missing-vs-null PATCH behavior.
- Each intentional create attempt has one persisted idempotency key.
- The user can distinguish saved/queued, processing, completed and failed processing.
- Proposal acceptance/rejection cannot silently target a stale generation.
- Streaming has an explicit confirmed/unconfirmed final state.
- The client neither invents endpoints nor adds unsupported request fields.
- Device network access and real email callbacks are verified in the agreed environment.
- Quota/outage messages retain drafts and provide safe recovery.
- Production release decisions below have owners and resolutions.

## Release decisions and known gaps

The following are **not resolved by writing documentation**:

| Topic | Implemented/current fact | Required agreement or follow-up |
| --- | --- | --- |
| Legacy development email links | Current mail adapter rejects HTTP, including localhost; earlier localhost-only exception was approved. | Fix/verify development compatibility; don't claim localhost links work on this source. |
| Legacy production action routing | One configurable HTTPS base URL; both purposes currently append token only. No purpose discriminator is supplied. | Agree verification/reset cold-start routing, domain/app association and mobile behavior. |
| Production API host | No production URL or successful Azure deployment supplied by this handbook. | Choose hosting/budget and deploy after environment approval. |
| Running local revision | Review observed old images, stopped dependencies and missing dedicated consumers. | Complete a coordinated local upgrade before testing this contract live. |
| Google platform setup | Backend takes one configured audience. | Coordinate native platform registrations/signing IDs and a valid matching ID token with platform owners. |
| Proposal editing | Route exists; product requirement unconfirmed. | Explicitly choose whether mobile exposes an editor. |
| Languages | Note title/refinement prompt preserves note language; chat prompt follows question language; transcription returns SMART text. | Supported-language matrix and mixed-language behavior remain unconfirmed; do not promise universal language quality. |
| Long notes | API allows default 100,000 characters; one embedding call; no chunking/token-coverage contract. | Agree processing/input/reindexing behavior and validate provider limits. |
| Collections and graph scale | Lists/full history/graph are unpaginated; overview continuation is bounded retrieval, not exhaustive pagination. | Agree mobile pagination/graph evolution before large-scale promises. |
| Cross-store recovery | PostgreSQL is authoritative; vector cleanup/work are durable but eventual; no shared transaction with Qdrant. | Reconciliation and restore policy remain operational/design work. |
| Offline sync/history | No sync cursor, optimistic edit precondition or note-version API. | Do not promise conflict-free multi-device/offline synchronization or undo. |
| Production capacity | Default quotas/budgets exist; no user-count/latency SLA is established. | Agree expected load, costs, storage/recovery and observable acceptance thresholds. |

These entries explain missing decisions; they are not instructions to change the API in Flutter.

## 6. Contract maintenance

When changing backend behavior:

1. Update HTTP schemas/routes and the affected runtime error mappings.
2. Update the relevant endpoint entry and shared DTO definition once.
3. Update the affected user story and integration/recovery guidance.
4. Regenerate `openapi.json` from the reviewed application revision.
5. Compare its method/path set, field names/required/nullability and success statuses with the handbook.
6. Verify the affected behavior with backend/mobile integration coverage; do not claim provider-backed validation from schema checks.
7. Communicate any public contract change before the mobile team relies on it.

The existing [interactive endpoint script](../../scripts/test_api_endpoints.py) and [smoke script](../../scripts/smoke_api.py) are development helpers. Their execution can mutate dedicated test data and, with provider-backed options, consume provider quota. They are not a substitute for native session, lifecycle and transport integration checks.

## 7. Related backend references

- [Architecture and layer responsibilities](../architecture/layered-modular-architecture.md)
- [Product scope and unresolved decisions](product-and-user-journeys.md)
- [Authentication](../features/authentication/overview.md)
- [Note management](../features/note-management/overview.md)
- [Refinement actions](../features/note-refinement-actions/overview.md)
- [Voice transcription](../features/voice-transcription/recorded-audio-transcription.md)
- [Grounded chat and streaming](../features/project-knowledge-chat/overview.md)
- [Request budgets](../features/request-budgets/request-and-content-limits.md)
- [Local stack](../../infra/deployment-guide.md)
- [Worker rollout](../features/celery-work-isolation/queues-and-rollout.md)
