# Celery Work Isolation and Rollout

## Queue ownership

| Registered task | Queue | Compose consumer | Default concurrency |
| --- | --- | --- | ---: |
| `app.worker.tasks.process_note_job` | `fikrah.notes` | `worker` | 4 |
| `app.worker.tasks.dispatch_pending_work` | `fikrah.dispatch` | `dispatcher-worker` | 1 |
| `app.workers.tasks.deliver_account_emails` | `fikrah.emails` | `email-worker` | 1 |
| `app.worker.tasks.delete_note_vector` | `fikrah.vectors` | `vector-worker` | 1 |
| Existing queued messages / default fallback | `celery` | `worker` | Shared with note slots |

`app/workers/routing.py` is the single task-routing definition. Registered task
publication, API name-only dispatcher notifications, and Beat use it. Task names,
arguments, JSON serialization, late acknowledgements, prefetch=1, retry policy,
processing states, ownership/revision/generation fences, and HTTP contracts stay
unchanged. No queue or message is purged or renamed.

Every role uses a separate prefork worker process and explicit `--queues`.
Long note processing cannot take execution slots from dispatch, SMTP retries,
or vector deletion. SMTP and Qdrant cleanup cannot occupy dispatcher slots.
Workers still share Redis, PostgreSQL, the host and upstream services; this is
execution-slot isolation, not a guarantee against resource or dependency outages.
An ad-hoc worker without `--queues` consumes all declared queues and defeats the
isolation. Use the Compose commands or equivalent explicit role commands.

Compose concurrency is configurable through `FIKRAH_NOTE_WORKER_CONCURRENCY`
(default 4), `FIKRAH_DISPATCH_WORKER_CONCURRENCY` (1),
`FIKRAH_EMAIL_WORKER_CONCURRENCY` (1), and `FIKRAH_VECTOR_WORKER_CONCURRENCY` (1).
Values must be positive integers. These are per-container starting capacities,
not load-tested production sizing; replace the former CPU-count-based worker
pool with measured values for each role. Compose definitions share one worker
anchor to avoid environment/build configuration drift. The note worker gets a
300-second stop grace period for its 270-second hard attempt limit.

Dispatch and Beat depend on PostgreSQL/migrations and Redis, not SMTP/Qdrant
startup health. Vector cleanup additionally waits for Qdrant; email delivery
waits for Mailpit in the default local stack. The legacy consumer retains both
local dependencies because it can receive any old task type. Initial SMTP sends
inside auth responses are unchanged; moving them fully to the outbox is separate
work. This does not make one slow email batch or vector cleanup task faster.

## Upgrade without abandoning old work

1. Keep the current Redis/PostgreSQL volumes and make the normal database backup.
   Do not purge Redis, delete `celery`, use `down -v`, or discard reserved work.
2. Pause API publishers and the single Beat scheduler, and let the old worker
   drain/warm-shut down. Hard-interrupted jobs still rely on existing durable
   processing leases and email claims for recovery.
3. Build and start the updated note/legacy, dispatcher, email and vector workers.
   Verify `inspect ping` and `inspect active_queues` report all role consumers.
4. Start the updated API and exactly one Beat scheduler after consumers are ready.
   Queue publication and consumer rollout must use the same routing version.
5. Retain the legacy consumer for queued, reserved, unacknowledged and scheduled
   retries from old deliveries. A zero ready-queue length alone is insufficient
   to prove the old queue is drained. Retries retain their delivery route.

Example commands after the deliberate drain:

```bash
docker compose up -d --build worker dispatcher-worker email-worker vector-worker
docker compose exec dispatcher-worker celery -A app.workers.celery_app:celery_app inspect active_queues
docker compose up -d --build api scheduler
```

The default stack uses Mailpit for initial and retry delivery. An external SMTP
Compose override must cover **`api`, `worker` (legacy emails), and `email-worker`**
with matching SMTP settings. Do not deploy a partial override which sends initial
mail externally and retry mail to Mailpit. The running local Gmail/auth setup is
not recreated or altered by this change.

Rollback also requires stopping upgraded publishers, retaining all five queues,
and keeping consumers for the new queues until their pending/reserved/retry work
is drained. Simply starting an old worker that only knows `celery` strands new
queue messages. No automated queue deletion or rollback is performed.

## Verification and remaining work

Unit tests exercise registered/name-only publication, Beat routing, default and
legacy routing, retry-route preservation, capacity validation and Compose's own
configuration renderer. An opt-in isolated PostgreSQL/Redis test starts four real
prefork workers with synthetic providers. It blocks the only note slot, proves
email/dispatch/vector work completes, then drains note and legacy retry work.
It sends no SMTP or paid LLM requests and makes no production capacity claim.

Partial-publication recovery still uses the existing stale dispatch timer in
this step. It is the next part of the same feature; processing crash recovery,
durable lifetime retry budgets and SQL lock/statement bounds remain separate.

## References

- [Celery routing and queue consumers](https://docs.celeryq.dev/en/stable/userguide/routing.html)
- [Celery optimization: separate long/short tasks](https://docs.celeryq.dev/en/stable/userguide/optimizing.html)
