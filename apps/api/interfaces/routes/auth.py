"""Registro de las rutas de autenticacion en FastAPI."""

from typing import Annotated

from fastapi import APIRouter, Cookie, Header, Response, status

from apps.api.dependencies import (
    AccessClaimsDep,
    CurrentUserDep,
    GetMeUseCaseDep,
    LoginUseCaseDep,
    LogoutAllUseCaseDep,
    LogoutUseCaseDep,
    RefreshUseCaseDep,
    SignupUseCaseDep,
    VerifyEmailUseCaseDep,
)
from apps.api.infrastructure.cookies import REFRESH_COOKIE
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


@router.post(
    "/refresh",
    status_code=status.HTTP_200_OK,
    response_model=MessageResponse,
    summary="Renueva la sesion con el refresh token",
)
async def refresh(
    caso: RefreshUseCaseDep,
    response: Response,
    # La ruta TIENE que ser exactamente /api/auth/refresh: es el Path con el
    # que se emitio la cookie, y el navegador no la manda a ninguna otra.
    # Cambiar este prefijo sin cambiar REFRESH_COOKIE_PATH deja la renovacion
    # muerta sin que falle ningun test de unidad.
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
    user_agent: Annotated[str | None, Header()] = None,
) -> MessageResponse:
    # POST y no GET: cambia el estado del servidor —gasta un token y emite
    # otro—, asi que no puede ser cacheable ni repetible sin consecuencias.
    return await controller.refresh(caso, response, refresh_token, user_agent)


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


@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
    response_model=MessageResponse,
    summary="Cierra la sesion actual",
)
async def logout(
    caso: LogoutUseCaseDep,
    response: Response,
    # Ruta protegida: sin access token valido no se ejecuta. El claim `fid`
    # sale de ese mismo token, asi que la autenticacion y el dato que hace
    # falta llegan juntos.
    claims: AccessClaimsDep,
) -> MessageResponse:
    return await controller.logout(caso, response, claims.family_id)


@router.post(
    "/logout-all",
    status_code=status.HTTP_200_OK,
    response_model=MessageResponse,
    summary="Cierra todas las sesiones del usuario",
)
async def logout_all(
    caso: LogoutAllUseCaseDep,
    response: Response,
    # CurrentUserDep y no AccessClaimsDep: aqui hace falta el id del usuario, y
    # comprobar de paso que sigue existiendo.
    usuario: CurrentUserDep,
) -> MessageResponse:
    return await controller.logout_all(caso, response, usuario)
