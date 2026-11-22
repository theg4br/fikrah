# Note Attempt Limits and Retry Ownership

## Scope

Bound Celery note-processing attempts and prevent SDK/worker retry multiplication.
The API routes, schemas, status codes, polling states, task names, note text,
refinement approval rules, and revision/generation fencing are unchanged.
This change does not claim a faster successful model response or solve queue
congestion. The limits bound failures rather than speeding up provider inference.

## Single owner for retries

- DeepSeek/OpenRouter SDK clients use `max_retries=0` for all operations.
- Gemini text/embedding clients explicitly use `HttpRetryOptions(attempts=1)`.
  The installed SDK already made one attempt with absent retry options.
- Existing network timeouts remain 90 seconds for text/embedding calls and
  30 seconds per Qdrant request. These are network inactivity limits, not
  whole-operation deadlines; a stream receiving data can outlive a read timeout.
- Celery retries only `RetryableProcessingError`, not every `Exception`.
  The worker composition root injects dependency-error classification into the
  note pipeline; SDK, HTTP, SQLAlchemy, and Celery types stay out of its ports.
- Known transient cases: network/timeout failures, soft task expiration, HTTP
  408/409/429/5xx, invalidated database connections, connection SQLSTATE class 08,
  serialization/deadlock/lock-not-available, too many connections, and database
  shutdown/unavailable states. Qdrant transport wrappers inspect their source.
- Invalid credentials, unsupported input/model, malformed/empty structured
  output, permission/constraint errors, and unknown programming/configuration
  errors are terminal after a claimed attempt. Do not retry them just to seek
  another model result. A user may request normal note reprocessing after the
  cause is fixed.

The pipeline releases only its own attempt token before requesting a retry.
Transient failures return the current revision to `queued`; permanent failures
or exhausted retries mark it `failed` without modifying its original text.
Existing stale-attempt guards prevent a failed old worker from changing a newer
attempt, revision, or generation. Client/resource cleanup stays on the executing
thread, including error paths.

## Note task limits

| Variable | Default | Meaning |
| --- | ---: | --- |
| `FIKRAH_NOTE_SOFT_TIME_LIMIT_SECONDS` | `240` | Interrupt an attempt so its transaction/client cleanup and fenced failure transition can run. |
| `FIKRAH_NOTE_HARD_TIME_LIMIT_SECONDS` | `270` | Terminate a prefork child that cannot finish cleanup after the soft interruption. |

Both values are positive integers. Worker startup rejects `soft >= hard`, or
`hard >=` either `FIKRAH_PROCESSING_STALE_AFTER_SECONDS` (existing default 900)
or `FIKRAH_CELERY_VISIBILITY_TIMEOUT_SECONDS` (existing default 3600). The default
30-second soft-to-hard gap is cleanup allowance, not a guaranteed database timeout.
The hard bound is per attempt, not a total end-to-end job SLA.

Limits apply only to `app.worker.tasks.process_note_job`; email delivery,
dispatch, and cleanup task settings are unchanged. Run note workers using
**Celery's prefork pool on a platform with soft-limit signal support**, as in the
Linux Compose setup. Eager/solo execution and unsupported pools cannot provide
these guarantees. Beat and worker processes need the same environment values.
No `.env` or Compose changes are required to use the defaults.

## Failure and recovery boundaries

The existing retry count remains three retries after the initial attempt,
with exponential backoff and jitter. A timeout may repeat the complete analysis
and embedding pipeline; single-attempt SDK calls do not guarantee billing
idempotency at the upstream provider. Chat/search calls have no new automatic
provider retries.

A hard kill, process/container crash, or persistence failure may leave a job
`processing`; a killed process cannot reliably write a failure transition.
The durable dispatcher reclaims it after the existing processing lease expires.
An expired lease does not authorize an older attempt to persist or fail a newer
one. Stale dispatch/lease recovery can publish a new Celery delivery with a fresh
retry counter, so the current retry limit is **not a lifetime durable job budget**.
Setup/claim failures before a snapshot exists likewise depend on durable
recovery when immediate retries are unavailable or exhausted.

The existing Qdrant write remains inside the consistency lock. A timeout can
leave PostgreSQL rolled back while Qdrant has applied a write; this existing
cross-store reconciliation issue still requires the separately agreed design.
Search/chat now have [preparation budgets and bounded executor admission](../api-execution-budgets/search-and-chat-execution.md).
Cancellation-aware async clients, complete-response bounds, queue isolation, SQL
statement/lock bounds, durable lifetime attempt budgets, and quicker outbox
recovery remain separate work. Cancelling a thread await still does not stop an
already-running SDK request; its capacity remains occupied until cleanup finishes.

## Verification

- Provider constructor tests assert disabled SDK retries and preserved timeouts.
- Error classification covers SDK status/transport errors, wrapped Qdrant errors,
  and SQL connection/transaction errors.
- Persisted SQLite worker tests cover analysis/embedding/vector failures,
  permanent-vs-transient transitions, cleanup, retry exhaustion, and task scope.
- Opt-in `tests/test_worker_deadlines.py` starts actual dedicated prefork workers
  with 2/4-second test limits, isolated PostgreSQL schemas and Redis queues, and
  a blocked synthetic provider. It proves soft expiration/retry exhaustion and
  hard termination followed by lease reclaim and stale-attempt fencing. It
  performs no paid provider calls; it is not a latency/load benchmark.
- Full regression suite, Ruff, startup/health, OpenAPI comparison, Alembic metadata,
  and registered task-name checks accompany the change.

## References

- [OpenAI Python retries and timeouts](https://github.com/openai/openai-python#retries)
- [Google Gen AI retry options](https://googleapis.github.io/python-genai/genai.html#genai.types.HttpRetryOptions)
- [Celery task retries](https://docs.celeryq.dev/en/stable/userguide/tasks.html#retrying)
- [Celery worker time limits and pool restrictions](https://docs.celeryq.dev/en/stable/userguide/workers.html#time-limits)
