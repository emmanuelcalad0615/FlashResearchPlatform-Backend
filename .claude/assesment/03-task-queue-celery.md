# 03 — Task Queue: Celery Evaluation and Implementation

## 1. What the workload actually is

Before choosing a tool, be precise about the jobs, because they have different shapes
and the differences drive the decision:

| Job | Trigger | Shape | Notes |
|---|---|---|---|
| Sync instrument catalog | Daily | ~10–15 paginated calls | Small. Trivially fits any queue. |
| Ingest EOD bars | Daily after close | **Fan-out ~10,000 tickers × 2 HTTP calls** | The hard one. Rate-limited, long-running, must be resumable. |
| Momentum scan / metrics | Every N minutes during market hours | CPU + DB read over the universe | Latency-sensitive; feeds the dashboard. |
| Push updates to dashboards | On scan completion | Fan-out to connected clients | Not a queue job — pub/sub. See [04 §5](04-scaling-and-performance.md). |
| Backfill history | Ad hoc | Very large fan-out | Must not starve the daily pipeline → separate queue. |

Two properties dominate: **large fan-out against a rate-limited external API**, and
**scheduled execution that must survive worker restarts**.

Critically — none of this is user-triggered. Users read *precomputed* results. That
means the queue is not on the request path, which relaxes latency requirements
enormously and is the single most important scaling decision in this document.

## 2. Does Celery fit?

**Yes, well — with one significant caveat.**

### What Celery gives you here

- **Mature scheduling** via Celery Beat (with RedBeat for HA — plain Beat is a single
  point of failure and will happily double-fire if you run two).
- **Canvas primitives** that map directly onto your fan-out:
  `group` (parallel ticker ingestion), `chord` (run the momentum scan *after* all
  ingestion completes), `chain` (catalog sync → bar ingest → scan).
- **Per-task retry with backoff**: `autoretry_for`, `retry_backoff`, `retry_jitter`.
- **Queue routing and priorities** — put backfill on its own queue with its own worker
  pool so it can't starve the daily pipeline.
- **Operational maturity**: Flower, extensive Prometheus exporters, a decade of
  production war stories, and — not a small thing — most Python engineers you hire
  will already know it.

### The caveat: sync/async impedance

Celery workers are synchronous (prefork). Your API is async (asyncpg + async
SQLAlchemy). So you get two persistence stacks.

**Why this is less bad than it sounds for *you* specifically:**

Your `PolygonAdapter` is *already synchronous* (`httpx.Client`). It was written for a
sync execution context. Adopting Celery requires **zero changes to your most complex
existing component**. Going async-native would require rewriting the adapter and its
entire test suite.

And per [02 §5](02-target-architecture.md), SQLAlchemy 2.x shares query-construction
API between sync and async, so repository duplication is thin — the executor differs,
the queries don't.

**Do not** try to bridge with `asyncio.run()` inside Celery tasks. It works in demos
and then produces event-loop-reuse bugs, connection pools bound to dead loops, and
non-obvious deadlocks under the prefork pool. If you use Celery, write sync tasks.

## 3. Alternatives, honestly compared

| Option | Async-native | Scheduling | Maturity | Verdict for you |
|---|---|---|---|---|
| **Celery** + Redis + RedBeat | ✗ (sync workers) | ✓ Beat/RedBeat | ★★★★★ | **Recommended.** Matches the existing sync adapter; best ops tooling. |
| **ARQ** | ✓ | ✓ cron jobs | ★★★ | Best choice *if* you commit to async everywhere. Simple, Redis-only, elegant. Smaller ecosystem, thinner monitoring. |
| **TaskIQ** | ✓ | ✓ | ★★ | Modern, FastAPI-like DI, pluggable brokers. Youngest — less battle-tested. |
| **Dramatiq** | ✗ | via APScheduler | ★★★★ | Cleaner API than Celery, genuinely good. Weaker scheduling story; smaller talent pool. |
| **RQ** | ✗ | needs rq-scheduler | ★★★ | Too simple for 10k-task fan-out with rate limits. |
| **Temporal** | ✓ | ✓ | ★★★★ | Best-in-class durability for multi-step resumable workflows. **Premature** — significant operational overhead for a pre-launch team. Revisit if ingestion becomes a complex multi-stage pipeline. |
| **APScheduler alone** | ✓ | ✓ | ★★★ | In-process only. Not a distributed queue — can't fan out across workers. Insufficient alone. |

### Recommendation

**Celery 5.x + Redis broker + RedBeat scheduler.**

Rationale: your adapter is already sync, the ingestion path is not latency-critical,
you already run Redis, and operational maturity matters more than architectural purity
when a small team is running production for the first time.

**Choose ARQ instead if** you'd rather rewrite `PolygonAdapter` to `httpx.AsyncClient`
now and keep one uniform async stack. That is a legitimate, defensible choice — the
codebase is small enough today that the rewrite is maybe a day. It is the *cleaner*
answer; it is not the *safer* one.

**On the broker:** Redis is fine to start and you already run it. Be aware it gives
weaker delivery guarantees than RabbitMQ (visibility-timeout semantics mean a task
that outlives `visibility_timeout` can be redelivered while still running). Two
mitigations: make every task idempotent (you want this anyway), and set
`visibility_timeout` comfortably above your longest task. Move to RabbitMQ/SQS only if
you observe real duplicate-delivery pain.

## 4. Concrete implementation

### Dependencies

```toml
# pyproject.toml
dependencies = [
    # ...existing...
    "celery[redis]>=5.4",
    "redis>=5.0",
    "celery-redbeat>=2.2",     # HA scheduler; avoids Beat single-point-of-failure
    "tenacity>=9.0",           # retry/backoff for the Polygon adapter
]
```

### Celery app

```python
# apps/worker/app.py
from celery import Celery

from packages.core.config import get_settings

settings = get_settings()

celery_app = Celery("flash_research", broker=settings.redis_url, backend=settings.redis_url)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="America/New_York",        # market timezone — deliberate, not UTC
    enable_utc=True,
    task_acks_late=True,                 # redeliver if a worker dies mid-task
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,        # fair dispatch for long, uneven tasks
    task_track_started=True,
    result_expires=3600,
    task_routes={
        "flash.ingest.*":   {"queue": "ingest"},
        "flash.scan.*":     {"queue": "scan"},
        "flash.backfill.*": {"queue": "backfill"},   # isolated: cannot starve daily work
    },
    redbeat_redis_url=settings.redis_url,
    beat_scheduler="redbeat.RedBeatScheduler",
)
```

`task_acks_late=True` + `worker_prefetch_multiplier=1` is the correct pairing for long,
variable-duration tasks: work is acknowledged only on completion (so a killed worker's
task is redelivered), and workers don't hoard queued messages.

### Tasks

```python
# apps/worker/tasks/ingest.py
from datetime import date, timedelta

from celery import group

from apps.worker.app import celery_app
from apps.worker.deps import build_ingest_use_case


@celery_app.task(
    name="flash.ingest.eod_bars_for_ticker",
    bind=True,
    autoretry_for=(TransientProviderError,),
    retry_backoff=5,          # 5s, 10s, 20s, 40s...
    retry_backoff_max=600,
    retry_jitter=True,        # decorrelate retries across 10k tasks — important
    max_retries=5,
)
def ingest_eod_bars_for_ticker(self, ticker: str, start: str, end: str) -> int:
    use_case = build_ingest_use_case()
    return use_case.execute(ticker, date.fromisoformat(start), date.fromisoformat(end))


@celery_app.task(name="flash.ingest.daily_eod")
def ingest_daily_eod() -> str:
    """Fan out one task per active ticker. Scheduled after market close."""
    tickers = load_active_tickers()
    day = (date.today() - timedelta(days=1)).isoformat()
    job = group(
        ingest_eod_bars_for_ticker.s(t, day, day) for t in tickers
    ).apply_async(queue="ingest")
    return job.id
```

`retry_jitter=True` matters more than it looks: without it, 10,000 tasks that all hit a
`429` retry in lockstep and hammer the API again simultaneously.

### Schedule

```python
# apps/worker/schedules.py
from celery.schedules import crontab

from apps.worker.app import celery_app

celery_app.conf.beat_schedule = {
    "sync-instrument-catalog": {
        "task": "flash.ingest.sync_catalog",
        "schedule": crontab(hour=3, minute=0),              # pre-market
    },
    "ingest-daily-eod": {
        "task": "flash.ingest.daily_eod",
        "schedule": crontab(hour=17, minute=30, day_of_week="mon-fri"),  # after close
    },
    "momentum-scan": {
        "task": "flash.scan.momentum",
        "schedule": crontab(minute="*/5", hour="9-16", day_of_week="mon-fri"),
    },
}
```

### Running it

```bash
# worker (per queue, scaled independently)
uv run celery -A apps.worker.app worker -Q ingest   --concurrency=8  -n ingest@%h
uv run celery -A apps.worker.app worker -Q scan     --concurrency=4  -n scan@%h
uv run celery -A apps.worker.app worker -Q backfill --concurrency=2  -n backfill@%h

# scheduler (RedBeat — safe to run 2 for HA, it holds a Redis lock)
uv run celery -A apps.worker.app beat -S redbeat.RedBeatScheduler

# monitoring
uv run celery -A apps.worker.app flower
```

## 5. The rate-limiting problem (do not skip this)

Celery's built-in `rate_limit` is **per worker process**, not global. With 8 workers
each limited to 5/min you make 40 requests/min and get throttled by Polygon anyway.

You need a **distributed token bucket in Redis**, shared by all workers:

```python
# packages/core/infrastructure/rate_limit.py
import time

import redis

_ACQUIRE = """
local tokens_key = KEYS[1]
local ts_key     = KEYS[2]
local rate       = tonumber(ARGV[1])   -- tokens per second
local capacity   = tonumber(ARGV[2])
local now        = tonumber(ARGV[3])

local tokens = tonumber(redis.call("get", tokens_key) or capacity)
local last   = tonumber(redis.call("get", ts_key) or now)

tokens = math.min(capacity, tokens + (now - last) * rate)
local allowed = tokens >= 1
if allowed then tokens = tokens - 1 end

redis.call("setex", tokens_key, 120, tokens)
redis.call("setex", ts_key, 120, now)
return allowed and 1 or 0
"""


class RedisRateLimiter:
    def __init__(self, client: redis.Redis, key: str, rate_per_sec: float, capacity: int):
        self._script = client.register_script(_ACQUIRE)
        self._keys = [f"rl:{key}:tokens", f"rl:{key}:ts"]
        self._args = [rate_per_sec, capacity]

    def acquire(self) -> bool:
        return bool(self._script(keys=self._keys, args=[*self._args, time.time()]))
```

Then in the task, if `acquire()` fails, `self.retry(countdown=...)` rather than
blocking a worker slot on a sleep.

**Design note:** the rate limiter is infrastructure, so per [02](02-target-architecture.md)
it belongs behind a port (`RateLimiterPort`) if a use case needs it — but the cleanest
placement is wrapping the `PolygonAdapter` itself (a decorator adapter), so use cases
never know rate limiting exists.

## 6. Retry/backoff in the adapter

Complementary to Celery-level retries — this handles *within-call* transience:

```python
from tenacity import (
    retry, retry_if_exception_type, stop_after_attempt,
    wait_exponential_jitter,
)

class PolygonAdapter(MarketDataProvider):
    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, RateLimitedError)),
        wait=wait_exponential_jitter(initial=1, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _get(self, url: str, params: dict | None = None) -> dict:
        resp = self._client.get(url, params={**(params or {}), "apiKey": self._api_key})
        if resp.status_code == 429:
            raise RateLimitedError(retry_after=resp.headers.get("Retry-After"))
        resp.raise_for_status()
        return resp.json()
```

Use both layers deliberately: **tenacity** for fast transient failures inside one
attempt, **Celery retries** for durable failures that should release the worker slot.

## 7. Idempotency

With `task_acks_late=True` and a Redis broker, tasks **will** occasionally run twice.
Design for it rather than trying to prevent it:

- All bar writes go through `upsert_many()` (`ON CONFLICT DO UPDATE`) — see
  [02 §5](02-target-architecture.md).
- Catalog sync upserts on `ticker` primary key.
- Never `INSERT` bare in a task; never increment counters non-transactionally.

Idempotency is what makes "at-least-once delivery" acceptable, and it's far cheaper
than pursuing exactly-once.

## 8. What Celery should *not* be used for

- **Real-time dashboard push.** Use Redis pub/sub or Streams directly —
  see [04 §5](04-scaling-and-performance.md). A task queue adds latency and offers
  nothing here.
- **Request-path work.** If a user action needs a result immediately, it doesn't belong
  in a queue. Precompute instead.
- **Cron-only trivia** where a scheduled Kubernetes `CronJob` would do. Don't run a
  broker to send one HTTP request a day.
