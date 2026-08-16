# Architecture Assessment — Flash Research Backend

Assessment date: **2026-08-14** · Commit reviewed: **`001dfd9`** (branch `app/doc`)
Reviewed against: `.claude/doc/architecture.md`, `database.md`, `communication.md`, and the
source tree itself.

## Documents

| File | Contents |
|---|---|
| [`01-current-state.md`](01-current-state.md) | What exists today, severity-ranked findings and risks |
| [`02-target-architecture.md`](02-target-architecture.md) | Clean/hexagonal architecture proposal, module layout, dependency rules |
| [`03-task-queue-celery.md`](03-task-queue-celery.md) | Celery evaluation, alternatives, concrete implementation plan |
| [`04-scaling-and-performance.md`](04-scaling-and-performance.md) | Horizontal scaling for high concurrency, TimescaleDB, caching, real-time push |
| [`05-roadmap.md`](05-roadmap.md) | Phased priorities with rationale |
| [`06-reference-architecture-comparison.md`](06-reference-architecture-comparison.md) | Lessons extracted from a mature (~170k LOC) momentum backend solving the same problem — patterns to adopt and mistakes to avoid |

## Executive summary

The foundation is **better than typical for a project this young**. The
port/adapter boundary in `packages/core/providers/` is genuine hexagonal design, the
`Decimal`-only money rule is enforced at the schema boundary, the "never delete
instruments" rule prevents survivorship bias, and CI runs migrations against a real
TimescaleDB container rather than mocking the database. Those are the decisions that
are expensive to retrofit, and they were made correctly.

The gap is that **almost nothing is wired together yet**, and a few structural choices
will become painful under the stated goal of massive simultaneous users. The honest
summary: you have a well-designed data-provider slice, an empty API, a worker that is
a `logging.info` call, and infrastructure (Redis, TimescaleDB) that is provisioned but
carries zero code.

### The five things that matter most

1. **RLS is currently inert.** The migration enables row-level security on `profiles`,
   but the app connects as `flash` — the role that *owns* those tables — and Postgres
   exempts table owners from RLS unless `FORCE ROW LEVEL SECURITY` is set. Combined
   with the fact that nothing ever issues `SET LOCAL app.current_user_id`, the
   policies do nothing today. This reads as a security control but is not one yet.
   See [01 §1.1](01-current-state.md).

2. **`PolygonAdapter` is fully synchronous** (`httpx.Client`, no `async def`). Called
   from any async context it blocks the event loop, which under concurrency stalls
   every other request on that worker. Decide deliberately whether the ingestion path
   is sync (Celery-style) or async — do not let it be accidental.
   See [01 §1.2](01-current-state.md) and [03](03-task-queue-celery.md).

3. **TimescaleDB is enabled but there are no hypertables and no time-series table.**
   The whole point of the extension is unrealised; `OHLCVBar` exists only as an
   in-memory DTO. The `ohlcv_bars` hypertable is the single most important missing
   piece of the data model. See [04 §2](04-scaling-and-performance.md).

4. **Config lives in the wrong layer.** `apps/api/core/config.py` is already imported by
   `scripts/smoke_polygon.py`, and the worker will need it too — so a *shared* concern
   currently lives inside one deployable. Move it to `packages/core/`.
   See [02 §3](02-target-architecture.md).

5. **No auth, no Dockerfile, no observability.** For a platform expecting heavy
   concurrent load these are not "later" items; auth in particular shapes the data
   model and the RLS story, so it should land before there is much surface to retrofit.

### What we recommend you *don't* change

- The `MarketDataProvider` port. It is correct. Extend the same pattern to
  persistence and caching rather than redesigning it.
- The `Decimal`/`numeric` money rule and the `is_active` soft-delete convention.
- Raw-SQL Alembic migrations. They are more verbose than `op.create_table`, but they
  make RLS policies, triggers and TimescaleDB calls explicit and reviewable, which is
  worth more than the brevity.
- Running real migrations in CI.

### Honest caveat on sizing

Several recommendations here (PgBouncer, read replicas, a dedicated push tier,
Temporal) are correct at scale and *premature today*. They are marked accordingly in
[05-roadmap.md](05-roadmap.md). Building all of it now would be the classic mistake of
paying full architectural cost before having a single user. The roadmap orders work by
what is expensive to retrofit versus what can safely wait.
