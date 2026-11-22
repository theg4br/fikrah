# Request and Stage Timing

## Purpose and boundaries

Measure the existing request/worker flow before changing deadlines, queue routing,
or retrieval. This step changes no endpoints, schemas, status codes, transaction
boundaries, retries, provider selection, or environment variables. It introduces
no service or dependency.

`app/core/telemetry.py` provides framework-independent timing and correlation.
`app/api/timing.py` observes ASGI delivery without buffering responses.
`app/db/timing.py` observes SQL execution without inspecting query contents.
`app/infrastructure/observability.py` owns JSON output and its bounded writer.
Application services and adapters emit timings through the core helper.

## Event fields and privacy

Each JSON event has `event=performance`, `stage`, `duration_ms`, `outcome`
(`ok`, `error`, or `cancelled`), and an internally generated `trace_id`.
HTTP events include the registered route template, method, and known status code.
An unmatched URL is labelled `unmatched`; raw URLs and query strings are excluded.
Worker note attempts include the durable UUID `job_id`, but not note/user IDs.

Never log bodies, questions, answers, transcripts, note content, embeddings,
SQL statements/parameters/results, headers, credentials, or exception messages.
Tracing IDs are generated internally rather than trusting client-supplied IDs.
Context propagates through `asyncio.to_thread` and is reset between requests and
worker attempts. HTTP and worker traces are separate: this step does not persist
or propagate an originating HTTP trace through the durable dispatcher.

## Measurements

| Stage | What it includes |
| --- | --- |
| `http.request` | Application handling through the ASGI response/disconnect, including streaming. Background/lifecycle cleanup may be included; this is not client network latency. |
| `http.first_body` | Time to deliver the first non-empty ASGI body. For SSE this can be an error/completion event; it is not necessarily a generated token. |
| `chat.generation_first_delta` | Time from starting streamed generation to delivery of its first validated answer delta. It excludes earlier retrieval and does not exist for buffered refusals or local social replies. |
| `chat.total`, `chat.persistence` | Complete chat orchestration and its final database persistence. |
| `llm.chat_generation`, `llm.note_analysis` | Generation workflows, including prompt serialization and response validation. |
| `search.total` | Ownership, retrieval, and PostgreSQL validation. |
| `search.embedding`, `search.vectors`, `search.validation` | Retrieval stages; embedding/vector times include executor scheduling. |
| `embedding.query`, `embedding.note` | Adapter calls, excluding construction and executor scheduling. |
| `note.analysis`, `note.embedding` | Worker provider construction, operation, and cleanup for each stage. |
| `worker.note_task`, `note.processing`, `note.claim`, `note.persistence` | Worker lifecycle, orchestration, claim, and fenced completion. Persistence currently includes project/note locking, Qdrant, and connection refresh. |
| `worker.dispatch`, `worker.email_batch`, `worker.vector_cleanup` | Full dispatcher, account-email batch, and vector cleanup tasks, each with a separate correlation context. |
| `note.queue_wait` | Approximate wall-clock age from `dispatched_at` to a successful processing claim. Includes publishing and claim waits, not just time inside Redis; clock skew is clamped at zero. Missing dispatch timestamps produce no sample. |
| `vectors.search`, `vectors.upsert`, `vectors.delete` | Entire adapter operation, including client setup, collection requests, and cleanup. |
| `connections.refresh`, `outbox.claim` | Connection generation and durable dispatch claims. |
| `db.query` | Cursor execution, including server lock waits. Excludes pool acquisition, some driver fetching, and transaction commit/rollback. |
| `email.smtp`, `email.delivery` | SMTP invocation and the delivery attempt (including claim/finalization). `email.delivery=ok` means normal return, not proof of SMTP success: retries/skip decisions can return normally. |
| `auth.google_verification`, `budget.consume`, `note.dispatch_notification` | Google verification, limiter access, and immediate worker notification. |
| `transcription.total` | Validated transcription use case, including provider lifetime. HTTP timing additionally covers body upload/parsing. |

Nested stages overlap: do not sum every row to estimate total latency. An `ok`
timing means the measured function returned normally, not necessarily that a
business result was supported/successful. Endpoint status and existing durable
job/delivery states remain authoritative. No SLO or alert threshold is invented.

## Output lifecycle and overload

API lifespan and Celery startup enable one writer per process. Celery prefork
children replace inherited queues/threads; shutdown is bounded to 0.5 seconds.
The 1,024-event queue uses non-blocking enqueue and drops excess events instead
of retaining unlimited memory or waiting on stdout. `dropped_records` is a
cumulative counter for queue saturation in that writer process, reported on
subsequent accepted events. Shutdown may discard pending logs; a failed output
sink can lose events. This is diagnostic logging, not a durable audit channel.
Celery uses the original stdout stream so its stdout redirection does not
reclassify performance events as WARNING. Instrumentation exceptions do not alter business results, and no external
collector/network call runs on the request path.

## Reading measurements

```bash
docker compose logs --no-log-prefix api worker |
  .venv/bin/python scripts/summarize_performance.py
```

The report groups stage/method/route/outcome and displays count, retained sample
count, nearest-rank p50/p95/p99, and maximum in milliseconds. It retains only the
last 10,000 samples per group, up to 1,024 groups; counts/maxima cover accepted
events. The dropped-record value is the largest observed per-process counter,
not a sum across processes. Quantiles summarize the supplied logs, not workload
capacity or a representative production SLO. Small samples and dropped events
must not be treated as a reliable p99.

## Regression coverage

Tests cover successful/failed/cancelled operations, thread/request isolation,
streaming delivery before completion, template-only HTTP labels, content-free SQL
success/error events, slow/saturated sinks, idempotent startup, prefork ownership,
and bounded offline summaries. Existing endpoint/provider/worker regressions
continue to define behavior.

Verification includes the full backend suite with isolated PostgreSQL and Redis;
the final test count is recorded in the pull request. API startup/health,
an exact OpenAPI comparison against `dev`, offline Alembic metadata, and all four
existing Celery registered names passed. Two synthetic notes also completed on
an actual prefork worker with distinct traces and content-free JSON output;
provider/vector calls were substitutes, so this is not an LLM latency benchmark.
