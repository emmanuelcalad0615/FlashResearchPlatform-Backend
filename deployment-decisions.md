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

Ninguna está tomada. Cada una se convertirá en `DD-002` y siguientes cuando se
resuelva, con su contexto, sus alternativas y sus consecuencias.

Este registro se mantiene **vivo**: cuando aparece una tecnología o un ajuste que habrá
que elegir en algún momento, se anota aquí en vez de dejarlo en la memoria de alguien.

### Infraestructura

| Tema | Opciones en juego | Cuándo decidir |
|---|---|---|
| Proveedor de servidor | VPS (Hetzner, DigitalOcean, Vultr) · PaaS con dominio propio (Railway, Render, Fly.io) | Al preparar el despliegue |
| Reverse proxy | **Caddy** (HTTPS automático, cero configuración) · Nginx (más control) · Traefik (nativo en Docker) | Con lo anterior |
| Registrador del dominio | Cloudflare · Namecheap · Porkbun | Con lo anterior |
| CDN / protección DDoS | Cloudflare (gratis) · ninguno al principio | Cuando haya tráfico real |
| Orquestación | Docker Compose en un VPS · Kubernetes · PaaS gestionado | Al preparar el despliegue |

### Observabilidad

Hoy la API emite logs estructurados en JSON (HU-A08), pero **nadie los recoge**: se van a
stdout y se pierden al reiniciar el contenedor. Todo lo de abajo está sin decidir.

| Tema | Opciones en juego | Cuándo decidir |
|---|---|---|
| Agregación de logs | Grafana Loki (autoalojado, barato) · Datadog (gestionado, caro) · CloudWatch · Better Stack | Antes del primer despliegue: sin esto, un fallo nocturno no deja rastro |
| Métricas | Prometheus + Grafana · Datadog · ninguna al principio | Cuando haya usuarios reales |
| Seguimiento de errores | Sentry (tiene plan gratis) · Rollbar · solo logs | Con el primer despliegue |
| Trazas distribuidas (APM) | OpenTelemetry · Datadog APM · ninguna | Cuando existan API + worker + gateway y haga falta seguir una petición entre servicios |
| Alertas | ¿Qué dispara un aviso, y a dónde llega? (correo, Slack, Telegram) | Con la agregación de logs |
| Uptime externo | UptimeRobot · Better Stack · ninguno | Con el primer despliegue |

> El `request_id` de la HU-A08 solo rinde de verdad cuando existe un agregador donde poder
> filtrar por él. Hoy solo sirve leyendo la terminal.

### Correo

| Tema | Opciones en juego | Cuándo decidir |
|---|---|---|
| Proveedor SMTP en producción | Resend · SendGrid · Amazon SES · Postmark · Mailgun | Al desplegar la HU-A07 |
| Dominio remitente y verificación | Registros SPF, DKIM y DMARC del dominio propio | Con lo anterior — sin esto los correos van a spam |
| Correo transaccional vs marketing | ¿Un solo proveedor o dos? | Cuando exista comunicación no transaccional |

En desarrollo ya está resuelto: **Mailpit** en `docker-compose.yml`, bandeja en
`http://localhost:8025`, sin salir a internet.

### Base de datos

| Tema | Opciones en juego | Cuándo decidir |
|---|---|---|
| **Rol de aplicación separado del dueño** | Crear `flash_app` sin privilegios de superusuario | **Antes de producción** — ver la nota de abajo |
| Backups | Frecuencia, retención, destino (S3, Backblaze), y **prueba de restauración** | Antes de tener datos reales |
| Pool de conexiones | El de SQLAlchemy · PgBouncer delante | Cuando haya varias instancias de la API |
| Escalado de TimescaleDB | Políticas de retención y compresión de las hypertables | Con la Épica B, cuando lleguen las velas |

> **Hallazgo pendiente (2026-08-27).** La RLS del esquema no se está aplicando. Alembic
> crea las tablas como `flash`, la API se conecta como el mismo `flash`, y ese rol es
> **superusuario**: los superusuarios se saltan la RLS incondicionalmente, y
> `FORCE ROW LEVEL SECURITY` (migración `0004`) no los alcanza — solo cubre el caso del
> dueño de la tabla.
>
> El arreglo es un rol `flash_app` sin privilegios especiales, con `GRANT` sobre las
> tablas, usado por la API y el worker; `flash` queda solo para las migraciones. Implica
> dos URLs de conexión y tocar el CI.
>
> No es urgente: la RLS es una **segunda** línea de defensa, y la primera —el
> `WHERE user_id = ...` del código— sí funciona. Pero debe cerrarse antes de producción.

### Despliegue y entornos

| Tema | Opciones en juego | Cuándo decidir |
|---|---|---|
| Estrategia de despliegue | Push desde CI · imágenes en un registry (GHCR, Docker Hub) · `git pull` en el servidor | Al preparar el despliegue |
| Entorno de staging | ¿Existe uno, o solo local y producción? | Antes del primer despliegue |
| Gestión de secretos | `.env` en el servidor · Doppler · 1Password · los secretos del proveedor | Con el primer despliegue |
| Migraciones en despliegue | ¿Automáticas al arrancar, o paso manual aprobado? | Antes del primer despliegue |
| Rollback | ¿Cómo se vuelve atrás, y qué pasa con las migraciones ya aplicadas? | Con lo anterior |

---

## Configuración que cambia al salir a producción

Estos valores **no** son decisiones abiertas: ya están decididos. Es una lista de
verificación para el día del despliegue, porque son fáciles de olvidar y cada uno tiene
consecuencias de seguridad.

| Variable | Desarrollo | Producción | Por qué |
|---|---|---|---|
| `LOG_JSON` | `false` | **`true`** | En consola el texto plano es legible; en producción el agregador de logs necesita JSON para poder indexar y consultar |
| `LOG_LEVEL` | `INFO` | `INFO` | Igual. `DEBUG` en producción llena el disco y ralentiza |
| `DEBUG` | `true` | **`false`** | Activa las guardas de arranque que se describen abajo |
| `COOKIE_SECURE` | `false` | **`true`** | En local no hay HTTPS; en producción la cookie no puede viajar en claro |
| `ACCESS_TOKEN_MINUTES` | `60` | **`15`** | Es la ventana que tiene un token robado. En dev prima no re-loguearse a cada rato |
| `REFRESH_TOKEN_DAYS` | `30` | **`7`** | Un refresh robado y no detectado caduca en una semana |
| `JWT_SECRET` | uno cualquiera | **uno propio, en el gestor de secretos** | Compartirlo entre entornos permitiría firmar tokens válidos contra producción desde una máquina de desarrollo |
| `CORS_ORIGINS` | `http://localhost:5173` | el dominio real | Nunca `*` |
| `SMTP_HOST` / `SMTP_PORT` | Mailpit (`localhost:1025`) | el proveedor real | |
| `SMTP_USER` / `SMTP_PASSWORD` | vacías | credenciales del proveedor | |
| `SMTP_FROM` | `no-reply@flashresearch.local` | dirección de un dominio verificado | Sin SPF/DKIM los correos van a spam |
| `FRONTEND_BASE_URL` | `http://localhost:5173` | `https://flashresearch.com` | Se usa para armar el enlace del correo de verificación |
| `RATE_LIMIT_REQUESTS` | `60` | a revisar con tráfico real | 60/min es una estimación, no una medición |
| `DATABASE_URL` | contenedor local | servidor real, contraseña fuerte, y con el rol `flash_app` cuando exista | |

### Guardas de arranque (a implementar en la HU-A07)

La aplicación debe **negarse a arrancar** si:

- `DEBUG=false` y `COOKIE_SECURE=false` → configuración insegura en producción.
- `JWT_SECRET` vacío, en cualquier entorno.
- `len(JWT_SECRET) < 32`.
- El adapter de correo que escribe en el log estuviera activo con `DEBUG=false`.

Una configuración insegura debe fallar ruidosamente, no pasar desapercibida.
