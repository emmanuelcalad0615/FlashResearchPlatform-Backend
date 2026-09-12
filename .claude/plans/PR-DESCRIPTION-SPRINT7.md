# Contrato de API versionado (HU-A10, HU-A14)

Publica el contrato público de la API (`openapi.json`) como artefacto versionado que el frontend consume, con un gate en CI que detecta cuando el código y el snapshot se desincronizan.

## Qué incluye

| Etapa | Entregable | Archivos |
|---|---|---|
| Fuente única de versión + `operationId` estables | `CONTRACT_VERSION`, `operation_id_for` | `apps/api/infrastructure/middlewares/contract.py` |
| Exportador determinista + snapshot commiteado | script + `contract/openapi.json` | `scripts/export_openapi.py`, `contract/openapi.json` |
| El envelope de error entra al contrato | `ErrorResponse`/`ErrorDetail` documentados en 422/429/500 de toda ruta | `apps/api/infrastructure/middlewares/error_handlers.py` |
| Manifiesto del paquete npm | para que el frontend instale los tipos generados | `contract/package.json`, `contract/README.md` |
| Tests del contrato (10) | ver detalle abajo | `tests/test_openapi_contract.py` |
| Gate de sincronización en CI | falla el pipeline si el contrato quedó desactualizado | `.github/workflows/ci.yml` |
| Publicación versionada por tag | `contract-vX.Y.Z` → paquete npm | `.github/workflows/contract-release.yml` |
| Documentación del proceso | cómo versionar, publicar y dar acceso | `CONTRACT.md` |

## Los 10 tests de `tests/test_openapi_contract.py`

- `test_export_is_deterministic` — dos exportaciones dan el mismo texto
- `test_committed_snapshot_matches_app` — **gate de drift**: el snapshot es el que produce el código de hoy
- `test_contract_version_is_single_sourced` — mismo número en código, spec y `package.json`
- `test_contract_version_is_semver`
- `test_every_operation_has_stable_operation_id`
- `test_decimal_fields_are_strings`
- `test_every_operation_documents_its_success_shape` — toda ruta declara su `response_model` (arreglado en este PR para no asumir 200 fijo, ver abajo)
- `test_every_operation_documents_the_error_envelope`
- `test_documented_error_shape_matches_runtime`
- `test_http_validation_error_is_not_in_the_spec`

## Decisiones de diseño (detalle en el reporte de sprint)

- `status`/`code` quedan tipados como `str`, no `Literal[...]`: un enum estrecho convertiría cada valor nuevo en un `MAJOR` del contrato.
- El 422 se declara en toda ruta aunque no reciba parámetros: es el mecanismo que sustituye el `HTTPValidationError` automático de FastAPI.
- Publicación del paquete por `npm` aunque el frontend use `pnpm`: el registro no distingue el cliente que publicó, y el backend no tiene toolchain de Node.

## Cambios adicionales de este PR (post-rebase sobre `main`)

Al rebasar esta rama sobre `main` (que ya traía el merge de auth) y correr la suite completa, aparecieron tres problemas que se resolvieron aquí:

- **Rutas movidas**: `apps.api.core.*` / `apps.api.routers.*` (de este trabajo) se reconciliaron con `apps.api.infrastructure.*` / `apps.api.interfaces.*` (de `main`), incluyendo el router de `auth` que este trabajo no conocía.
- **`greenlet` faltante en macOS arm64**: el metadata de SQLAlchemy condiciona `greenlet` a un marker que no cubre Apple Silicon; se declaró como dependencia directa en `pyproject.toml`.
- **Snapshot desactualizado**: `contract/openapi.json` no incluía las rutas de `auth` ya mergeadas en `main`; regenerado con `scripts/export_openapi.py`.
- **Test con supuesto incorrecto**: `test_every_operation_documents_its_success_shape` asumía 200 fijo; `POST /api/auth/signup` responde 201. Ajustado para buscar el primer `2xx` declarado.

## Verificación

- `uv run ruff check .` — limpio
- `uv run pytest` — 400 passed (suite completa, con Postgres/TimescaleDB y Redis reales, sin mocks)
- `uv run python scripts/export_openapi.py --check` — contrato sincronizado

## Pendiente (fuera de este PR)

- Publicar `contract-v0.1.0` (tag) tras el merge.
- Conceder acceso de lectura al paquete npm para el repo frontend (paso manual en GitHub Packages).
- Arrancar el consumo del contrato en el frontend (bloqueado por lo anterior).
- `scripts/smoke_polygon.py` sigue roto por una causa preexistente y no relacionada (`ModuleNotFoundError: No module named 'apps'`) — no se tocó, ver reporte de sprint §4.1.
