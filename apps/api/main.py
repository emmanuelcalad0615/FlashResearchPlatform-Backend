from fastapi import FastAPI

from apps.api.routers import health

app = FastAPI(title="Flash Research API", version="0.1.0")

app.include_router(health.router)
