# Flash Research — Decisiones de despliegue

> Registro de decisiones sobre **cómo y dónde se ejecuta** la plataforma en producción.
>
> Cada decisión se numera (`DD-00N`), no se borra y no se reescribe. Si una decisión se
> revierte, se añade una nueva que la sustituye y se marca la anterior como *Sustituida*.
> Así queda el rastro de por qué las cosas son como son.
>
> Estas decisiones son independientes de la arquitectura del código (esa vive en
> `CLAUDE.md`) y del alcance funcional (ese vive en `backlog.md`).

---

## Índice

| ID | Decisión | Estado | Fecha |
|---|---|---|---|
| DD-001 | Un solo origen en producción, con reverse proxy | **Aceptada** | 2026-08-27 |

---

## DD-001 — Un solo origen en producción, con reverse proxy

**Estado:** Aceptada
**Fecha:** 2026-08-27
**Afecta a:** `flash-research-api` (este repo) y `flash-research-web`

### Contexto

El proyecto es un **polirepo**: el backend (FastAPI + worker) y el frontend (React + Vite)
viven en repositorios de git separados. Eso decide cómo se organiza el *código*, pero no
dice nada sobre en qué dirección responde cada parte una vez desplegada.

Las dos aplicaciones son cosas distintas al desplegarse:

- El **frontend** compila a archivos estáticos (`index.html`, JS, CSS). Se descargan y se
  ejecutan en el navegador del usuario.
- El **backend** es un proceso que debe estar ejecutándose permanentemente, con acceso a
  Postgres y Redis.

Esa diferencia empuja a desplegarlos en sitios distintos. Pero la autenticación
(`HU-A07`) transporta los tokens en **cookies `HttpOnly`**, y las cookies son sensibles a
cómo el navegador clasifica los dominios. La decisión de topología deja de ser un detalle
de infraestructura y pasa a condicionar si el login funciona o no.

### Decisión

**En producción, frontend y backend se sirven bajo un único origen, detrás de un reverse
proxy.**

```
                    https://flashresearch.com
                              │
                    ┌──── reverse proxy ────┐
                    │       (Caddy)         │
                    └───────────────────────┘
                       │                 │
        /api/*  ───────┘                 └─────── /*
           │                                       │
           ▼                                       ▼
      FastAPI :8000                     archivos estáticos del build
                                              de React (dist/)
```

Rutas reservadas para el backend:

| Prefijo | Destino |
|---|---|
| `/api/*` | FastAPI |
| `/ws` | WebSocket Gateway (Épica C) |
| todo lo demás | archivos del frontend, con *fallback* a `index.html` |

En **desarrollo** se reproduce la misma topología con el proxy integrado de Vite, para que
el navegador vea un solo origen también en local:

```ts
// vite.config.ts — repo flash-research-web
server: {
  proxy: {
    '/api': { target: 'http://localhost:8000', changeOrigin: true },
  },
}
```

### Por qué

#### 1. Elimina CORS en lugar de configurarlo

Conviene precisar el mecanismo, porque suele malentenderse:

- El navegador compara **orígenes**, no direcciones IP. Un origen es el texto
  `esquema://host:puerto`. Dos dominios distintos apuntando a la misma IP siguen siendo
  orígenes distintos.
- **CORS lo aplica el navegador, no la API.** El middleware solo añade cabeceras que
  declaran qué orígenes están permitidos; quien bloquea la lectura de la respuesta es el
  navegador del usuario. Por eso `curl` ignora CORS: no es un navegador.

Con un solo origen, el frontend pide `/api/movers` desde el mismo sitio del que se cargó.
El navegador no lo clasifica como petición entre orígenes: no hay *preflight* `OPTIONS`,
no busca cabeceras de permiso, y el problema desaparece.

El middleware de CORS se mantiene configurado igualmente, porque sigue haciendo falta en
desarrollo si alguien no usa el proxy de Vite, y como red de seguridad.

#### 2. Las cookies de autenticación funcionan sin concesiones

Los tokens viajan en cookies con `SameSite=Lax`, que instruye al navegador a mandarlas
solo cuando la petición nace del mismo sitio. Con un único origen esa condición se cumple
siempre y de forma trivial.

La alternativa —dominios distintos— obligaría a `SameSite=None`, con dos consecuencias
graves:

- Se pierde la protección contra **CSRF** que `SameSite` daba gratis, y habría que
  implementar *double-submit* a mano.
- **Safari y Firefox bloquean por defecto las cookies entre sitios distintos** como medida
  antirrastreo. No es configurable desde el servidor. El login funcionaría en Chrome y
  fallaría en Safari, sin mensaje de error claro.

#### 3. El tiempo real de la Épica C lo agradece

`HU-C04` monta un WebSocket Gateway con fan-out a clientes. Los WebSockets autenticados
por cookie entre orígenes distintos arrastran los mismos problemas que las peticiones
HTTP, y con menos herramientas para depurarlos. Bajo un solo origen, `/ws` es una ruta
más.

#### 4. Desarrollo y producción comparten topología

Con el proxy de Vite en local, los problemas de cookies y de rutas aparecen en la máquina
del desarrollador, no el día del despliegue. Reduce la categoría de fallos que solo se
manifiestan en producción.

#### 5. El costo es bajo

Un dominio propio cuesta del orden de 10-15 USD al año. Es la inversión más barata del
proyecto y es requisito para todo lo anterior.

### Consecuencias

**Positivas**

- Sin CORS en producción; sin *preflight* en cada petición que lo requeriría.
- Cookies *first-party*: `SameSite=Lax` protege de CSRF sin código adicional.
- Compatible con todos los navegadores, incluidos los que bloquean cookies de terceros.
- Una sola URL que recordar, compartir y presentar.
- HTTPS gestionado en un solo punto.

**Negativas / costos asumidos**

- Aparece un componente más que operar y configurar: el reverse proxy.
- El frontend deja de poder desplegarse de forma totalmente independiente en una
  plataforma de estáticos; su build tiene que llegar al servidor donde vive el proxy.
- Requiere comprar un dominio: no se puede usar el subdominio gratuito de una plataforma
  (ver alternativa C).
- El despliegue de ambos repos queda coordinado por un mismo destino, aunque los
  repositorios sigan siendo independientes.

### Alternativas consideradas

#### B — Dos subdominios del mismo dominio

```
app.flashresearch.com  →  frontend
api.flashresearch.com  →  backend
```

**Rechazada, pero es la segunda opción válida.** Funciona correctamente: el navegador
considera ambos subdominios el **mismo sitio** (porque el dominio registrable
`flashresearch.com` es el mismo), así que `SameSite=Lax` sigue protegiendo y las cookies
llegan.

Requiere configurar CORS y fijar `Domain=.flashresearch.com` en las cookies para que valgan
en ambos subdominios.

Se descarta frente a A porque no elimina CORS, complica el WebSocket, y no aporta ninguna
ventaja real a esta escala. Sigue siendo una salida aceptable si algún día conviene
escalar frontend y backend por separado.

#### C — Dominios distintos (subdominios gratuitos de plataformas)

```
flash-research.vercel.app   →  frontend
flash-api.railway.app       →  backend
```

**Rechazada.**

Es la opción a la que se llega por defecto al desplegar en plataformas gratuitas, y la
que más problemas causa.

> **Trampa importante:** `mi-front.vercel.app` y `mi-api.vercel.app` **no** son el mismo
> sitio, aunque lo parezcan. Dominios como `vercel.app`, `netlify.app`, `github.io` o
> `railway.app` están en la **Public Suffix List** — el registro de dominios bajo los
> cuales cualquiera puede crear subdominios. Los navegadores tratan cada subdominio como
> un dominio raíz independiente, tan ajenos entre sí como `google.com` y `amazon.com`.
>
> Es correcto que sea así: si dos desconocidos registran `hacker.vercel.app` y
> `banco.vercel.app`, no queremos que el primero pueda tocar las cookies del segundo.

Consecuencia: obliga a `SameSite=None`, se pierde la protección CSRF, y los navegadores
con protección antirrastreo bloquean las cookies. El login fallaría de forma intermitente
según el navegador del usuario.

#### D — Tokens en `localStorage` en lugar de cookies

**Rechazada.** Evitaría el problema de dominios por completo, ya que el token se adjunta a
mano en una cabecera `Authorization` y las reglas de cookies dejan de aplicar.

Se descarta porque `localStorage` es legible por cualquier JavaScript de la página. Un
XSS —o una dependencia de npm comprometida— roba el token con una línea y obtiene acceso
persistente a la cuenta. La cookie `HttpOnly` es invisible para el JavaScript, lo que
convierte un robo permanente en un daño acotado a la sesión abierta.

Ver `HU-A07-auth-plan.md` §1.2 para el detalle.

### Implicaciones concretas

**En este repo (`flash-research-api`)**

- Ningún cambio de código requerido hoy. La configuración de la HU-A08 y el plan de la
  HU-A07 ya son compatibles.
- `CORS_ORIGINS` se mantiene: sirve en desarrollo y como red de seguridad.
- Las cookies de auth mantienen `SameSite=Lax`; no hace falta `SameSite=None`.
- Al desplegar: `COOKIE_SECURE=true` y `DEBUG=false`.

**En `flash-research-web`**

- Añadir el proxy de Vite al `vite.config.ts`.
- El cliente HTTP usa rutas relativas (`/api/...`), nunca una URL absoluta al backend.
- El cliente HTTP debe mandar credenciales (`credentials: 'include'`).

**Al desplegar (HU futura)**

1. Comprar el dominio.
2. Servidor con Docker (VPS pequeño basta para empezar).
3. Caddy como reverse proxy — gestiona los certificados HTTPS automáticamente, sin
   configuración.
4. Apuntar los registros DNS del dominio a la IP del servidor.
5. Pipelines de CI que publiquen el build del frontend y la imagen del backend en ese
   destino.

---

## Decisiones pendientes

No están tomadas. Se convertirán en `DD-002` y siguientes cuando se resuelvan.

| Tema | Opciones en juego | Cuándo decidir |
|---|---|---|
| Proveedor de servidor | VPS (Hetzner, DigitalOcean) · PaaS con dominio propio | Al preparar el despliegue |
| Reverse proxy | **Caddy** (HTTPS automático) · Nginx (más control, más configuración) | Con lo anterior |
| Registrador del dominio | Cloudflare · Namecheap · Porkbun | Con lo anterior |
| Estrategia de despliegue | Push desde CI · imágenes en un registry · `git pull` en el servidor | Al preparar el despliegue |
| Backups de Postgres | Frecuencia, retención, destino | Antes de tener datos reales |
| Proveedor SMTP | Resend · SendGrid · Amazon SES | Al desplegar la HU-A07 |
| Entorno de staging | ¿Existe uno, o solo local y producción? | Antes del primer despliegue |
