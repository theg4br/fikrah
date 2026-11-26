# Product and user journeys

## 1. What the mobile app is building

FIKRAH is a private second brain: capture a thought, save it as text, then let the backend refine it, extract concepts, connect it with other knowledge, and retrieve it through search or project chat.

**Capture → Understand → Organize → Connect → Retrieve → Discover**

This is a personal workspace. There are no shared workspaces, collaborator invitations, public notes, roles or team permissions in the current API.

### Responsibilities

| Area | Flutter/mobile | Backend |
| --- | --- | --- |
| Presentation | Screens, navigation, drafts, empty/loading/error states, graph layout, localization. | Explicit HTTP contracts and server authorization. |
| Identity | Native Google flow, secure session storage, code entry and optional legacy email links. | Password/Google verification, FIKRAH tokens, session revocation and account isolation. |
| Capture | Typing, microphone permissions/recording, local PDF files, user review of transcripts. | Accept text notes; optionally transcribe an uploaded recording. |
| Notes | Select project, submit edits, display processing state/proposals. | IDs, timestamps, persistence, processing generations, title generation and background processing. |
| Knowledge | Display concepts, graph nodes/edges and citations; open referenced notes. | Concepts, connections, project-scoped semantic retrieval and grounded answers. |
| Operations | Reconnect and reconcile state after network/lifecycle changes. | Durable jobs, workers, storage, quotas and dependency handling. |

The architecture is a layered modular backend: HTTP routes/schemas → services → repositories and LLM/infrastructure ports. PostgreSQL stores authoritative account/project/note/chat state. Qdrant holds vectors; Redis supports Celery and request limits. These are backend implementation details, not services the mobile app connects to.

## 2. Shared product rules

- Every account gets one protected General project.
- Project selection is client state; there is no server endpoint to select the active project.
- The selected project's ID is supplied when creating notes, searching, reading its graph or creating/listing conversations.
- Notes cannot currently be moved between projects through this API.
- A custom project can be renamed/deleted. General is identified by `is_general`, remains protected if renamed, and can currently be renamed.
- No title is required at capture. The backend generates one after processing if the saved title is null.
- Processing creates a rewrite proposal and up to eight concepts; it does not automatically replace the saved text or select a different project.
- Accepting a rewrite permanently replaces saved content. There is no note-version history/undo endpoint.
- Deleting a note removes its relationships and citations; vector cleanup is asynchronous. Deleting a project also removes its notes and conversations.
- Notes and conversations are private to their owner. An inaccessible resource normally appears as `404`, so the client does not discover another user's data.
- All timestamps are timezone-aware server timestamps. The client formats local date, time and weekday from them.

## 3. Account stories

### US-01 — Sign up with email/password

**As a new user**, I want a private account and a default knowledge space.

1. Submit `POST /v1/auth/register/otp`.
2. On `202`, show a check-email state, not a logged-in workspace.
3. Registration creates a verification intent and a welcome email for a new eligible account. Delivery can be deferred.
4. Show a six-digit code entry screen; the user reads the code from email.
5. Submit email and code to `POST /v1/auth/verify-email/otp`.
6. On `200`, return to login. Verification does not issue access/refresh tokens.
7. Submit login, then bootstrap the workspace.

**Acceptance:** no tokens are assumed from registration/verification; password login before verification returns `403`. Generic registration text is retained even if the address already exists.

### US-02 — Resend verification

From the code-entry screen, call `POST /v1/auth/resend-verification/otp`. Show the same generic acknowledgment on `202`. The backend applies a 60-second cooldown and five sends per address/hour; it can suppress sending without changing that generic success response. A newly issued code retires previous open codes/links for that purpose. Codes expire after 10 minutes, are single-use and stop accepting submissions after five wrong guesses.

**Acceptance:** the screen cannot promise that every press generated a new email.

### US-03 — Log in and restore a session

1. Submit email/password to `POST /v1/auth/login`.
2. Store the access and refresh tokens together.
3. Call `GET /v1/auth/me`; use returned identity rather than decoding JWT claims as authorization.
4. Call `GET /v1/projects`; restore an accessible previous selection or choose `is_general: true`.
5. Open `GET /v1/projects/{project_id}/notes`.

On app restart, restore protected credentials, refresh once if appropriate, then validate the session and reload account-scoped state. Access expires after 15 minutes; refresh rotation/session invalidation is described in the integration guide.

### US-04 — Sign in with Google

1. Mobile completes native Google sign-in.
2. Send the **Google ID token**, not an access token, email address or server auth code, to `POST /v1/auth/google`.
3. Backend validates issuer/audience/signature and its account-linking policy, then returns a normal FIKRAH token pair.
4. Continue with the same workspace bootstrap as password login.

New linking/account creation requires a verified Gmail address or verified Workspace authority. Previously linked subjects are treated by the implemented identity rules. A conflicting Google identity returns `409`; unsupported/unverified identity returns `401`. Linking an unverified password account discards its unverified password and old action links; do not assume that old password becomes valid.

**Acceptance:** Google authentication is not Firebase authentication, and Google credentials are not used as FIKRAH Bearer tokens.

### US-05 — Recover a password

1. Request `POST /v1/auth/password-reset/otp/request` from the recovery screen.
2. Show its generic `202` acknowledgment.
3. Present code entry and a new-password form.
4. Submit email, code and new password together to `POST /v1/auth/password-reset/otp/confirm`. No separate code-validation request is required.
5. Clear existing local credentials and ask the user to log in again.

Reset codes last 10 minutes, are single-use and allow five wrong guesses. A successful reset revokes the user's existing session families, including current access tokens. Google-only accounts do not currently acquire a password via the reset-request route.

Legacy registration, verification and reset links remain available for older clients; see the endpoint reference. New mobile integration should use OTP, without waiting for production app-link setup.

### US-06 — Log out

Send the currently held refresh token to `POST /v1/auth/logout`. Clear local tokens, cached private data, active project and ongoing authenticated work. This signs out that session family, not all devices. If offline, local cleanup can still happen, but server revocation must not be claimed as successful.

## 4. Project and capture stories

### US-07 — Create/select/rename a project

- Load projects after authentication.
- Create with `POST /v1/projects`; after `201`, select its returned ID.
- Rename with `PATCH /v1/projects/{project_id}`; use the returned object.
- Changing selection cancels/replaces note/graph/search/chat loads for the previous project. Discard stale responses keyed to a different project.

The backend has no project description, color, icon, count or project-level model setting fields.

### US-08 — Save a thought without a title

1. Keep a local draft and current project ID.
2. Allocate one idempotency key for this save attempt before sending.
3. Send content with an omitted/null title to `POST /v1/projects/{project_id}/notes`.
4. On `201`, replace the draft's temporary identity with returned note ID and keep the returned state.
5. Show a local title placeholder such as “Processing…” while the title is null.
6. Poll `GET /v1/notes/{note_id}`.
7. On `completed`, render title/concepts/proposal; on `failed`, retain saved content and offer retry.

The placeholder is presentation only. A save can succeed even when an immediate dispatcher notification fails; PostgreSQL holds durable processing work.

### US-08a — Keep a saved note in favorites

The user stars or unstars a saved note. Call N11 with an explicit boolean and use
the returned `NoteView` as the confirmed state. Favorites are private and survive
content edits and processing. They do not change content revision/generation,
timestamps or create processing work. A favorites screen calls N02 with
`is_favorite=true` within the selected project; empty results are normal.
Note detail/list and search return the same PostgreSQL-backed boolean. If a write
times out, reload detail before replaying; serialize per-note intents because
there is no stale-write conflict protocol. Deleting the note removes its star.

### US-09 — Edit/reprocess an existing note

Use `PATCH /v1/notes/{note_id}` for title/content changes. Each accepted PATCH increments revision and processing generation and schedules processing, including title-only or unchanged saves. Avoid an automatic request on every keystroke unless that behavior is separately agreed.

Use `POST /v1/notes/{note_id}/processing-retries` for retry/reanalysis. It works after failure or completion. While already queued/processing it returns the existing active state instead of adding a new generation.

The same note ID survives edits/retries. Older worker results cannot replace the current generation.

### US-10 — Review a rewrite

The UI keeps saved content and proposal separate.

| Choice | Call | Result |
| --- | --- | --- |
| Accept | `POST /v1/notes/{note_id}/refinement-acceptance` | Replaces content, clears proposal, increments revision/generation and queues processing. |
| Reject | `POST /v1/notes/{note_id}/refinement-rejections` | Clears the current proposal; preserves content. |
| Generate another | `POST /v1/notes/{note_id}/refinement-regenerations` | Requires completed processing, clears proposal, increments generation and queues reanalysis. |
| Edit proposal text | `PATCH /v1/notes/{note_id}/refinement-proposal` | Implemented but product approval is unresolved; edits the proposal only and advances generation. |

Every action sends the latest `expected_processing_generation`. After `409`, reload the note and ask the user to review its current state; do not blindly resubmit the old choice.

Only show proposal actions as available once the note has completed processing and the relevant proposal exists. Rejection does not cancel an in-flight job; a job can later produce a proposal. Follow the returned note rather than assuming permanent suppression.

### US-11 — Capture voice

Mobile records audio with microphone permission and handles permission denial/cancellation. The approved development recorder stops at one minute; the backend enforces file bytes and media type, not a 60-second duration rule.

1. Upload one recording to `POST /v1/transcriptions`.
2. Receive `{"text":"..."}` in SMART mode, which cleans disfluencies/self-corrections.
3. Put that text in a draft for user review.
4. Only when the user saves, call ordinary note creation with an idempotency key.

A successful transcript is not a saved note. The backend does not create an audio-library item or return an audio URL. Recordings are sent to Gemini for transcription; avoid suggesting that optional server transcription is fully on-device.

### US-12 — Work with a PDF

The original PDF stays on-device. There is no backend upload, extraction, OCR, file-download or attachment association endpoint. If the mobile flow has user-entered/available text, it can save that text as a note. This handbook does not require a new PDF extractor or promise text extraction as implemented behavior.

If mobile wants to associate a local PDF path with a note, that association is local application state, not a field accepted by the note API.

### US-13 — Delete a note/project

Ask the user to confirm destructive intent in the app.

- Delete a note: `DELETE /v1/notes/{note_id}` → `204`.
- Delete a custom project: `DELETE /v1/projects/{project_id}` → `204`; notes, conversations and relationships are removed.
- General deletion → `409`; leave it in navigation.
- Remove deleted objects from local caches and active screens. Refresh citations/graph as needed.

A `204` has no JSON body. PostgreSQL deletion is immediate; Qdrant cleanup is durable/eventual. Deleted notes must not remain visible merely because a local cached graph/search result still references them.

## 5. Retrieval and discovery stories

### US-14 — Search inside the current project

Send query and optional result limit to `POST /v1/projects/{project_id}/search`.

Show returned results in order and open a tapped `note_id` through note detail. Results can be fewer than the requested limit after current ownership/revision checks. Empty results are a valid success. Scores are ranking signals, not a probability/percentage of truth.

Search excludes unfinished/failed/stale-indexed notes. Saving a note does not immediately make it searchable. Do not substitute results from a different project after a failed search.

### US-15 — Explore related notes and graph clusters

- Note detail can load `GET /v1/notes/{note_id}/connections`.
- Connections view loads `GET /v1/projects/{project_id}/knowledge-graph`.
- Each graph node is a completed note; each edge represents an actual stored relationship with shared concepts.
- Show only returned edges. Similar screen positions/colors do not create a backend relationship.
- The graph can contain isolated nodes. A view that displays only connected nodes can derive that subset from edge endpoints.
- Treat relationships as undirected visually. Incoming/outgoing storage rows are deduplicated into unordered pairs in graph responses.
- Mobile computes layout coordinates, cluster colors, zoom, drag, hover/tap details and animations. The API returns no positions, cluster IDs, edge weights or layout instructions.
- Relationships are based on normalized shared concepts, not a guarantee of linking every semantically similar note. Generation selects up to ten outgoing matches per note; undirected degree can exceed ten through incoming links.
- Graphs and connection lists are project/user scoped; there is no cross-project graph endpoint.

### US-16 — Ask project knowledge a question

1. List/create a project conversation.
2. Load its history when opened.
3. Send one message at a time using either the JSON endpoint or its SSE alternative.
4. Render the returned user and assistant messages and clickable sources.
5. Replace the cached conversation summary with the returned summary so title/activity order stays current.

The project is fixed by the conversation; switching projects requires selecting/creating a conversation in that project.

| Assistant outcome | Meaning | Presentation |
| --- | --- | --- |
| `supported: true` | Answer grounded in current authorized note sources. | Show answer and clickable source references. |
| `supported: false` | No supported answer from the supplied project knowledge. | Show the refusal, not an HTTP/network error. |
| `supported: null` | Recognized greeting, identity/help or simple social reply. | Normal concise reply, no unsupported badge. |

Unknown/mixed social phrases can take the grounded path; do not duplicate the backend phrase classifier in mobile. Unrelated substantive questions can consume model requests because model judgment was selected instead of a strict keyword filter.

When asked for note overviews, the backend presents at most three notes per batch from an eight-candidate retrieval window and recent 12-message history. “Continue” is a conversation follow-up, not an API pagination cursor or a guarantee to enumerate every note.

### US-17 — Read a streamed answer

Show deltas promptly, without treating them as persisted truth. A `complete` event contains the full final message submission, including saved IDs and validated sources. Replace preview text with that final content even if it differs.

Greeting/refusal paths may produce no deltas and still complete normally. If sources change during generation, the backend can replace a provisional answer with a final unsupported response. A concurrent exchange can produce `409` or an SSE error. A broken connection has an uncertain outcome: reload history before retrying.

## 6. Scope not provided by this release

No production web client, Flutter implementation, full offline synchronization protocol, push notifications for processing, note attachments, PDF processing, collaboration, account deletion, profile editing, note moving, user-defined graph edges, note version history or paginated collections are implemented by this API. Those require separate product/contracts work.

For exact paths/payloads see the [endpoint reference](api-reference.md); for lifecycle/retry mechanics see [Flutter integration](flutter-integration.md).
