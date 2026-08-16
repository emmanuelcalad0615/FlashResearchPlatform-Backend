# 05 — Prioritised Roadmap

Ordered by **cost of retrofitting later**, not by effort. Items near the top are cheap
now and expensive after there's code depending on them.

## Phase 0 — Foundations (before writing more features)

Small, mechanical, unblocks everything else. Roughly a few days.

| # | Item | Why now |
|---|---|---|
| 0.1 | Move `config.py` → `packages/core/` | Two importers today; every future worker/script adds another. [01 §2.2](01-current-state.md) |
| 0.2 | Remove import-time globals (engine, settings); add `lifespan` + `get_session` dependency | Blocks proper testing and will cause event-loop bugs the moment Celery or async tests arrive. [01 §2.3](01-current-state.md) |
| 0.3 | Fix `migrations/env.py` `KeyError` on missing `DATABASE_URL` | 5-minute fix, saves every new contributor an hour. [01 §3.1](01-current-state.md) |
| 0.4 | Split `/health/live` and `/health/ready` (ready checks DB + Redis) | Must exist before any orchestrator does. [01 §3.3](01-current-state.md) |
| 0.5 | Add `Dockerfile` + `.dockerignore` for API and worker | There is currently no deployable unit. [04 §4](04-scaling-and-performance.md) |
| 0.6 | Add `conftest.py` with DB fixtures (transaction rollback per test) | Every subsequent test benefits; retrofitting is painful. |

## Phase 1 — Security and correctness

Do **not** ship user-facing endpoints before this phase.

| # | Item | Why |
|---|---|---|
| 1.1 | **Decide the auth strategy** (Supabase Auth vs. self-issued JWT) | Determines whether `profiles.id` is yours or an external claim. Shapes everything downstream. [01 §1.3](01-current-state.md) |
| 1.2 | **Fix RLS**: `FORCE ROW LEVEL SECURITY` + non-owner app role + transaction-scoped `set_config` | Currently a security control that does nothing. [01 §1.1](01-current-state.md) |
| 1.3 | Add auth middleware + `get_current_user` dependency; wire the user ID into the DB session GUC | The mechanism that makes 1.2 real. |
| 1.4 | Decide sync-vs-async for the provider path and document it | Prevents the sync adapter being called from an async route under load. [01 §1.2](01-current-state.md) |
| 1.5 | Structured logging + correlation IDs | Cheap now, tedious later. [04 §8](04-scaling-and-performance.md) |

## Phase 2 — The data model that's missing

This is where the product actually starts existing.

| # | Item | Why |
|---|---|---|
| 2.1 | **`ohlcv_bars` hypertable** with compression policy, created as a hypertable from the first migration | Converting a large table later requires a rewrite + downtime. [04 §2](04-scaling-and-performance.md) |
| 2.2 | `BarRepository` / `InstrumentRepository` ports + SQLAlchemy implementations with `ON CONFLICT DO UPDATE` | Makes ingestion idempotent and retry-safe. [02 §5](02-target-architecture.md) |
| 2.3 | First real use case: `IngestEodBars`, `SyncInstrumentCatalog` | Proves the layering end to end. |
| 2.4 | Retry/backoff in `PolygonAdapter` (`tenacity`) + typed provider errors | The first `429` currently kills an entire ingestion run. [03 §6](03-task-queue-celery.md) |
| 2.5 | Test migration `downgrade()` in CI, not just `upgrade head` | Downgrades are written but never exercised. |

## Phase 3 — Background processing

| # | Item | Why |
|---|---|---|
| 3.1 | Celery + Redis + RedBeat; queues split `ingest` / `scan` / `backfill` | Backfill must not starve the daily pipeline. [03 §4](03-task-queue-celery.md) |
| 3.2 | Distributed Redis token-bucket rate limiter for Polygon | Celery's per-worker `rate_limit` does not bound total outbound rate. [03 §5](03-task-queue-celery.md) |
| 3.3 | Scheduled catalog sync + daily EOD ingestion (`group` fan-out) | The core write path. |
| 3.4 | Flower + queue-depth metrics/alerting | Growing queue depth is the earliest signal of trouble. |
| 3.5 | Momentum scan use case writing precomputed results | Enables the read path to avoid computation entirely. |

## Phase 4 — Read path and scale

| # | Item | Why |
|---|---|---|
| 4.1 | `CachePort` + Redis cache-aside for scanner results, with stampede protection | Redis is provisioned and unused. [04 §6](04-scaling-and-performance.md) |
| 4.2 | Scanner/instrument read endpoints, served purely from cache/precomputed tables | The "never call Polygon on the request path" rule. [04 §1](04-scaling-and-performance.md) |
| 4.3 | Continuous aggregates for weekly/monthly rollups | Turns dashboard reads into trivial indexed scans. [04 §2](04-scaling-and-performance.md) |
| 4.4 | SSE (preferred) or WebSocket push with Redis pub/sub backplane | Keeps API pods stateless. [04 §5](04-scaling-and-performance.md) |
| 4.5 | Per-user API rate limiting at the edge | Stops one client exhausting the pool. [04 §9](04-scaling-and-performance.md) |
| 4.6 | `import-linter` in CI | Locks in the architecture before it erodes. [02 §7](02-target-architecture.md) |

## Phase 5 — Scale-out (only when metrics justify it)

Explicitly **premature today**. Trigger conditions given so you don't build early.

| # | Item | Trigger |
|---|---|---|
| 5.1 | PgBouncer (transaction mode) | Approaching `max_connections`; ~7+ API pods. [04 §3](04-scaling-and-performance.md) |
| 5.2 | Read replicas | Read load saturates the primary. |
| 5.3 | Dedicated push tier / managed WebSocket service | Tens of thousands of concurrent connections. |
| 5.4 | Horizontal pod autoscaling | Traffic is variable enough to matter. |
| 5.5 | Temporal for multi-stage ingestion workflows | Only if Celery canvas becomes unmanageable. [03 §3](03-task-queue-celery.md) |

---

## Cross-cutting suggestions

### Testing
- The `test_polygon_adapter.py` pattern — pure translation tested with fixtures, HTTP
  paths with `respx` — should be the template for every adapter. It's genuinely good.
- Add contract tests: any `MarketDataProvider` implementation must pass one shared
  suite. That's the payoff of having a port; without it the abstraction is untested.
- Test use cases with in-memory fake repositories (no DB) — keeps the suite fast as it
  grows.

### Developer experience
- `pre-commit` with ruff (lint + format) — shortens the CI feedback loop.
- A `Makefile` or `justfile` for the common incantations (`make dev`, `make test`,
  `make migrate`); the README already lists them, codify them.
- Reconcile `.python-version` (3.13) with `requires-python >=3.12` and Ruff's `py312`
  target so local and CI can't silently diverge.

### Data engineering
- **Corporate actions.** You fetch adjusted and unadjusted closes — good. But splits
  and dividends retroactively change *historical* adjusted prices. Plan how you
  re-adjust history when Polygon revises it, or backtests silently drift.
- **Market calendar.** Weekends, holidays, half-days. Ingesting "yesterday" naively on
  a Monday or after Thanksgiving produces empty results that look like failures.
  `pandas-market-calendars` or an explicit calendar table.
- **Data quality gates.** Before publishing scan results, assert coverage (e.g. "≥95% of
  active tickers have a bar for date D"). Publishing a scan built on 40% coverage is
  worse than publishing nothing.
- **Point-in-time correctness.** Since delisted instruments are retained (good), make
  sure scans filter by "active as of date D", not "active today" — otherwise you have
  reintroduced survivorship bias through the query rather than the schema.

### Documentation
- Keep `.claude/doc/` accurate as the code lands — it's currently precise about what
  *doesn't* exist, which is unusually honest and worth preserving.
- Add ADRs for the decisions in this assessment (auth choice, Celery vs. ARQ,
  sync vs. async provider path). Future contributors will ask "why" and the reasoning
  is easy to lose.
- `.claude/` is gitignored, so none of this is shared with the team. Consider moving
  `doc/` and `assesment/` somewhere tracked (e.g. `docs/`) if others should read them.

---

## If you only do five things

1. Fix RLS ([01 §1.1](01-current-state.md)) — it currently protects nothing.
2. Create the `ohlcv_bars` hypertable ([04 §2](04-scaling-and-performance.md)) — the
   product has no data model without it.
3. Remove import-time globals and add DI ([01 §2.3](01-current-state.md)) — blocks
   testing and will break under Celery.
4. Decide and implement auth ([01 §1.3](01-current-state.md)) — it constrains
   everything downstream.
5. Add retry/backoff + a distributed rate limiter before the first real ingestion run
   ([03 §5](03-task-queue-celery.md)) — otherwise the first run dies on a `429`.
