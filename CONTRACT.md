# El contrato de la API

Backend y frontend viven en repos separados. El precio de esa decisión es que los
tipos de la API cruzan una frontera de repos, y este documento es el mecanismo
que lo hace seguro: el contrato se **publica versionado** desde el CI del backend
y el frontend genera sus tipos a partir de él.

Trazabilidad: HU-A10 (contrato versionado + generación de tipos) y HU-A14
(chequeo de sincronización en CI).

## 1. Qué es el contrato

`contract/openapi.json` — el spec OpenAPI que genera FastAPI a partir del código
de `apps/api`. Es la **única fuente de verdad** de la frontera. Se commitea a
propósito: así un cambio de forma de la API aparece como diff en el PR, que es
donde el equipo debe verlo, y el CI puede detectar que alguien cambió la API sin
regenerarlo.

**No se edita a mano.** Lo escribe `scripts/export_openapi.py`.

La versión del contrato vive en `apps/api/core/contract.py` (`CONTRACT_VERSION`)
y es **independiente de la versión del paquete Python**: `pyproject.version`
versiona el deployable, `CONTRACT_VERSION` versiona la *forma de la API*. Un
arreglo interno del backend no debe obligar al frontend a subir su dependencia.

El mismo número vive en tres sitios, y `tests/test_openapi_contract.py` falla si
se separan:

| Sitio | Qué es |
|---|---|
| `apps/api/core/contract.py` → `CONTRACT_VERSION` | La fuente |
| `contract/openapi.json` → `info.version` | Generado desde la anterior |
| `contract/package.json` → `version` | Lo que se publica en npm |

## 2. Cómo se regenera

```bash
uv run python scripts/export_openapi.py          # escribe el snapshot
uv run python scripts/export_openapi.py --check  # solo compara (lo que corre el CI)
```

Cuando cambies la forma de la API:

1. `uv run python scripts/export_openapi.py`
2. Revisa el diff de `contract/openapi.json` — **¿rompe algo?** (tabla de abajo)
3. Sube `CONTRACT_VERSION` en `apps/api/core/contract.py` según semver
4. Sube `version` en `contract/package.json` al mismo número
5. Commitea los tres archivos juntos

### Semver del contrato

| Nivel | Cuándo | Ejemplos |
|---|---|---|
| `MAJOR` | Rompe al consumidor | Borrar o renombrar un endpoint o un campo de respuesta; volver requerido un campo opcional de request; estrechar un tipo |
| `MINOR` | Aditivo | Endpoint nuevo; campo opcional nuevo en una respuesta |
| `PATCH` | La forma no cambia | Descripciones, ejemplos, tags |

El CI detecta **que** el contrato cambió, no **si** rompe. Esa lectura es humana:
por eso el paso 2 es obligatorio en la revisión del PR.

## 3. Cómo se publica

El contrato se publica como paquete npm en GitHub Packages:
`@emmanuelcalad0615/flash-api-contract`.

Se dispara con un tag, no con cada merge — publicar es deliberado:

```bash
# con CONTRACT_VERSION y package.json ya subidos y mergeados a main
git checkout main && git pull
git tag contract-v0.2.0
git push origin contract-v0.2.0
```

`.github/workflows/contract-release.yml` valida que el tag coincida con
`CONTRACT_VERSION`, que el snapshot esté al día, y publica. Un `npm publish` de
una versión ya existente falla: la inmutabilidad la da el registro, no hace falta
protegerla aparte.

> El frontend usa **pnpm**; para publicar da igual el cliente. El tarball que
> sube `npm publish` es exactamente el que `pnpm add` descarga — el registro no
> distingue.

### Acceso al paquete (una vez, al publicar la primera versión)

Los dos repos son privados, así que leer el paquete requiere permiso explícito:

1. GitHub → *Your packages* → `flash-api-contract` → *Package settings*
2. Quitar los permisos heredados del repo backend (mientras hereda, la sección
   granular no aparece)
3. *Manage Actions access* → **Add repository** → `FlashResearchPlatform-Frontend`,
   rol **Read**

Con eso el CI del frontend lee el paquete con su propio `GITHUB_TOKEN`, sin
secretos nuevos. En local cada desarrollador necesita un **PAT clásico** con
`read:packages` (GitHub Packages no soporta fine-grained PAT) exportado como
`NODE_AUTH_TOKEN`.

## 4. Cómo se consume (frontend)

```bash
pnpm --filter web add "@emmanuelcalad0615/flash-api-contract@0.2.0" --save-exact
pnpm contract:types      # regenera types.gen.ts
git add apps/web/src/shared/infrastructure/contract/
```

Versión **exacta**, sin `^` ni `~`: subir de contrato es una decisión, no algo
que ocurra solo. Los tipos generados se commitean.

## 5. Resolver una desincronización

### CI del backend rojo — "El contrato exportado no coincide con contract/openapi.json"

Cambiaste la forma de la API sin regenerar el snapshot. Los cuatro pasos del §2.

### Conflicto de merge en `contract/openapi.json`

Dos PRs cambiaron el contrato a la vez. **No se resuelve a mano**: se toma la
versión de `main`, se rebasa, y se regenera con el script. El JSON tiene las
claves ordenadas, así que el conflicto es real (dos cambios de forma), no
cosmético.

### CI del frontend rojo — `contract:check` falla

- **`reason: version`** — subiste la dependencia y no regeneraste los tipos:
  `pnpm contract:types` y commitea.
- **`reason: stale-types`** — los tipos commiteados no corresponden al contrato
  instalado: igual, `pnpm contract:types`.

### CI del frontend rojo — `contract:check` pasa pero `type-check` falla

**Es el sistema funcionando, no un fallo del pipeline.** El contrato trajo un
cambio incompatible y el frontend tiene que adaptarse: el error apunta al mapper
que dejó de cuadrar. Ese es exactamente el runtime error que esta HU convierte en
error de compilación.

## 6. Los `Decimal` viajan como `string`

Convención del repo: precios y montos son siempre `Decimal`, nunca `float`.
Pydantic los serializa a **string** para no perder precisión, así que en el
contrato salen como `{"type": "string"}` y el frontend los recibe como `string`.

**No hagas aritmética con `Number()` sin pensarlo.** Para mostrar un precio
alcanza; para acumular (P&L, backtests) hace falta una librería decimal. La
conversión va en el mapper del frontend, nunca más adentro: el dominio recibe
`number` y no se entera de cómo viaja el dato.

`tests/test_openapi_contract.py::test_decimal_fields_are_strings` ancla esto en
el contrato para que nadie lo "arregle" a `number` sin darse cuenta.
