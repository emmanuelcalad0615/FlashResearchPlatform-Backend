from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api.config import settings
from apps.api.infrastructure.logging import REQUEST_ID_HEADER, configure_logging
from apps.api.infrastructure.middlewares.contract import CONTRACT_VERSION, operation_id_for
from apps.api.infrastructure.middlewares.error_handlers import (
    ERROR_RESPONSES,
    register_error_handlers,
)
from apps.api.infrastructure.middlewares.rate_limit import RateLimitMiddleware
from apps.api.infrastructure.middlewares.request_id import RequestIDMiddleware
from apps.api.interfaces.routes import auth, health

# Todas las rutas del backend cuelgan de aqui. Ver DD-001: en produccion el
# frontend y la API comparten dominio, y el proxy manda /api/* al backend.
API_PREFIX = "/api"

# Antes de crear la app, para que hasta los logs de arranque de uvicorn
# salgan ya con el formato configurado.
configure_logging()

# version: la del CONTRATO (apps/api/infrastructure/middlewares/contract.py), no la del paquete
# Python. Sale como info.version en el openapi.json y es lo que el frontend fija
# como dependencia.
# generate_unique_id_function: operationIds estables, que son los nombres de los
# metodos del cliente TypeScript generado.
# responses: el envelope de error entra al contrato para TODAS las rutas.
app = FastAPI(
    title="Flash Research API",
    version=CONTRACT_VERSION,
    generate_unique_id_function=operation_id_for,
    responses=ERROR_RESPONSES,
)

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
        rules=settings.parsed_rate_limit_rules,
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

# Prefijo /api en la ruta REAL, no via root_path. Asi la URL es identica en
# desarrollo y en produccion (DD-001: un solo dominio con reverse proxy), y no
# depende de que el proxy quite o conserve el prefijo.
app.include_router(health.router, prefix=API_PREFIX)
app.include_router(auth.router, prefix=API_PREFIX)
