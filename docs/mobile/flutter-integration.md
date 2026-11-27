# Flutter integration guide

This guide describes the mobile/backend boundary and transport patterns. It does not add Flutter source, choose a state-management package, or replace the mobile team's architecture.

## 1. Environment setup

Obtain these values from the backend/platform owners:

| Value | Owner / meaning |
| --- | --- |
| API base origin | Reachable API server, without an appended `/v1`; currently local development uses port 8000. |
| Google backend audience/client ID | Must match backend `GOOGLE_CLIENT_ID`; native platform registration is coordinated separately. It is not a client secret. |
| Legacy verification/reset link routing (OTP does not need it) | HTTPS destination, app association and action routing; not finalized. |
| API version/revision | Match running environment with this handbook; an old local container can expose an older contract. |

Never place `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, database passwords, JWT signing secret or SMTP credentials in Flutter. The mobile app authenticates only to the backend.

### Reach the development machine correctly

| Client | Addressing guidance |
| --- | --- |
| Desktop/curl on the backend machine | `http://127.0.0.1:8000`, assuming the stack is started. |
| Android Emulator on that machine | `http://10.0.2.2:8000`; this address aliases the host loopback. [Android documentation](https://developer.android.com/studio/run/emulator-networking-address). |
| Physical device | Obtain a reachable development endpoint or an agreed device-forwarding setup; device `localhost` is the device itself. |
| iOS simulator | Use an endpoint reachable from the Mac running it; a backend on another machine is not that simulator's localhost. |
| Production | Agreed HTTPS API origin. No production host/domain is assigned by this document. |

Current Compose publishes port 8000 only on host loopback. Entering the host's LAN IP on a phone does not by itself make that binding accessible. Coordinate access instead of exposing PostgreSQL/Redis/Qdrant or disabling transport verification.

Port 8765 is the local web testing tool, not the mobile API origin. Native Flutter does not need browser CORS/cookies to use this Bearer API. If a separate Flutter Web target is later required, browser CORS is separate work.

Android requires internet permission; development cleartext exceptions, if needed, belong only in the relevant debug configuration. Do not carry broad insecure transport settings into production. See [Flutter networking](https://docs.flutter.dev/data-and-backend/networking).

Use `/health` to distinguish basic connectivity from request/auth problems; use `/ready` for dependency diagnostics. A green liveness result does not prove every feature is usable.

## 2. Suggested client responsibilities

Use equivalent pieces in the existing mobile architecture; these names are suggestions, not backend requirements.

- Environment configuration: API origin and public platform OAuth identifiers.
- HTTP transport: JSON, multipart, SSE, timeout/cancellation and safe error parsing.
- Session coordinator: protected token storage, single-flight refresh and logout.
- Feature repositories: typed auth/project/note/search/graph/chat/transcription calls.
- Controllers/state: screen state, project/account isolation, drafts and polling subscriptions.

Backend business rules remain on the server. Client validation and button disabling improve UX but cannot replace authorization, generation fencing or rate limits.

## 3. JSON request example

Illustrative Dart transport using `package:http`; adapt to the existing client. This helper intentionally does not hide refresh or mutation replay in a generic retry loop.

```dart
import 'dart:convert';
import 'package:http/http.dart' as http;

Future<http.Response> sendJson({
  required http.Client client,
  required Uri baseOrigin,
  required String method,
  required String path,
  String? accessToken,
  String? idempotencyKey,
  Map<String, dynamic>? body,
}) {
  final request = http.Request(method, baseOrigin.resolve(path));
  request.headers['Accept'] = 'application/json';
  if (accessToken != null) {
    request.headers['Authorization'] = 'Bearer $accessToken';
  }
  if (idempotencyKey != null) {
    request.headers['Idempotency-Key'] = idempotencyKey;
  }
  if (body != null) {
    request.headers['Content-Type'] = 'application/json';
    request.body = jsonEncode(body);
  }
  return client.send(request).then(http.Response.fromStream);
}
```

Usage flow:

```dart
final response = await sendJson(
  client: client,
  baseOrigin: apiOrigin,
  method: 'POST',
  path: '/v1/projects/$projectId/notes',
  accessToken: accessToken,
  idempotencyKey: pendingDraft.idempotencyKey,
  body: {'content': pendingDraft.content, 'title': pendingDraft.title},
);
// For 201, decode NoteView and replace the local draft identity.
// For 204, do not decode JSON.
// Other statuses go through the route-aware error mapper.
```

Do not log authorization headers or private content. Close owned HTTP clients at the appropriate app/service lifetime, not in the middle of an active stream. [Flutter authorization headers](https://docs.flutter.dev/cookbook/networking/authenticated-requests).

## 4. Session coordinator

### Storage and bootstrap

- Use platform-protected credential storage for refresh tokens and persisted access tokens; never plain preference files, analytics or URLs.
- Track the access expiry from `expires_in`; use a small client clock margin if proactively refreshing. The server remains authoritative.
- Protect the token pair as a unit. Associate private caches and pending requests with the current account/session epoch.
- On restart, coordinate refresh as needed, fetch `/v1/auth/me`, then projects. Handle revoked sessions even if a cached JWT has not reached its expiry.
- Keep password, OTP code, Google ID token, email-action token and refresh token out of normal request logs and crash breadcrumbs.

### Single-flight refresh algorithm

```text
Protected request receives 401:
  1. If already retried once, stop and return authentication failure.
  2. If the request used an older access token than the one now stored:
       retry once with the current token; do not refresh it again.
  3. Otherwise join the one shared in-progress refresh Future, or create it.
  4. POST current refresh token to /v1/auth/refresh.
  5. On success, atomically persist BOTH returned tokens.
  6. Only then release waiting requests to retry once.
  7. On refresh 401, clear session/private caches and require login.
```

Do not apply this algorithm to `/auth/login`, `/auth/google`, action-token requests or `/auth/refresh` itself. Their `401`/token failures do not mean the access token needs another refresh.

If refresh times out/disconnects after sending, the old token might already have been rotated. Replaying it can revoke the family. There is no refresh-idempotency/grace-window endpoint in this release. Stop dependent requests and require reauthentication rather than automatically retrying that uncertain token.

Guard against refresh completing after logout/account switch: discard its result if the session epoch changed. An older async response must never restore a signed-out account.

### Logout and multiple devices

Each login starts a separate family. Logout revokes the supplied token's family; password reset revokes all families. Clear local private data on logout even if the network is unavailable, while reporting server-revocation uncertainty honestly.

## 5. Email actions and Google setup

### Preferred: email OTP without callback links

- Registration: submit name/email/password to `POST /v1/auth/register/otp`; on generic `202`, open a six-digit code-entry screen.
- Activation: submit `{email, code}` to `POST /v1/auth/verify-email/otp`; on `200`, show login. No session is issued here.
- Activation resend: `POST /v1/auth/resend-verification/otp` with email.
- Recovery: request `POST /v1/auth/password-reset/otp/request` with email, then submit `{email, code, new_password}` to `POST /v1/auth/password-reset/otp/confirm`. No extra verification call/ticket is needed. On success clear local sessions and show login.
- Codes are strings: preserve leading zeroes. Use numeric keyboard/one-time-code autofill where supported and allow paste of all six digits. Do not put a code in a URL, analytics or logs.
- A code expires 10 minutes after issuance. A local countdown is guidance, not server authority; account for delivery delay and app restarts. Five wrong submissions exhaust it. Generic `400` means re-enter or request another code, never refresh an access token.
- Disable resend locally for 60 seconds after a request; the backend also shares its five-send/hour/address allowance across activation/recovery and OTP/link methods. Generic `202` may suppress an actual send, so never promise a fresh code. A newly issued same-purpose action invalidates earlier codes/links.
- Keep the email in flow state so the user need not retype it; allow correction of a mistaken address. Do not store passwords or codes for session restoration.
- Password login and refresh are unchanged. Google name recovery is described below. No OTP step is added after a successful login.

These routes are independent of a production domain, app association, browser cookies and `EMAIL_ACTION_BASE_URL`. Working SMTP is still required.

### Legacy: verification and reset links


Opening an email URL is not itself a backend API success. The app must obtain the opaque action token and submit it to:

- `POST /v1/auth/verify-email` with `token`; then show login.
- `POST /v1/auth/password-reset/confirm` with `token` and `new_password`; then require login.

These routes do not require a Bearer token. Do not put action tokens into the normal auth header, decode them as JWTs, or store them as refresh tokens.

**Current routing gap:** both email purposes use the same configured base URL and append only the `token` query parameter. There is no documented `purpose`, `action` or `type` query discriminator, and the opaque token is not client-decodable. The backend/mobile teams must agree cold-start verification-versus-reset routing before production. Do not invent query parameters or finalize a custom scheme/domain in the client alone.

Verification links expire after 24 hours; reset links after one hour. Both are single-use. A new same-purpose action, whether OTP or link, retires earlier open codes/links. Cooldown/hourly suppression still returns generic acknowledgments.

### Google

The platform Google setup must obtain an ID token whose audience matches the backend configuration. Send it as JSON to `POST /v1/auth/google`; retain only the FIKRAH token pair for API authorization.

Use the name from the verified Google token when available. If a new account receives `422` with detail `Name is required to create an account`, ask for a name and repeat the exchange with `id_token` and `name`; this is not a token-refresh failure. Existing accounts may return `name: null` from `/me`; offer name completion through `PATCH /v1/auth/me` without preventing session restoration. Render names as text, never HTML.

The backend currently accepts one configured audience. Android/iOS platform IDs/signing fingerprints and plugin configuration are separate platform work, not endpoints or secret values supplied by this handbook. No browser session or testing website callback is required for mobile exchange. [Google backend authentication](https://developers.google.com/identity/sign-in/web/backend-auth).

## 6. Reliable note creation

Before sending a draft, persist a client attempt record:

```text
account identity + selected project ID + create payload + idempotency key
```

Use a random unique key, such as a UUID string. Keep the exact attempt through timeouts, access-token refresh and app restarts.

- Same intentional save + same project/body → reuse the key.
- New intentional note → new key.
- Edited draft after an uncertain save → first reconcile the old attempt, then save the changed content deliberately.
- Never allocate a new key inside a generic retry interceptor.
- Key reuse scope is per user across projects. Do not carry an attempt into a different account.
- `201` on replay is normal. The returned note can already be completed or subsequently edited.
- `409` means the key does not match that create request; do not “fix” it by generating a new key automatically.
- `410` means the previously created note was deleted. Respect that deletion.
- Backend has no idempotency-status lookup endpoint. Repeating the exact create request/key is the recovery mechanism.

Even matching retries can hit the shared request counter. Back off on `429`; never bypass it with another key.

### Other writes

Project/conversation creation and chat messages have no idempotency protocol. Generic automatic replay after a socket failure risks duplicates. Reload the relevant list/history before deciding whether to offer a user retry.

Note PATCH has no expected-revision precondition. Do not infer conflict detection from a returned revision. Serialize a screen's own saves and avoid silently overwriting a newer server result with a late response.

### Favorite metadata

Use `PUT /v1/notes/{note_id}/favorite` with `{"is_favorite":true}` or false.
Keep a per-note intent sequence and serialize writes; never replay an invert/toggle.
Use the returned `NoteView` as the confirmed state and roll back an optimistic
star on failure. After an uncertain response, reload detail before retrying.
Favorites do not increment content revision/generation or change timestamps, so
those fields cannot order two favorite responses: discard stale local requests
using their intent sequence and account/project epoch.

For the selected project's favorites view, fetch the server list with
`?is_favorite=true`; do not filter a partially loaded client cache. Omit the query
for all notes or pass false for unstarred notes. Lists remain unpaginated arrays.
Update matching detail/list/search caches from confirmed responses. A favorite
write does not require processing polling; any pre-existing processing continues.

## 7. Polling asynchronous processing

Recommended **client policy**, not a server-mandated interval:

1. Render the save response immediately.
2. For queued/processing notes, GET note detail approximately every two seconds initially.
3. Back off (for example 2, 4, 8 seconds, capped around 15) with jitter if work continues.
4. Keep at most one in-flight poll per note/account. Do not repeatedly fetch full project lists.
5. Stop on completed, failed, note 404, logout or view cancellation.
6. Pause foreground polling when the app is backgrounded; reload/poll pending notes on resume.
7. A local wait limit means “still processing,” not a server-side failure. Processing continues independently of the screen.
8. On `failed`, preserve saved content and offer explicit reprocessing; do not automatically resubmit indefinitely.

Track account/project/navigation epoch and latest local request sequence; discard stale responses. Never let an older generation response replace a newer saved generation in local state.

The backend has no websocket, push notification or processing-progress endpoint. Worker deadlines/recovery leases are operational settings, not a promised user-visible processing duration. GET polling does not consume the LLM request counter.

## 8. Refinement actions

Use the note object's last returned `processing_generation` as `expected_processing_generation`. Do not substitute revision or invent a locally incremented value.

- On successful acceptance, replace displayed saved content from the response, clear the proposal and resume polling.
- On rejection, use the response's null proposal and retain content.
- On regeneration, show queued processing.
- On a stale-generation `409`, reload and let the user review the current proposal before taking another action.
- Proposal editing is implemented but not finalized as a product feature. If enabled, its successful response advances generation without a new processing job.

No backend version history exists for reversing acceptance.

## 9. Streamed chat in Flutter

Use an HTTP **POST** stream, not a browser EventSource GET. Body and authorization match the non-streaming message route. `package:http` exposes response bytes through `Client.send`. [Client.send reference](https://pub.dev/documentation/http/latest/http/Client/send.html).

Illustrative framing parser (Dart 3 records):

```dart
import 'dart:convert';
import 'package:http/http.dart' as http;

Stream<(String, Map<String, dynamic>)> chatEvents(
  http.Client client,
  Uri uri,
  String accessToken,
  String question,
) async* {
  final request = http.Request('POST', uri);
  request.headers.addAll({
    'Authorization': 'Bearer $accessToken',
    'Content-Type': 'application/json',
    'Accept': 'text/event-stream',
  });
  request.body = jsonEncode({'content': question});
  final response = await client.send(request);
  if (response.statusCode != 200) {
    // Feed this response to the normal safe HTTP error mapper.
    await response.stream.drain<void>();
    throw StateError('Chat HTTP status: ${response.statusCode}');
  }
  final mediaType =
      response.headers['content-type']?.split(';').first.trim();
  if (mediaType != 'text/event-stream') {
    await response.stream.drain<void>();
    throw const FormatException('Expected an SSE response');
  }

  var event = 'message';
  final data = <String>[];
  final lines = response.stream
      .transform(utf8.decoder)
      .transform(const LineSplitter());
  await for (final line in lines) {
    if (line.isEmpty) {
      if (data.isNotEmpty) {
        final payload = jsonDecode(data.join('\n')) as Map<String, dynamic>;
        yield (event, payload);
      }
      event = 'message';
      data.clear();
    } else if (!line.startsWith(':')) {
      final colon = line.indexOf(':');
      final field = colon < 0 ? line : line.substring(0, colon);
      var value = colon < 0 ? '' : line.substring(colon + 1);
      if (value.startsWith(' ')) value = value.substring(1);
      if (field == 'event') event = value;
      if (field == 'data') data.add(value);
    }
  }
  // A partial trailing frame is not a successful completed exchange.
}
```

This is a framing example, not a complete production session/cancellation implementation. It correctly decodes UTF-8 across byte chunks, handles LF/CRLF lines, joins multi-line data and dispatches only at blank-line frame boundaries. [Dart LineSplitter](https://api.dart.dev/dart-convert/LineSplitter-class.html).

In the actual controller:

| Event/state | Required action |
| --- | --- |
| Before send | Keep draft; disable duplicate send for this conversation; ensure an appropriate active session. |
| `delta` | Append `data.content` to a temporary assistant buffer. |
| `complete` | Replace preview with final `assistant_message`; store returned user/assistant IDs, sources and conversation summary; mark confirmed. |
| `error` | Interpret embedded `status`/`detail`; mark draft/preview unconfirmed and reconcile history. |
| EOF without complete | Treat as uncertain. Reload conversation before offering retry. |
| User cancels/navigation | Cancel the subscription/HTTP operation appropriately; cancellation is not proof the backend did not commit. |
| New message | Choose either JSON or SSE, never both. |

Do not concatenate a refusal final answer onto a supported preview; replace it. Never show citations inferred from token text; use final `sources`. A supported/unsupported/social response may have zero delta events.

No guaranteed SSE heartbeat is emitted. An aggressive “no bytes for five seconds” timeout can interrupt retrieval before the first delta. Set connection/read/overall timeouts deliberately with the backend team; current generation preparation defaults to 110 seconds, search to 100 seconds, transcription to 90 seconds, while final database work and transfer can add time. These settings are limits, not normal response times or latency guarantees.

There is no resumable stream cursor. Do not automatically reconnect/repost the same question after EOF. Backend `409` handles concurrent generation, but client-side serialization still prevents avoidable duplicate work.

## 10. Recorded audio upload

- Record/stop/cancel on the device; support microphone denial without creating empty requests.
- Stop the approved recorder UI at 60 seconds. The current backend byte cap does not enforce duration.
- Upload one binary `file` part with the actual supported MIME type, not a JSON string, path or bare base64 data.
- Let the HTTP library produce the multipart boundary. Do not force `Content-Type: application/json` on multipart requests.
- Send no `project_id`, title or language form field. The endpoint does not accept extra text form fields.
- Preserve the local recording/draft until the user decides whether to discard it; the backend does not provide a download URL.
- Display/edit returned text, then save through the same idempotent text-note flow.

For HTTP `413/415/422`, repair the input rather than retrying identical bytes. Provider/unavailability `503` should retain the draft and allow deliberate retry.

## 11. Local state, presentation and cache invalidation

- Key private caches by user ID plus resource/project ID.
- Select projects by stable ID, not display name; clear per-project results on a switch.
- Use `DateTime.parse(value).toLocal()` for display. Derive weekday from local time; no separate day field is sent. Keep the original instant when sorting/syncing.
- Handle null note/conversation/source titles with local placeholders. Do not send placeholder text as a real title.
- After note updates/deletion/completion, invalidate relevant note list, related notes, graph and search state.
- After successful chat sends, update conversation activity/order and merge messages by server IDs, not only by matching text.
- Source taps read the current note; a missing citation target can be `404`. History text is not a frozen copy of source content.
- Render note/chat strings safely as text, or sanitize any optional Markdown renderer. The API does not promise safe HTML.
- No server sync cursor, ETag, client timestamp write or offline conflict protocol exists. Keep offline drafts clearly local until a server save is confirmed.

## 12. Handover agreement

Before mobile integration acceptance, both teams should run the [acceptance checklist](integration-acceptance.md), agree the production callback/Google configuration and resolve listed contract discrepancies. HTTP examples and the OpenAPI snapshot can drive local clients without depending on the development website.
