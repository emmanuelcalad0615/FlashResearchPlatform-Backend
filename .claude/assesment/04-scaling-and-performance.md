# 04 — Scaling for High Concurrency

Target: **massive simultaneous users**, horizontal growth. This document is ordered by
what will actually bite first.

## 1. The single most important design decision

> **No user request may ever trigger a call to Polygon, or an expensive scan
> computation, synchronously.**

Everything users read is **precomputed by workers and served from cache or an indexed
table**. Get this right and the read path scales almost linearly with pod count. Get it
wrong and you inherit an external API's rate limit as your own concurrency ceiling —
which for the free tier is *five requests per minute*, shared across your whole user
base.

This is why the read/write split matters more than any other item here:

```
WRITE PATH (workers, scheduled, slow, rate-limited)
  Polygon → PolygonAdapter → use case → upsert → TimescaleDB → precomputed scan results
                                                                        ↓
                                                              Redis (hot results)
                                                                        ↓
READ PATH (API pods, per-request, fast, no external I/O) ────────────────┘
  client → LB → stateless FastAPI pod → Redis hit (or indexed PG read) → response
```

## 2. TimescaleDB — the missing core of the data model

Currently: extension enabled, **zero hypertables**, no `ohlcv_bars` table. This is the
biggest gap. Proposed initial DDL (raw SQL, matching your existing migration style):

```sql
CREATE TABLE ohlcv_bars (
    ticker      TEXT        NOT NULL REFERENCES instruments(ticker),
    trade_date  DATE        NOT NULL,
    open        NUMERIC(18,6) NOT NULL,
    high        NUMERIC(18,6) NOT NULL,
    low         NUMERIC(18,6) NOT NULL,
    close       NUMERIC(18,6) NOT NULL,
    adj_close   NUMERIC(18,6) NOT NULL,
    volume      BIGINT      NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, trade_date),
    CONSTRAINT ohlcv_high_ge_low CHECK (high >= low),
    CONSTRAINT ohlcv_volume_non_negative CHECK (volume >= 0)
);

-- hypertable partitioned on time; 1-month chunks suit daily bars
SELECT create_hypertable('ohlcv_bars', 'trade_date', chunk_time_interval => INTERVAL '1 month');

-- the dominant query is "one ticker, a date range" → ticker first, time descending
CREATE INDEX idx_ohlcv_ticker_date ON ohlcv_bars (ticker, trade_date DESC);
```

Note the `CHECK` constraints mirror the invariants already enforced in
`OHLCVBar._check_sane()`. Enforcing them in *both* places is deliberate: the Pydantic
validator gives fast feedback at the boundary, the DB constraint guarantees no path
(including a future bulk-load script) can violate them.

### Compression and retention

Historical bars are never updated after settlement — ideal for TimescaleDB compression
(typically 90%+ on this shape of data):

```sql
ALTER TABLE ohlcv_bars SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'ticker',
    timescaledb.compress_orderby   = 'trade_date DESC'
);
SELECT add_compression_policy('ohlcv_bars', INTERVAL '90 days');
```

### Continuous aggregates

For dashboards that show weekly/monthly rollups, precompute rather than aggregating on
read:

```sql
CREATE MATERIALIZED VIEW ohlcv_weekly
WITH (timescaledb.continuous) AS
SELECT ticker,
       time_bucket('1 week', trade_date) AS week,
       first(open, trade_date)  AS open,
       max(high)                AS high,
       min(low)                 AS low,
       last(close, trade_date)  AS close,
       sum(volume)              AS volume
FROM ohlcv_bars
GROUP BY ticker, week;

SELECT add_continuous_aggregate_policy('ohlcv_weekly',
    start_offset => INTERVAL '1 month',
    end_offset   => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 hour');
```

A continuous aggregate is incrementally maintained — dashboard reads become a trivial
indexed scan of a small table instead of aggregating millions of rows per request.

⚠️ **Retrofit warning:** converting a large existing table into a hypertable requires a
full rewrite and a maintenance window. Create `ohlcv_bars` as a hypertable *from the
first migration*.

## 3. Connection pooling — the first hard ceiling

The arithmetic that surprises teams:

```
API pods × pool_size  +  workers × pool_size  ≤  postgres max_connections
   20    ×     10      +    16    ×    5       =  280 connections
```

Default Postgres `max_connections` is 100. You hit this at roughly 7 API pods. And
Postgres connections are expensive (~10 MB each) — raising `max_connections` to 1000
trades one failure mode for another.

**Solution: PgBouncer in transaction pooling mode**, sitting between app and Postgres.
Hundreds of client connections multiplex onto a few dozen server connections.

Two gotchas that will cost you a day each if unknown:

**(a) asyncpg prepared statements break under transaction pooling.** asyncpg caches
prepared statements per connection; PgBouncer hands you a different server connection
each transaction. Configure:

```python
create_async_engine(
    url,
    poolclass=NullPool,                       # let PgBouncer own pooling
    connect_args={
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
    },
)
```

**(b) RLS and `SET` do not survive transaction pooling.** A session-scoped
`SET app.current_user_id = ...` leaks to whichever client next receives that server
connection — a cross-tenant data leak. You **must** use transaction-scoped
`set_config(..., true)` / `SET LOCAL` inside an explicit transaction, exactly as shown
in [01 §1.1](01-current-state.md). This is the strongest argument for fixing the RLS
implementation *before* PgBouncer arrives, not after.

## 4. Stateless API pods

Requirements for horizontal scaling, all currently satisfiable because the API is
nearly empty — keep it that way:

- No in-memory session state, no local caches of user data, no local file writes.
- No sticky sessions required.
- Config strictly from environment.
- Graceful shutdown: `lifespan` disposes the engine, and the process handles `SIGTERM`
  so in-flight requests drain before the pod dies (see [01 §2.3](01-current-state.md)).

You need a **Dockerfile** first — there is none today ([01 §2.5](01-current-state.md)):

```dockerfile
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app

FROM base AS deps
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

FROM base AS runtime
COPY --from=deps /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY . .
RUN useradd -m -u 1000 app && chown -R app:app /app
USER app                                    # never run as root
EXPOSE 8000
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

Sizing note: with async workloads, `--workers` ≈ CPU cores; scale out with pods rather
than up with workers, since each worker holds its own connection pool (§3).

## 5. Real-time dashboards

The README promises a real-time dashboard. WebSockets are stateful, which conflicts
with stateless pods — the standard solution is a **Redis pub/sub backplane**:

```
worker finishes scan → PUBLISH scan:results {...}
                              ↓  (fan-out)
      ┌───────────────┬───────┴────────┬───────────────┐
   API pod 1       API pod 2        API pod 3      API pod N
      ↓               ↓                 ↓              ↓
   WS clients      WS clients       WS clients     WS clients
```

Any pod can serve any client because none of them owns the data — they all subscribe.

Practical guidance:

- **Prefer SSE over WebSockets** if the traffic is server→client only, which for a
  scanner dashboard it largely is. SSE is plain HTTP: it survives proxies, reconnects
  automatically, and needs far less operational care.
- **Redis Streams over pub/sub** if clients must not miss messages during a reconnect —
  pub/sub is fire-and-forget with no replay.
- **Fan-out ceiling.** Each pod holds one socket per connected client; memory and file
  descriptors bound you well before CPU. At tens of thousands of concurrent
  connections, move push to a dedicated tier or a managed service (Ably, Pusher,
  managed WebSocket gateways) rather than scaling the whole API for connection count.

## 6. Caching

Redis is provisioned and unused. The scanner read path is the obvious first consumer:

- **Cache-aside** for scan results, keyed by scan parameters, TTL slightly longer than
  the scan interval.
- **Stampede protection.** When a hot key expires under high concurrency, every request
  recomputes simultaneously. Use single-flight (a short Redis lock; the loser waits and
  re-reads) or probabilistic early expiry.
- **Cache the shape you serve**, not raw rows — store the assembled response so a cache
  hit avoids serialization work too.
- **Negative caching** for "no results" so pathological queries don't hit Postgres every
  time.

Define a `CachePort` ([02 §3](02-target-architecture.md)) so use cases don't import
`redis` directly and can be tested with a dict-backed fake.

## 7. Read replicas — later, but design for it now

When read load exceeds one primary: route reads to replicas, writes to the primary.
Cheap to prepare for, expensive to retrofit into scattered `SessionLocal()` calls.

Since all DB access will go through repositories, adding a read-only session factory is
a contained change — *provided* repositories exist. One caveat to plan for: replica lag
means data just written by a worker may not be immediately visible on a replica. For a
scanner that publishes results on a schedule this is usually acceptable; for
read-your-own-writes flows (user updates a profile, immediately re-reads it) route
those specific reads to the primary.

## 8. Observability

You cannot scale what you cannot see, and retrofitting correlation IDs is tedious:

| Layer | Add |
|---|---|
| Logs | `structlog` JSON output, correlation ID injected by middleware, propagated into Celery task headers |
| Metrics | `prometheus-fastapi-instrumentator` (RED per endpoint) + Celery queue depth and task duration |
| Traces | OpenTelemetry, auto-instrumenting FastAPI + SQLAlchemy + httpx |
| Errors | Sentry (or equivalent) in both API and workers |

The metric that will tell you the most, earliest: **Celery queue depth over time**. A
monotonically growing `ingest` queue means workers can't keep up with the schedule, and
it shows up there long before users notice stale data.

## 9. Rate limiting your own API

Distinct from limiting *outbound* Polygon calls ([03 §5](03-task-queue-celery.md)). With
many concurrent users you need per-user/per-IP limits to stop one client exhausting the
pool. Implement at the edge (ingress/API gateway/Cloudflare) if possible — cheaper than
in-process, and it protects the app from traffic it never has to see. Redis-backed
in-app limiting (e.g. `slowapi`) is the fallback.

## 10. Realistic capacity expectations

Rough, order-of-magnitude figures for planning — not benchmarks:

| Stage | Setup | Comfortable concurrent users |
|---|---|---|
| Today | 1 uvicorn process, no cache, no CDN | Hundreds (nothing to serve yet) |
| Phase 1 | 2–4 API pods, Redis cache, indexed hypertable | Low thousands |
| Phase 2 | + PgBouncer, continuous aggregates, CDN for static | Tens of thousands |
| Phase 3 | + read replicas, dedicated push tier, autoscaling | 100k+ |

The determining factor is almost never FastAPI throughput — it's database connection
exhaustion and cache hit rate. Optimise those two before anything else.
