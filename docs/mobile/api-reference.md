# Endpoint reference

**37 application operations**, matching the [OpenAPI snapshot](openapi.json). Paths are relative to the configured API origin.

Each entry states what the operation does, why/when mobile calls it, its inputs and its returned data. Shared response objects and validation rules are defined once in [Data models and errors](data-models-and-errors.md).

## Common request rules

- **Protected** means a FIKRAH access token is required in the Bearer header. **Public** means no access token is required; action/refresh/Google tokens can still be required in the body.
- Every `{project_id}`, `{note_id}` and `{conversation_id}` is a UUID path parameter.
- JSON bodies are required where shown. “No body” means do not send a fabricated command object.
- JSON request schema violations return `422`. Any request can be rejected by the body cap with `413`.
- Protected routes can return `401`; routes with UUID path parameters can return `422`.
- Failure lists below add operation-specific cases to those common failures. Unexpected database/server failures remain possible; the client must have a generic fallback.
- Resource lists have no pagination/sorting/filter query contract unless an entry explicitly says otherwise. Do not send a guessed `page`, `cursor` or `user_id`.
- Examples use placeholder text/IDs. Copy the **shape**, not example credentials.

## A. Authentication

### A01 — POST /v1/auth/register

- **Public. Purpose:** request an email/password account, protected General project and verification/welcome email delivery.
- **When/why:** submit the sign-up form so the user can establish a private workspace.
- **Input:** `Credentials`.
- **Success:** `202` → `Message`; generic acknowledgment, no user ID or auth tokens. New-account email delivery may be deferred; duplicate addresses and send suppression preserve generic behavior.
- **Additional failures:** `429` when password processing is saturated.
- **Next:** check-email screen → A02, then A04.

```json
{"email":"reader@example.com","password":"example-only-long-passphrase"}
```

### A02 — POST /v1/auth/verify-email

- **Public. Purpose:** consume a one-time verification token and verify email ownership.
- **When/why:** the user opens a verification email action link.
- **Input:** `TokenRequest`; token from that email, not an access or refresh token.
- **Success:** `200` → `{"message":"Email verified"}`.
- **Additional failures:** `400` for invalid, expired or used token. Verification lifetime is 24 hours.
- **Next:** ask the user to log in; no tokens are issued by this route.

```json
{"token":"<verification-token-from-email>"}
```

### A03 — POST /v1/auth/resend-verification

- **Public. Purpose:** request a fresh link for an unverified account.
- **When/why:** resend action on the verification screen.
- **Input:** `EmailRequest`.
- **Success:** `202` → generic `Message`. Existing open verification tokens are retired when a new token is actually issued.
- **Behavior:** email cooldown/hourly suppression is deliberately not an account-existence or delivery-status API. Never promise a fresh email from the status alone.

```json
{"email":"reader@example.com"}
```

### A04 — POST /v1/auth/login

- **Public. Purpose:** validate email/password and issue a FIKRAH session.
- **When/why:** user submits login; do not submit on each input change.
- **Input:** `Credentials`; current source validates 15–128 password characters even for login.
- **Success:** `200` → `AuthTokens`.
- **Additional failures:** `401` invalid credentials; `403` email unverified; `429` login/password-work limit; `503` limiter unavailable.
- **Next:** persist token pair, then A10 and P01.

```json
{"email":"reader@example.com","password":"example-only-long-passphrase"}
```

### A05 — POST /v1/auth/google

- **Public. Purpose:** verify a Google identity and create/link/sign in a FIKRAH account.
- **When/why:** native Google sign-in returns an ID token for the configured backend audience.
- **Input:** `GoogleLoginRequest`.
- **Success:** `200` → `AuthTokens`; new users receive a General project and welcome-email intent.
- **Additional failures:** `401` invalid/unverified/non-authoritative new identity; `409` existing identity conflict; `429` IP budget or verifier capacity; `503` missing configuration/verification dependency/deadline.
- **Next:** same bootstrap as A04. See account-linking details in the user journeys; do not substitute a Firebase token or Google access token.

```json
{"id_token":"<google-id-token-for-the-backend-audience>"}
```

### A06 — POST /v1/auth/password-reset/request

- **Public. Purpose:** request a recovery link for an eligible password account.
- **When/why:** user submits “Forgot password.”
- **Input:** `EmailRequest`.
- **Success:** `202` → generic `Message`, independent of account existence/send suppression.
- **Behavior:** current Google-only accounts have no password and do not receive a password-reset action from this route; eligible new reset tokens retire prior open reset tokens.
- **Next:** check-email recovery state → A07.

```json
{"email":"reader@example.com"}
```

### A07 — POST /v1/auth/password-reset/confirm

- **Public. Purpose:** consume the recovery token and replace the password.
- **When/why:** user opens a reset link and submits the new-password form.
- **Input:** `ResetPasswordRequest`.
- **Success:** `200` → `{"message":"Password reset successfully"}`; all existing user session families are revoked immediately.
- **Additional failures:** `400` invalid/expired/used token; `429` password-work saturation. Reset-token lifetime is one hour.
- **Next:** clear local session and use A04. This does not issue a replacement session or independently complete email verification.

```json
{"token":"<password-reset-token-from-email>","new_password":"another-example-only-passphrase"}
```

### A08 — POST /v1/auth/refresh

- **Public. Purpose:** rotate an active refresh token and issue a replacement token pair.
- **When/why:** session restoration, proactive expiry handling or one coordinated response to a protected-resource `401`.
- **Input:** `RefreshRequest`.
- **Success:** `200` → `AuthTokens`; previous refresh token is invalidated.
- **Additional failures:** `401` invalid/expired/revoked refresh token. Reuse of an old revoked token revokes the whole family.
- **Next:** atomically replace both local tokens. No automatic retry of an uncertain refresh POST; see the integration guide.

```json
{"refresh_token":"<current-server-issued-refresh-token>"}
```

### A09 — POST /v1/auth/logout

- **Public. Purpose:** revoke the family belonging to the supplied refresh token.
- **When/why:** user logs out.
- **Input:** `RefreshRequest`; no Bearer token is required.
- **Success:** `200` → `{"message":"Signed out"}`, including for a well-shaped unknown token.
- **Behavior:** family access tokens cease to authorize protected requests. This is not logout-all-devices.
- **Next:** clear all account-scoped client state; distinguish local offline logout from confirmed server revocation.

```json
{"refresh_token":"<current-server-issued-refresh-token>"}
```

### A10 — GET /v1/auth/me

- **Protected. Purpose:** resolve the current authenticated user.
- **When/why:** bootstrap/restore a session, or refresh current identity.
- **Input:** Bearer access token; no body/query.
- **Success:** `200` → `UserView`.
- **Next:** P01; handle `401` through the session coordinator.

## P. Projects

### P01 — GET /v1/projects

- **Protected. Purpose:** list the user's projects and identify General.
- **When/why:** workspace bootstrap and refresh after project changes.
- **Input:** no body or documented query parameters.
- **Success:** `200` → `ProjectView[]`, General first, then creation time/ID. No counts/pagination envelope.
- **Next:** select an ID locally and request its notes. General lookup must use `is_general`.

### P02 — POST /v1/projects

- **Protected. Purpose:** create another organization space.
- **When/why:** user confirms “New project.”
- **Input:** `ProjectInput`.
- **Success:** `201` → `ProjectView` with `is_general: false`.
- **Behavior:** no idempotency contract; a blind retry can create another project. Names need not be treated as unique IDs.
- **Next:** select returned project and load its notes.

```json
{"name":"Deep learning"}
```

### P03 — GET /v1/projects/{project_id}

- **Protected. Purpose:** read one accessible project.
- **When/why:** open/revalidate a selected project.
- **Input:** project UUID; no body/query.
- **Success:** `200` → `ProjectView`.
- **Additional failures:** `404` project missing/inaccessible.

### P04 — PATCH /v1/projects/{project_id}

- **Protected. Purpose:** rename a project.
- **When/why:** user saves the project-name form.
- **Input:** project UUID and `ProjectInput`.
- **Success:** `200` → updated `ProjectView`.
- **Additional failures:** `404`.
- **Behavior:** current source permits renaming General; its protected boolean remains true.

```json
{"name":"Deep learning review"}
```

### P05 — DELETE /v1/projects/{project_id}

- **Protected. Purpose:** permanently delete a custom project and its owned notes/conversations/relationships.
- **When/why:** user confirms destructive project deletion.
- **Input:** project UUID; no body.
- **Success:** `204`, empty body; durable vector cleanup is scheduled.
- **Additional failures:** `404`; `409` if `is_general`.
- **Next:** remove caches/active conversation; select an existing accessible project, normally General. There is no restore endpoint.

## N. Notes and processing

### N01 — POST /v1/projects/{project_id}/notes

- **Protected; LLM request budget. Purpose:** persist text and durable processing work.
- **When/why:** user saves a text/transcription draft.
- **Input:** project UUID, required `Idempotency-Key` header (1–128 characters), `NoteCreate`.
- **Success:** `201` → `NoteView`. A matching retry also returns `201` and the existing note's **current** representation, not necessarily its original queued response.
- **Additional failures:** `404` project; `409` same user/key with different normalized request/project; `410` replay references a deleted note; `429` budget; `503` limiter.
- **Next:** save ID, show current content/state, then N03 polling. A timeout is retried only with the original project/body/key.

```http
Idempotency-Key: cfe25fbe-8974-4acd-a880-d67d05e8f9c0
```

```json
{"content":"Review neural networks, then batch normalization.","title":null}
```

Key scope is the authenticated user, not one project. The fingerprint includes project ID, title and content after request normalization. Reusing the key after editing the saved note returns its current state when the original create payload still matches.

### N02 — GET /v1/projects/{project_id}/notes

- **Protected. Purpose:** list all saved notes for a project, including pending/failed ones.
- **When/why:** open/refresh project notes.
- **Input:** project UUID; no body/query.
- **Success:** `200` → full `NoteView[]`, ordered by updated time descending then ID. Empty array is valid.
- **Additional failures:** `404` project.
- **Behavior:** full content/proposals are returned; no summary-only/pagination API.

### N03 — GET /v1/notes/{note_id}

- **Protected. Purpose:** read a note's current saved and derived state.
- **When/why:** note detail, processing polling and conflict/network recovery.
- **Input:** note UUID; no body/query.
- **Success:** `200` → `NoteView`.
- **Additional failures:** `404` note.
- **Behavior:** read-only; does not restart processing or consume the LLM request counter.

### N04 — PATCH /v1/notes/{note_id}

- **Protected; LLM request budget. Purpose:** update title/content and schedule reanalysis.
- **When/why:** user saves an existing note.
- **Input:** note UUID and nonempty `NoteUpdate`; preserve missing/null differences.
- **Success:** `200` → `NoteView` with incremented revision/generation and queued processing.
- **Additional failures:** `404`; `422` null/blank content or empty patch; `429`; `503` limiter.
- **Behavior:** no expected-revision compare-and-set field. Concurrent saves are not an optimistic edit-conflict protocol; do not invent an ETag or `If-Match` guarantee.

```json
{"content":"Review neural networks first, then study batch normalization.","title":"Study plan"}
```

### N05 — DELETE /v1/notes/{note_id}

- **Protected. Purpose:** delete saved note, processing records, links and citations; queue vector cleanup.
- **When/why:** user confirms note deletion.
- **Input:** note UUID; no body.
- **Success:** `204`, empty body.
- **Additional failures:** `404`.
- **Next:** remove local note, stop polling and invalidate graph/related/search views. A later repeated delete can return `404`.

### N06 — POST /v1/notes/{note_id}/processing-retries

- **Protected; LLM request budget. Purpose:** request another processing generation after completion/failure.
- **When/why:** user selects retry/reprocess.
- **Input:** note UUID; no body.
- **Success:** `202` → `NoteView`. If already queued/processing, returns existing active state without generating new work.
- **Additional failures:** `404`; `429`; `503` limiter.
- **Next:** N03 polling. The path name does not restrict this to failed notes.

### N07 — POST /v1/notes/{note_id}/refinement-regenerations

- **Protected; LLM request budget. Purpose:** clear a completed result's proposal and request reanalysis.
- **When/why:** user wants another rewrite after processing completes.
- **Input:** note UUID and `ProposalAction`.
- **Success:** `202` → `NoteView`; content/revision preserved, generation advanced, processing queued.
- **Additional failures:** `404`; `409` stale generation or processing incomplete; `429`; `503` limiter.
- **Next:** N03 polling; no distinct provider-generation endpoint is called by mobile.

```json
{"expected_processing_generation":1}
```

### N08 — PATCH /v1/notes/{note_id}/refinement-proposal

- **Protected. Purpose:** edit an existing completed proposal without replacing saved content.
- **When/why:** only if the product enables the currently unresolved proposal-editing flow.
- **Input:** note UUID and `ProposalUpdate`.
- **Success:** `200` → `NoteView`; proposal updated, generation +1, revision unchanged, no processing job created.
- **Additional failures:** `404`; `409` stale generation or no completed proposal.
- **Next:** use the **returned** generation for a later accept/reject/regenerate action.

```json
{"expected_processing_generation":1,"refinement_proposal":"Review neural networks before batch normalization."}
```

### N09 — POST /v1/notes/{note_id}/refinement-acceptance

- **Protected; LLM request budget. Purpose:** permanently replace content with its completed proposal.
- **When/why:** user explicitly accepts the rewrite.
- **Input:** note UUID and `ProposalAction`.
- **Success:** `200` → `NoteView`; content replaced, proposal cleared, revision/generation +1, processing queued.
- **Additional failures:** `404`; `409` stale generation/no completed proposal; `429`; `503` limiter.
- **Next:** display returned saved content and poll. There is no backend undo/history endpoint.

```json
{"expected_processing_generation":1}
```

### N10 — POST /v1/notes/{note_id}/refinement-rejections

- **Protected. Purpose:** dismiss the current proposal and retain content.
- **When/why:** user rejects the suggested rewrite.
- **Input:** note UUID and `ProposalAction`.
- **Success:** `200` → `NoteView`; proposal null, revision/generation unchanged.
- **Additional failures:** `404`; `409` stale generation.
- **Behavior:** no completed-state precondition and no cancellation of processing. A future successful generation can expose a new proposal.

```json
{"expected_processing_generation":1}
```

## K. Relationships and search

### K01 — GET /v1/notes/{note_id}/connections

- **Protected. Purpose:** return related notes for a note detail screen.
- **When/why:** user opens its connections panel.
- **Input:** note UUID; no body/query.
- **Success:** `200` → `RelatedNoteView[]`, deduplicated, current shared concepts only. An unfinished current note returns `[]`.
- **Additional failures:** `404` note.
- **Behavior:** only accessible completed related notes from the same project; no invented cross-project relationships.

### K02 — GET /v1/projects/{project_id}/knowledge-graph

- **Protected. Purpose:** retrieve graph data for the selected project.
- **When/why:** open/refresh the graph after processing or deletion.
- **Input:** project UUID; no body/query.
- **Success:** `200` → `ProjectGraph` with all completed note nodes and deduplicated current edges. Empty project yields `{"nodes":[],"edges":[]}`.
- **Additional failures:** `404` project.
- **Behavior:** nodes can be isolated; there is no viewport/pagination/clustering parameter. Edges are visually undirected shared-concept links.

### K03 — POST /v1/projects/{project_id}/search

- **Protected; LLM request budget. Purpose:** search that project's completed current knowledge by meaning.
- **When/why:** user submits a search query.
- **Input:** project UUID and `SearchRequest`.
- **Success:** `200` → ordered `SearchResult[]`, possibly empty/fewer than limit.
- **Additional failures:** `404`; `429`; `503` limiter, embedding/vector dependency or execution budget.
- **Behavior:** no search over all projects and no mobile-side call to Qdrant.

```json
{"query":"What should I study after neural networks?","limit":10}
```

## V. Recorded voice transcription

### V01 — POST /v1/transcriptions

- **Protected; LLM request budget. Purpose:** transcribe one recorded audio file through Gemini SMART mode.
- **When/why:** user finishes a recording and requests editable text.
- **Input:** `multipart/form-data`, exactly one binary field `file` with supported audio MIME. No other text/file fields; no JSON/base64 body, language/project selector or note-create flag.
- **Success:** `200` → `TranscriptionView`; text only, no saved note/audio resource.
- **Additional failures:** `413` size; `415` request/media type; `422` missing/invalid/multiple file form, empty/untranscribable audio; `429`; `503` limiter/provider/configuration/deadline.
- **Next:** put text in an editable draft; N01 only after user saves. The default provider budget is 90 seconds, not a latency SLA.

```bash
curl -X POST "$FIKRAH_BASE_URL/v1/transcriptions" \
  -H "Authorization: Bearer $FIKRAH_ACCESS_TOKEN" \
  -H "Accept: application/json" \
  -F "file=@recording.m4a;type=audio/m4a"
```

See media types and size limits in [Data models and errors](data-models-and-errors.md#http-errors-and-recovery). The backend has no audio-streaming capture or transcription-result lookup route.

## C. Conversations and chat

### C01 — GET /v1/projects/{project_id}/conversations

- **Protected. Purpose:** list project chat sessions.
- **When/why:** user opens chat navigation or refreshes after an exchange/deletion.
- **Input:** project UUID; no body/query.
- **Success:** `200` → `ConversationView[]`, most recently updated first, then ID.
- **Additional failures:** `404` project.
- **Behavior:** no pagination; another project's conversations must not be reused in this view.

### C02 — POST /v1/projects/{project_id}/conversations

- **Protected. Purpose:** start a conversation fixed to one project.
- **When/why:** user starts a new chat.
- **Input:** project UUID and `ConversationCreate`. Send `{}` if no explicit title.
- **Success:** `201` → `ConversationView`; title null until first successful message if omitted.
- **Additional failures:** `404` project.
- **Behavior:** no create-idempotency key. The automatic title is the first submitted message truncated to 120 characters, not an extra LLM request.

```json
{}
```

### C03 — GET /v1/conversations/{conversation_id}

- **Protected. Purpose:** load conversation metadata and full chronological history.
- **When/why:** opening history, reconnecting after an uncertain send, or resolving `409`.
- **Input:** conversation UUID; no body/query.
- **Success:** `200` → `ConversationDetail`; metadata fields plus `messages` array.
- **Additional failures:** `404`.
- **Behavior:** source references resolve currently existing note titles; there is no message cursor or resumable event offset.

### C04 — DELETE /v1/conversations/{conversation_id}

- **Protected. Purpose:** delete a conversation and its messages/citations.
- **When/why:** user confirms deleting chat history.
- **Input:** conversation UUID; no body.
- **Success:** `204`, empty body.
- **Additional failures:** `404`.
- **Behavior:** does not delete the notes cited by that conversation.

### C05 — POST /v1/conversations/{conversation_id}/messages

- **Protected; LLM request budget. Purpose:** generate and save one complete user/assistant exchange.
- **When/why:** mobile chooses the single-response transport.
- **Input:** conversation UUID and `MessageCreate`.
- **Success:** `200` → `MessageSubmission`; unsupported/social responses are also successful exchanges.
- **Additional failures:** `404`; `409` another exchange committed during generation; `429`; `503` retrieval/generation/invalid model result/limiter/deadline.
- **Behavior:** the pair is saved together after preparation succeeds. There is no message idempotency contract or route for editing/deleting one message. On a transport timeout, reload C03 before any retry.

```json
{"content":"What should I review first?"}
```

### C06 — POST /v1/conversations/{conversation_id}/messages/stream

- **Protected; shared LLM request budget. Purpose:** same exchange with provisional incremental text.
- **When/why:** mobile wants a responsive typing-style response.
- **Input:** same `MessageCreate`; Bearer token, JSON content type, `Accept: text/event-stream`.
- **Success transport:** `200 Content-Type: text/event-stream`; events below.
- **Before stream starts:** `401`, `404`, `422`, `429`, `503` can be ordinary HTTP errors.
- **After stream starts:** a failure is an SSE `error` event inside HTTP 200, not a changed HTTP status.
- **Next:** consume until `complete` or error/EOF; replace preview from final result. Do not submit both C05 and C06 for the same user action.

| Event | JSON data | Meaning |
| --- | --- | --- |
| `delta` | `{"content":"Start with "}` | Append text to a provisional buffer. |
| `complete` | Full `MessageSubmission` object | Persisted final result; replace the preview and store returned IDs/sources. |
| `error` | `{"status":409,"detail":"..."}` | Workflow failure after headers; no final-complete guarantee. |

Wire example (blank line terminates each event):

```text
event: delta
data: {"content":"Start with "}

event: delta
data: {"content":"neural networks."}

```

The final `complete` data has exactly the `MessageSubmission` example shape in the data-model document. Social replies and refusals can skip all delta events. There are no event IDs, resume tokens or guaranteed heartbeat events; do not send `Last-Event-ID` expecting replay.

Client disconnect does not prove absence of a committed exchange. Reload C03. A final unsupported answer can replace provisional text if current citations no longer support it.

## O. Operations

### O01 — GET /health

- **Public. Purpose:** process liveness only.
- **When/why:** backend diagnostics or first connectivity check; not required on every mobile action.
- **Input:** no body/query.
- **Success:** `200` → `{"status":"ok"}`.
- **Meaning:** the HTTP process responds; database, worker, email and LLM features can still be unavailable.

### O02 — GET /ready

- **Public. Purpose:** read-only readiness for PostgreSQL, Redis and Qdrant within a shared three-second budget.
- **When/why:** deployment/diagnostics; helpful when reporting environment problems.
- **Input:** no body/query.
- **Success:** `200` → `ReadinessView` with all checks ready.
- **Unavailable:** `503` → the same shape with failed checks; `Cache-Control: no-store`.
- **Meaning:** does not certify SMTP, provider quota, task consumers, migrations or answer quality. Do not block every screen on a separate readiness call.

## Documentation utilities

FastAPI also exposes `GET /docs`, `GET /redoc` and `GET /openapi.json`. They are documentation utilities, excluded from the 37 application-operation count. Their availability is not an auth/session or feature-readiness check.

For client-side orchestration, code examples and recovery rules continue to [Flutter integration](flutter-integration.md).
