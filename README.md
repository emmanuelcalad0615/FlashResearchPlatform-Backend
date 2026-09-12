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

## Autenticación

Ocho rutas bajo `/api/auth`:

| Método | Ruta | Protegida | Qué hace |
|---|---|---|---|
| `POST` | `/signup` | no | Crea la cuenta y envía el correo de verificación |
| `POST` | `/verify-email` | no | Activa la cuenta con el token del correo |
| `POST` | `/resend-verification` | no | Reenvía el enlace si se perdió |
| `POST` | `/login` | no | Abre sesión y emite las cookies |
| `POST` | `/refresh` | cookie de refresh | Renueva la sesión |
| `GET` | `/me` | sí | Quién es el usuario de la sesión |
| `POST` | `/logout` | sí | Cierra esta sesión |
| `POST` | `/logout-all` | sí | Cierra todas las sesiones del usuario |

### Cómo viajan los tokens

**En cookies `HttpOnly`, nunca en el cuerpo.** El frontend no los lee ni los
guarda: no hay nada que un XSS pueda robar, porque no existe API del navegador
que exponga una cookie `HttpOnly`.

| Cookie | Vida | Path | Para qué |
|---|---|---|---|
| `access_token` | 15 min en producción | `/` | Identifica al usuario en cada petición. JWT firmado, no se consulta la base para validarlo |
| `refresh_token` | 7 días en producción | `/api/auth/refresh` | Se canjea por un par nuevo. Cadena opaca, validada siempre contra la base |

El `Path` estrecho del refresh es deliberado: el navegador solo manda una cookie
a las rutas que cuelgan de su `Path`, así que la credencial larga no aparece en
las cientos de peticiones normales de un dashboard.

### Rotación y detección de robo

Cada refresh gasta el token presentado y emite otro **de la misma familia**. Un
token ya usado que vuelve a aparecer significa que hay dos copias en
circulación, así que se revoca la familia entera: el ladrón y la víctima quedan
fuera, y la víctima vuelve a entrar con su contraseña.

⚠️ **Límite conocido:** cerrar sesión revoca los refresh tokens, pero un access
token ya emitido sigue valiendo hasta que caduca. Es el precio de un JWT sin
estado, y la razón de que dure 15 minutos. Ver `HU-A16` para la revocación
inmediata.

### Probarlo a mano

```bash
# Con la API levantada y Mailpit en http://localhost:8025
curl -i -c cookies.txt -X POST localhost:8000/api/auth/signup \
  -H 'Content-Type: application/json' \
  -d '{"email":"ana@ejemplo.com","password":"una-frase-larga-y-segura"}'

# El enlace de verificación llega a Mailpit. Copia el token y:
curl -i -X POST localhost:8000/api/auth/verify-email \
  -H 'Content-Type: application/json' -d '{"token":"EL_TOKEN"}'

curl -i -c cookies.txt -X POST localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"ana@ejemplo.com","password":"una-frase-larga-y-segura"}'

curl -i -b cookies.txt localhost:8000/api/auth/me
```

## Notas de infraestructura

- `docker compose down` conserva los datos (volúmenes persistentes).
- `docker compose down -v` **borra** los datos. ⚠️ Úsalo solo para empezar de cero.
- La extensión `timescaledb` se activa automáticamente en la migración inicial.

## Convenciones

- Precios y montos: **siempre `numeric`, nunca `float`**.
- Instrumentos delistados: `is_active = false`, **nunca se borran** (evita survivorship bias).
- Cambios de esquema: **solo con Alembic**, nada de SQL manual.
