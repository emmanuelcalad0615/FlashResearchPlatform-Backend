# 02 — Target Architecture

## 1. Guiding principle

You already have hexagonal architecture in one slice (`providers/`). The proposal is
not to introduce a new paradigm — it is to **apply the pattern you already chose
consistently**, especially to persistence, and to make the dependency direction
explicit and enforced.

The rule, in one line:

> **Dependencies point inward. The domain knows nothing about FastAPI, SQLAlchemy,
> Celery, Redis, or Polygon.**

## 2. Why this matters for *your* specific goals

Clean architecture is often cargo-culted. Here are the concrete, non-theoretical
payoffs for this project:

| Your stated goal | What the layering buys you |
|---|---|
| Massive simultaneous users | The API becomes a thin read layer over precomputed data. Business logic lives in use cases that can run in *either* an API pod or a worker, so you can move work off the request path without rewriting it. |
| Horizontal growth | Stateless delivery layers. All state is in Postgres/Redis, so any pod can serve any request. |
| Swap data vendors | Already solved by `MarketDataProvider`. Extend to repositories so you can also swap/mock persistence. |
| Test without infrastructure | Domain and use cases test with in-memory fakes — no DB, no network, no Docker. Fast test suite that stays fast at 10× the code. |
| Two deployables sharing code | A single `packages/core` that neither `apps/api` nor `apps/worker` has to reach around. |

## 3. Proposed module layout

```
packages/core/
  config.py                    # ← MOVED from apps/api/core/config.py (see 01 §2.2)

  domain/                      # Pure. Zero I/O. Zero framework imports.
    instrument.py              #   Instrument entity, Exchange VO, InstrumentType VO
    bar.py                     #   OHLCVBar entity + invariants (high >= low, vol >= 0)
    momentum.py                #   Momentum/scan scoring — the actual business value
    errors.py                  #   Domain exceptions

  application/                 # Use cases + PORTS (interfaces only)
    ports/
      market_data.py           #   MarketDataProvider  (move existing providers/base.py here)
      repositories.py          #   InstrumentRepository, BarRepository, ProfileRepository
      cache.py                 #   CachePort
      clock.py                 #   ClockPort  (never call datetime.now() in domain code)
      unit_of_work.py          #   UnitOfWork
    use_cases/
      sync_instrument_catalog.py
      ingest_eod_bars.py
      run_momentum_scan.py
      get_scanner_results.py

  infrastructure/              # ADAPTERS. Implements the ports above.
    persistence/
      models/                  #   SQLAlchemy ORM (existing models/ moves here)
      repositories/            #   SqlAlchemyInstrumentRepository, SqlAlchemyBarRepository
      unit_of_work.py
      session.py               #   engine/session factories (no import-time globals)
    providers/
      polygon.py               #   existing adapter
    cache/
      redis_cache.py           #   implements CachePort
    clock.py                   #   SystemClock

apps/api/                      # Delivery: HTTP
  main.py                      #   app factory + lifespan
  deps.py                      #   composition root — wires ports to adapters
  routers/
  schemas/                     #   request/response models (NOT domain DTOs)

apps/worker/                   # Delivery: background jobs
  app.py                       #   Celery app (see 03)
  deps.py                      #   composition root for worker context
  tasks/
  schedules.py
```

### On the DTO-vs-entity question

Today `packages/core/schemas/` holds `InstrumentDTO` / `OHLCVBar` / `Quote`, and they
serve as the provider↔system contract. Keep them — but be deliberate about the three
distinct shapes that will emerge, because conflating them is a common source of pain:

| Shape | Lives in | Purpose |
|---|---|---|
| **Domain entity** | `domain/` | Business rules and invariants. No serialization concerns. |
| **Provider DTO** | `application/ports/` (or alongside) | Canonical wire-shape from external vendors. What `InstrumentDTO` is today. |
| **API schema** | `apps/api/schemas/` | Public HTTP contract. Versioned. Changing it is a breaking API change. |

Pragmatic advice: **do not create all three on day one.** For a small team, letting
the provider DTO double as the domain entity is a reasonable simplification while the
domain logic is thin. Split them at the point where domain invariants start diverging
from the vendor's shape — which for you will be when momentum scoring lands. The cost
of splitting later is a mechanical refactor; the cost of three parallel hierarchies
today is real friction for no benefit.

## 4. Composition root and dependency injection

FastAPI's `Depends` is your DI container — you don't need a third-party one.

```python
# apps/api/deps.py
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.application.ports.repositories import BarRepository
from packages.core.config import Settings
from packages.core.infrastructure.persistence.repositories import SqlAlchemyBarRepository


@lru_cache
def get_settings() -> Settings:
    return Settings()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.session_factory
    async with factory() as session:
        yield session


def get_bar_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BarRepository:                       # ← return the PORT type, not the concrete class
    return SqlAlchemyBarRepository(session)


BarRepo = Annotated[BarRepository, Depends(get_bar_repository)]
```

```python
# apps/api/routers/scanner.py
@router.get("/scanner/momentum", response_model=list[ScannerResultResponse])
async def momentum_scan(repo: BarRepo, cache: CacheDep) -> list[ScannerResultResponse]:
    results = await GetScannerResults(repo, cache).execute()
    return [ScannerResultResponse.from_domain(r) for r in results]
```

The route depends on the **port type**. Swapping in a fake for tests is a one-line
`app.dependency_overrides[get_bar_repository] = lambda: FakeBarRepository()`.

## 5. Repository + Unit of Work

Two things this solves: use cases stop knowing about SQLAlchemy, and transaction
boundaries become explicit rather than accidental.

```python
# packages/core/application/ports/repositories.py
from abc import ABC, abstractmethod
from datetime import date

from packages.core.domain.bar import OHLCVBar


class BarRepository(ABC):
    @abstractmethod
    async def upsert_many(self, bars: list[OHLCVBar]) -> int:
        """Idempotent bulk write keyed on (ticker, trade_date). Returns rows affected."""

    @abstractmethod
    async def get_range(self, ticker: str, start: date, end: date) -> list[OHLCVBar]: ...
```

```python
# packages/core/infrastructure/persistence/repositories/bar_repository.py
from sqlalchemy.dialects.postgresql import insert


class SqlAlchemyBarRepository(BarRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_many(self, bars: list[OHLCVBar]) -> int:
        if not bars:
            return 0
        stmt = insert(OHLCVBarModel).values([b.model_dump() for b in bars])
        stmt = stmt.on_conflict_do_update(
            index_elements=["ticker", "trade_date"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "adj_close": stmt.excluded.adj_close,
                "volume": stmt.excluded.volume,
            },
        )
        result = await self._session.execute(stmt)
        return result.rowcount
```

The `ON CONFLICT DO UPDATE` is what makes ingestion **safely retryable** — re-running
a failed batch corrects rather than duplicates. This directly addresses
[01 §2.4](01-current-state.md).

### A note on sync + async repositories

If you adopt Celery (sync workers) alongside an async API, you'll need both a sync and
an async implementation of each repository. This is less duplication than it sounds:
SQLAlchemy 2.x uses the **same `select()` / `insert()` construction API** for both —
only execution differs. Factor query construction into shared module-level functions
and keep the two executors thin:

```python
def _bars_in_range_stmt(ticker: str, start: date, end: date):
    return select(OHLCVBarModel).where(
        OHLCVBarModel.ticker == ticker,
        OHLCVBarModel.trade_date.between(start, end),
    )
# async repo:  (await session.execute(stmt)).scalars().all()
# sync repo:   session.execute(stmt).scalars().all()
```

## 6. Use case example

```python
# packages/core/application/use_cases/ingest_eod_bars.py
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class IngestEodBars:
    provider: MarketDataProvider      # port
    bars: BarRepository               # port
    uow: UnitOfWork                   # port

    async def execute(self, ticker: str, start: date, end: date) -> int:
        fetched = await self.provider.get_eod_bars(ticker, start, end)
        async with self.uow:
            written = await self.bars.upsert_many(fetched)
            await self.uow.commit()
        return written
```

No FastAPI import. No SQLAlchemy import. No Celery import. This exact object runs
unchanged inside an HTTP request, a Celery task, or a test with two in-memory fakes —
which is the entire point.

## 7. Enforcing the dependency rule mechanically

Architecture that isn't enforced decays within about three sprints. Add
[`import-linter`](https://import-linter.readthedocs.io/) to CI:

```ini
# .importlinter
[importlinter]
root_packages = packages, apps

[importlinter:contract:layers]
name = Clean architecture layers
type = layers
layers =
    apps
    packages.core.infrastructure
    packages.core.application
    packages.core.domain

[importlinter:contract:domain-purity]
name = Domain imports no frameworks
type = forbidden
source_modules = packages.core.domain
forbidden_modules = fastapi, sqlalchemy, celery, redis, httpx
```

Then add `uv run lint-imports` to `.github/workflows/ci.yml` next to `ruff check`.
This is cheap and it is the difference between a documented architecture and a real
one.

## 8. Migration path (non-disruptive)

You do **not** need a big-bang refactor. In dependency order, each step independently
shippable:

1. Move `apps/api/core/config.py` → `packages/core/config.py`; update the two importers.
2. Remove import-time globals from `db/session.py`; add `lifespan` + `get_session`.
3. Add `application/ports/` and move `providers/base.py` there (it *is* a port).
4. Introduce `BarRepository` + the `ohlcv_bars` hypertable together (see [04 §2](04-scaling-and-performance.md)).
5. Write the first real use case (`IngestEodBars`) and call it from a Celery task.
6. Add `import-linter` once the structure exists, to stop it eroding.

Steps 1–2 are an afternoon and unblock everything else.
