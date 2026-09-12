# 01 — Current State Assessment

Commit `001dfd9`. Every finding below was verified against the source, not inferred
from documentation.

## What actually exists

| Area | State |
|---|---|
| `apps/api` | FastAPI app with two routes: `GET /health`, `GET /`. No DB access, no auth, no middleware, no DI. |
| `apps/worker` | Skeleton. `logging.basicConfig` + one `logger.info("worker up")`. Imports nothing from `packages/core`. |
| `packages/core/providers` | **Complete and well designed.** `MarketDataProvider` ABC + `PolygonAdapter`. |
| `packages/core/schemas` | `InstrumentDTO`, `OHLCVBar`, `Quote` — frozen Pydantic, `Decimal` money, sanity validators. |
| `packages/core/models` | `Base`, `Instrument`, `Profile` SQLAlchemy 2.x models. |
| `packages/core/db` | Async engine + `async_sessionmaker`. **Never imported by any router or task.** |
| `migrations` | Two revisions: initial schema (+RLS, triggers, TimescaleDB extension) and `instruments.type`. |
| Redis | In `docker-compose.yml` and `Settings.redis_url`. **Zero Python code references it.** |
| TimescaleDB | Extension enabled in `0001`. **Zero hypertables. No time-series table exists.** |
| Auth | None. No JWT, no OAuth, no `Depends()` anywhere in the codebase. |
| Deployment | No Dockerfile, no Procfile, no K8s manifests. Only local `docker-compose` for infra. |
| Observability | None. No structured logging, metrics, tracing, or error reporting. |
| CI | Lint → migrate → test against a real TimescaleDB service container. Solid. |

---

## Severity-ranked findings

### 🔴 Critical

#### 1.1 Row-Level Security is inert — it looks like a control but isn't one

`migrations/versions/0001_initial_schema.py` does:

```sql
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY profiles_select_own ON profiles
    FOR SELECT USING (id = current_setting('app.current_user_id', true)::uuid);
```

Two independent reasons this currently protects nothing:

1. **Table ownership bypass.** The migration runs as `flash` (the `DATABASE_URL` user),
   so `flash` owns `profiles`. PostgreSQL exempts a table's owner from its RLS policies
   unless you also issue `ALTER TABLE profiles FORCE ROW LEVEL SECURITY`. The app
   connects as that same owner. Result: policies are skipped entirely.
2. **The GUC is never set.** Nothing anywhere calls
   `SET LOCAL app.current_user_id = '<uuid>'`. Even with `FORCE` enabled,
   `current_setting('app.current_user_id', true)` returns `NULL`, the predicate
   evaluates `NULL`, and every row is filtered out — i.e. it would fail closed and
   return zero rows rather than leaking, but it still wouldn't *work*.

**Fix.** Three parts, all required:

```sql
-- migration
ALTER TABLE profiles FORCE ROW LEVEL SECURITY;
```

```python
# create a non-owner application role that RLS actually applies to
CREATE ROLE flash_app LOGIN PASSWORD '...';
GRANT SELECT, INSERT, UPDATE ON profiles TO flash_app;
-- run migrations as `flash` (owner), run the app as `flash_app`
```

```python
# packages/core/db/session.py — set the GUC per transaction
@asynccontextmanager
async def user_scoped_session(user_id: UUID) -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": str(user_id)},
            )
            yield session
```

Use `set_config(..., true)` (the `true` = *local to transaction*) rather than a plain
`SET`. This matters enormously later: session-scoped `SET` leaks across clients under
PgBouncer transaction pooling, transaction-scoped does not. See
[04 §4](04-scaling-and-performance.md).

> Note the migration comment already anticipates swapping `current_setting(...)` for
> `auth.uid()` under Supabase Auth. If you go that route, Supabase provides the
> non-owner role and GUC plumbing for you — but the `FORCE` requirement still applies
> to any table you create yourself as owner.

#### 1.2 `PolygonAdapter` is synchronous and will block the event loop

```python
self._client = httpx.Client(timeout=timeout)   # sync client
def get_eod_bars(self, ticker, start, end):    # sync def
```

There is not one `async def` in the file. If this is ever called from a FastAPI route
or any coroutine, the `httpx` call blocks the entire event loop for the duration of
the network round-trip — with a 30s timeout and Polygon's free-tier latency, a single
slow call stalls *every* concurrent request served by that process.

This is not currently a live bug (nothing calls it from async code yet), which is
exactly why it should be resolved now, deliberately, rather than discovered under
load.

**Two coherent options — pick one, don't drift:**

| Option | Implication |
|---|---|
| **Keep sync**, call only from sync task workers (Celery prefork) | Zero rewrite. Ingestion runs in worker processes; the API never touches Polygon directly. Clean separation. |
| **Go async** (`httpx.AsyncClient`, `async def` port) | Uniform async stack. Requires an async-native queue (ARQ/TaskIQ) and rewriting the adapter + tests. |

Our recommendation is the first — see [03](03-task-queue-celery.md) for the full
argument. What must *not* happen is a sync adapter being called from an async route
because "it worked in testing."

#### 1.3 No authentication or authorization at all

`profiles` is modelled as 1:1 with "the auth user", but no auth exists. This is
critical not because it's missing (the project is young) but because **auth decisions
constrain the data model and the entire RLS design**, and the longer you build routes
without it, the more surface you retrofit.

Decide now: self-issued JWTs, or a managed provider (Supabase Auth / Auth0 / Clerk)?
The migration comments already gesture at Supabase. If that's the direction, adopt it
before writing user-facing endpoints, because it determines whether `profiles.id` is a
foreign key you control or an external subject claim.

---

### 🟠 High

#### 2.1 TimescaleDB is enabled but unused — no time-series table exists

`CREATE EXTENSION timescaledb` runs in `0001`, and then nothing uses it. There is no
`ohlcv_bars` table, no `create_hypertable(...)`, no compression policy, no continuous
aggregate. `OHLCVBar` lives only as an in-memory DTO returned by the adapter and
immediately discarded.

For a momentum-scanning platform, the OHLCV hypertable is *the* central table — it
will dwarf everything else in row count and drive nearly every query. Designing it
late means retrofitting partitioning and compression onto a large table, which is
painful. See [04 §2](04-scaling-and-performance.md) for a concrete DDL proposal.

#### 2.2 Shared configuration lives inside one deployable

`apps/api/core/config.py` defines `Settings`, and `scripts/smoke_polygon.py` already
imports it:

```python
from apps.api.core.config import settings
from packages.core.providers.polygon import PolygonAdapter
```

So a script that has nothing to do with the HTTP API depends on the API package. The
worker will need `polygon_api_key` and `database_url` too, and would have to do the
same. That is a layering inversion: `apps/*` are *deployables*, `packages/core` is the
shared library — config is shared.

**Fix:** move to `packages/core/config.py`; have `apps/api` and `apps/worker` import
from there. Small change now, annoying later.

#### 2.3 Import-time global side effects (engine, settings)

```python
# packages/core/db/session.py — executes at import
engine = create_async_engine(_async_url(), pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# apps/api/core/config.py — executes at import
settings = Settings()
```

Consequences:

- **Testing.** You cannot point the engine at a test database without manipulating
  `os.environ` before the first import, and cannot easily construct an isolated
  `Settings` per test.
- **Event-loop binding.** An `AsyncEngine` created at import time binds its pool to
  whichever loop first uses it. Under Celery workers, pytest-asyncio, or any process
  that runs multiple loops, this surfaces as `attached to a different loop` errors.
- **Import cost.** Importing anything from `packages.core.db` opens a connection pool,
  even in a process that never queries.

**Fix:** factory functions plus a lifespan-managed singleton.

```python
# packages/core/db/session.py
def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.async_database_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )

def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
```

```python
# apps/api/main.py
@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = create_engine(get_settings())
    app.state.session_factory = create_session_factory(engine)
    yield
    await engine.dispose()          # also fixes: no graceful shutdown today

app = FastAPI(title="Flash Research API", version="0.1.0", lifespan=lifespan)
```

Then a FastAPI dependency yields request-scoped sessions — see
[02 §4](02-target-architecture.md).

#### 2.4 No retry, backoff, or rate limiting against Polygon

`_get()` calls `raise_for_status()` and propagates. There is no handling for `429`
(the free tier is **5 requests/minute**), no exponential backoff, no jitter, no
circuit breaker.

A universe-wide ingestion is ~10,000 tickers × 2 calls for `get_eod_bars` — 20,000
requests. At 5/min that is ~67 hours, and the first `429` kills the whole run with an
unhandled `HTTPStatusError`. Even on a paid tier, transient 5xx will happen.

You need three distinct things, and they're often conflated:

1. **Retry with exponential backoff + jitter** on 429/5xx (e.g. `tenacity`).
2. **A distributed rate limiter** in Redis — per-worker limits don't bound total
   outbound rate once you scale to N workers. See [03 §5](03-task-queue-celery.md).
3. **Idempotent writes** so a retried batch doesn't duplicate rows (`INSERT ... ON
   CONFLICT DO UPDATE` keyed on `(ticker, trade_date)`).

#### 2.5 No deployment artifact

There is no Dockerfile. Local dev runs `uvicorn --reload` and Compose provides only
Postgres and Redis. Nothing describes how the API or worker is built or run in a
non-local environment, which means "horizontally scalable" is currently aspirational —
there is no unit to scale.

---

### 🟡 Medium

#### 3.1 `migrations/env.py` crashes without `DATABASE_URL`

```python
config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
```

Raises a bare `KeyError: 'DATABASE_URL'` if unset — an unhelpful failure for a new
contributor who forgot `cp .env.example .env`. Note the inconsistency: `db/session.py`
falls back to a default URL, `env.py` does not. Prefer an explicit error:

```python
url = os.environ.get("DATABASE_URL")
if not url:
    raise RuntimeError("DATABASE_URL is not set — copy .env.example to .env")
```

#### 3.2 No dependency injection wiring

Zero uses of `Depends()`. With no DB session dependency, no settings dependency, and
no provider dependency, every future route will be tempted to import module-level
globals directly — which is precisely what makes routes untestable. Establish the DI
pattern before there are routes that need it.

#### 3.3 Health check doesn't check anything

```python
@router.get("/health")
async def health_check():
    return {"status": "ok"}
```

This returns `ok` even when Postgres is unreachable. Under an orchestrator this is
actively harmful: a pod with a dead DB pool stays in the load-balancer rotation.

Split the concerns — they answer different questions:

- **Liveness** (`/health/live`): is the process alive? Keep it trivial like this.
- **Readiness** (`/health/ready`): can it serve traffic? `SELECT 1` against the pool,
  ping Redis, with a short timeout.

#### 3.4 No structured logging or observability

`apps/worker/main.py` uses `logging.basicConfig`; the API configures nothing. Under
concurrency you cannot debug what you cannot correlate — you need JSON logs with a
request/correlation ID, plus RED metrics (rate, errors, duration) per endpoint. Retro-
fitting correlation IDs across an existing codebase is tedious; adding the middleware
now is cheap.

#### 3.5 Two `tested_by` relationships, thin coverage

Only `polygon.py` and `main.py` have tests. The adapter's test suite is genuinely good
(pure translation tested with fixtures, HTTP paths with `respx`) — that pattern should
be the template. But there are no tests for models, migrations (up *and* down), or
config. Migration `downgrade()` paths in particular are never exercised; CI only runs
`upgrade head`.

#### 3.6 `Quote` schema is defined but never produced

`packages/core/schemas/market.py` defines `Quote`, and the `MarketDataProvider` port
has no method returning it. Dead code today — either add `get_quote()` to the port or
remove it until needed. Minor, but the graph flagged it and it signals intent drift.

---

### 🟢 Low / informational

- **`.python-version` says 3.13, `pyproject.toml` requires `>=3.12`, Ruff targets
  `py312`.** Not broken, but pick one and be consistent — CI uses whatever `setup-uv`
  resolves, so local and CI can silently differ.
- **`docker-compose.yml` has a hardcoded dev password.** Fine for local; make sure the
  production path uses a secret manager and that `.env` is never committed (it is
  correctly gitignored today).
- **No `conftest.py`.** As soon as tests need a database or an app fixture, you'll want
  shared fixtures with proper transaction rollback per test.
- **No pre-commit hooks.** CI catches lint, but a local hook shortens the loop.
- **Empty `packages/core/__init__.py`** while `packages/core/models/__init__.py`
  re-exports. Inconsistent barrel usage — harmless, worth standardising.
- **README is in Spanish, code comments in Spanish, docs in `.claude/doc` in Spanish,
  this assessment in English.** Fine, but agree a convention; mixed-language codebases
  get harder to onboard into as the team grows.

---

## What's genuinely good (keep doing this)

These are not padding — they're the decisions that would have been costly to fix later:

1. **The port/adapter boundary.** `MarketDataProvider` is real hexagonal architecture,
   not ceremony. Vendor lock-in to Polygon is confined to exactly one file.
2. **Separation of I/O from pure translation** inside `PolygonAdapter`
   (`_parse_instruments`, `_merge_bars` are `staticmethod`s). This is *why* the tests
   are good — the interesting logic is testable without mocking anything.
3. **`Decimal` enforced at the schema boundary**, with conversion via `str()` to avoid
   float contamination. Exactly right for money.
4. **The `adjusted`/`unadjusted` dual fetch**, merging by epoch-ms timestamp with a
   documented fallback. This is a subtle correctness issue in market data that many
   teams get wrong.
5. **Soft-delete for delisted instruments** with an explicit survivorship-bias
   rationale in a column comment. Domain knowledge encoded where it can't be lost.
6. **CI runs real migrations against real TimescaleDB.** Catches migration drift that
   mocked tests never would.
7. **Frozen Pydantic DTOs** (`ConfigDict(frozen=True)`) — immutability by default for
   data crossing boundaries.
