# PLAN-HU — Implementación progresiva (Sprint 7)

> Repo: `FlashResearchPlatform-Backend` · Rama base: `app/feature`
> Alcance de este plan: **HU-A10** (contrato versionado + generación de tipos) y
> **HU-A14** (chequeo de sincronización del contrato en CI).
> Arquitectura de referencia obligatoria: `.claude/doc/architecture.md`.
> Plan hermano (lado frontend): `.claude/plans/PLAN-HU-FRONTEND.md`.
> Reglas de ejecución: `.claude/skill/code_skill.md` (mínimo código, cambios
> quirúrgicos, criterios de verificación por paso).

---

## 0. Contexto y frontera de repos

El backlog (`.claude/doc/backlog.md` §1) documenta la desviación a **polirepo**:
backend y frontend viven en repos separados y el contrato de la API cruza esa
frontera. HU-A10 y HU-A14 son precisamente el mecanismo que reemplaza el cordón
umbilical que daba el monorepo.

Consecuencia práctica para este plan: **las dos historias son bi-repo**. Se
planifican completas, marcando de qué lado vive cada entregable:

| Criterio de aceptación | Repo |
|---|---|
| A10.1 — backend exporta `openapi.json` y lo publica versionado desde su CI | **Backend** |
| A10.2 — frontend consume el contrato con versión fija y genera `api-types` | Frontend |
| A10.3 — el cliente API del frontend usa esos tipos | Frontend |
| A10.4 — proceso de regeneración documentado y reproducible | Ambos |
| A14.1 — CI del frontend verifica que los tipos correspondan a la versión declarada | Frontend |
| A14.2 — si el contrato cambió y los tipos no se regeneraron, el build falla con mensaje claro | Frontend (+ gate espejo en backend) |
| A14.3 — procedimiento de desincronización documentado | Ambos |

En este repo se implementan las etapas **1.1 → 1.7**. Las etapas **1.8 → 1.9**
son el trabajo espejo en el repo frontend; se dejan especificadas al detalle
(scripts, mensajes de error, orden del pipeline) para que se ejecuten allá sin
volver a diseñar nada.

---

## 1. Supuestos y decisiones de diseño

Declarados explícitamente (code_skill §1). Los marcados **[CONFIRMAR]** cambian
el trabajo si la respuesta es distinta; el resto son defaults razonables.

> **Estado a 2026-09-09: nada pendiente.** No queda ningún `[CONFIRMAR]` abierto
> —la decisión #3 (registro y scope) se resolvió con los hechos verificados que
> se listan dentro de ella— y la única decisión de alcance que esperaba al
> equipo, declarar el envelope de error en el OpenAPI, **fue aprobada** y es
> ahora la Etapa 1.2-bis.

1. **La versión del contrato es independiente de la versión del paquete Python.**
   `pyproject.version` versiona el deployable; el contrato versiona la *forma de
   la API*. Un fix interno del backend no debe obligar al frontend a subir
   dependencia. Fuente única: `CONTRACT_VERSION` en `apps/api/core/contract.py`,
   que alimenta `FastAPI(version=...)` → `info.version` del spec.

2. **Semver del contrato:**
   - `MAJOR` — cambio incompatible: borrar/renombrar endpoint o campo de
     respuesta, volver requerido un campo opcional de request, estrechar un tipo.
   - `MINOR` — aditivo: endpoint nuevo, campo opcional nuevo en respuesta.
   - `PATCH` — solo descripciones/ejemplos/tags; la forma no cambia.

3. **Registro de publicación: paquete npm en GitHub Packages** —
   `@emmanuelcalad0615/flash-api-contract`. **RESUELTO — ya no requiere
   confirmación.** Hechos verificados (2026-09-09):
   - Los dos repos son **privados** (`api.github.com` responde 404 sin
     autenticar) y el owner es una **cuenta personal** (`"type": "User"`), no una
     organización.
   - El registro npm de GitHub **sí soporta permisos granulares** (Maven y
     Gradle son los que no). El `GITHUB_TOKEN` del CI del frontend **puede** leer
     el paquete una vez se le concede acceso explícito: **el CI del frontend no
     necesita ningún secreto nuevo**. Procedimiento exacto y su orden en
     `PLAN-HU-FRONTEND.md` §8.1.
   - En local hace falta un **PAT clásico** con `read:packages` por
     desarrollador: GitHub Packages **no soporta fine-grained PAT**
     (`PLAN-HU-FRONTEND.md` §8.2).
   - El scope npm debe coincidir con el owner, de ahí `@emmanuelcalad0615`. Un
     scope de marca (`@flash-research/…`) obligaría a publicar en npmjs.com, y
     con repos privados eso significa o pagar por paquete privado, o publicar la
     forma de la API en abierto. Ninguna de las dos compensa hoy.

   **Única condición para revisar esta decisión:** si el proyecto migra a una
   organización de GitHub, el scope cambia con el owner y hay que re-publicar
   (y el frontend cambia su dependencia). Hacerlo hoy es gratis; hacerlo con
   versiones ya publicadas y consumidas, no. **Si esa migración está en el
   horizonte, conviene hacerla ANTES del primer `contract-v*`.**

4. **Publicación disparada por tag `contract-vX.Y.Z`**, no por cada merge a
   `main`. Publicar es un acto deliberado: evita quemar versiones en cada PR y
   deja el historial de contrato legible. El workflow valida que el tag coincida
   con `CONTRACT_VERSION`.

5. **El snapshot del spec se commitea** en `contract/openapi.json`. Sin snapshot
   no hay forma de que el CI del backend detecte un cambio de forma no
   intencional, ni de revisar el diff del contrato en el PR — que es
   precisamente donde el equipo debe verlo.

6. **`Decimal` viaja como `string` en el contrato.** Verificado contra el código
   actual: Pydantic v2 en modo serialización emite
   `{"type": "string", "pattern": "^(?!^[-+.]*$)[+-]?0*\\d*\\.?\\d*$"}` para los
   campos `Decimal` de `OHLCVBar`. Es coherente con la convención del repo
   (precios nunca `float`) y hay que documentarlo: el frontend recibirá
   `close: string` y **no debe** hacer aritmética con `Number()` sin una librería
   decimal. Este es exactamente el tipo de detalle que la generación de tipos
   hace visible en vez de dejarlo explotar en runtime.

7. **`operationId` explícito y estable.** Hoy FastAPI genera
   `health_check_health_get` (nombre de función + path + método): renombrar la
   función de un router cambia el nombre del método generado en el cliente TS,
   produciendo un breaking change fantasma. Se fija con
   `generate_unique_id_function`.

8. **Sin endpoint runtime de metadatos del contrato.** Sería especulativo
   (code_skill §2): el chequeo que piden las historias es de *build time*.
   FastAPI ya sirve `/openapi.json`.

9. Los comentarios de código nuevos van **en español**, como el resto del repo
   (CLAUDE.md).

---

## 2. Restricciones de arquitectura que este plan respeta

- **No se toca `packages/core`.** El contrato es vocabulario HTTP; vive en
  `apps/api`, igual que `STATUS_BY_ERROR` vive en `apps/api/core/error_handlers.py`
  y no en el dominio. El worker no debe arrastrar nada de esto.
- **No se toca el puerto/adapter de proveedores** (`providers/base.py`,
  `polygon.py`) ni los DTOs canónicos. Este trabajo es transversal a la capa
  HTTP; si un DTO aparece en el spec es porque un router lo expone, no porque el
  contrato lo importe.
- **Sin migraciones ni SQL**: ninguna etapa toca la base de datos.
- **Config por `Settings`**: si hiciera falta parametrizar algo (no está
  previsto), se extiende `apps/api/core/config.py`, no `os.environ` ad hoc.
- **Tests sin red**: el spec se genera desde la app en memoria; ninguna etapa
  añade llamadas HTTP reales a la suite.

---

# FASE 1 — SPRINT 7

Orden de ejecución. Cada etapa lista su verificación; no se pasa a la siguiente
sin verde (code_skill §4). Etapas 1.1–1.5 son un solo PR coherente
("contrato exportable + gate de drift"); 1.6–1.7 pueden ir en el mismo PR o en
uno seguido; 1.8–1.9 son PRs del repo frontend.

---

## Etapa 1.1 — Fuente única de la versión del contrato · HU-A10

**Archivo nuevo:** `apps/api/core/contract.py`

```python
"""Version del contrato publico de la API (el openapi.json que consume el frontend).

Es independiente de la version del paquete Python (pyproject): esa versiona el
deployable, esta versiona la FORMA de la API. Un arreglo interno no obliga al
frontend a subir dependencia.

Semver del contrato:
  MAJOR - rompe: se borra/renombra un endpoint o campo, un opcional se vuelve
          requerido, un tipo se estrecha.
  MINOR - aditivo: endpoint nuevo, campo opcional nuevo en la respuesta.
  PATCH - solo descripciones o ejemplos; la forma no cambia.

Al cambiarla hay que regenerar el snapshot: `uv run python scripts/export_openapi.py`.
"""

CONTRACT_VERSION = "0.1.0"
```

**Edición:** `apps/api/main.py` — `FastAPI(title="Flash Research API",
version=CONTRACT_VERSION, generate_unique_id_function=operation_id_for)`.

**Edición:** `apps/api/core/contract.py` añade el generador de `operationId`:

```python
def operation_id_for(route: APIRoute) -> str:
    """operationId estable: nombre explicito de la ruta, o su `name` como fallback.

    El default de FastAPI mezcla nombre de funcion + path + metodo, asi que
    renombrar una funcion de router cambia el nombre del metodo en el cliente TS
    generado: un breaking change fantasma. Aqui el nombre es un dato del
    contrato, no un detalle de implementacion.
    """
    return route.operation_id or route.name
```

Y en los routers se declara explícito donde el `name` no sea buen nombre público:
`@router.get("/health", operation_id="getHealth")`,
`@router.get("/", operation_id="getRoot")`.

**Verificar:**
```bash
uv run python -c "from apps.api.main import app; import json; s=app.openapi(); print(s['info']['version']); print([o.get('operationId') for p in s['paths'].values() for o in p.values()])"
# -> 0.1.0 · ['getHealth', 'getRoot']
uv run ruff check .
```

---

## Etapa 1.2 — Export determinista del spec · HU-A10 (AC1, AC4)

**Archivo nuevo:** `scripts/export_openapi.py`

Responsabilidades, mínimas y separadas (misma disciplina que el adapter: lógica
pura testeable, aparte del I/O):

```python
CONTRACT_PATH = Path(__file__).resolve().parents[1] / "contract" / "openapi.json"

def render_spec() -> str:
    """Serializa el openapi de la app de forma DETERMINISTA.

    sort_keys ordena las claves siempre igual, asi el diff del PR muestra el
    cambio real del contrato y no un reordenamiento de dict. El newline final
    es para que git no marque "\\ No newline at end of file".
    """
    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"

def write_spec() -> None: ...           # escribe CONTRACT_PATH
def check_spec() -> int: ...            # compara y devuelve exit code + mensaje
```

CLI: `uv run python scripts/export_openapi.py` escribe;
`--check` compara sin escribir y falla con:

```
El contrato exportado no coincide con contract/openapi.json.
Cambiaste la forma de la API sin regenerar el snapshot.
  1. Ejecuta: uv run python scripts/export_openapi.py
  2. Revisa el diff de contract/openapi.json (¿es breaking?).
  3. Sube CONTRACT_VERSION en apps/api/core/contract.py segun semver.
  4. Commitea ambos archivos.
```

**Archivo nuevo (generado):** `contract/openapi.json` — primera corrida del
script, commiteado.

**Verificar:**
```bash
uv run python scripts/export_openapi.py && git diff --stat contract/
uv run python scripts/export_openapi.py --check   # exit 0
uv run python scripts/export_openapi.py --check && echo OK
```

---

## Etapa 1.2-bis — El envelope de error entra al contrato · APROBADA (2026-09-09)

> Aprobada por el equipo tras la revisión del plan. Estaba en §6 como fuera de
> alcance; pasa a ser trabajo del Sprint 7.

**Por qué deja de ser opcional: hoy el contrato miente.** Verificado ejecutando
el `openapi()` real — FastAPI documenta automáticamente un `422` con schema
`HTTPValidationError` (`{"detail": [...]}`) en **toda** ruta que reciba un
parámetro, pero `validation_error_handler` devuelve
`{"error": {"code": "validation_error", ...}}`. El frontend generaría tipos
contra una forma que la API nunca emite. Esto no es una mejora aditiva: es
corregir una discrepancia que ya existe.

### Orden: va ANTES del primer `contract-v0.1.0`

Declarar el envelope **cambia la forma documentada del 422**. Si se publica
después de la v0.1.0, es un cambio incompatible y obliga a un `MAJOR` con un solo
consumidor y cero beneficio. Se hace ahora, mientras no hay nada publicado.
Secuencia: 1.1 → 1.2 → **1.2-bis** → 1.3 → … → 1.6 (publicar).

### Los modelos

Van en `apps/api/core/error_handlers.py`, junto a `build_error_response`, que es
la función que produce exactamente esta forma. Mismo argumento que ya usa el
módulo para `STATUS_BY_ERROR`: el envelope es vocabulario HTTP y **no baja a
`packages/core`**; el worker lanza las mismas excepciones sin arrastrar esto.

```python
class ErrorDetail(BaseModel):
    """El cuerpo de `error` que arma build_error_response()."""

    code: str = Field(
        description=(
            "Codigo estable, snake_case, legible por maquina. Es EL contrato con "
            "el frontend. Valores actuales: not_found, conflict, unauthorized, "
            "forbidden, validation_error, rate_limited, internal_error, "
            "bad_gateway, service_unavailable, bad_request, method_not_allowed, "
            "not_acceptable, unsupported_media_type, http_error."
        ),
        examples=["not_found"],
    )
    message: str = Field(description="Texto para humanos. Puede cambiar sin romper a nadie.")
    details: dict[str, Any] = Field(default_factory=dict)
    # Ausente cuando no hay request_id (el handler lo omite en vez de mandarlo
    # vacio), asi que aqui es opcional de verdad, no un string vacio.
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
```

**`code` se queda como `str`, no como enum.** Un `Literal[...]` daría tipos más
finos en el frontend, pero convertiría **cada código nuevo en un `MAJOR`** del
contrato: estrechar un tipo rompe a los consumidores. Los valores conocidos van
en la descripción, donde informan sin congelar. Es la misma razón por la que
`CODE_BY_STATUS` puede crecer sin coordinar un release.

### El cableado

`apps/api/core/error_handlers.py` expone el mapa, y `main.py` lo pasa al
constructor:

```python
# Los tres estados que puede devolver CUALQUIER ruta, haga lo que haga:
# 422 lo emite la validacion de Pydantic, 429 el middleware de rate limit, y
# 500 la red de seguridad de unhandled_exception_handler. Los especificos de
# cada ruta (404, 409, 401...) los declara su router, no esto.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorResponse, "description": "Request validation failed"},
    429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
    500: {"model": ErrorResponse, "description": "Unexpected server error"},
}
```

```python
app = FastAPI(
    title="Flash Research API",
    version=CONTRACT_VERSION,
    generate_unique_id_function=operation_id_for,
    responses=ERROR_RESPONSES,
)
```

Verificado que funciona: el `responses` del constructor se propaga a **todas** las
operaciones y **sustituye** el 422 automático — `HTTPValidationError` y
`ValidationError` desaparecen de `components.schemas`, y quedan `ErrorDetail` y
`ErrorResponse`.

### Tests (se añaden a `tests/test_openapi_contract.py`)

| Test | Qué protege |
|---|---|
| `test_every_operation_documents_the_error_envelope` | toda operación declara 422, 429 y 500 apuntando a `ErrorResponse`. Una ruta nueva no puede escaparse. |
| `test_documented_error_shape_matches_runtime` | dispara un 404 real con `TestClient` contra una ruta inexistente y valida el body con `ErrorResponse.model_validate`. **Este es el que cierra el círculo**: si alguien cambia `build_error_response` sin tocar el modelo, falla aquí, no en el frontend. |
| `test_http_validation_error_is_not_in_the_spec` | `HTTPValidationError` no está en `components.schemas`. Es la regresión concreta que esta etapa arregla; sin el test vuelve sola en cuanto alguien añada un `responses` por ruta mal puesto. |

**Verificar:**
```bash
uv run pytest tests/test_openapi_contract.py -v
uv run python scripts/export_openapi.py          # regenera el snapshot
git diff contract/openapi.json                    # aparecen ErrorDetail/ErrorResponse,
                                                  # desaparece HTTPValidationError
uv run pytest && uv run ruff check .
```

### Efecto en el frontend

`types.gen.ts` pasa a incluir `components["schemas"]["ErrorResponse"]`, así que
el manejo de errores del frontend deja de tipar a mano lo que ya sabe el
backend. Encaja sin fricción con su arquitectura: `HttpError` vive en
`shared/infrastructure/http/http-client.ts` y el parseo del cuerpo de error puede
tiparse desde el contrato en la capa de infraestructura. No cambia ninguna etapa
del `PLAN-HU-FRONTEND.md`: llega dentro del mismo `contract-v0.1.0`.

---

## Etapa 1.3 — Manifiesto del paquete de contrato · HU-A10 (AC1)

**Archivo nuevo:** `contract/package.json`

```json
{
  "name": "@emmanuelcalad0615/flash-api-contract",
  "version": "0.1.0",
  "description": "Contrato OpenAPI versionado de Flash Research API",
  "files": ["openapi.json"],
  "publishConfig": { "registry": "https://npm.pkg.github.com" },
  "license": "UNLICENSED"
}
```

**Archivo nuevo:** `contract/README.md` — qué es el paquete, cómo se consume
desde el frontend, y el enlace al procedimiento de regeneración (`CONTRACT.md`,
etapa 1.7).

Invariante que se prueba en la etapa 1.4: `contract/package.json:version ==
CONTRACT_VERSION == contract/openapi.json:info.version`. Un solo número, tres
lugares que deben coincidir, un test que lo garantiza.

---

## Etapa 1.4 — Tests · DoD (pruebas de integración del contrato)

**Archivo nuevo:** `tests/test_openapi_contract.py` (sin red, sin DB — usa la app
en memoria, igual que `test_health.py`):

| Test | Qué protege |
|---|---|
| `test_export_is_deterministic` | `render_spec()` dos veces → idéntico. Sin esto el gate de CI daría falsos positivos. |
| `test_committed_snapshot_matches_app` | `contract/openapi.json` == `render_spec()`. **Este es el gate real de drift del backend** (A14 lado backend): corre en el `pytest` que ya existe en CI, sin añadir jobs. |
| `test_contract_version_is_single_sourced` | `CONTRACT_VERSION` == `info.version` del spec == `contract/package.json:version`. |
| `test_contract_version_is_semver` | formato `X.Y.Z`. |
| `test_every_operation_has_stable_operation_id` | todo path·método tiene `operationId`, único y sin el sufijo autogenerado (`_get`/`_post`). Lo que evita el breaking change fantasma de la etapa 1.1. |
| `test_decimal_fields_are_strings` | si un schema del spec expone un campo `Decimal`, aparece como `"type": "string"`. Ancla la convención del repo en el contrato para que nadie la "arregle" a `number` sin darse cuenta. |

Este último test solo aplica cuando un router exponga DTOs con `Decimal`; hoy
`/health` no expone ninguno. Se escribe igual, parametrizado sobre
`components.schemas`, y pasa vacío — es la red que atrapa el primer endpoint de
mercado que llegue en las HU de la Épica B.

**Verificar:**
```bash
uv run pytest tests/test_openapi_contract.py -v
uv run pytest    # la suite completa sigue verde
```

---

## Etapa 1.5 — Gate de sincronización en el CI del backend · HU-A14 (AC2, espejo)

`.github/workflows/ci.yml` — el test de la etapa 1.4 ya falla dentro del
`uv run pytest` existente, así que **no se añade un job nuevo**. Se añade un solo
paso explícito antes de `pytest`, por legibilidad del log de CI (que el
desarrollador vea "contract drift" y no un test rojo entre veinte):

```yaml
      - name: Contrato OpenAPI sincronizado
        run: uv run python scripts/export_openapi.py --check
```

Va después de `ruff check .` y antes de `alembic upgrade head` (no necesita DB).

**Verificar (drift a propósito, smoke manual del DoD):**
```bash
# 1. cambiar la forma de la API sin regenerar
sed -i '' 's/"status": "ok"/"status": "ok", "uptime": 0/' apps/api/routers/health.py
uv run python scripts/export_openapi.py --check   # DEBE fallar con el mensaje de 4 pasos
git checkout apps/api/routers/health.py
uv run python scripts/export_openapi.py --check   # vuelve a exit 0
```

---

## Etapa 1.6 — Publicación del paquete versionado desde CI · HU-A10 (AC1)

**Archivo nuevo:** `.github/workflows/contract-release.yml`

```yaml
name: contract-release

on:
  push:
    tags: ["contract-v*"]

permissions:
  contents: read
  packages: write

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v6
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
          registry-url: "https://npm.pkg.github.com"

      # El tag es la unica fuente de la version publicada: si no coincide con
      # CONTRACT_VERSION, se publicaria una version que el codigo no declara.
      - name: El tag coincide con CONTRACT_VERSION
        run: |
          TAG_VERSION="${GITHUB_REF_NAME#contract-v}"
          CODE_VERSION="$(uv run python -c 'from apps.api.core.contract import CONTRACT_VERSION; print(CONTRACT_VERSION)')"
          test "$TAG_VERSION" = "$CODE_VERSION" || {
            echo "Tag $GITHUB_REF_NAME != CONTRACT_VERSION $CODE_VERSION"; exit 1; }

      # El snapshot commiteado debe ser el que genera el codigo de este tag.
      - name: El snapshot esta al dia
        run: uv sync && uv run python scripts/export_openapi.py --check

      - run: npm publish
        working-directory: contract
        env:
          NODE_AUTH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

**Procedimiento de release (va a `CONTRACT.md`):**
```bash
# tras mergear a main el cambio de contrato, con CONTRACT_VERSION ya subida
git checkout main && git pull
git tag contract-v0.2.0
git push origin contract-v0.2.0
```

**Verificar:** el workflow corre en verde y el paquete aparece en la pestaña
*Packages* del repo con la versión del tag. Un `npm publish` de una versión ya
existente falla — es la protección de inmutabilidad, no hay que añadir nada.

---

## Etapa 1.7 — Documentación del proceso · HU-A10 (AC4) + HU-A14 (AC3)

**Archivo nuevo:** `CONTRACT.md` en la raíz del backend. Es el documento que las
dos historias piden como "reproducible" y "documentado". Secciones:

1. **Qué es el contrato** — `contract/openapi.json`, generado por FastAPI, única
   fuente de verdad de la frontera back↔front.
2. **Cómo se regenera** (backend) — `uv run python scripts/export_openapi.py`,
   cuándo subir `CONTRACT_VERSION`, tabla de semver.
3. **Cómo se publica** — tag `contract-vX.Y.Z` → workflow → paquete npm.
4. **Cómo se consume** (frontend) — `npm i @…/flash-api-contract@X.Y.Z` con
   versión **exacta**, `npm run contract:types`, commitear `types.gen.ts`.
5. **Resolver una desincronización** — los dos escenarios, con el comando exacto:
   - *CI del backend rojo* (`export_openapi.py --check` falla): cambiaste la API
     sin regenerar → los 4 pasos del mensaje de error.
   - *CI del frontend rojo* (`contract:check` falla): subiste la dependencia del
     contrato sin regenerar tipos, o alguien editó `types.gen.ts` a mano →
     `npm run contract:types` + commit; si el type-check sigue rojo, el contrato
     trajo un breaking change y hay que adaptar el código que lo consume.
6. **Nota sobre `Decimal` → `string`** (supuesto 6): por qué los precios llegan
   como string y qué no hacer con ellos.
7. **README.md** — un enlace a `CONTRACT.md` en la sección de desarrollo.

---

## Etapa 1.8 — [REPO FRONTEND] Generación de tipos · HU-A10 (AC2, AC3)

> **SUPERSEDIDA por `.claude/plans/PLAN-HU-FRONTEND.md`.** Esta etapa se escribio
> antes de leer el repo frontend real, que usa **pnpm en workspace** (no npm),
> ya tiene la ruta del contrato protegida por ESLint
> (`apps/web/src/shared/infrastructure/contract/types.gen.ts`) y **prohibe**
> `openapi-fetch`: los tipos generados solo pueden importarse desde
> `infrastructure/mappers/**`. Ver §7 de ese documento. Lo de abajo se conserva
> como registro de la intencion; al ejecutar manda el plan del frontend.


Especificado aquí para que se ejecute allá sin rediseñar.

1. **Dependencia con versión fija** (`package.json`, sin `^` ni `~`):
   ```json
   "dependencies": { "@emmanuelcalad0615/flash-api-contract": "0.1.0" },
   "devDependencies": { "openapi-typescript": "^7", "openapi-fetch": "^0.13" }
   ```
   Más `.npmrc`: `@emmanuelcalad0615:registry=https://npm.pkg.github.com`.

2. **Script de generación** (`package.json`):
   ```json
   "contract:types": "openapi-typescript node_modules/@emmanuelcalad0615/flash-api-contract/openapi.json -o src/api/types.gen.ts"
   ```
   `src/api/types.gen.ts` **se commitea** (es lo que permite diffear el impacto
   del cambio de contrato en el PR, y lo que el chequeo de la etapa 1.9 compara).

3. **Cliente API tipado** (`src/api/client.ts`) — AC3:
   ```ts
   import createClient from "openapi-fetch";
   import type { paths } from "./types.gen";

   export const api = createClient<paths>({ baseUrl: import.meta.env.VITE_API_URL });
   ```
   El resto de la app llama `api.GET("/health")` y obtiene tipado de path,
   parámetros y respuesta desde el contrato. Ningún módulo declara a mano tipos
   de respuesta de la API.

**Verificar:** `npm run contract:types` → `git diff` limpio en una segunda
corrida; `tsc --noEmit` verde; una llamada a un path inexistente debe fallar el
type-check.

---

## Etapa 1.9 — [REPO FRONTEND] Chequeo de sincronización en CI · HU-A14

> **SUPERSEDIDA por `.claude/plans/PLAN-HU-FRONTEND.md`.** Esta etapa se escribio
> antes de leer el repo frontend real, que usa **pnpm en workspace** (no npm),
> ya tiene la ruta del contrato protegida por ESLint
> (`apps/web/src/shared/infrastructure/contract/types.gen.ts`) y **prohibe**
> `openapi-fetch`: los tipos generados solo pueden importarse desde
> `infrastructure/mappers/**`. Ver §7 de ese documento. Lo de abajo se conserva
> como registro de la intencion; al ejecutar manda el plan del frontend.


1. **Script `contract:check`** (`scripts/check-contract.mjs`), dos aserciones:

   a. *Versión declarada == versión instalada*: `package.json:dependencies` vs
      `node_modules/.../openapi.json:info.version`. Atrapa el lockfile
      desalineado (AC1).

   b. *Tipos regenerados*: genera a un temporal y compara byte a byte con
      `src/api/types.gen.ts` (AC2). Mensaje de fallo:
      ```
      Los tipos generados no corresponden al contrato @…/flash-api-contract@0.2.0.
      Regenera y commitea:
          npm run contract:types
          git add src/api/types.gen.ts
      Si el type-check sigue fallando, el contrato trae un cambio incompatible:
      ver CONTRACT.md §5 en el repo backend.
      ```

2. **Pipeline** (`.github/workflows/ci.yml` del frontend), en este orden — el
   chequeo de contrato va **primero** para que la desincronización se reporte
   como tal y no como cien errores de `tsc`:
   ```
   npm ci → npm run contract:check → npm run lint → npx tsc --noEmit → npm run build
   ```

3. **Test del script** (DoD pide pruebas): test unitario de la lógica de
   comparación con un contrato falso — caso sincronizado (exit 0) y caso
   desincronizado (exit 1 + mensaje). La misma disciplina del backend: lógica
   pura separada del I/O, testeable sin tocar red ni `node_modules` reales.

**Verificar (smoke del DoD, desincronización a propósito):**
```bash
echo "// drift" >> src/api/types.gen.ts
npm run contract:check     # DEBE fallar con el mensaje de arriba
git checkout src/api/types.gen.ts
npm run contract:check     # verde
```

---

## 3. Trazabilidad criterio → entregable

| Criterio | Entregable | Etapa |
|---|---|---|
| A10.1 export `openapi.json` | `scripts/export_openapi.py` + `contract/openapi.json` | 1.2 |
| A10.1 publicado versionado desde CI | `contract/package.json` + `contract-release.yml` | 1.3, 1.6 |
| A10.2 frontend con versión fija + `api-types` | dependencia exacta + `contract:types` | 1.8 |
| A10.3 cliente API usa los tipos | `openapi-fetch` tipado con `paths` | 1.8 |
| A10.4 regeneración documentada y reproducible | `CONTRACT.md` §2–4 | 1.7 |
| A14.1 CI valida tipos vs versión declarada | `check-contract.mjs` aserción (a) | 1.9 |
| A14.2 build falla con mensaje claro | aserción (b) + mensaje de 3 líneas | 1.9 |
| A14.2 (espejo backend) | `--check` en `ci.yml` + `test_committed_snapshot_matches_app` | 1.4, 1.5 |
| A14.3 procedimiento documentado | `CONTRACT.md` §5 | 1.7 |
| — (alcance aprobado, no es AC) envelope de error en el contrato | `ErrorResponse` + `responses` global + 3 tests | 1.2-bis |

## 4. Definición de Done — checklist de cierre

- [ ] Criterios de aceptación de A10 y A14 cubiertos (tabla §3).
- [ ] Pruebas: `tests/test_openapi_contract.py` (backend, 6 tests + los 3 de la
      Etapa 1.2-bis) + test del
      script de chequeo (frontend). Unitarias para la lógica pura de comparación,
      de integración contra la app real para el snapshot.
- [ ] CI verde en ambos repos: `ruff` + `--check` + `alembic upgrade head` +
      `pytest` (backend); `contract:check` + lint + type-check + build (frontend).
- [ ] Revisión visual de UI: **no aplica** (ningún cambio visual en esta HU).
- [ ] Smoke manual: los dos ejercicios de drift a propósito (etapas 1.5 y 1.9)
      fallan con el mensaje correcto y se recuperan.
- [ ] Zonas protegidas (UI base y tokens): intactas — este trabajo no toca UI.
- [ ] Publicación real verificada: `contract-v0.1.0` publicado y consumido por el
      frontend con `npm ci` limpio.

## 5. Riesgos

| Riesgo | Mitigación |
|---|---|
| Migrar a una organización de GitHub después de publicar obliga a re-publicar todo el historial de contrato bajo el nuevo scope | Decisión #3: si la migración está prevista, hacerla antes del primer `contract-v*`. |
| Snapshot commiteado genera conflictos de merge en PRs paralelos | Es un JSON con claves ordenadas: el conflicto es real (dos cambios de contrato a la vez) y debe resolverse regenerando, nunca a mano. Documentado en `CONTRACT.md` §5. |
| El equipo olvida subir `CONTRACT_VERSION` y publica un breaking como MINOR | El gate detecta *que* cambió, no *si rompe*. Mitigación de proceso: el diff de `contract/openapi.json` es obligatorio de revisar en el PR. Un chequeo automático de compatibilidad (oasdiff) queda fuera de alcance. |
| `openapi-typescript` cambia su salida entre versiones menores → `contract:check` rojo sin cambio de contrato | Pinnear `openapi-typescript` a versión exacta en el frontend. |

## 6. Fuera de alcance (explícito)

- Endpoint runtime de metadatos del contrato (supuesto 8).
- Chequeo automático de compatibilidad breaking (oasdiff / `openapi-diff`).
- Cualquier trabajo sobre worker, Redis, ingesta o endpoints de negocio.
