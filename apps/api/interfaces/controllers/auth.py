"""Controllers de autenticacion.

Traducen HTTP a casos de uso y nada mas. Cero logica de negocio: si aparece un
`if` de negocio aqui, esta en la capa equivocada.

Tampoco atrapan errores. Los del dominio suben y los convierte el manejo
centralizado de la HU-A08, que ya los tiene mapeados:

    DomainValidationError  -> 422
    InvalidTokenError      -> 400
    TokenExpiredError      -> 410
"""

from uuid import UUID

from fastapi import Response

from apps.api.infrastructure.cookies import clear_session_cookies, set_session_cookies
from apps.api.schemas.auth import (
    LoginRequest,
    MeResponse,
    MessageResponse,
    ResendVerificationRequest,
    SignupRequest,
    VerifyEmailRequest,
)
from packages.core.application.usecases.auth.login import LoginUseCase
from packages.core.application.usecases.auth.logout import LogoutUseCase
from packages.core.application.usecases.auth.logout_all import LogoutAllUseCase
from packages.core.application.usecases.auth.me import GetMeUseCase
from packages.core.application.usecases.auth.refresh import RefreshUseCase
from packages.core.application.usecases.auth.resend_verification import (
    ResendVerificationUseCase,
)
from packages.core.application.usecases.auth.signup import SignupUseCase
from packages.core.application.usecases.auth.verify_email import VerifyEmailUseCase
from packages.core.domain.entities import User
from packages.core.domain.errors import UnauthorizedError

# Identico en las tres ramas del signup —email nuevo, pendiente o ya
# registrado— para no delatar quien tiene cuenta. Quien distingue los casos es
# el dueno del buzon, porque cada rama manda un correo distinto.
_MENSAJE_SIGNUP = "Revisa tu correo para activar tu cuenta"
_MENSAJE_VERIFICADO = "Cuenta verificada. Ya puedes iniciar sesion"
# Identico en las cuatro ramas del reenvio: cuenta inexistente, ya
# verificada, en enfriamiento o reenvio hecho. Distinguirlas convertiria
# este endpoint en un comprobador de correos registrados.
_MENSAJE_REENVIO = "Si la cuenta existe y esta pendiente, te enviamos el enlace"
_MENSAJE_LOGIN = "Sesion iniciada"
_MENSAJE_REFRESH = "Sesion renovada"
_MENSAJE_LOGOUT = "Sesion cerrada"
_MENSAJE_LOGOUT_ALL = "Todas las sesiones fueron cerradas"


async def signup(body: SignupRequest, caso: SignupUseCase) -> MessageResponse:
    await caso.execute(body.email, body.password)
    return MessageResponse(message=_MENSAJE_SIGNUP)


async def verify_email(
    body: VerifyEmailRequest, caso: VerifyEmailUseCase
) -> MessageResponse:
    await caso.execute(body.token)
    return MessageResponse(message=_MENSAJE_VERIFICADO)


async def login(
    body: LoginRequest,
    caso: LoginUseCase,
    response: Response,
    user_agent: str | None,
) -> MessageResponse:
    """Abre sesion y deja los tokens en cookies.

    Los tokens NO viajan en el cuerpo, a proposito. Si fueran parte del JSON, el
    JavaScript del frontend tendria que leerlos para guardarlos, y entonces un
    XSS podria leerlos tambien. En una cookie HttpOnly el navegador los gestiona
    y ningun script los alcanza.
    """
    resultado = await caso.execute(body.email, body.password, user_agent=user_agent)

    set_session_cookies(
        response,
        access_token=resultado.access_token,
        refresh_token=resultado.refresh_token,
    )

    return MessageResponse(message=_MENSAJE_LOGIN)


async def refresh(
    caso: RefreshUseCase,
    response: Response,
    refresh_token: str | None,
    user_agent: str | None,
) -> MessageResponse:
    """Canjea el refresh token por un par nuevo y reemplaza las cookies.

    El token NO llega en el cuerpo: viene en la cookie, que el navegador manda
    solo a esta ruta por su Path. El frontend no lo lee ni lo escribe nunca,
    asi que pedir un refresh es una peticion sin cuerpo.
    """
    if refresh_token is None:
        # Sin cookie no hay nada que canjear. Es ausencia de sesion, no un
        # token malo: mismo 401 que una ruta protegida sin credenciales.
        raise UnauthorizedError("No active session")

    resultado = await caso.execute(refresh_token, user_agent=user_agent)

    # Las dos cookies se reemplazan, no solo la de acceso: la rotacion dejo el
    # refresh anterior gastado, asi que si no se sustituyera, la siguiente
    # renovacion mandaria un token usado y dispararia la deteccion de robo
    # contra el propio usuario.
    set_session_cookies(
        response,
        access_token=resultado.access_token,
        refresh_token=resultado.refresh_token,
    )

    return MessageResponse(message=_MENSAJE_REFRESH)


async def me(usuario: User, caso: GetMeUseCase) -> MeResponse:
    """Los datos del usuario que manda la peticion.

    El User no llega en el cuerpo: lo puso la dependencia que valido la cookie.
    Si la peticion llega hasta aqui, la sesion ya esta demostrada; no hay ningun
    caso en el que este controller tenga que responder 401 por su cuenta.
    """
    perfil = await caso.execute(usuario.id)

    return MeResponse(
        id=usuario.id,
        email=usuario.email,
        email_verified=usuario.email_verified,
        display_name=perfil.display_name if perfil else None,
    )


async def logout(
    caso: LogoutUseCase,
    response: Response,
    family_id: str | None,
) -> MessageResponse:
    """Cierra esta sesion: revoca su familia y borra las cookies.

    El family_id sale del claim `fid` del access token, que esta firmado. No
    llega del cuerpo ni de la URL a proposito: si el cliente pudiera elegirlo,
    cualquiera cerraria la sesion de otro escribiendo un UUID ajeno.

    Las cookies se borran pase lo que pase, incluso cuando no hay familia que
    revocar. Cerrar sesion no puede terminar con el usuario todavia dentro.
    """
    await caso.execute(UUID(family_id) if family_id else None)
    clear_session_cookies(response)
    return MessageResponse(message=_MENSAJE_LOGOUT)


async def logout_all(
    caso: LogoutAllUseCase,
    response: Response,
    usuario: User,
) -> MessageResponse:
    """Cierra TODAS las sesiones del usuario, incluida esta.

    Incluirla es deliberado: quien pide esto sospecha de un intruso y no sabe
    cual de las sesiones abiertas es la suya.
    """
    await caso.execute(usuario.id)
    clear_session_cookies(response)
    return MessageResponse(message=_MENSAJE_LOGOUT_ALL)


async def resend_verification(
    body: ResendVerificationRequest, caso: ResendVerificationUseCase
) -> MessageResponse:
    await caso.execute(body.email)
    return MessageResponse(message=_MENSAJE_REENVIO)
