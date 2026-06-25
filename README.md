# Flash Research — API

Backend de **Flash Research**, plataforma de inteligencia para *momentum trading*
(escaneo de mercado NYSE/NASDAQ, métricas cuantitativas, dashboard en tiempo real).

Este repo contiene dos deployables que comparten código:

- **API** (`apps/api`) — la cara HTTP (FastAPI).
- **Worker** (`apps/worker`) — ingesta y cálculos batch (esqueleto por ahora).
- **`packages/core`** — código compartido (modelos SQLAlchemy, schemas).

## Stack

| Capa | Tecnología |
|---|---|
| Lenguaje | Python 3.12+ |
| API | FastAPI |
| ORM | SQLAlchemy 2.x |
| Migraciones | Alembic |
| Gestor de deps | uv |
| Base de datos | PostgreSQL 17 + TimescaleDB |
| Cache / bus | Redis 7 |
| Lint/format | Ruff |
| Tests | pytest |
| Infra local | Docker Compose |

## Requisitos previos

- [uv](https://docs.astral.sh/uv/) — gestor de dependencias de Python.
- [Docker Desktop](https://www.docker.com/) — para Postgres y Redis.

## Arranque de cero

```bash
# 1. Clonar
git clone https://github.com/emmanuelcalad0615/FlashResearchPlatform-Backend.git
cd FlashResearchPlatform-Backend

# 2. Instalar dependencias
uv sync

# 3. Configurar variables de entorno
cp .env.example .env        # Windows PowerShell: Copy-Item .env.example .env

# 4. Levantar infraestructura (Postgres + Redis)
docker compose up -d

# 5. Aplicar migraciones (crea profiles, instruments, RLS)
uv run alembic upgrade head

# 6. Levantar la API en modo dev
uv run uvicorn apps.api.main:app --reload
```

La API queda en `http://localhost:8000`. Verifica:

```bash
curl http://localhost:8000/health   # -> {"status":"ok"}
```

Docs interactivas (Swagger): `http://localhost:8000/docs`.

## Comandos útiles

```bash
# Lint
uv run ruff check .
uv run ruff check . --fix     # autofix

# Tests
uv run pytest

# Migraciones
uv run alembic upgrade head        # aplicar todas
uv run alembic downgrade base      # revertir todo
uv run alembic revision -m "msg"   # nueva migración vacía
uv run alembic check               # verificar que modelos == esquema

# Worker (esqueleto)
uv run python -m apps.worker.main
```

## Estructura

```
apps/
  api/                  # FastAPI: la cara HTTP
    main.py             # crea la app, monta routers
    core/config.py      # settings desde variables de entorno
    routers/health.py   # GET /health
  worker/main.py        # ingesta + batch (esqueleto)
packages/
  core/
    models/             # modelos SQLAlchemy (Profile, Instrument)
    schemas/            # shapes Pydantic (más adelante)
migrations/             # Alembic (env.py + versions/)
tests/                  # pytest
docker-compose.yml      # Postgres+TimescaleDB + Redis
```

## Notas de infraestructura

- `docker compose down` conserva los datos (volúmenes persistentes).
- `docker compose down -v` **borra** los datos. ⚠️ Úsalo solo para empezar de cero.
- La extensión `timescaledb` se activa automáticamente en la migración inicial.

## Convenciones

- Precios y montos: **siempre `numeric`, nunca `float`**.
- Instrumentos delistados: `is_active = false`, **nunca se borran** (evita survivorship bias).
- Cambios de esquema: **solo con Alembic**, nada de SQL manual.
