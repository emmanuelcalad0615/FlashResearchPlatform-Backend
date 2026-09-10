# Reporte de implementación — Sprint 7 (backend)

> Fecha: 2026-09-09 · Rama: `app/feature` · Plan ejecutado:
> [`PLAN-HU.md`](PLAN-HU.md), etapas 1.1 → 1.7 (incluida la 1.2-bis).
> Historias: **HU-A10** (contrato versionado + generación de tipos) y
> **HU-A14** (chequeo de sincronización en CI), lado backend.
> **Estado: implementado y verificado. Sin commit — todo está en el working tree.**

---

## 1. Qué quedó implementado

| Etapa | Entregable | Archivos |
|---|---|---|
| 1.1 | Fuente única de la versión + `operationId` estables | `apps/api/core/contract.py` (nuevo), `apps/api/main.py`, `apps/api/routers/health.py` |
| 1.2 | Exportador determinista del spec + snapshot commiteado | `scripts/export_openapi.py` (nuevo), `contract/openapi.json` (nuevo, 202 líneas) |
| 1.2-bis | El envelope de error entra al contrato | `apps/api/core/error_handlers.py` |
| 1.3 | Manifiesto del paquete npm | `contract/package.json`, `contract/README.md` (nuevos) |
| 1.4 | Tests del contrato | `tests/test_openapi_contract.py` (nuevo, 10 tests) |
| 1.5 | Gate de sincronización en CI | `.github/workflows/ci.yml` |
| 1.6 | Publicación versionada por tag | `.github/workflows/contract-release.yml` (nuevo) |
| 1.7 | Documentación del proceso | `CONTRACT.md` (nuevo, 149 líneas), `README.md` |

**Diff:** 5 archivos modificados (+115 líneas), 7 archivos nuevos.

### Los 10 tests de `tests/test_openapi_contract.py`

| Test | Qué protege |
|---|---|
| `test_export_is_deterministic` | Dos exportaciones dan el mismo texto (si no, el gate daría falsos positivos) |
| `test_committed_snapshot_matches_app` | **El gate de drift**: el snapshot es el que produce el código de hoy |
| `test_contract_version_is_single_sourced` | El mismo número en `CONTRACT_VERSION`, `info.version` y `package.json` |
| `test_contract_version_is_semver` | Formato `X.Y.Z` |
| `test_every_operation_has_stable_operation_id` | `operationId` explícito, único, sin el sufijo autogenerado |
| `test_decimal_fields_are_strings` | Los `Decimal` salen como `string` (hoy pasa vacío: red para la Épica B) |
| `test_every_operation_documents_its_success_shape` | Toda ruta declara `response_model` (ver §3.2) |
| `test_every_operation_documents_the_error_envelope` | 422/429/500 apuntan a `ErrorResponse` en toda operación |
| `test_documented_error_shape_matches_runtime` | Un 404 real valida contra `ErrorResponse` — ata contrato y runtime |
| `test_http_validation_error_is_not_in_the_spec` | El `HTTPValidationError` de FastAPI no vuelve al contrato |

---

## 2. Verificación ejecutada

| Comprobación | Resultado |
|---|---|
| `uv run ruff check .` | Limpio |
| `uv run pytest` | **58 passed** (48 previos + 10 nuevos), sin regresiones |
| `uv run python scripts/export_openapi.py --check` | Exit 0, "Contrato sincronizado" |
| Smoke de deriva: ruta nueva sin regenerar | `--check` exit 1 con el mensaje de 4 pasos; `test_committed_snapshot_matches_app` en rojo; verde al restaurar |
| Smoke del guard de tipos: ruta sin `response_model` | Falla con `GET /ping no declara response_model`; verde al restaurar |
| YAML de los dos workflows | Parsea correctamente |
| `HTTPValidationError` fuera del spec | Confirmado; `components.schemas` = `ErrorDetail`, `ErrorResponse`, `HealthResponse`, `RootResponse` |

Estado final del contrato: 2 operaciones (`getHealth`, `getRoot`), las dos con
respuesta 200 tipada y con 422/429/500 apuntando al envelope.

---

## 3. Decisiones tomadas durante la implementación

Cosas que el plan no preveía y hubo que resolver. Todas están comentadas en el
código donde aplican.

### 3.1 `sys.path` en `scripts/export_openapi.py`

El proyecto **no se instala como paquete** (`pyproject.toml` no tiene
`[build-system]`), así que ejecutar `python scripts/x.py` deja `scripts/` en
`sys.path` pero no la raíz del repo: `import apps` falla. pytest no lo sufre
porque inserta el rootdir por su cuenta.

Se resolvió con un bootstrap de 2 líneas en el script (con el comentario que
explica por qué, y un `# noqa: E402` en el import que va después). Se eligió eso
sobre `python -m scripts.export_openapi` para que el comando documentado en
`CONTRACT.md` funcione tal cual, sin `PYTHONPATH`. **El arreglo de fondo es otro
— ver §4.1.**

### 3.2 `response_model` en `/health` y `/` (aprobado en la revisión)

Descubierto al ejecutar el smoke de deriva: el primer intento **no detectó nada**
porque cambié el cuerpo devuelto por `/health` y el spec no se movió. La causa no
era el gate: era que las rutas no declaraban `response_model`, así que el
contrato documentaba su 200 como `"schema": {}` y el frontend habría generado
`unknown`.

Se añadieron `HealthResponse` y `RootResponse`, más el test
`test_every_operation_documents_its_success_shape` para que la próxima ruta no
nazca igual. Se hizo **antes** del primer `contract-v0.1.0` a propósito: declarar
el tipo cambia la forma del contrato, y publicarlo después habría sido un `MAJOR`
con un solo consumidor y cero beneficio — el mismo argumento que ordenó la
Etapa 1.2-bis antes de la 1.3.

### 3.3 `status` y `code` son `str`, no enums

Ni `HealthResponse.status` ni `ErrorDetail.code` se tiparon como `Literal[...]`.
Darían tipos más finos en el frontend, pero convertirían **cada valor nuevo en un
`MAJOR`** del contrato: estrechar un tipo rompe a los consumidores. Los valores
conocidos van en la descripción del campo, donde informan sin congelar.

### 3.4 El 422 se declara también en rutas que no pueden emitirlo

`ERROR_RESPONSES` es global, así que `/health` documenta un 422 aunque no reciba
parámetros. Es deliberado: el `responses` de la app es justo el mecanismo que
**sustituye** el 422 automático de FastAPI (`HTTPValidationError`), que era la
discrepancia que la Etapa 1.2-bis venía a corregir. El precio es una respuesta
declarada de más en rutas sin parámetros; la alternativa era declararlo ruta por
ruta y que la primera que se olvidara volviera a mentir.

### 3.5 `npm publish` en el workflow, aunque el frontend use pnpm

Confirmado con el equipo: se mantiene `npm`. El repo backend no tiene toolchain
de Node y `actions/setup-node` ya trae npm, así que publicar así no añade pasos.
El tarball que sube `npm publish` es exactamente el que `pnpm add` descarga: el
registro no distingue el cliente que publicó. Anotado en `CONTRACT.md` §3.

---

## 4. Pendientes

### 4.1 `scripts/smoke_polygon.py` está roto (preexistente, no lo toqué)

```
$ uv run python scripts/smoke_polygon.py
ModuleNotFoundError: No module named 'apps'
```

**No es una regresión de este trabajo**: falla por la misma causa del §3.1 y ya
fallaba antes de tocar nada. Está documentado como funcional en
`architecture.md` §3 y en `communication.md` §6, así que la documentación
también quedó desactualizada respecto al comportamiento real.

No lo arreglé porque queda fuera del alcance de estas HU (code_skill §3: tocar
solo lo que la petición implica). **Tres salidas, de menos a más sólida:**

1. Copiar el mismo bootstrap de `sys.path` que lleva `export_openapi.py` — 2
   líneas, arregla el síntoma en ese script.
2. Documentar `uv run python -m scripts.smoke_polygon` (necesita
   `scripts/__init__.py`) y corregir los dos documentos.
3. **El arreglo de fondo:** añadir `[build-system]` a `pyproject.toml` (hatchling)
   para que `uv sync` instale `apps` y `packages` en el venv. Desaparecen todos
   los bootstraps, incluido el del exportador, y cualquier script futuro nace
   funcionando. Es un cambio de empaquetado y toca cómo instala el CI: merece su
   propia HU y su propio PR.

### 4.2 El contrato todavía no se ha publicado

`contract-v0.1.0` no existe. Requiere: mergear a `main` → `git tag contract-v0.1.0`
→ `git push origin contract-v0.1.0`. El workflow está escrito pero **no se ha
podido ejercitar**: solo corre con un tag real, no hay forma de verificarlo desde
local.

### 4.3 La concesión de acceso al paquete es manual y no se ha hecho

Los dos repos son privados. Después de la primera publicación hay que ir a
*Package settings* → quitar los permisos heredados → *Manage Actions access* →
añadir `FlashResearchPlatform-Frontend` con rol **Read**. Es un paso de UI, no
automatizable desde aquí. Procedimiento completo en `CONTRACT.md` §3 y en
`PLAN-HU-FRONTEND.md` §8.1.

Además, cada desarrollador necesita un **PAT clásico** con `read:packages`
(GitHub Packages no soporta fine-grained PAT) exportado como `NODE_AUTH_TOKEN`.

### 4.4 Todo el lado frontend está sin empezar

Etapas F.1 → F.9 de [`PLAN-HU-FRONTEND.md`](PLAN-HU-FRONTEND.md). Bloqueadas por
§4.2 y §4.3: no se puede añadir la dependencia de un paquete que no existe.

### 4.5 A10.3 sigue parcialmente bloqueada

"El cliente API del frontend usa esos tipos generados": con `/health` y `/` como
único contenido del contrato, se puede demostrar que el circuito cierra, pero no
que un adapter real consuma un endpoint de negocio tipado. Se desbloquea con el
primer endpoint de la Épica B en `contract-v0.2.0`. Los 36 marcadores
`FOLLOWUP(contract)` del frontend siguen esperando ese día.

### 4.6 Fuera de alcance, por si se reabre

- Chequeo automático de *breaking changes* (`oasdiff`): el CI detecta **que** el
  contrato cambió, no **si** rompe. Esa lectura sigue siendo humana, en la
  revisión del PR.
- Contrato de WebSocket: OpenAPI no cubre WS, y el frontend tiene 3 adapters
  `ws-*` con DTOs a mano. Es un hueco real de la frontera, no un olvido.
- Consumir `ErrorResponse` en el frontend (hoy `HttpError` no mira el cuerpo).

### 4.7 Sin commit

Los cambios están en el working tree de `app/feature`. No se hizo commit ni push.

---

## 5. Orden sugerido para continuar

1. Revisar el diff y commitear en `app/feature`; abrir PR a `main`.
2. Verificar que el CI pasa con el paso nuevo (`Contrato OpenAPI sincronizado`).
3. Mergear y publicar `contract-v0.1.0` (§4.2).
4. Conceder acceso al paquete y repartir el procedimiento del PAT (§4.3).
5. Arrancar F.1 en el repo frontend (§4.4).
6. Decidir aparte qué hacer con `smoke_polygon.py` (§4.1) — recomendado el
   `[build-system]`, en su propia HU.
