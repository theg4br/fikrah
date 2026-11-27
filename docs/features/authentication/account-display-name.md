# Account display names

## Approved behavior

- Accounts have `name` only; no username or new login identifier.
- Both email registration routes require a name. It is trimmed, contains 1–120 Unicode code points and cannot contain a null character. Names may repeat and retain their spelling/case. The length bound fits one display-name field; no first/last-name split or language restriction is imposed.
- New Google accounts use a usable `name` claim from the verified ID token. If it is absent or invalid, mobile supplies the optional fallback `name` alongside the ID token. Without either, `POST /v1/auth/google` returns `422` with `detail: "Name is required to create an account"` before creating any account, project, email or session.
- The provider claim takes precedence over the fallback for creation. Subsequent Google logins preserve an existing saved name, or fill a null name when one is available. Google email-authority/linking checks still apply before account creation.
- Existing accounts keep `name: null` and remain able to sign in. Migration `20261127_01` adds a nullable column without fabricating names or changing credentials.
- `GET /v1/auth/me` includes `name`. Authenticated `PATCH /v1/auth/me` accepts only `{ "name": "..." }` and returns the updated account summary. Null, blank, invalid and unrelated fields return `422`; no other user's identifier is accepted. Updates serialize on the account row; the last completed write wins. Tokens, email, verification and passwords are unchanged.

## Integration and rollout

Update the mobile registration form to send `name` before releasing this backend change. Login still accepts email/password only. Existing email clients that omit a name at registration receive `422`; legacy login, OTP and recovery remain unchanged. Run the new migration before starting the new API/workers. The column can remain nullable permanently for legacy accounts.

Use the [API reference](../../mobile/api-reference.md) and [shared models](../../mobile/data-models-and-errors.md) for payloads. Refresh account caches from the profile response and render names as text. Names confer no permissions and are not sent to LLM providers.

## Validation

Coverage includes both registration methods, duplicate/Unicode/bounded names, required/invalid names, old-account login, Google missing-name recovery with no partial writes, preserved user choices, authenticated profile updates and account isolation. PostgreSQL migration coverage upgrades an existing account, verifies its unchanged credentials and nullable name, then tests the additive migration's downgrade in an isolated schema.

Google documents that the name claim is not guaranteed: [OpenID Connect claims](https://developers.google.com/identity/openid-connect/openid-connect#an-id-tokens-payload).
