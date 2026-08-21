from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api.core.config import settings
from apps.api.core.error_handlers import register_error_handlers
from apps.api.core.logging import configure_logging
from apps.api.core.middleware import REQUEST_ID_HEADER, RequestIDMiddleware
from apps.api.routers import health

# Antes de crear la app, para que hasta los logs de arranque de uvicorn
# salgan ya con el formato configurado.
configure_logging()

app = FastAPI(title="Flash Research API", version="0.1.0")

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
