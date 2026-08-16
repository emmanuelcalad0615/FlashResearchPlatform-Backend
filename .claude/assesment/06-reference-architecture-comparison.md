# 06 — Lessons from a Mature Momentum Backend

Comparative review of `/Users/andrew/Documents/Projects/momentum/backend` ("the
reference") against this repo ("Flash"), extracting patterns worth adopting and
mistakes worth avoiding.

## What was reviewed

The reference is a **production stock-scanner backend** solving nearly the same problem
Flash is starting on: multi-market momentum screening (Minervini, CANSLIM, IPO, Volume
Breakthrough, Setup Engine), market breadth, relative strength, group rankings, served
to a live UI.

| Dimension | Reference | Flash today |
|---|---|---|
| Scale | 609 Python files, ~170k LOC | 32 files, ~800 LOC |
| Layering | `domain/` → `use_cases/` → `infra/` → `interfaces/` + `wiring/` | `packages/core` (providers/schemas/models) + `apps/` |
| Framework | FastAPI 0.109, SQLAlchemy 2.0 **sync** (psycopg2) | FastAPI, SQLAlchemy 2.0 **async** (asyncpg) |
| Database | PostgreSQL (plain) | PostgreSQL 17 + **TimescaleDB** |
| Queue | Celery 5.3 + Redis, market-scoped queue topology | none |
| Markets | 12 (US, HK, IN, JP, KR, TW, CN, CA, DE, SG, AU, MY) | US only (NYSE/NASDAQ) |
| Providers | yfinance, finviz, pykrx, akshare, baostock, AlphaVantage | Polygon.io |
| Migrations | Alembic (60+ revisions) + a second runtime migration system | Alembic (2 revisions) |
| Deployment | Multi-stage Dockerfile, non-root, split requirements | none |

The reference is roughly 200× larger. That asymmetry is the value: **it has already
made the mistakes Flash is about to make**, and the scar tissue is visible in the code.

---

## Executive summary — the seven things worth copying

1. **The feature-store publish pointer.** Precomputed snapshots with an atomic
   `latest_published` pointer swap. This is *the* pattern that makes a scanner serve
   massive concurrent reads. §1
2. **Pure domain policy functions.** `publish_policy.py` and `quality.py` are zero-I/O,
   fully deterministic, trivially testable. §2
3. **Run reproducibility hashes** (`input_hash`, `universe_hash`, `code_version`) that
   let you *skip work* by finding an existing run that already covers a request. §3
4. **`engine.dispose()` on Celery `worker_process_init`.** A one-line fix for a bug that
   is genuinely painful to diagnose. §4
5. **Queue topology that serializes external I/O** — one global data-fetch worker at
   concurrency 1, separate compute queues. Solves provider rate limits structurally. §5
6. **Distributed rate limiter keyed on Redis server time**, with in-process fallback. §6
7. **Distributed locks with heartbeats and stale-lock recovery on worker start** —
   because containers restart and `finally` blocks don't run. §7

And the single most important thing to *avoid*: the reference's `app/services/`
directory holds **251 files**, and its own README concedes the codebase is "moving
toward a ports/use-case architecture **while preserving existing service modules**."
Flash has the rare chance to never create that layer. §12

---

# PART A — Patterns to adopt

## 1. Feature store with atomic pointer publish 🔴 highest value

### What the reference does

Scheduled runs compute per-symbol snapshots into `stock_feature_daily`, then publish by
swapping a pointer in `feature_run_pointers`:

```
RUNNING ──▶ COMPLETED ──▶ [data-quality checks] ──▶ PUBLISHED
                                                └─▶ QUARANTINED
```

From `domain/feature_store/ports.py`:

```python
def publish_atomically(self, run_id: int, pointer_key: str = "latest_published") -> FeatureRunDomain:
    """Transition COMPLETED|QUARANTINED → PUBLISHED and update the pointer.

    Both the status change and the pointer swap happen in the same
    flush, so the UoW's commit() makes them visible atomically.
    """
```

Readers call `query_latest()`, which resolves the pointer. There is also a per-market
convention: `latest_published_market:{market}`.

### Why this matters enormously for Flash

My earlier assessment ([04 §1](04-scaling-and-performance.md)) said "precompute and
serve from cache." The reference shows the *correct mechanism*, and it's better than
cache-aside:

- **Readers never see a partial run.** A scan writing 10,000 symbol rows is invisible
  until the pointer moves. With naive "write into the results table," concurrent readers
  see half-computed universes — for a trading product that's not a glitch, it's wrong
  data driving decisions.
- **Instant rollback.** `repoint_published()` moves the pointer to a previous good run.
  Recovery from a bad data day is one pointer update, not a restore.
- **Quality gates before exposure.** `QUARANTINED` means "computed but not trusted."
  My earlier note about "assert ≥95% coverage before publishing" is exactly this,
  formalised as a state.
- **Cache invalidation becomes trivial.** Cache keys include the run ID, so a new
  publish naturally produces new keys. No invalidation logic, no stampede on expiry.

### Concrete change for Flash

Add alongside the `ohlcv_bars` hypertable ([04 §2](04-scaling-and-performance.md)):

```sql
CREATE TABLE feature_runs (
    id              BIGSERIAL PRIMARY KEY,
    as_of_date      DATE        NOT NULL,
    status          TEXT        NOT NULL
                    CHECK (status IN ('RUNNING','COMPLETED','PUBLISHED','QUARANTINED','FAILED')),
    run_type        TEXT        NOT NULL,
    code_version    TEXT,
    universe_hash   TEXT,
    input_hash      TEXT,
    config_json     JSONB       NOT NULL DEFAULT '{}',
    stats_json      JSONB       NOT NULL DEFAULT '{}',
    dq_results      JSONB       NOT NULL DEFAULT '[]',
    correlation_id  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

CREATE TABLE scan_results (
    run_id      BIGINT NOT NULL REFERENCES feature_runs(id) ON DELETE CASCADE,
    ticker      TEXT   NOT NULL REFERENCES instruments(ticker),
    score       NUMERIC(10,4),
    rating      SMALLINT,
    details     JSONB  NOT NULL DEFAULT '{}',
    PRIMARY KEY (run_id, ticker)
);

-- the pointer table: one row per named pointer
CREATE TABLE feature_run_pointers (
    pointer_key TEXT PRIMARY KEY,          -- 'latest_published'
    run_id      BIGINT NOT NULL REFERENCES feature_runs(id),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Publishing is then one transaction:

```python
async def publish_atomically(self, run_id: int, pointer_key: str = "latest_published") -> None:
    await self._session.execute(
        update(FeatureRun).where(FeatureRun.id == run_id).values(status="PUBLISHED")
    )
    stmt = insert(FeatureRunPointer).values(pointer_key=pointer_key, run_id=run_id)
    await self._session.execute(
        stmt.on_conflict_do_update(
            index_elements=["pointer_key"],
            set_={"run_id": run_id, "updated_at": func.now()},
        )
    )
    # caller's UoW commit makes both visible atomically
```

**Impact:** read scaling stops depending on cache TTL tuning. The API does one indexed
join through the pointer. Adding it later means migrating live result tables — do it
with the first scan table.

---

## 2. Pure domain policy functions

`domain/feature_store/publish_policy.py` is ~100 lines with **zero imports outside its
own package**:

```python
def evaluate_publish_readiness(
    status: RunStatus,
    dq_results: Sequence[DQResult],
) -> PublishDecision:
    if status != RunStatus.COMPLETED:
        return PublishDecision(allowed=False, blocking_checks=(), warnings=())
    blocking = tuple(r for r in dq_results if r.severity == DQSeverity.CRITICAL and not r.passed)
    warnings = tuple(r for r in dq_results if r.severity == DQSeverity.WARNING and not r.passed)
    return PublishDecision(allowed=len(blocking) == 0, blocking_checks=blocking, warnings=warnings)
```

Similarly `DQThresholds` validates its own invariants in `__post_init__` and every
quality check is a pure function returning a `DQResult` value object.

**Why it matters:** these are the rules that decide whether users see data. They're
tested without a database, a broker, or a network. This is what
[02 §6](02-target-architecture.md) proposed, and the reference proves it holds up at
170k LOC.

**Adopt:** momentum scoring, universe eligibility, publish gates, and data-quality
thresholds all belong in `packages/core/domain/` as pure functions over value objects.
Enforce with `import-linter` ([02 §7](02-target-architecture.md)) — the reference
maintains this discipline by convention alone, which is why its `services/` layer
eroded.

---

## 3. Reproducibility hashes → skip work instead of doing it faster

Each run stores `code_version`, `universe_hash`, `input_hash`. That enables:

```python
def find_latest_published_covering(self, *, symbols: Sequence[str], market: str | None = None):
    """Return the newest published run whose universe covers all *symbols*.

    Used by the feature-store-first custom scan path: when the criteria compile
    cleanly into queryable fields we don't need an exact signature match — we just
    need a published run that already covers every symbol the scan would process.
    """
```

The reference's README states user scans "prefer published feature-store paths and fall
back to `run_bulk_scan` when the request cannot be served from precomputed rows."

**This is the highest-leverage performance idea in the whole codebase.** A user scan
that would take minutes becomes a filtered query over an existing published run —
often single-digit milliseconds. Under massive concurrency, the cheapest computation is
the one you skip.

**Adopt:** stamp every Flash scan run with `code_version` (git SHA), `universe_hash`
(sorted tickers), and `input_hash` (criteria + as-of date). Route user requests to a
covering published run first; only compute on a genuine miss.

---

## 4. `engine.dispose()` after Celery fork 🔴 do this on day one

```python
@worker_process_init.connect
def _dispose_engine_after_fork(sender=None, **kwargs):
    """Dispose inherited SQLAlchemy engine after Celery prefork.

    When using prefork pool, the child process inherits the parent's engine and
    open file descriptors. Disposing forces each child to create fresh connections
    instead of reusing inherited DB state.
    """
    from .database import engine
    engine.dispose()
```

Without this, forked children share the parent's TCP sockets. Symptoms are ugly and
intermittent: `SSL error: decryption failed or bad record mac`, connections returning
another process's result set, random `InterfaceError`. It typically appears only under
concurrency, i.e. in production.

The reference pairs it with `worker_shutting_down` → `engine.dispose()` for clean
shutdown, and rebuilds its service container per process.

**Adopt verbatim** when Flash adds Celery ([03](03-task-queue-celery.md)). This also
reinforces [01 §2.3](01-current-state.md): an engine created at *import* time is
inherited across the fork — which is exactly the bug. Factory functions plus this signal
handler.

---

## 5. Queue topology that structurally solves rate limits

From `start_celery.sh`:

```bash
# Global data-fetch worker: handles all external fetch queues under a single
# concurrency-1 worker so yfinance-bound jobs never overlap across markets.
celery -A app.celery_app worker --concurrency=1 -Q "$DATA_FETCH_QUEUES" -n datafetch-global@%h
```

Queue lanes:

| Lane | Purpose | Concurrency |
|---|---|---|
| `data_fetch_*` | All external provider I/O | **1, global** |
| `market_jobs_<market>` | Breadth, group rankings, feature snapshots | per market |
| `user_scans_<market>` + `user_scans_shared` | User-initiated scans | per market + safety net |
| `celery` | General compute | default |

**The insight:** provider rate limits are enforced by *topology*, not just by a limiter.
One worker at concurrency 1 physically cannot exceed the serialized rate, no matter how
many tasks queue up. The distributed limiter (§6) is then a second layer for
finer-grained pacing.

The `user_scans_shared` "safety net" queue is a nice touch — any task dispatched
without an explicit market still lands somewhere a worker is listening, instead of
silently queueing forever.

**Adopt for Flash:** Polygon's free tier is 5 req/min — even more restrictive than
yfinance. My earlier proposal ([03 §4](03-task-queue-celery.md)) split
`ingest`/`scan`/`backfill`; the reference improves on it: **all Polygon-touching tasks
share one serialized lane**, while scan/compute tasks scale out freely. Backfill and
daily ingest then contend for one ordered lane rather than racing.

---

## 6. Distributed rate limiter on Redis *server* time

```lua
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local next_allowed = tonumber(redis.call('GET', KEYS[1]) or 0)
local interval = tonumber(ARGV[1])
if now >= next_allowed then
    redis.call('SET', KEYS[1], tostring(now + interval), 'EX', ttl)
    return '0'
else
    redis.call('SET', KEYS[1], tostring(next_allowed + interval), 'EX', ttl)
    return tostring(next_allowed - now)
end
```

Three details my earlier token-bucket sketch ([03 §5](03-task-queue-celery.md)) got
less right:

1. **`redis.call('TIME')` instead of client-supplied time.** Worker clocks drift; the
   Redis server clock is the single source of truth. My version passed `time.time()`
   from the client — replace it with this.
2. **Reservation semantics.** On contention it *advances* `next_allowed` and returns the
   caller's wait time, so concurrent callers are queued into distinct future slots
   rather than all retrying into the same instant.
3. **Graceful degradation.** The Python wrapper falls back to an in-process limiter when
   Redis is down and periodically retries — the pipeline slows instead of stopping. Plus
   jitter (`jitter_ms=50`) against thundering herd.

**Adopt:** this Lua script almost unchanged, keyed `ratelimit:polygon`.

---

## 7. Distributed locks with heartbeat + stale recovery

```python
@worker_ready.connect
def _clear_stale_data_fetch_lock(sender, **kwargs):
    """When containers restart, Python finally blocks don't execute, leaving the
    Redis lock key with its 2-hour TTL. This blocks all new data_fetch tasks
    until the TTL expires."""
```

The logic only clears a lock when a heartbeat proves staleness (`>30 min`), and leaves
locks with no heartbeat evidence in place — failing safe rather than stomping a live
holder.

**Why it matters:** any long-running singleton job (daily ingestion) needs a lock so two
schedulers can't double-run it. Every naive Redis-lock implementation eventually
deadlocks on `SIGKILL`. The heartbeat + conservative startup recovery is the mature
answer.

**Adopt** when Flash's daily ingestion becomes a singleton — likely Phase 3
([05-roadmap.md](05-roadmap.md)).

---

## 8. Health endpoints, done properly

```
/livez   → process liveness, no dependency checks
/readyz  → checks PostgreSQL; reports Redis as a soft dependency
/health  → deprecated alias for /readyz
```

This validates [01 §3.3](01-current-state.md) and adds a refinement worth stealing:
**Redis is a *soft* dependency**. A Redis outage degrades performance (cache misses) but
the service can still serve from Postgres — so it must not fail readiness and get pulled
from the load balancer. Hard-fail on Postgres, soft-report on Redis.

---

## 9. Market calendars as first-class domain data

The reference vendors calendar data per market (`data/market_calendars/{us,hk,jp,...}`),
pins two calendar libraries (`pandas-market-calendars`, `exchange-calendars`), and adds a
domain override mechanism:

```python
DEFAULT_CALENDAR_SESSION_OVERRIDES: tuple[CalendarSessionOverride, ...] = (
    CalendarSessionOverride("JP", date(2026, 3, 20), False),
    ...
)
REGULAR_MARKET_CLOSE_TIMES: dict[str, time] = {"US": time(16, 0), ...}
```

Even for US-only Flash, this confirms the gap I flagged in
[05](05-roadmap.md#cross-cutting-suggestions): calendar correctness is not a
library call, it's domain data with exceptions. The overrides exist because upstream
calendar libraries *are wrong sometimes*, and you need an authoritative place to correct
them. Half-days (day after Thanksgiving, Christmas Eve) matter for EOD ingestion timing.

The Dockerfile bakes calendars into the image at
`/opt/stockscanner/market-calendars` — no network dependency at runtime.

---

## 10. Infrastructure hygiene worth copying directly

**Refuse non-PostgreSQL at import** (`database.py`):

```python
if _backend_name != "postgresql" and not _allow_test_sqlite:
    raise ValueError(f"Only PostgreSQL is supported. Got '{_backend_name}'.")
```

Prevents the classic "SQLite in dev, Postgres in prod" divergence, with one explicit,
loudly-named test escape hatch (`STOCKSCANNER_TEST_ALLOW_SQLITE`).

**Document the connection math where it's configured:**

```python
# Per-process connection pool. Total Postgres connections scale with
# WEB_CONCURRENCY × (db_pool_size + db_max_overflow) plus Celery workers
db_pool_size: int = 5
db_max_overflow: int = 5
```

Same arithmetic as [04 §3](04-scaling-and-performance.md) — but placed where someone
tuning it will actually read it.

**Dockerfile:** multi-stage, `python:3.11-slim`, non-root uid 1000, venv copied from
builder, `WEB_CONCURRENCY=4` with an explanatory comment. Split requirements
(`runtime` / `server` / `test` / `theme-ml`) keep the image lean and make optional ML
deps genuinely optional.

**Celery broker resilience:**

```python
broker_connection_retry_on_startup=True,
result_backend_always_retry=True,
result_backend_max_retries=120,
```
plus a custom `RetryableRedisBackend`. Redis blips are normal in cloud environments;
without this, workers die on transient failures.

**Test markers** (`pytest.ini`): `integration`, `live_service`, `slow`, `performance`,
`load`, `allow_real_celery_dispatch`. Fast unit tests stay the default; expensive suites
are opt-in. Per-area `conftest.py` files (`tests/unit/use_cases/`,
`tests/unit/repositories/`) keep fixtures scoped.

---

## 11. Runtime service container

`wiring/bootstrap.py` defines `RuntimeServices` — a process-scoped container with lazy,
`RLock`-protected initialization, holding cache bundles, provider clients, the scan
orchestrator, task dispatcher, rate limiter, and job backend. It's bound to
`app.state` in FastAPI lifespan and rebuilt per Celery worker process after fork.

The good part is the **shape**: one composition root, process-scoped, explicitly rebuilt
across forks, with `CacheBundle` as a frozen dataclass grouping related services.

The caution: at 37,872 bytes it has become a god-object. For Flash, keep the pattern but
**split by bounded context** (`IngestionServices`, `ScanningServices`) before it reaches
that size, and prefer FastAPI `Depends` for request-scoped things — reserve the
container for genuinely process-scoped resources (pools, clients, limiters).

---

# PART B — What to avoid

## 12. The `services/` graveyard 🔴 the most important lesson

`app/services/` contains **251 Python files**. The reference's own README:

> "The backend is moving toward a ports/use-case architecture **while preserving
> existing service modules**."

So `domain/`, `use_cases/`, `infra/`, and `interfaces/` are the *new* structure, and
`services/` is the old one — both live, both used, with `bootstrap.py` importing ~50
services under `TYPE_CHECKING` to avoid circular imports. That last detail is the tell:
when your DI container needs deferred imports to avoid cycles, layering has already
been violated.

**This is the single biggest thing Flash can avoid.** Not because the reference team was
careless — this is what *always* happens when a codebase grows before its layering is
settled. Flash is at 32 files. Establishing `domain/` / `application/` / `infrastructure/`
now ([02 §3](02-target-architecture.md)), with `import-linter` enforcing it in CI, costs
about a day and prevents this outcome entirely.

**Concrete rule for Flash: there is no `services/` package.** Business logic goes in
`domain/` (pure) or `application/use_cases/` (orchestration). Anything touching I/O is an
adapter in `infrastructure/` behind a port. When you feel the urge to create
`services/foo_service.py`, that's a use case or an adapter — name it accordingly.

## 13. Configuration monolith

`config/settings.py` is **878 lines** with 54+ env vars in `.env.example`, flat on one
`Settings` class: API keys, feature flags, pool sizes, per-market batch sizes, per-market
rate limits, universe source URLs, timeouts…

```python
yfinance_batch_size_us: int | None = None
yfinance_batch_size_hk: int | None = None
...          # ×10 markets, then again for rate limits
```

**Avoid** by grouping nested settings from the start:

```python
class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DB_")
    url: str
    pool_size: int = 5
    max_overflow: int = 5

class PolygonSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POLYGON_")
    api_key: str = ""
    base_url: str = "https://api.polygon.io"
    rate_limit_per_min: int = 5

class Settings(BaseSettings):
    db: DatabaseSettings = DatabaseSettings()
    polygon: PolygonSettings = PolygonSettings()
```

Also note: per-market values that scale linearly with markets belong in a **table or
config file keyed by market**, not as N× flat fields.

## 14. Two migration systems

Alongside 60+ Alembic revisions, `app/db_migrations/` holds hand-rolled runtime
migrations (`theme_lifecycle_migration.py`, `universe_migration.py`, …). Two systems
means two mental models and no single answer to "what is the schema at commit X?"

Flash's convention — *schema changes only via Alembic* — is already correct. **Keep it
absolutely.**

## 15. Auto-migration on startup

```python
def initialize_runtime() -> None:
    action = migrate_database_to_head(engine)
```

Convenient for single-instance deploys, dangerous horizontally: N replicas starting
simultaneously all race to migrate. Alembic takes a lock so you usually get errors rather
than corruption, but startup becomes nondeterministic and a slow migration blocks every
pod's readiness.

**For Flash:** run migrations as a **separate step** — a Kubernetes init container, a
one-shot Job, or an explicit deploy stage — never in application startup. This matters
specifically *because* Flash intends to scale horizontally.

## 16. Sync-only stack — a deliberate fork in the road

The reference is fully synchronous: psycopg2, sync SQLAlchemy, sync FastAPI handlers,
Celery prefork. Flash already has asyncpg + async SQLAlchemy in
`packages/core/db/session.py`, but a **synchronous** `PolygonAdapter`
([01 §1.2](01-current-state.md)).

The reference is useful evidence: **a sync stack demonstrably works** for this workload
at 12 markets and ~10k symbols, because the work is batch and queue-driven, not
request-path. It does *not* prove sync is better — it proves sync is sufficient.

Flash's genuine choice:

| Path | Consequence |
|---|---|
| **Async API + sync Celery workers** (recommended) | Keeps the async read path (better for many idle concurrent connections, WebSocket/SSE fan-out). Zero rewrite of `PolygonAdapter`. Requires sync + async repository variants — thin, since SQLAlchemy 2.x shares query construction ([02 §5](02-target-architecture.md)). |
| **Fully sync** (mirror the reference) | Simplest mental model, one repository set. Sacrifices async's advantage for high-concurrency connection handling — which is precisely Flash's stated goal. |
| **Fully async** (ARQ/TaskIQ) | Cleanest uniformity. Requires rewriting the adapter + tests, and a less mature queue ecosystem. |

The reference's own macOS workaround (`CELERY_POOL=solo`, `OBJC_DISABLE_INITIALIZE_FORK_SAFETY`,
`PYTORCH_ENABLE_MPS_FALLBACK`) is a reminder that prefork carries platform baggage.

## 17. `task_time_limit = 86400`

A 24-hour task limit ("for very large scans like 9650 stocks") means one task processes
the entire universe. That's the opposite of the fan-out model
([03 §4](03-task-queue-celery.md)): no partial progress, no parallelism across workers,
and a single failure at hour 23 loses everything.

**Prefer** many small tasks (`group` of per-ticker or per-chunk tasks) with short time
limits. Batch ~50–100 tickers per task to amortise overhead without creating 10,000
tiny messages.

---

# PART C — Consolidated recommendations for Flash

### Additions to the existing roadmap

| Priority | Item | Phase | Ref |
|---|---|---|---|
| 🔴 | **Feature-run + pointer tables** designed with the first scan table | 2 | §1 |
| 🔴 | **No `services/` package** — `domain/` + `application/use_cases/` only, enforced by `import-linter` | 0–1 | §12 |
| 🔴 | **`engine.dispose()` on `worker_process_init`** the moment Celery lands | 3 | §4 |
| 🟠 | **Serialized Polygon lane** — one worker, concurrency 1, all provider tasks | 3 | §5 |
| 🟠 | **Redis-`TIME` Lua rate limiter** with in-process fallback + jitter | 3 | §6 |
| 🟠 | **DQ checks as pure functions** + `QUARANTINED` state before publish | 2–3 | §1, §2 |
| 🟠 | **Run hashes** (`code_version`, `universe_hash`, `input_hash`) + covering-run lookup | 3 | §3 |
| 🟠 | **Migrations as a deploy step**, never on app startup | 0 | §15 |
| 🟡 | **Nested settings groups** before config sprawls | 0 | §13 |
| 🟡 | **`/livez` + `/readyz`**, Redis as a soft dependency | 0 | §8 |
| 🟡 | **Refuse non-PostgreSQL URLs** at engine construction | 0 | §10 |
| 🟡 | **Market calendar** as domain data with an override table | 2 | §9 |
| 🟡 | **Heartbeat locks** with stale recovery for singleton jobs | 3 | §7 |
| 🟡 | **Split requirements / uv groups**, multi-stage Dockerfile, non-root | 0 | §10 |
| 🟡 | **pytest markers** (`integration`, `slow`, `load`, `performance`) | 0 | §10 |
| 🟢 | **Small fan-out tasks**, not one 24-hour mega-task | 3 | §17 |

### Where Flash is already ahead

Worth stating plainly, because the size difference makes it easy to assume otherwise:

1. **TimescaleDB.** The reference runs plain PostgreSQL and hand-rolls
   `cleanup_old_price_data` retention. Flash gets hypertables, native compression, and
   continuous aggregates ([04 §2](04-scaling-and-performance.md)) — a structural
   advantage for time-series, *if* the hypertable actually gets created.
2. **A single high-quality provider.** Polygon with a clean port beats six scraped
   sources. The reference's provider sprawl (yfinance, finviz, pykrx, akshare, baostock)
   drives much of its complexity.
3. **`Decimal` discipline enforced at the schema boundary**, from day one.
4. **Async foundation** already in place for the read path.
5. **`uv` + `pyproject.toml`** versus four `requirements-*.txt` files.
6. **A clean slate on layering** — the reference cannot realistically undo `services/`;
   Flash never has to create it.

### Closing note

The reference's greatest value isn't its architecture diagram — it's the **specific,
unglamorous production lessons** encoded in signal handlers, lock-recovery logic, and
retry policies. Things like `engine.dispose()` after fork, clearing stale locks on
worker start, and using Redis server time for rate limiting are invisible in any design
document and only learned by being paged at 3am.

Flash should copy those directly and skip the two structural mistakes the reference is
now living with: an unbounded `services/` layer and an 878-line settings monolith. Both
are cheap to avoid today and effectively permanent once established.
