# Reporte — Preparación del tag `contract-v0.1.0`

> Fecha: 2026-09-12 · Rama: `app/contractpre` (idéntica a `main`, commit `20a8be1`,
> sin divergencia — `git merge-base HEAD main` == `HEAD` == `main`).
> Continúa el trabajo de `PR-DESCRIPTION-SPRINT7.md` y
> `REPORTE-IMPLEMENTACION-SPRINT7.md`. Referencia cruzada:
> `.claude/plans/PLAN-HU-FRONTEND.md` §0.9 y §10 (registro de verificación
> 2026-09-12), que documenta el mismo bloqueo desde el lado del repo frontend.

## Contexto

`PR-DESCRIPTION-SPRINT7.md` deja explícito en su sección "Pendiente (fuera de
este PR)" que, tras el merge de PR #3 a `main`, quedan tres pasos para
desbloquear el consumo del contrato en el frontend:

1. Publicar el tag `contract-v0.1.0`.
2. Conceder acceso de lectura al paquete npm para el repo frontend.
3. Arrancar el consumo del contrato en el frontend (bloqueado por los dos
   anteriores).

Verificado hoy contra el repo real: `git tag -l` (tras `git fetch --tags`)
seguía vacío y `contract/package.json` seguía en `0.1.0` sin publicar — es
decir, ninguno de los tres pasos se había dado todavía.

## Qué se hizo en esta sesión

Se creó el tag **localmente**, sin empujarlo:

```bash
git tag contract-v0.1.0
```

El tag apunta a `20a8be1` (merge de PR #3 a `main`), que es el mismo commit en
el que está parada `app/contractpre` ahora mismo — no hace falta mergear nada
más antes de publicar.

**Deliberadamente no se ejecutó `git push origin contract-v0.1.0`.** Ese push
dispara `contract-release.yml`, que corre un `npm publish` real contra
`npm.pkg.github.com`. A diferencia del tag local (reversible con
`git tag -d`), una vez publicada una versión en un registro npm-compatible no
se puede volver a publicar el mismo número tras borrarla — un error en ese
primer publish obligaría a subir a `0.1.1` (y `contract-release.yml` exige que
el tag coincida con `CONTRACT_VERSION` en código, así que sería un cambio de
código, no solo de git). Es un paso vivo sobre un sistema compartido y el
workflow nunca se ha ejercitado; queda para que lo dispares tú a propósito,
no como efecto colateral de este PR.

## Qué falta, en orden, para desbloquear el frontend

1. **Empujar el tag** (dispara la publicación):
   ```bash
   git push origin contract-v0.1.0
   ```
   Verificar que `contract-release.yml` corre en verde en Actions — nunca se
   ha ejercitado, no darlo por bueno sin mirarlo.

2. **Soltar los permisos heredados del paquete** — GitHub → foto de perfil →
   *Your packages* → `flash-api-contract` → *Package settings* → quitar la
   herencia de permisos del repo backend (mientras herede, la sección granular
   no aparece).

3. **Conceder acceso al repo frontend** — en *Manage Actions access* →
   **Add repository** → `FlashResearchPlatform-Frontend` → rol **Read**.

4. **Verificación de humo** (con un PAT clásico `read:packages` exportado):
   ```bash
   npm view @emmanuelcalad0615/flash-api-contract version \
     --registry=https://npm.pkg.github.com
   # debe imprimir 0.1.0
   ```

Los pasos 2–4 son manuales en la UI de GitHub / npm CLI, no algo que un PR de
este repo pueda entregar. El detalle completo de cada uno está en
`PLAN-HU-FRONTEND.md` §8.1–§8.3 (repo frontend, copia idéntica en este repo).

## Cómo se entrega este trabajo

Este reporte y el tag local viajan en la rama `app/contractpre`. El tag **no**
se empuja como parte de este PR — no es un artefacto de archivo, así que no
aparece en el diff de la PR de todas formas, pero se documenta aquí para que
quien mergee sepa que existe localmente y que el paso 1 de la sección anterior
sigue pendiente de ejecución manual, después de revisar y mergear este PR.

## Actualización — 2026-09-13, tras el reinicio de la máquina

Verificado contra el estado real del repo y de GitHub Actions (`gh run list`,
`gh pr list`) tras retomar la sesión:

**Paso 1 (empujar el tag) — ✅ hecho y verificado en verde.**

- El tag se empujó. `contract-release.yml` corrió tres veces sobre
  `contract-v0.1.0`:
  1. PR #3 (`app/feature`) → **failure**.
  2. PR #4 `fix(ci): repair contract-release.yml module path + prep
     contract-v0.1.0 tag` (`app/contractpre`) → **failure**.
  3. PR #5 `fix(ci): give contract-release the env vars Settings() requires`
     → **success** (run `34770989510`, 2026-09-13T17:14:32Z).
- Log del run exitoso confirma la publicación real:
  `@emmanuelcalad0615/flash-api-contract@0.1.0` publicado a
  `https://npm.pkg.github.com/` (`npm notice Publishing to ... with tag latest
  and default access`).
- Las 3 PRs (#3, #4, #5) están mergeadas a `main`. `main` está en el commit
  `4025893`, árbol de trabajo limpio.

**Pasos 2 y 3 (soltar permisos heredados + conceder acceso al repo
frontend) — ✅ hechos**, confirmado por el usuario tras el reinicio. No
verificado por API en esta sesión (el token de `gh` activo, cuenta
`aquintero17`, no tiene scope `read:packages`).

**Paso 4 — pendiente.** Verificación de humo (con un PAT clásico
`read:packages` exportado).

### 4.1 Crear el PAT clásico

Los registros npm de GitHub Packages solo soportan **tokens clásicos**, no
*fine-grained* (§1, `PLAN-HU-FRONTEND.md` §0.9/§1.2).

1. GitHub → foto de perfil (arriba a la derecha) → **Settings**.
2. Menú izquierdo, bajar hasta **Developer settings**.
3. **Personal access tokens** → **Tokens (classic)**.
4. **Generate new token** → **Generate new token (classic)**.
5. Completar:
   - **Note**: algo identificable, p. ej. `flash-contract-read`.
   - **Expiration**: 30–90 días; se puede regenerar cuando caduque.
   - **Scopes**: marcar **`read:packages`**. Si `FlashResearchPlatform-Backend`
     es privado (lo es, verificado en §8 de `PLAN-HU-FRONTEND.md`), GitHub
     puede exigir además **`repo`** para resolver el paquete contra su repo
     de origen — marcarlo también si el paso 4.3 da 404 con solo
     `read:packages`.
6. **Generate token** al final de la página.
7. **Copiar el token de inmediato** (empieza con `ghp_...`) — GitHub solo lo
   muestra una vez.

### 4.2 Exportarlo en la shell

```bash
export NODE_AUTH_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Reemplazar por el token real. Es la variable que consume la autenticación del
registro npm (y la misma que ya usa el `.npmrc` commiteado del frontend,
`${NODE_AUTH_TOKEN}`). Para que sobreviva a la sesión de terminal, añadir la
línea a `~/.zshrc`.

### 4.3 Correr la verificación de humo

```bash
npm view @emmanuelcalad0615/flash-api-contract version \
  --registry=https://npm.pkg.github.com
```

### 4.4 Cómo leer el resultado

| Resultado | Significa | Acción |
|---|---|---|
| Imprime `0.1.0` | Acceso funciona de punta a punta: paquete publicado + concesión al repo frontend efectiva. | Paso 4 cerrado — desbloquea `PLAN-HU-FRONTEND.md` Etapa F.1. |
| `401 Unauthorized` | Problema de token: no se exportó bien (`echo $NODE_AUTH_TOKEN`), falta el scope `read:packages`, o expiró. | Regenerar/reexportar el PAT. |
| `404 Not Found` | El token es válido pero **no tiene acceso a este paquete concreto** — no es problema de token. | Revisar que los pasos 2–3 (soltar herencia + conceder al repo frontend) se aplicaron al paquete y repo correctos. |
| `403 Forbidden` | Problema de scope, similar al 401. | Añadir el scope `repo` al PAT (repo backend privado). |

Con los pasos 1–3 hechos, `PLAN-HU-FRONTEND.md` Etapa F.1 (dependencia
pinneada en el frontend) queda desbloqueada en cuanto se corra el paso 4 y
confirme acceso real.

## Actualización — 2026-09-14, paso 4 cerrado

**Paso 4 (verificación de humo) — ✅ hecho.** `PAT` clásico creado con scope
`read:packages`, exportado como `NODE_AUTH_TOKEN`. Primer intento dio
`401 Unauthorized` porque `NODE_AUTH_TOKEN` como variable de entorno sola no
autentica nada — hacía falta una línea en `.npmrc` (`//npm.pkg.github.com/
:_authToken=${NODE_AUTH_TOKEN}`, añadida a `~/.npmrc`) para que `npm`
la usara contra ese registro. Con esa línea:

```bash
npm view @emmanuelcalad0615/flash-api-contract version \
  --registry=https://npm.pkg.github.com
# 0.1.0
```

Acceso confirmado de punta a punta (paquete publicado + concesión al repo
frontend efectiva). Los cuatro pasos de este reporte están completos;
`PLAN-HU-FRONTEND.md` Etapa F.1 queda desbloqueada.
