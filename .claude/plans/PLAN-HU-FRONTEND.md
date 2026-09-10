# PLAN-HU-FRONTEND — Implementación progresiva (Sprint 7, lado frontend)

> Repo objetivo: `/Users/andrew/Documents/Projects/flash-ui/FlashResearchPlatform-Frontend`
> (`FlashResearchPlatform-Frontend`, rama `main`, último commit `8449a17`).
> Este documento vive en el repo backend por pedido explícito; **el código que
> describe se implementa en el repo frontend**.
> Complementa `.claude/plans/PLAN-HU.md` (lado backend) y **corrige** su Etapa 1.8
> — ver §7.
> Autoridad de arquitectura del frontend: su `CLAUDE.md` (regla de dependencia
> entre capas, frontera del contrato, zona protegida) + `.claude/doc/architecture.md`.
> Reglas de ejecución: `.claude/skill/code_skill.md`.
>
> Prosa en español (convención de este repo). **Todo identificador, comentario,
> mensaje de error y nombre de test que se escriba en el frontend va en inglés**
> — su `CLAUDE.md` lo exige: "Source code (comments, error messages, UI strings,
> test names) is English only".

---

## 0. Estado real del frontend (hallazgos, no supuestos)

Verificado leyendo el repo, no inferido del backlog:

1. **El gestor de paquetes es `pnpm` en workspace, no npm.** `pnpm@11.22.0` fijado
   por `packageManager`, vía Corepack, Node `24.18.0` (`.nvmrc`), `engine-strict=true`
   en `.npmrc`. Todo lo que el PLAN-HU del backend escribió como `npm i` / `npm ci`
   se traduce a `pnpm add` / `pnpm install --frozen-lockfile`.

2. **La frontera del contrato ya está diseñada y a medio construir.** Existe
   `apps/web/src/shared/infrastructure/contract/` **con solo un `.gitkeep`**:
   `types.gen.ts` **no existe todavía**. Pero la regla que lo protege sí está
   activa en `eslint.config.js` — `no-restricted-imports` prohíbe importar
   `**/contract/types.gen*` desde cualquier archivo que no sea un mapper o el
   propio directorio `contract/`. La arquitectura ya está esperando el archivo.

3. **Hay 36 marcadores `FOLLOWUP(contract)` en 36 archivos** (19 mappers + 17
   adapters HTTP/WS), cada uno declarando exactamente en qué se convertirá:
   ```ts
   // FOLLOWUP(contract): once types.gen.ts exists this becomes
   //   type PriceBarDTO = components["schemas"]["PriceBar"];
   ```
   Es decir: el frontend ya escribió a mano los DTOs de **todos** los endpoints
   que espera, y dejó firmado el punto de reemplazo. Este plan es lo que los
   activa.

4. **Ninguno de esos DTOs existe todavía en el contrato del backend.** El
   `openapi.json` que producirá el backend en Sprint 7 contiene hoy **dos
   operaciones**: `GET /health` y `GET /`. No hay `PriceBar`, ni `TickerQuote`,
   ni `BreakoutBreadthPoint`, ni `/market/breakouts/history`. Los 36 marcadores
   apuntan a schemas que llegarán con la Épica B, no con esta HU. **Esto es el
   hecho que determina el alcance realista de este plan** (§1.6 y Etapa F.7).

5. **Los mappers tipan los precios como `number`.** `PriceBarDTO.c: number`,
   `TickerQuoteDTO.price: number`. El contrato del backend emite los campos
   `Decimal` como **`string`** (verificado ejecutando el `openapi()` real del
   backend: `{"type": "string", "pattern": "^(?!^[-+.]*$)[+-]?0*\\d*\\.?\\d*$"}`).
   Cuando llegue el endpoint real, la flip del mapper **no será un renombre de
   tipo: será un cambio de forma**. Ver Etapa F.7 y §5.

6. **El CI del frontend ya corre 6 pasos** (`lint · format:check · type-check ·
   test · build`, con `pnpm install --frozen-lockfile`). `pnpm format:check`
   corre sobre todo el repo — un `types.gen.ts` generado que no cumpla el estilo
   de Prettier **rompe el CI**. Hay que decidirlo explícitamente (Etapa F.2).

7. **`pnpm test` solo recoge `apps/web/src/**` y `packages/ui/**`**
   (`vitest.config.ts` agrupa dos *projects*; el de web tiene
   `include: ["src/**/*.test.{ts,tsx}"]`). Un script de chequeo puesto en
   `scripts/` del root **no tendría sus tests ejecutados**, y el DoD pide
   pruebas. Se resuelve en la Etapa F.4.

8. **No hay `vite-node` ni `tsx` instalados.** Un script CLI en TypeScript no se
   puede ejecutar hoy sin añadir dependencia; el checker se escribe en `.mjs`
   plano (Etapa F.4).

---

## 1. Supuestos y decisiones

Marcados **[CONFIRMAR]** los que cambian el trabajo si la respuesta es otra.

1. **Consumo del contrato: dependencia npm con versión exacta**, tal como pide
   A10.2 — `"@emmanuelcalad0615/flash-api-contract": "0.1.0"`, sin `^` ni `~`,
   en `apps/web/package.json` (no en el root: el que consume la API es la app).

2. **Acceso al paquete — resuelto, no [CONFIRMAR].** Verificado contra la
   documentación de GitHub y contra el estado real de los repos: **ambos son
   privados** (`api.github.com` responde 404 sin autenticar) y el owner es una
   **cuenta personal**, no una organización. Consecuencias:
   - El registro npm de GitHub **sí soporta permisos granulares** (a diferencia
     de Maven/Gradle, que son repository-scoped y no se pueden abrir a otro
     repo). Corrijo lo que asumí en la primera versión de este plan: el
     `GITHUB_TOKEN` del CI del frontend **sí puede** leer el paquete, una vez se
     le concede acceso explícito. **El CI no necesita ningún secreto nuevo.**
   - En local **sí hace falta un token por desarrollador**: un **PAT clásico**
     con scope `read:packages`. Los *fine-grained* PAT **no están soportados**
     por GitHub Packages — es un detalle que cuesta una tarde si se descubre a
     mano.
   - El procedimiento exacto y su orden están en §8.

3. **`types.gen.ts` se commitea.** Es lo que permite ver en el diff del PR qué
   cambió el contrato, y es el artefacto contra el que el CI compara (A14.2).

4. **La "versión declarada" de A14.1 se materializa en un archivo:**
   `apps/web/src/shared/infrastructure/contract/contract.lock.json` con
   `{ name, version, sha256 }` del `openapi.json` que generó los tipos. Sin él, la
   verificación "los tipos corresponden a la versión declarada" solo se puede
   inferir; con él, es una comparación de tres líneas y el PR **dice** que el
   contrato se movió. Cuesta 6 líneas y un test.

5. **El cliente API NO se reemplaza por `openapi-fetch`.** Aquí corrijo la Etapa
   1.8 del plan del backend: `openapi-fetch` tipa las llamadas *en el adapter*,
   y el `CLAUDE.md` del frontend prohíbe exactamente eso — los tipos generados
   solo pueden importarse desde `infrastructure/mappers/**`, y hay una regla de
   ESLint que lo hace fallar. El mapper es el amortiguador deliberado: "when the
   backend renames a field, the break surfaces as one line in one mapper instead
   of in every component". Meter `openapi-fetch` sería romper la arquitectura del
   repo para satisfacer la letra de un AC. **A10.3 se cumple por la vía del repo**:
   el DTO que el adapter pasa a `HttpClient.get<T>` viene tipado desde el mapper,
   y el mapper lo toma de `components["schemas"][...]`. La cadena de tipos llega
   igual al cliente, respetando la frontera.

6. **Alcance realista de la migración de los 36 `FOLLOWUP(contract)`:** en Sprint
   7 se puede flipear **cero** mappers, porque el contrato del backend todavía no
   contiene ninguno de esos schemas (§0.4). Este plan **no simula** que sí. En su
   lugar entrega: el circuito completo (F.1–F.5), un mecanismo que convierte los
   36 marcadores en una checklist automatizada que se va vaciando sola conforme
   el backend publique endpoints (F.6), y la política de flip documentada (F.7).
   Lo que queda bloqueado se declara como bloqueado, con la condición de
   desbloqueo nombrada.

7. **`openapi-typescript` se pinnea a versión exacta.** Su salida cambia entre
   menores; con rango, el `contract:check` se pondría rojo sin que nadie tocara
   el contrato.

---

## 2. Restricciones de arquitectura del frontend que este plan respeta

- **La regla de dependencia entre capas no se toca.** Nada nuevo importa hacia
  arriba; `ui` sigue sin ver `infrastructure`.
- **`types.gen.ts` solo se importa desde `infrastructure/mappers/**`** — ya
  garantizado por `no-restricted-imports`; este plan no relaja esa regla, la usa.
- **Zona protegida intacta**: ni `packages/ui`, ni `global.css`, ni los tokens.
  Esta HU no produce un solo píxel.
- **El interruptor mock/http no se toca**: `VITE_DATA_SOURCE` se lee donde se lee
  hoy. Los adapters mock siguen pasando por los mismos mappers, así que cuando un
  DTO cambie de forma, **el modo mock también lo ejercita** — que es justo lo que
  hace útil el diseño actual.
- **`HttpClient` se queda como está**: transporte mínimo, sin reintentos ni
  interceptores. Este plan no le añade nada.

---

# FASE 1 — SPRINT 7 (frontend)

Orden de ejecución; cada etapa trae su verificación (code_skill §4). F.1–F.5 son
un PR ("contract pipeline"); F.6–F.8 pueden ir en un segundo PR.

---

## Etapa F.1 — Acceso al registro y dependencia pinneada · HU-A10 (AC2)

**Precondición:** el backend publicó `contract-v0.1.0` (Etapa 1.6 del PLAN-HU) y
el acceso al paquete está concedido (§8.1).

1. **`.npmrc`** (root) — añadir sin tocar la línea existente:
   ```
   engine-strict=true
   @emmanuelcalad0615:registry=https://npm.pkg.github.com
   //npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}
   ```
   `${NODE_AUTH_TOKEN}` se expande desde el entorno: el `.npmrc` sigue siendo
   commiteable, el token nunca entra al repo.

2. **Dependencia en `apps/web/package.json`**, versión exacta:
   ```bash
   pnpm --filter web add "@emmanuelcalad0615/flash-api-contract@0.1.0" --save-exact
   pnpm --filter web add -D "openapi-typescript@7.9.1" --save-exact   # fijar la que resuelva
   ```

3. **Documentar el requisito de token** en el README del frontend (sección
   *Getting started*): sin `NODE_AUTH_TOKEN` exportado, `pnpm install` falla con
   401 y el mensaje de GitHub no explica por qué.

**Verificar:**
```bash
pnpm install --frozen-lockfile
node -p "require('@emmanuelcalad0615/flash-api-contract/openapi.json').info.version"   # 0.1.0
```

---

## Etapa F.2 — Generación de tipos · HU-A10 (AC2)

1. **Script** en `apps/web/package.json`:
   ```json
   "contract:types": "openapi-typescript ../../node_modules/@emmanuelcalad0615/flash-api-contract/openapi.json -o src/shared/infrastructure/contract/types.gen.ts"
   ```
   Con hoisting de pnpm la ruta real puede ser `node_modules/...` dentro de
   `apps/web`; resolverla en la implementación con
   `node -e "console.log(require.resolve('@emmanuelcalad0615/flash-api-contract/openapi.json'))"`
   en vez de hardcodear una ruta que dependa del layout del store.
   Alias en el root: `"contract:types": "pnpm --filter web contract:types"`.

2. **El archivo generado queda fuera de las herramientas de estilo** — es código
   generado, no código revisable:
   - `.prettierignore`: añadir
     `apps/web/src/shared/infrastructure/contract/types.gen.ts`.
     **Sin esto `pnpm format:check` rompe el CI** (§0.6).
   - `eslint.config.js`, el bloque `{ ignores: [...] }` de arriba: añadir el mismo
     path. Un archivo generado no debe pelear con `@typescript-eslint`.
   - `sonar-project.properties`, `sonar.exclusions`: añadir el mismo path — no
     tiene sentido medir cobertura ni *code smells* sobre tipos generados.

3. **Primera generación y commit** de `types.gen.ts`.

**Verificar:**
```bash
pnpm contract:types && git status --short   # aparece types.gen.ts
pnpm contract:types && git diff --exit-code apps/web/src/shared/infrastructure/contract/types.gen.ts  # segunda corrida: sin diff
pnpm format:check && pnpm lint && pnpm type-check
```

---

## Etapa F.3 — Sello de la versión declarada · HU-A14 (AC1)

**Archivo nuevo (generado):**
`apps/web/src/shared/infrastructure/contract/contract.lock.json`
```json
{
  "name": "@emmanuelcalad0615/flash-api-contract",
  "version": "0.1.0",
  "sha256": "<sha256 del openapi.json que generó types.gen.ts>"
}
```
Lo escribe el mismo comando que genera los tipos (`contract:types` pasa a ser
`node scripts/generate-contract.mjs`, que genera **y** sella, para que no puedan
desincronizarse entre sí).

**Verificar:** `git diff` muestra `version` y `sha256` cambiando juntos cuando se
sube la dependencia, y nunca por separado.

---

## Etapa F.4 — Checker de sincronización · HU-A14 (AC1, AC2)

**Archivo nuevo:** `scripts/check-contract.mjs` — Node plano (§0.8), con la
lógica pura exportada aparte del I/O, igual que el adapter de Polygon separa
traducción de red:

```js
// Pure comparison logic — exported so it can be tested without touching disk.
export function compareContract({ declared, installed, lock, generated, committed }) { ... }
// -> { ok: true } | { ok: false, reason: "version" | "stale-types", message: "..." }
```

Tres aserciones, en este orden (la primera que falla es la que se reporta, porque
la segunda sería ruido derivado de la primera):

| # | Compara | Atrapa |
|---|---|---|
| a | `apps/web/package.json` dep version ↔ `openapi.json:info.version` instalado | lockfile desalineado, `pnpm add` a medias |
| b | `contract.lock.json` `{version, sha256}` ↔ el `openapi.json` instalado | subiste la dependencia y no regeneraste |
| c | `types.gen.ts` commiteado ↔ regeneración a un temporal, byte a byte | regeneraste mal, o alguien editó el generado a mano |

**Mensaje de fallo** (inglés, como todo el código del repo):
```
Generated contract types are stale.
  declared: @emmanuelcalad0615/flash-api-contract@0.2.0
  types generated from: 0.1.0

Regenerate and commit:
    pnpm contract:types
    git add apps/web/src/shared/infrastructure/contract/

If type-check still fails afterwards, the contract carries a breaking change:
see CONTRACT.md §5 in the backend repo.
```

**Tests (DoD).** `pnpm test` no ve `scripts/` (§0.7), así que se añade un tercer
*project* de Vitest — 6 líneas, es la solución honesta y no mueve lógica de sitio:

- `scripts/vitest.config.ts`:
  ```ts
  export default defineConfig({
    test: { name: "scripts", environment: "node", include: ["**/*.test.mjs"] },
  });
  ```
- `vitest.config.ts` (root): añadir `"scripts/vitest.config.ts"` a `projects`.
- `scripts/check-contract.test.mjs`: caso sincronizado → `{ok: true}`; versión
  desalineada → `reason: "version"`; tipos rancios → `reason: "stale-types"`;
  y que el mensaje nombre las dos versiones (un mensaje que no dice *cuál* es
  cuál obliga a investigar, que es justo lo que A14.2 quiere evitar).

**Scripts** en el root `package.json`:
```json
"contract:types": "node scripts/generate-contract.mjs",
"contract:check": "node scripts/check-contract.mjs"
```

**Verificar:**
```bash
pnpm contract:check     # exit 0
pnpm test               # el project "scripts" aparece y pasa
```

---

## Etapa F.5 — CI · HU-A14 (AC2)

`.github/workflows/ci.yml`, un paso nuevo **inmediatamente después de instalar y
antes de `lint`**. El orden importa: si el contrato se movió, `type-check`
escupiría decenas de errores derivados y el desarrollador perdería el tiempo
leyendo síntomas en vez de la causa.

```yaml
      - name: Contrato sincronizado
        run: pnpm contract:check
```

Si se eligió la salida (b) de §8.4 (secret propio en vez de `GITHUB_TOKEN`), el
paso de instalación necesita además:
```yaml
        env:
          NODE_AUTH_TOKEN: ${{ secrets.CONTRACT_READ_TOKEN }}
```

**Verificar (smoke manual del DoD — desincronización a propósito):**
```bash
echo "// drift" >> apps/web/src/shared/infrastructure/contract/types.gen.ts
pnpm contract:check          # DEBE fallar con reason stale-types y el mensaje de 3 bloques
git checkout apps/web/src/shared/infrastructure/contract/types.gen.ts
pnpm contract:check          # verde
```

---

## Etapa F.6 — De 36 marcadores a una checklist automatizada · HU-A10 (AC4)

Este es el paso que convierte el plan en algo que sigue trabajando después del
sprint. Los 36 `FOLLOWUP(contract)` son hoy comentarios que nadie releerá; el
riesgo real no es olvidarse de crearlos, es **no enterarse el día en que el
backend por fin publica el schema** y seguir con el DTO a mano, que es
exactamente la desincronización que HU-A14 quiere matar.

**Script nuevo:** `scripts/contract-coverage.mjs` → `pnpm contract:coverage`.
Lee los nombres de schema declarados en los marcadores
(`components["schemas"]["X"]`) de los 19 mappers, los cruza con las claves de
`components.schemas` en `types.gen.ts`, e imprime:

```
Contract coverage — @…/flash-api-contract@0.1.0
  ready to migrate (2):
    dashboard/price-bar.mapper.ts        PriceBar
    dashboard/ticker-quote.mapper.ts     TickerQuote
  awaiting backend (17):
    market-pulse/breakout.mapper.ts      BreakoutBreadthPoint
    ...
```

**Guard test recomendado** (`apps/web/src/shared/infrastructure/contract/contract-coverage.test.ts`):
falla si un mapper sigue declarando su DTO a mano **cuando el schema ya existe**
en `types.gen.ts`. Es la versión automática del marcador: el día que el backend
publique `PriceBar`, el CI del frontend se pone rojo pidiendo la flip, en vez de
esperar a que alguien lea un comentario. Coste: ~30 líneas y un test. Es
opcional en el sentido de que ningún AC lo pide; recomendado en el sentido de que
sin él la Etapa F.7 depende de la memoria del equipo.

**Verificar:** con el contrato v0.1.0 actual, la lista *ready to migrate* sale
vacía y las 19 quedan en *awaiting backend* — que es la verdad del estado hoy.

---

## Etapa F.7 — Flip de mappers y política de migración · HU-A10 (AC3)

**Estado honesto:** hoy la lista de flip es **vacía** (§0.4). Lo que sí se
entrega en este sprint:

1. **Prueba de que el circuito cierra de punta a punta.** Un test de nivel de
   tipos en `apps/web/src/shared/infrastructure/contract/types.gen.test.ts` que
   importa `paths` y afirma que `paths["/health"]["get"]` existe y que su
   respuesta 200 es un objeto. No inventa una feature; demuestra que los tipos
   generados compilan, se resuelven por el alias y son consumibles desde donde la
   regla de ESLint permite. Es el equivalente frontend del smoke test manual.

2. **La política de flip, documentada** (va al `CLAUDE.md` del frontend, sección
   *The contract boundary*, y a `CONTRACT.md`):
   > Cuando el backend publique el endpoint de un mapper, la HU que lo consuma
   > hace la flip en ese mismo PR: se borra la `interface XDTO` a mano, se pone
   > `type XDTO = components["schemas"]["X"]`, se borra el marcador
   > `FOLLOWUP(contract)`, y **se ajusta el mapper a la forma real**, no solo al
   > nombre.

3. **La advertencia que ahorrará el primer incidente:** los `Decimal` del backend
   llegan como **`string`**, y los 19 mappers asumen `number` (§0.5). La flip de
   `price-bar.mapper.ts` no será `c: number` → `c: number`, será `c: string` y el
   mapper tendrá que convertir. Dónde: **en el mapper, nunca después** — el
   dominio recibe `number` y no se entera de que el transporte usa string. Y la
   conversión es una decisión de producto, no un `Number()` automático: para
   precios de display alcanza `Number()`, para aritmética acumulada (P&L,
   backtests) hace falta una librería decimal. Se decide cuando llegue la HU que
   lo necesite; lo que este plan fija es **dónde** se decide.

**A10.3 — estado:** cubierto en su forma verificable (la cadena de tipos llega al
cliente por la vía de los mappers, §1.5) y **parcialmente bloqueado** en su forma
plena: no hay endpoint de negocio en el contrato que un adapter real pueda
consumir tipado. **Condición de desbloqueo, nombrada:** el primer endpoint de la
Épica B publicado en `contract-v0.2.0`. Ese día, la flip de su mapper cierra el
AC sin trabajo adicional de infraestructura — todo lo demás ya estará puesto.

---

## Etapa F.8 — Documentación · HU-A10 (AC4) + HU-A14 (AC3)

1. **`CLAUDE.md` del frontend**, sección *The contract boundary* — reemplazar
   "holds types generated from the backend's `openapi.json`" (que hoy describe un
   archivo inexistente) por el flujo real: de dónde viene, cómo se regenera, qué
   lo verifica, y la política de flip de la Etapa F.7.

2. **README del frontend** — en *Getting started*: el requisito de
   `NODE_AUTH_TOKEN` (§8.2) y el comando `pnpm contract:types`.

3. **Procedimiento de desincronización** (AC A14.3) — vive en `CONTRACT.md` del
   **backend** (Etapa 1.7 del PLAN-HU), que es la fuente única; el frontend
   enlaza a él. Los dos escenarios del lado frontend:
   - `contract:check` rojo con `reason: version` → subiste la dependencia sin
     regenerar: `pnpm contract:types` + commit.
   - `contract:check` verde pero `type-check` rojo → el contrato trae un cambio
     incompatible: hay que adaptar el mapper. **Es el sistema funcionando**, no
     un fallo del pipeline.

4. **`DECISIONES.md`** — una entrada: por qué el contrato se consume por mappers
   y no con `openapi-fetch` (§1.5). Sin eso, el próximo que lea el AC va a
   intentar meter `openapi-fetch` otra vez.

---

## 3. Trazabilidad criterio → entregable

| Criterio | Entregable | Etapa |
|---|---|---|
| A10.2 contrato con versión fija | dep exacta + `.npmrc` con registro | F.1 |
| A10.2 genera `api-types` | `contract:types` + `types.gen.ts` commiteado | F.2 |
| A10.3 el cliente usa los tipos generados | cadena mapper→adapter→`HttpClient` tipada; test de tipos sobre `paths` | F.7 (parcial, ver §4) |
| A10.4 regeneración documentada y reproducible | `CLAUDE.md` + README + `CONTRACT.md` | F.8 |
| A14.1 tipos ↔ versión declarada | `contract.lock.json` + aserciones (a) y (b) | F.3, F.4 |
| A14.2 build falla con mensaje claro | aserción (c) + mensaje de 3 bloques + paso de CI | F.4, F.5 |
| A14.3 procedimiento documentado | `CONTRACT.md` §5 (backend) + enlaces | F.8 |
| — (deuda de los 36 marcadores) | `contract:coverage` + guard test | F.6 |

## 4. Definición de Done — checklist de cierre

- [ ] AC de A10 y A14 cubiertos, **con la excepción declarada de A10.3**
      (Etapa F.7): cubierto en su forma verificable, pleno cuando exista el
      primer endpoint de negocio en el contrato. No se marca como hecho lo que
      no lo está.
- [ ] Pruebas: `scripts/check-contract.test.mjs` (3 casos + mensaje),
      `types.gen.test.ts`, y el guard de cobertura si se aprueba F.6.
- [ ] CI verde: `contract:check` → lint → format:check → type-check → test → build.
- [ ] Revisión visual de UI: **no aplica**, esta HU no toca UI ni tokens.
- [ ] Smoke manual: drift a propósito (F.5) falla con el mensaje correcto y se
      recupera; `pnpm install --frozen-lockfile` limpio desde cero con el token
      configurado.
- [ ] Zona protegida (`packages/ui`, tokens, `global.css`): sin cambios —
      verificable con `git diff --stat` sobre esas rutas.
- [ ] `pnpm contract:coverage` imprime el estado real (19 *awaiting backend*).

## 5. Riesgos

| Riesgo | Mitigación |
|---|---|
| Un desarrollador nuevo clona, corre `pnpm install` y recibe un 401 que no explica nada | El PAT clásico documentado en el README (§8.2) y el mensaje de diagnóstico en el paso de instalación |
| El paquete no se puede conceder al repo frontend (UI no disponible para el tipo de cuenta) | Salida (b) de §8.4: secret `CONTRACT_READ_TOKEN`. Salida (c): vendorizar el `openapi.json` |
| Los 19 DTOs a mano divergen del contrato real y nadie se entera | `contract:coverage` + guard test (F.6): el CI avisa el día que el schema aparece |
| `Decimal` → `string` rompe 19 mappers de golpe cuando llegue la Épica B | Documentado en F.7 antes de que ocurra; la conversión se localiza en el mapper; el modo mock ejercita el mismo mapper, así que se detecta sin backend |
| `types.gen.ts` en `.prettierignore` = código que nadie formatea | Es generado; el byte-compare de F.4 es lo que garantiza su integridad, no Prettier |
| `openapi-typescript` cambia su salida en un menor → `contract:check` rojo sin cambio de contrato | Versión exacta (§1.7); actualizarla es un PR deliberado que regenera |
| El tercer *project* de Vitest ralentiza `pnpm test` | Un único archivo de test en entorno `node`, sin jsdom: coste despreciable |

## 6. Fuera de alcance (explícito)

- Migrar los 36 `FOLLOWUP(contract)` (bloqueado por el contrato del backend, §1.6).
- `openapi-fetch` o cualquier cliente generado (§1.5 — rompería la regla de capas).
- Autenticación en `HttpClient` (sigue pendiente de la capa de sesión).
- Tipos generados para WebSocket: el contrato OpenAPI no cubre WS; los 3 adapters
  `ws-*` mantienen sus DTOs a mano hasta que exista un contrato de streaming
  (AsyncAPI o equivalente). **No es un olvido: es un hueco real de la frontera**
  y conviene que quede escrito.
- Chequeo automático de *breaking changes* del contrato (queda en el backend).
- **Consumir** `ErrorResponse` en el frontend. El backend aprobó declarar su
  envelope de error en el contrato (`PLAN-HU.md` Etapa 1.2-bis), así que
  `components["schemas"]["ErrorResponse"]` **llegará ya en `contract-v0.1.0`** y
  aparecerá en `types.gen.ts` sin trabajo extra. Tiparse contra él —hoy
  `HttpError` en `shared/infrastructure/http/http-client.ts` no mira el cuerpo de
  la respuesta— es una mejora real, pero no la pide ningún AC de A10/A14: entra
  como HU aparte cuando exista una pantalla que muestre errores del backend.

## 7. Correcciones al PLAN-HU.md del backend

La Etapa 1.8 del plan del backend se escribió antes de leer este repo y contiene
tres cosas incorrectas para él. Al ejecutar, mandan las de este documento:

| PLAN-HU.md (backend) dice | Realidad del repo frontend |
|---|---|
| `npm ci`, `npm i`, `npm run` | `pnpm` en workspace, vía Corepack, Node 24.18.0 |
| tipos en `src/api/types.gen.ts` | `apps/web/src/shared/infrastructure/contract/types.gen.ts` (ruta ya protegida por ESLint) |
| cliente `openapi-fetch` tipado con `paths` | Prohibido por la regla de capas: los tipos generados solo entran por `infrastructure/mappers/**` (§1.5) |
| `scripts/check-contract.mjs` con test | Correcto, pero necesita un tercer *project* de Vitest o sus tests no corren (§0.7, F.4) |

Conviene aplicar esta corrección sobre `.claude/plans/PLAN-HU.md` §1.8 cuando se
ejecute, para que los dos documentos no se contradigan.

---

## 8. Resolución de los dos puntos operativos

Hechos verificados (no supuestos) sobre los que se apoya esta sección:

- `emmanuelcalad0615` es una **cuenta personal** (`"type": "User"`), no una organización.
- **Los dos repos son privados** — `GET api.github.com/repos/...` devuelve 404 sin
  autenticar, para backend y frontend.
- El **registro npm de GitHub soporta permisos granulares** (Container, npm, NuGet
  y RubyGems sí; Maven y Gradle son repository-scoped y no admiten esto).
- **Los fine-grained PAT NO están soportados** por GitHub Packages: hace falta un
  **PAT clásico**.

### 8.1 El CI del frontend: cero secretos nuevos

Con permisos granulares, el `GITHUB_TOKEN` del repo frontend puede leer un
paquete publicado desde el repo backend, si se le concede el acceso. Orden:

1. **Publicar una vez.** No se puede conceder acceso a un paquete que aún no
   existe. Sugerencia de secuencia: el backend publica un **`contract-v0.0.1`
   desechable** (el `openapi.json` con solo `/health`, que es lo que hay hoy) en
   cuanto termine su Etapa 1.6. Sirve para desbloquear todo lo de abajo y para
   probar el circuito completo del frontend **antes** de que exista contenido
   real que migrar.
2. **Soltar los permisos heredados.** GitHub → foto de perfil → *Your packages* →
   `flash-api-contract` → *Package settings*. Por defecto el paquete **hereda**
   los permisos del repo backend, y mientras herede, la sección granular no
   aparece: hay que quitar la herencia primero ("remove the package's inherited
   permissions").
3. **Conceder al repo frontend.** En *Manage Actions access* → **Add repository**
   → `FlashResearchPlatform-Frontend` → rol **Read**.
4. **Declarar el permiso en el workflow del frontend** (`.github/workflows/ci.yml`):
   ```yaml
   jobs:
     verify:
       permissions:
         contents: read
         packages: read      # sin esto el GITHUB_TOKEN no lleva el scope
   ```
   y en el paso de instalación:
   ```yaml
       - name: Instalar dependencias
         run: pnpm install --frozen-lockfile
         env:
           NODE_AUTH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
   ```

**Verificar:** el job instala en verde y
`node -p "require('@emmanuelcalad0615/flash-api-contract/openapi.json').info.version"`
imprime la versión dentro del runner.

### 8.2 Desarrollo local: un PAT clásico por persona

No hay forma de evitarlo con repos privados: el registro pide token siempre.

1. GitHub → *Settings* → *Developer settings* → *Personal access tokens* →
   **Tokens (classic)** → *Generate new token (classic)*.
   Scope: **`read:packages`** (nada más; si el repo backend es privado, GitHub
   pide además `repo` para poder resolver el paquete asociado).
2. Exportarlo en el shell (`~/.zshrc`):
   ```bash
   export NODE_AUTH_TOKEN=ghp_xxxxxxxxxxxx
   ```
   El `.npmrc` commiteado ya lo consume con `${NODE_AUTH_TOKEN}`, así que el
   token **nunca entra al repo**.
3. **Documentarlo en el README con el síntoma**, no solo con el remedio: sin la
   variable, `pnpm install` falla con `401 Unauthorized` y el mensaje no menciona
   ni el token ni el scope. Quien no lo haya leído antes pierde media hora.

### 8.3 Verificación de humo del acceso (antes de tocar código)

```bash
export NODE_AUTH_TOKEN=ghp_...
npm view @emmanuelcalad0615/flash-api-contract version \
  --registry=https://npm.pkg.github.com
# imprime 0.0.1 -> acceso OK; 401/404 -> el token o la concesión están mal
```

### 8.4 Salidas si algo de lo anterior se atasca

La vía principal es la de §8.1 (permisos granulares + `GITHUB_TOKEN`); estas dos
son los planes B y C.

- **(b) Secret `CONTRACT_READ_TOKEN`.** Un PAT clásico con `read:packages`
  guardado como secret del repo frontend, usado en lugar de `GITHUB_TOKEN` en el
  paso de instalación. Funciona siempre, a cambio de un secreto que caduca y hay
  que rotar. Es el plan B, no el plan A.
- **(c) Vendorizar el contrato.** El backend adjunta `openapi.json` como *asset*
  de un GitHub Release; el frontend lo commitea en
  `shared/infrastructure/contract/openapi.json` junto a `contract.lock.json`, y
  un `pnpm contract:pull` lo actualiza usando las credenciales del desarrollador.
  **El CI deja de necesitar credenciales por completo** — solo compara archivos
  commiteados. Se pierde la letra de A10.2 ("dependencia con versión fija") y se
  conserva su intención: versión fija, reproducible y verificable en CI. Es la
  opción más barata de operar entre dos repos privados; conviene tenerla en
  cuenta si el equipo crece y el ritual del PAT empieza a estorbar.

---

### 8.5 El archivo generado frente a Prettier, ESLint y Sonar

Tres exclusiones, una por herramienta. La única **obligatoria** es la de
Prettier: sin ella el CI del frontend se rompe en el paso `format:check` en
cuanto se commitee el primer `types.gen.ts`.

1. **`.prettierignore`** — añadir al final:
   ```
   # Código generado desde el contrato del backend: lo formatea openapi-typescript,
   # no nosotros. Ver CONTRACT.md.
   apps/web/src/shared/infrastructure/contract/types.gen.ts
   ```

2. **`eslint.config.js`** — en el bloque `{ ignores: [...] }` del principio, que
   hoy dice `["**/dist/**", "**/node_modules/**", ".claude/sampleUI/**"]`:
   ```js
   { ignores: [
       "**/dist/**",
       "**/node_modules/**",
       ".claude/sampleUI/**",
       "apps/web/src/shared/infrastructure/contract/types.gen.ts",
     ] },
   ```
   No es obligatorio hoy, pero la salida de `openapi-typescript` puede disparar
   reglas de `@typescript-eslint` sobre código que nadie va a editar a mano.
   Ojo: **esto no toca** la regla `no-restricted-imports` que protege la
   frontera; esa mira a los *importadores*, no al archivo generado, y ya excluye
   el directorio `contract/**` por su cuenta.

3. **`sonar-project.properties`** — añadir a `sonar.exclusions`:
   ```
   sonar.exclusions=\
     .claude/**,\
     ...,\
     apps/web/src/shared/infrastructure/contract/types.gen.ts
   ```
   Medir *code smells* o cobertura sobre tipos generados solo produce deuda
   ficticia en el panel.

**Lo que NO se excluye, a propósito:** `pnpm type-check`. El archivo generado
**tiene** que ser type-checkeado — es exactamente el mecanismo por el que un
cambio de contrato aparece como error de compilación en el mapper que dejó de
cuadrar. Excluirlo del type-check anularía HU-A14 entera.

**Alternativa considerada y descartada:** pasar la salida por Prettier dentro de
`generate-contract.mjs`, y así no necesitar ninguna exclusión. Funciona, pero
mete a Prettier dentro de la comparación byte a byte del `contract:check`: con
`prettier: "^3.9.6"` (rango, no versión exacta), un menor que cambie el formato
pondría el CI rojo sin que nadie hubiera tocado el contrato. Serían **dos**
herramientas capaces de romper el chequeo en vez de una. Si aun así se prefiere
esta vía —tener cero excepciones en el repo tiene su valor— hay que pinnear
Prettier a versión exacta al mismo tiempo.

**Verificar las tres:**
```bash
pnpm contract:types
pnpm format:check     # verde: Prettier ni mira el archivo
pnpm lint             # verde
pnpm type-check       # verde, Y sí analiza types.gen.ts
```
