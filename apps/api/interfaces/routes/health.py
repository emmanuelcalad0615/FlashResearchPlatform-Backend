from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter()


class HealthResponse(BaseModel):
    """Respuesta del healthcheck."""

    status: str = Field(
        description="Estado del servicio. 'ok' cuando responde con normalidad.",
        examples=["ok"],
    )


class RootResponse(BaseModel):
    """Saludo de la raiz: sirve para confirmar que la API esta arriba."""

    message: str = Field(examples=["Flash Research API is running"])


# operation_id explicito: es el nombre publico de la operacion en el contrato,
# y no debe cambiar porque alguien renombre la funcion.
# response_model: sin el, el contrato documenta la respuesta como un objeto sin
# forma y el frontend genera `unknown`. El tipo es parte del contrato, no un
# adorno.
@router.get("/health", operation_id="getHealth", response_model=HealthResponse)
async def health_check():
    return {"status": "ok"}


@router.get("/", operation_id="getRoot", response_model=RootResponse)
async def root():
    return {"message": "Flash Research API is running"}
