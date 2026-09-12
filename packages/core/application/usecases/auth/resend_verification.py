"""Caso de uso: reenvio del correo de verificacion.

Sin esto, una cuenta cuyo correo se perdio queda inutilizable: existe, no puede
iniciar sesion hasta verificarse, y registrarse de nuevo con el mismo email no
crea nada. El usuario no tendria ninguna salida.

Los motivos reales por los que se pierde son mundanos y frecuentes: el correo
cayo en spam, el usuario lo borro, o tardo mas de las 24 horas que dura el
enlace.
"""

import logging
from datetime import UTC, datetime, timedelta

from packages.core.domain.entities import User
from packages.core.domain.errors import EmailDeliveryError
from packages.core.domain.policies.tokens import (
    generate_opaque_token,
    hash_opaque_token,
)
from packages.core.domain.ports import EmailSender
from packages.core.domain.repositories import (
    EmailVerificationRepository,
    UnitOfWork,
    UserRepository,
)

logger = logging.getLogger(__name__)


class ResendVerificationUseCase:
    def __init__(
        self,
        *,
        users: UserRepository,
        verifications: EmailVerificationRepository,
        emails: EmailSender,
        uow: UnitOfWork,
        verification_hours: int,
        cooldown_seconds: int,
        build_link,
    ) -> None:
        self._users = users
        self._verifications = verifications
        self._emails = emails
        self._uow = uow
        self._verification_hours = verification_hours
        self._cooldown_seconds = cooldown_seconds
        self._build_link = build_link

    async def execute(self, email: str) -> None:
        """Emite un enlace nuevo y lo manda.

        NO lanza error en ningun caso previsible, y eso es deliberado: la
        respuesta es identica exista la cuenta, este ya verificada, o se haya
        pedido un reenvio hace diez segundos. Cualquier diferencia convertiria
        este endpoint en un comprobador de correos registrados, que es justo lo
        que el signup se cuida de no ser.

        Quien tiene acceso al buzon si distingue los casos, porque cada rama
        manda —o no manda— un correo distinto.
        """
        usuario = await self._users.get_by_email(email)

        if usuario is None:
            logger.info("resend_verification_unknown_email")
            return

        if usuario.email_verified:
            # Nada que reenviar. No se avisa al buzon tampoco: el usuario ya
            # puede entrar, y un correo inesperado solo confundiria.
            logger.info(
                "resend_verification_already_verified user_id=%s", usuario.id
            )
            return

        if await self._en_enfriamiento(usuario):
            return

        token = await self._emitir_token(usuario)
        await self._uow.commit()

        await self._enviar(email, token)

    async def _en_enfriamiento(self, usuario: User) -> bool:
        """True si se emitio un enlace hace muy poco.

        Es la proteccion del BUZON, distinta de la del servidor. El rate limit
        del middleware cuenta por IP, asi que no impide que alguien escriba la
        direccion de otra persona desde mil IPs y le llene el correo. Este
        limite va por cuenta y corta justo eso.

        Vive en el caso de uso y no en el middleware por una razon simple: aqui
        es donde se sabe de quien es el email. El middleware corre antes de
        leer el cuerpo de la peticion.

        Se apoya en la fila del ultimo token, no en un contador aparte: un dato
        que ya se guarda y que sobrevive a un reinicio del proceso.
        """
        ultimo = await self._verifications.get_latest_for_user(usuario.id)
        if ultimo is None:
            return False

        desde_el_ultimo = datetime.now(UTC) - ultimo.created_at
        if desde_el_ultimo >= timedelta(seconds=self._cooldown_seconds):
            return False

        logger.warning(
            "resend_verification_throttled user_id=%s seconds_since_last=%d",
            usuario.id,
            int(desde_el_ultimo.total_seconds()),
        )
        return True

    async def _emitir_token(self, usuario: User) -> str:
        # Los anteriores dejan de valer. Si siguieran vivos, cada reenvio
        # sumaria un enlace valido mas circulando por buzones, reenvios y logs
        # de correo, y bastaria con que UNO se filtrara.
        await self._verifications.invalidate_for_user(usuario.id)

        token = generate_opaque_token()
        await self._verifications.create(
            user_id=usuario.id,
            token_hash=hash_opaque_token(token),
            expires_at=datetime.now(UTC)
            + timedelta(hours=self._verification_hours),
        )
        return token

    async def _enviar(self, email: str, token: str) -> None:
        try:
            await self._emails.send_verification(
                to=email, link=self._build_link(token)
            )
        except EmailDeliveryError:
            # No se propaga, igual que en el registro: el token ya quedo
            # emitido y confirmado. Devolver un error haria que el usuario
            # reintentara y chocara con el enfriamiento, que es peor.
            logger.exception("resend_verification_email_failed")
