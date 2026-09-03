"""Controllers de autenticacion.

Traducen HTTP a casos de uso y nada mas. Cero logica de negocio: si aparece un
`if` de negocio aqui, esta en la capa equivocada.

Tampoco atrapan errores. Los del dominio suben y los convierte el manejo
centralizado de la HU-A08, que ya los tiene mapeados:

    DomainValidationError  -> 422
    InvalidTokenError      -> 400
    TokenExpiredError      -> 410
"""

from apps.api.schemas.auth import (
    MessageResponse,
    SignupRequest,
    VerifyEmailRequest,
)
from packages.core.application.usecases.auth.signup import SignupUseCase
from packages.core.application.usecases.auth.verify_email import VerifyEmailUseCase

# Identico en las tres ramas del signup —email nuevo, pendiente o ya
# registrado— para no delatar quien tiene cuenta. Quien distingue los casos es
# el dueno del buzon, porque cada rama manda un correo distinto.
_MENSAJE_SIGNUP = "Revisa tu correo para activar tu cuenta"
_MENSAJE_VERIFICADO = "Cuenta verificada. Ya puedes iniciar sesion"


async def signup(body: SignupRequest, caso: SignupUseCase) -> MessageResponse:
    await caso.execute(body.email, body.password)
    return MessageResponse(message=_MENSAJE_SIGNUP)


async def verify_email(
    body: VerifyEmailRequest, caso: VerifyEmailUseCase
) -> MessageResponse:
    await caso.execute(body.token)
    return MessageResponse(message=_MENSAJE_VERIFICADO)
