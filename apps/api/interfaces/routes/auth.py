"""Registro de las rutas de autenticacion en FastAPI."""

from typing import Annotated

from fastapi import APIRouter, Header, Response, status

from apps.api.dependencies import (
    CurrentUserDep,
    GetMeUseCaseDep,
    LoginUseCaseDep,
    SignupUseCaseDep,
    VerifyEmailUseCaseDep,
)
from apps.api.interfaces.controllers import auth as controller
from apps.api.schemas.auth import (
    LoginRequest,
    MeResponse,
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


@router.post(
    "/login",
    status_code=status.HTTP_200_OK,
    response_model=MessageResponse,
    summary="Inicia sesion y emite las cookies de sesion",
)
async def login(
    body: LoginRequest,
    caso: LoginUseCaseDep,
    response: Response,
    # Informativo: sirve para mostrar "sesiones abiertas" y cerrarlas por
    # dispositivo. NUNCA para autenticar, porque lo controla el cliente.
    user_agent: Annotated[str | None, Header()] = None,
) -> MessageResponse:
    return await controller.login(body, caso, response, user_agent)


@router.get(
    "/me",
    status_code=status.HTTP_200_OK,
    response_model=MeResponse,
    summary="Devuelve el usuario de la sesion actual",
)
async def me(
    # Pedir CurrentUserDep es lo que convierte esta ruta en protegida: si la
    # cookie falta o no vale, la dependencia lanza y el endpoint no llega a
    # ejecutarse. No hay ningun `if` de autorizacion que se pueda olvidar.
    usuario: CurrentUserDep,
    caso: GetMeUseCaseDep,
) -> MeResponse:
    return await controller.me(usuario, caso)
