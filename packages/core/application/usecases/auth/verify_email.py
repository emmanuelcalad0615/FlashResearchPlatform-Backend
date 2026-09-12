"""Caso de uso: verificacion del correo.

Activa la cuenta cuando el usuario abre el enlace que se le envio. Hasta
entonces existe pero no puede iniciar sesion.
"""

from packages.core.domain.errors import InvalidTokenError, TokenExpiredError
from packages.core.domain.policies.tokens import hash_opaque_token
from packages.core.domain.repositories import (
    EmailVerificationRepository,
    UnitOfWork,
    UserRepository,
)


class VerifyEmailUseCase:
    def __init__(
        self,
        *,
        users: UserRepository,
        verifications: EmailVerificationRepository,
        uow: UnitOfWork,
    ) -> None:
        self._users = users
        self._verifications = verifications
        self._uow = uow

    async def execute(self, token: str) -> None:
        """Activa la cuenta asociada al token.

        Lanza InvalidTokenError (400) si el token no existe, y TokenExpiredError
        (410) si vencio.
        """
        # Se busca por el HASH: es lo unico que hay guardado. El token en claro
        # solo existe en el enlace del correo.
        verificacion = await self._verifications.get_by_hash(hash_opaque_token(token))

        if verificacion is None:
            raise InvalidTokenError

        if verificacion.is_expired:
            raise TokenExpiredError

        if verificacion.is_used:
            await self._resolver_token_ya_usado(verificacion.user_id)
            return

        # Las dos escrituras van juntas: si la primera confirmara sola y la
        # segunda fallara, el token quedaria reutilizable y cualquiera con esa
        # cadena podria volver a "verificar" la cuenta.
        await self._users.mark_email_verified(verificacion.user_id)
        await self._verifications.mark_used(verificacion.id)
        await self._uow.commit()

    async def _resolver_token_ya_usado(self, user_id) -> None:
        """Un token gastado no siempre es un error.

        Si la cuenta YA esta verificada, repetir la operacion no cambia nada:
        se responde exito. Cubre el doble clic y los clientes de correo que
        pre-visitan enlaces.

        No filtra informacion: para llegar aqui hace falta un token que fue
        valido, y quien lo tiene ya conocia la cuenta. Una cadena al azar sigue
        recibiendo InvalidTokenError.

        A DIFERENCIA del refresh token, donde reutilizar es un incidente de
        seguridad y obliga a revocar la familia entera. La diferencia: un
        refresh reutilizado puede dar acceso; uno de verificacion solo puede
        producir un efecto que ya ocurrio.
        """
        usuario = await self._users.get_by_id(user_id)

        if usuario is None or not usuario.email_verified:
            # Token gastado pero cuenta sin verificar: estado imposible, solo
            # puede venir de datos corruptos. No se trata como exito.
            raise InvalidTokenError
