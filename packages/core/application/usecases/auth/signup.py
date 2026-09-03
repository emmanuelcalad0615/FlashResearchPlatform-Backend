"""Caso de uso: registro de un usuario nuevo.

No conoce HTTP, ni SQLAlchemy, ni SMTP. Recibe puertos y decide QUE pasa; el
COMO lo resuelven los adapters de infraestructura.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from packages.core.domain.errors import EmailDeliveryError
from packages.core.domain.ports import EmailSender
from packages.core.domain.repositories import (
    EmailVerificationRepository,
    ProfileRepository,
    UnitOfWork,
    UserRepository,
)
from packages.core.domain.services.passwords import hash_password, validate_password_policy
from packages.core.domain.services.tokens import generate_opaque_token, hash_opaque_token

logger = logging.getLogger(__name__)


class SignupUseCase:
    def __init__(
        self,
        *,
        users: UserRepository,
        profiles: ProfileRepository,
        verifications: EmailVerificationRepository,
        emails: EmailSender,
        uow: UnitOfWork,
        verification_hours: int,
        build_link: Callable[[str], str],
        hasher: Callable[[str], str] = hash_password,
    ) -> None:
        self._users = users
        self._profiles = profiles
        self._verifications = verifications
        self._emails = emails
        self._uow = uow
        self._verification_hours = verification_hours
        self._build_link = build_link
        # Inyectable con el real por defecto: en los tests se pasa uno
        # instantaneo y los 70 ms de Argon2 no se multiplican por cada caso.
        self._hasher = hasher

    async def execute(self, email: str, password: str) -> None:
        """Registra al usuario. Devuelve lo mismo pase lo que pase.

        Las tres ramas responden identico a proposito: si la respuesta variara
        segun si el email ya existe, cualquiera podria averiguar quien tiene
        cuenta probando direcciones. Quien SI distingue las ramas es el dueno
        del buzon, porque cada una manda un correo distinto.
        """
        validate_password_policy(password)

        # Se hashea SIEMPRE, incluso en la rama que descarta el registro. Si
        # nos la saltaramos, esa rama respondería al instante mientras las
        # otras tardan ~70 ms, y esa diferencia delataria que emails existen.
        password_hash = self._hasher(password)

        existente = await self._users.get_by_email(email)

        if existente and existente.email_verified:
            await self._avisar_cuenta_existente(email)
            return

        if existente:
            # Existe pero nunca se verifico: esa cuenta no es de nadie. Quien
            # controle el buzon se la queda. Es la mitigacion del account
            # squatting: sin esto, alguien podria registrar el correo de otra
            # persona, no verificar, y dejarle la direccion bloqueada.
            user_id = existente.id
            await self._users.update_password_hash(user_id, password_hash)
        else:
            user_id = uuid4()
            # users primero: la FK de profiles apunta a users.id y necesita que
            # la fila exista. profiles.create() ademas declara el usuario para
            # que la politica RLS permita la insercion.
            await self._users.create(user_id, email, password_hash)
            await self._profiles.create(user_id)

        token = await self._crear_token_de_verificacion(user_id)

        # Hasta aqui, todo o nada. Un fallo a mitad no deja usuario sin perfil.
        await self._uow.commit()

        # DESPUES del commit: si fallara antes, tendriamos la transaccion viva
        # y un correo ya enviado apuntando a algo que podria no confirmarse.
        await self._enviar_verificacion(email, token)

    async def _crear_token_de_verificacion(self, user_id) -> str:
        token = generate_opaque_token()
        await self._verifications.create(
            user_id=user_id,
            # Solo el hash: si roban un volcado de la base, no sirve para
            # verificar cuentas ajenas.
            token_hash=hash_opaque_token(token),
            expires_at=datetime.now(UTC) + timedelta(hours=self._verification_hours),
        )
        return token

    async def _enviar_verificacion(self, email: str, token: str) -> None:
        try:
            await self._emails.send_verification(
                to=email, link=self._build_link(token)
            )
        except EmailDeliveryError:
            # NO se propaga: el usuario ya quedo creado. Devolverle un error lo
            # dejaria creyendo que no se registro, y al reintentar recibiria
            # "ya existe". La salida para el es /auth/resend-verification.
            logger.exception("verification_email_failed_during_signup")

    async def _avisar_cuenta_existente(self, email: str) -> None:
        try:
            await self._emails.send_already_registered(to=email)
        except EmailDeliveryError:
            logger.exception("already_registered_notice_failed")
