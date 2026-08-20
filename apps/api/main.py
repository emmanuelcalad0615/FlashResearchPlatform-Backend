from fastapi import FastAPI

from apps.api.core.logging import configure_logging
from apps.api.core.middleware import RequestIDMiddleware
from apps.api.routers import health

# Antes de crear la app, para que hasta los logs de arranque de uvicorn
# salgan ya con el formato configurado.
configure_logging()

app = FastAPI(title="Flash Research API", version="0.1.0")

app.add_middleware(RequestIDMiddleware)

app.include_router(health.router)
