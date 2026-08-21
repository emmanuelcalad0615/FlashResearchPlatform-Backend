from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api.core.config import settings
from apps.api.core.error_handlers import register_error_handlers
from apps.api.core.logging import REQUEST_ID_HEADER, configure_logging
from apps.api.core.middleware import RequestIDMiddleware
from apps.api.core.rate_limit import RateLimitMiddleware
from apps.api.routers import health

# Antes de crear la app, para que hasta los logs de arranque de uvicorn
# salgan ya con el formato configurado.
configure_logging()

app = FastAPI(title="Flash Research API", version="0.1.0")

# El orden de registro es al reves del orden de ejecucion: Starlette apila
# cada middleware por FUERA del anterior. Queda, de afuera hacia adentro:
#   CORS -> RequestID -> RateLimit -> rutas
# RateLimit va por dentro de RequestID para que el 429 salga con su request_id
# y quede en el log de acceso.
if settings.rate_limit_enabled:
    app.add_middleware(
        RateLimitMiddleware,
        limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
        exempt_paths=frozenset(settings.rate_limit_exempt_paths),
    )

app.add_middleware(RequestIDMiddleware)

# Se agrega de ultimo a proposito: Starlette apila los middleware al reves, asi
# que el ultimo queda por FUERA. CORS envuelve al resto y hasta las respuestas
# de error salen con sus cabeceras.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Sin esto el navegador OCULTA X-Request-ID al JavaScript del frontend: por
    # defecto solo expone un punado de cabeceras estandar.
    expose_headers=[REQUEST_ID_HEADER],
)

register_error_handlers(app)

app.include_router(health.router)
