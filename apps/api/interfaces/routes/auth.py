"""Registro de las rutas de autenticacion en FastAPI."""

from fastapi import APIRouter, status

from apps.api.dependencies import SignupUseCaseDep, VerifyEmailUseCaseDep
from apps.api.interfaces.controllers import auth as controller
from apps.api.schemas.auth import (
    MessageResponse,
    SignupRequest,
    VerifyEmailRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/signup",
    status_code=status.HTTP_201_CREATED,
    # response_model recorta la salida a lo declarado: si alguien devolviera de
    # mas, no llegaria al cliente.
    response_model=MessageResponse,
    summary="Registra una cuenta y envia el correo de verificacion",
)
async def signup(
    body: SignupRequest,
    caso: SignupUseCaseDep,
) -> MessageResponse:
    return await controller.signup(body, caso)


@router.post(
    "/verify-email",
    status_code=status.HTTP_200_OK,
    response_model=MessageResponse,
    summary="Activa la cuenta con el token del correo",
)
async def verify_email(
    body: VerifyEmailRequest,
    caso: VerifyEmailUseCaseDep,
) -> MessageResponse:
    # POST y no GET a proposito: algunos clientes de correo pre-visitan los
    # enlaces para escanearlos, y con GET la cuenta quedaria verificada sin que
    # el usuario hiciera nada.
    return await controller.verify_email(body, caso)
