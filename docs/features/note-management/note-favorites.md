# Saved note favorites

## Approved behavior

Favorites are private, account-authorized note metadata. Every saved note starts
with `is_favorite: false`. The user can set true or false explicitly; there is no
toggle operation. Favorite writes do not change content, title, concepts,
refinement proposals, revision, processing generation/status or timestamps.
They do not consume the LLM request budget, dispatch processing or reindex vectors.
Keeping `updated_at` unchanged preserves existing content/processing-time ordering.

The complete mobile contract and examples are maintained in
[N11 and N02](../../mobile/api-reference.md#n11--put-v1notesnote_idfavorite).
`NoteView` and `SearchResult` both include the non-null boolean. Search reads it
from the authorized PostgreSQL row during candidate validation, never Qdrant.
Related-note and graph schemas are unchanged; open note detail for this metadata.

## Layers and consistency

- HTTP: `NoteFavoriteUpdate` requires a strict JSON boolean; routes map it to
  `NoteService.set_favorite`. The value is a scalar, so no duplicate command DTO
  is needed. `?is_favorite=true/false` filters the owned project list in SQL.
- Application: ownership and a note row lock precede the write. The service owns
  commit and snapshots the result inside the transaction.
- Persistence: `NoteRepository` updates only `is_favorite`, explicitly retaining
  `updated_at` to suppress SQLAlchemy's normal update timestamp. It never commits.
- Migration `20261126_02` adds a non-null false-default column, preserving existing
  notes. Existing owner/project indexes bound the project query; there is no new
  cross-project scan or vector payload. No old migration is edited.

Writes serialize on the same note lock used by editing/processing. Last committed
state wins. Repeating true twice remains true; repeating false twice remains false.
There is no optimistic favorite version or stale-intent rejection. Mobile should
serialize changes per note and avoid replaying an old intent after a newer one.
After an uncertain outcome, read current detail before deciding whether to retry.
Ordinary create idempotency still returns the note's current state, including
favorites; the create fingerprint is unchanged.

Deleting a note/project removes the flag with the note. Missing and foreign-owned
notes/projects return the existing non-disclosing `404`. Full-array list responses,
order, pending/failed note inclusion and no pagination remain unchanged.

## Validation and rollout

Tests cover set/unset/retry, strict validation, unauthorized/missing resources,
server filtering across projects/accounts, note/project deletion, content edits,
creation replay, unchanged timestamps/lifecycle/jobs, and current search metadata
without vector reindexing. Disposable PostgreSQL tests cover upgrade/downgrade,
existing-row defaults and concurrent opposite writes under real row locks.

Apply `alembic upgrade head` before starting the new API/workers: ORM note reads
select the added column. Existing clients may ignore the additive response field.
The interactive endpoint script discovers the new PUT operation and list query
from runtime OpenAPI; no special local web transport is required.
