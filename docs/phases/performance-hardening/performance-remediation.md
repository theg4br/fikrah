# Performance Remediation

Work proceeds as independent features/fixes from `dev`, with regression coverage
and merge commits. The November 22 review identified both measured failures and
scaling risks; an idle healthy development stack does not establish load capacity.

## Sequence and status

1. **Implemented: request/stage timing and bounded diagnostic summaries.** See
   [request and stage timing](../../features/performance-observability/request-and-stage-timing.md).
   This is instrumentation, not a claim that the following bottlenecks are fixed.
2. **Implemented: single-attempt text/embedding SDK calls, classified worker
   retries, and prefork note attempt soft/hard limits.** See
   [note limits and retry ownership](../../features/processing-deadlines/note-attempt-limits-and-retries.md).
   **Implemented: search/chat preparation budgets and separate bounded API
   executors.** See [API execution budgets](../../features/api-execution-budgets/search-and-chat-execution.md).
   Pending: stronger async transport cancellation, database/complete-response
   bounds, and durable lifetime attempt budgets. Cancelling a thread await does
   not stop a running SDK read; admission retains its slot until cleanup finishes.
3. Pending: isolate dispatcher/email/vector cleanup work from long note processing;
   make partial publication and recovery timely without losing durable intents.
4. Pending: remove SMTP waiting from authentication responses while preserving
   durable delivery, retry fencing, and mobile verification/reset workflows.
5. Pending: replace full-project connection scans and reduce lock duration. Worker
   vector writes currently remain under the approved consistency lock; moving
   them requires an explicit vector-outbox/reconciliation design.
6. Pending: reconcile note/context/output token budgets and embedding chunking;
   improve provider connection reuse and Qdrant payload indexes without weakening
   user/project filters or PostgreSQL validation.
7. Pending: repair large graph/deletion parameter expansion, add query/index and
   lifecycle/retention improvements, and improve pool/statement/lock bounds.
8. Pending: pagination/lightweight note polling, readiness, operational sizing,
   and local tester responsiveness.

## Decisions requiring discussion before behavior changes

- Pagination defaults, response envelopes, and polling schema changes affect
  mobile contracts; do not silently replace current list/detail responses.
- Chunking/reindexing and cross-store reconciliation must preserve approved
  ownership, revision/generation fencing, deletion, and processing states.
- Retention periods and deletion of security/idempotency history are product /
  security decisions, not an automatic database cleanup operation.
- Avoiding processing after title-only/unchanged saves changes the approved
  processing-after-save behavior and requires confirmation.
- Do not lower grounding checks, reuse stale answers, or apply the rejected local
  keyword scope filter to make chat appear faster.

Existing local `.env`, migration history, and the ignored testing website are
outside the instrumentation change. No load/cost claims come from mocked tests;
later improvements need measurements with representative inputs and concurrency.
