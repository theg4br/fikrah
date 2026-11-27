# Email OTP activation and password recovery

## Approved scope

Email OTP is the preferred mobile activation/recovery flow. Password and Google
sign-in continue unchanged; no additional OTP is required on each login.
Legacy action-link endpoints remain available during migration. Flutter UI is
outside this backend feature.

The complete request/response contracts live in the
[mobile endpoint reference](../../mobile/api-reference.md#otp-activation-and-recovery-preferred).

## Lifecycle and implementation

- Six cryptographically generated ASCII digits, represented as a string to keep
  leading zeroes. Ten-minute expiry from issuance, single use, five failed
  submissions per code. Failure returns a generic `400` without distinguishing
  expiry, reuse, exhaustion or an unknown account.
- Existing 60-second cooldown and five-email/hour/address allowance are shared
  by verification and recovery, across both OTP and legacy link routes. Generic
  `202` acknowledgments do not guarantee delivery or reveal account existence.
- Issuing a new action retires older open actions for the same purpose, including
  actions sent through the other delivery method. A suppressed request does not
  invalidate the current code. Verification and recovery codes are separate.
- Registration, verification and reset do not issue session tokens. After
  verification or password replacement, the client uses ordinary login. Reset
  takes email, code and new password in one confirmation; there is no separate
  code-validation ticket or extra confirmation endpoint.
- A reset revokes every existing access/refresh session family. Google authority,
  account linking, unverified-password protection and General creation remain
  unchanged. Google-only accounts do not acquire passwords through recovery.

`AuthService` owns account mutations and transactions. `EmailOtpService` owns code
issuance/consumption, with SQL queries in `AuthRepository`. The HTTP layer maps
explicit schemas to application commands; it contains no OTP validation policy.
The existing user-before-action row-lock order serializes concurrent attempts,
resends, password reset, Google linking and refresh.

`core/email_otp.py` generates codes and computes an HMAC using the private signing
secret with an OTP-specific domain and a random per-action handle. Database
records contain a keyed digest, not a plain or reversibly encrypted code. A
database-only leak cannot enumerate short codes without that secret. As with
JWTs/pending action emails, secret rotation invalidates outstanding codes.

Durable pending email intents necessarily contain an encrypted delivery copy.
They reference the action's random handle rather than a hash of the short code.
The existing outbox validates expiry/retirement before dispatch, retries SMTP
failures, and scrubs encrypted codes on successful sending/cancellation.
Delivery is at least once; duplicate messages can occur after an SMTP/ack crash.
Neither codes nor ciphertext are logged. SMTP acceptance is not inbox delivery.

The new `otp.html` template extends the existing branded email layout and embeds
the existing logo. Codes appear in the plain-text and HTML bodies, never in a
URL or subject. OTP mail requires SMTP configuration, but does not require
`EMAIL_ACTION_BASE_URL`. Legacy link delivery still requires that setting.

## Rollout

Migration `20261126_01` adds nullable `otp_digest` and a durable `failed_attempts`
counter to existing email actions. It preserves legacy action rows and adds no
environment variables or dependencies. Apply `alembic upgrade head` before
starting the updated API and email workers. Do not mix old/new workers: old mail
adapters cannot deliver OTP-purpose intents.

Use the new OTP routes explicitly; existing `/register` and link routes retain
their legacy link behavior. Both registration routes now require the [account display name](account-display-name.md). Migration timing for mobile clients and eventual removal
of legacy routes require a separate decision.

## Verification

Coverage includes leading zeroes, keyed/action-bound digests, expiry, replay,
persisted failed guesses, resend/cross-method invalidation, shared send limits,
generic responses, Google linking, session revocation, durable delivery,
template rendering, PostgreSQL migration/rollback and concurrent submissions.
Tests capture email or use local SMTP; they do not prove delivery to Gmail or
another external inbox.

Reference: [OWASP password recovery guidance](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html).
