# Search and Chat Execution Budgets

## Behavior and configuration

The production composition root injects separate bounded executors for semantic
retrieval and chat generation. Neither uses asyncio's shared default executor,
which remains available to Google verification, password work, and publication
helpers. Executors create their threads lazily after an API process starts.

| Environment variable | Default | Scope |
| --- | ---: | --- |
| `FIKRAH_SEARCH_EXECUTOR_WORKERS` | `8` | Concurrent blocking query-embedding or Qdrant search calls per API process. |
| `FIKRAH_LLM_EXECUTOR_WORKERS` | `8` | Concurrent blocking chat generation or localized refusal calls per API process. |
| `FIKRAH_SEARCH_TIMEOUT_SECONDS` | `100` | Retrieval after project ownership is checked and its read transaction released, including PostgreSQL candidate validation. |
| `FIKRAH_CHAT_TIMEOUT_SECONDS` | `110` | Chat preparation: ownership/history, retrieval, generation, citation/conversation checks, and any localized refusal. |

Timeouts must be finite positive numbers; capacities must be positive integers.
These are optional new variables in `.env.example`; existing names/defaults
and the local `.env` are unchanged. Worker counts are read when the composition
root loads; restart API processes after changing configuration. Capacities are
**per process**, not a global limit across API replicas or Celery workers.
These defaults are starting operational limits, not a measured production SLO.

Admission has no waiting queue: when every slot is occupied, the new operation
fails immediately. Admission is per blocking call, not a reservation for an entire chat. A chat
may already have retrieved embeddings before discovering generation is full.
A slot belongs to the submitted concurrent future, and is
released only when actual work and provider cleanup finish, or an unstarted
future is successfully cancelled. Cancelling an HTTP waiter does not free a
slot while its SDK request still runs. Reopening a pool after an app lifespan
closes also retains the admission accounting of old draining work.

## Deadlines, cancellation, and persistence

Chat uses one shared deadline. Retrieval has its own configured limit capped by
the enclosing chat deadline; generation and a possible citation-repair refusal
use the remaining chat budget. They do not each restart a full timeout.

Request budgets propagate through copied ContextVars into executor threads.
Adapters check them before issuing requests, before and after stream reads, and
before consuming a returned response. Network waits are clamped to the remaining
budget, up to the existing 90-second text/embedding and 30-second Qdrant limits.
Gemini's millisecond and Qdrant's whole-second timeout resolutions are preserved;
Qdrant rounding can allow less than one extra second at the network boundary.
Qdrant search checks the budget between collection lookup and query, and gives
the query a bounded server-side timeout. Its per-client compatibility/version
probe is disabled: that probe creates an extra background network thread outside
the executor's admission accounting. Compatibility remains a deployment concern.
No search filters, vectors, payloads, or ownership/revision validation are removed.

Python cannot terminate a running thread. An already-blocked network read can
continue until its socket timeout; provider close runs on that thread afterward.
Shutdown does not wait for those threads, but Python itself can still wait for
them at interpreter exit; the deployment supervisor owns final process limits.
The slot remains occupied, and late results cannot continue into candidate
validation or message persistence. Checks between chunks stop a live stream
from extending work indefinitely just by keeping its read timeout alive. The
SSE bridge also checks the budget while blocked on a full delta queue, so a slow
consumer does not indefinitely retain a generation thread after expiration.

Successful chat preparation returns an internal prepared reply. The final
message/source write, commit, refresh, and response construction happen outside
the generation deadline. This avoids cancelling a possibly successful commit
and then reporting a generation-timeout error. SQL operation ordering, citation
locks, concurrent-message detection, source validation, and service-owned commit
remain unchanged. A timeout during preparation rolls back rather than saving a
late user/assistant pair. Social replies use no LLM/retrieval execution slot.

These are **preparation/retrieval budgets**, not strict complete HTTP response
latency guarantees. Authentication/request budgets, search's ownership read,
rollback/connection cleanup, final chat persistence, upload/response transport,
and a slow client can add time. Database statement/lock/pool bounds remain
separate work. Coroutine cancellation relies on the database driver's cleanup
behavior. This does not add idempotency to chat messages or remove uncertainty
if a client disconnects during final commit. JSON client disconnects do not
necessarily cancel an ASGI handler; the preparation deadline still applies.

## Mobile API compatibility

- No route, schema, status mapping, nullability, authentication, or note-polling
  contract changes. JSON/Bearer clients do not depend on the local tester.
- Search timeout/overload uses the existing `503` with
  `Semantic search is temporarily unavailable`.
- Chat generation timeout/overload uses the existing `503` with
  `LLM chat is temporarily unavailable`; retrieval failures keep the existing
  project-knowledge unavailable response.
- Once SSE headers are sent, failures remain `error` events with `status: 503`;
  HTTP remains 200. No `complete` event is emitted and no message pair is saved
  when preparation expires. Previously emitted deltas remain provisional: the
  client must discard/replace them on error, as in the existing streaming contract.
- Rate limits remain separate; this does not add retries or relax grounding.
  A failed/overloaded call still consumes the already-checked request budget.

## Ownership and verification

`app/core/execution.py` defines the runner port and provider-independent deadline
context. `app/infrastructure/execution.py` owns bounded thread pools; the
composition root injects them and the app lifespan closes them without waiting
on remote calls. Services and RAG receive a runner, not a concrete executor.
Standalone service/retriever construction retains its previous `asyncio.to_thread`
default; production API wiring explicitly supplies the bounded runner. Celery
keeps its separate prefork attempt limits and retry ownership.

Tests cover independent pool capacity, context propagation, cancellation,
retained slots, failed submissions, close/reopen, nested budgets, late results,
SDK timeout clamping, closed streams, slow-consumer backpressure, and actual
ASGI JSON/SSE timeout/overload responses. Late chat results do not persist;
health/social requests remain usable under synthetic generation saturation.
The final persistence phase is verified outside the generation deadline.
Tests substitute providers and use isolated databases; they do not establish
real-model latency, throughput, quota consumption, or production sizing.

Further work: asynchronous provider transports for stronger I/O cancellation,
shared/global capacity policy if needed, Qdrant client/index improvements,
database bounds, independent Celery queues, and durable lifetime retry budgets.

## References

- [Python asyncio timeouts and thread work](https://docs.python.org/3/library/asyncio-task.html#timeouts)
- [Concurrent futures cancellation and shutdown](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Executor.shutdown)
- [OpenAI Python timeout options](https://github.com/openai/openai-python#timeouts)
- [Google Gen AI HTTP options](https://googleapis.github.io/python-genai/genai.html#genai.types.HttpOptions)
