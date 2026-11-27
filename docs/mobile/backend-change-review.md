# Mobile design: backend change review

## Status and authority

Initial review against `dev` at `6bed704` on 2026-11-26; updated for the approved
favorites implementation and category ownership/cardinality decisions.
The mobile handoff is a request, not an implemented API specification. The
existing [API reference](api-reference.md), [models and errors](data-models-and-errors.md)
and [OpenAPI snapshot](openapi.json) describe the current contract.
Recommendations below are pending approval unless explicitly marked confirmed.
No new endpoint or DTO in this review is available merely because it is proposed.

The handoff's Flutter-only ownership statement describes the originating mobile
work. This repository remains responsible for backend persistence, authorization,
business rules and LLM orchestration. Flutter owns presentation and device APIs.

## Capability comparison

| Request | Current implementation | Remaining work |
| --- | --- | --- |
| Six-digit activation/recovery | Implemented, including leading zeroes, lifecycle limits, one-use validation, resend replacement and reset session revocation. | Mobile should adopt the documented OTP routes. Additional challenge metadata requires a separately agreed contract. |
| Favorite notes and server filters | Approved and implemented in this change; see N11/N02 and the favorite field in note/search DTOs. | Apply migration `20261126_02` before using the updated API. |
| User categories | Account-wide ownership and one optional category per note approved; persistence/API not implemented. | Agree lifecycle details, then add category CRUD, assignment and suggestions. |
| Profile, interests, goals | `GET /v1/auth/me` returns identity, verification and nullable display name; `PATCH /v1/auth/me` updates the name only. | Define profile/preferences independently from authentication credentials. |
| Activity/achievements | Note timestamps and real persisted connections exist; no activity ledger, streak or achievement API exists. | Agree qualifying events/calendar/counting first; implement authoritative computation afterward. |
| Reminders | No synchronized reminder preferences or notification delivery contract exists. | Decide local-only versus synchronized preferences; OS scheduling stays on mobile. |
| Existing knowledge behavior | Explicit saves, idempotent creation, processing polling, generation-aware proposals, project search, graph and streamed chat exist. | Preserve these contracts; no prototype autosave, Undo, arbitrary-code acceptance or local answer engine. |

## Confirmed authentication contract

See [OTP activation and recovery](api-reference.md#otp-activation-and-recovery-preferred)
for DTOs, routes, statuses and examples, and
[OTP lifecycle implementation](../features/authentication/email-otp.md).

- Signup: `POST /v1/auth/register/otp` with name/email/password, generic `202`.
- Activation: `POST /v1/auth/verify-email/otp` with email/code, `200` on success.
- Activation resend: `POST /v1/auth/resend-verification/otp`, generic `202`.
- Recovery request: `POST /v1/auth/password-reset/otp/request`, generic `202`.
- Recovery completion: `POST /v1/auth/password-reset/otp/confirm` with
  email/code/new_password, `200` on success; no separate reset grant.
- Codes are six ASCII digits as strings, valid for ten minutes, single-use,
  exhausted after five incorrect submissions. Resends have a 60-second cooldown
  and share the five-message/address/hour limit with existing email actions.
  These limits were explicitly approved in the backend conversation; the mobile
  handoff's assertion that they were unapproved is stale.
- Email and route purpose identify the challenge. Issuing a newer same-purpose
  action invalidates older codes/links. A suppressed send does not replace it.
- Invalid, expired, used, exhausted and unknown-account challenges deliberately
  share generic `400`; malformed requests return `422`. Existing infrastructure
  limits/outage mappings remain as documented. Do not invent distinct reason codes.
- Generic request acknowledgments can suppress delivery. They do not disclose
  whether an account exists or promise that a fresh code was issued.
- Verification/reset do not log the user in. Reset invalidates existing access
  and refresh session families; ordinary password/Google login remains separate.
- Passwords accept 5–128 characters without composition rules or trimming,
  consistently across signup/login/both recovery methods.
- Legacy link routes remain temporarily. Production domain/app-link/platform
  identifiers are still unresolved.

Current responses contain a message, not challenge ID, expiry, cooldown or
remaining-attempt metadata. Mobile can follow the documented policy, but cannot
claim an authoritative per-challenge counter. Adding such metadata is a new
requirement: avoid leaking account existence, confusing suppressed sends with
issuance, or exposing challenge internals through differentiated errors.

## First delivery: favorites (approved and implemented)

- Store an account-authorized boolean on a saved note, default false.
- Set a requested boolean explicitly; never implement an invert/toggle operation.
- Include the authoritative value in note detail/list and semantic search results.
- Filter on the server inside the owned project, before returning results.
- Metadata-only behavior: no content revision/generation increment,
  no processing dispatch, embeddings write or changed relationship selection.
- Approved concurrency: row-serialized writes, last committed set wins;
  repeated same-value writes retain that value. Explicit state retries cannot
  invert twice, but retrying an older opposite intent can still overwrite a newer
  one under this policy. Optimistic metadata versioning is an alternative if needed.
- Preserve the existing unpaginated list response/order. Favorite writes retain
  `updated_at` so starring does not act as a content edit or reorder the list.
- Note/project deletion removes the state with the note. Unauthorized/missing
  note/project operations must retain non-disclosing `404` behavior.
- The final method/path, schemas and examples are published in
  [N11](api-reference.md#n11--put-v1notesnote_idfavorite) and shared DTO documentation.

Acceptance must cover default/set/unset, retry, concurrent set, list/detail/search
consistency, project filtering, deletion, ownership and absence of LLM side effects.

## Decisions to resolve in small groups

### Categories

Confirmed: private account-wide categories, shared across that account's projects,
and one optional category per note. Category deletion preserves the notes and
removes their assignment and any suggestion referring to that category. LLM
suggestions remain separate until explicitly accepted. A manual selection or
removal establishes an override; an explicit reset re-enables suggestions.
Still define naming/uniqueness/length, color values and ordering, final routes/DTOs
and stale assignment handling before the category feature is implemented.
Suggestions must remain separate from deliberate selections; subsequent processing
must not replace a user's override. Categories are not concepts or graph edges.

### Profile and onboarding

Decide name/handle requirements, format/uniqueness/rename/visibility; interest
catalog versus free text; stored preferences versus LLM effects; daily goal values
and effective dates; onboarding completion/skip persistence. No public profile,
social lookup or avatar upload is implied.

### Activity and achievements

Decide qualifying event, whether daily streaks require one note or the full goal,
account/project scope, authoritative timezone/day boundaries and current-day grace.
Specify goal/timezone changes, offline/backdated events, retries, edits,
reprocessing and deletion effects. Define achievement thresholds, historical versus
current counts, permanent versus recalculated unlocks. Voice achievements require
an explicit provenance contract; text content is not proof of voice capture.
Count actual unique persisted relationships, not graph layout proximity. Missing
activity must be unavailable rather than fabricated zero progress.

### Reminders

Decide local-only versus synchronized preference, default, time/timezone handling,
completion suppression and account-switch/logout behavior. Mobile owns deliberate
permission requests and local scheduling. Backend push delivery is not implied.

## Delivery and integration requirements

Implement independent capabilities on separate feature branches from updated dev,
with new migrations, ownership/concurrency tests and updated integration docs.
Preserve existing public behavior unless a replacement is explicitly agreed.
For every approved operation publish method/path, DTO/OpenAPI changes, validation,
nullability, authorization, statuses/errors, retry/concurrency semantics and
success/empty/error examples. Keep direct JSON/Bearer integration independent of
the ignored local testing website.

Autosave, soft deletion/Undo, PDF upload/extraction, reset-all-data and inferred
relationships are not approved by this handoff. Existing General protection,
project cascade deletion, local PDFs and reviewed transcript-to-explicit-note-save
flows remain in force.
