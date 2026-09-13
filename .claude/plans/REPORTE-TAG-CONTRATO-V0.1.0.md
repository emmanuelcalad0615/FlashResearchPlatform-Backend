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
