"""Caso de uso: inicio de sesion.

Devuelve los dos tokens; como viajen —cookies, cabeceras— es decision de la
capa HTTP, no del dominio.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from packages.core.domain.errors import EmailNotVerifiedError, InvalidCredentialsError
from packages.core.domain.policies.passwords import (
    decoy_verify,
    hash_password,
    needs_rehash,
    verify_password,
)
from packages.core.domain.policies.tokens import (
    create_access_token,
    generate_opaque_token,
    hash_opaque_token,
)
from packages.core.domain.repositories import (
    RefreshTokenRepository,
    UnitOfWork,
    UserRepository,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoginResult:
    """Los dos tokens de una sesion recien abierta.

    El caso de uso no sabe que existen las cookies: entrega las cadenas y el
    controller decide como viajan.
    """

    access_token: str
    refresh_token: str


class LoginUseCase:
    def __init__(
        self,
        *,
        users: UserRepository,
        tokens: RefreshTokenRepository,
        uow: UnitOfWork,
        jwt_secret: str,
        jwt_algorithm: str,
        access_token_minutes: int,
        refresh_token_days: int,
        hasher: Callable[[str], str] = hash_password,
    ) -> None:
        self._users = users
        self._tokens = tokens
        self._uow = uow
        self._jwt_secret = jwt_secret
        self._jwt_algorithm = jwt_algorithm
        self._access_token_minutes = access_token_minutes
        self._refresh_token_days = refresh_token_days
        self._hasher = hasher

    async def execute(
        self, email: str, password: str, *, user_agent: str | None = None
    ) -> LoginResult:
        """Abre una sesion.

        Lanza InvalidCredentialsError (401) si el email no existe o la
        contrasena no coincide, y EmailNotVerifiedError (403) si la cuenta
        existe pero nunca se activo.
        """
        usuario = await self._users.get_by_email(email)

        if usuario is None:
            self._registrar_fallo("unknown_email", None)
            # Se verifica contra un hash falso para gastar los mismos ~70 ms.
            # Sin esto, un email inexistente respondería al instante y esa
            # diferencia, medible desde fuera, delataria que correos estan
            # registrados.
            decoy_verify(password)
            raise InvalidCredentialsError

        if not verify_password(password, usuario.password_hash):
            # MISMO error que arriba, a proposito: distinguir "no existe" de
            # "contrasena incorrecta" regalaria la lista de usuarios. En el LOG
            # si se distinguen: quien lo lee ya tiene acceso al sistema, y la
            # diferencia entre "prueban correos al azar" y "atacan esta cuenta
            # concreta" es justo lo que hace util el registro.
            self._registrar_fallo("wrong_password", usuario.id)
            raise InvalidCredentialsError

        # DESPUES de comprobar la contrasena, nunca antes. Si fuera antes,
        # cualquiera podria averiguar que correos estan registrados sin
        # verificar, solo probando direcciones.
        if not usuario.email_verified:
            self._registrar_fallo("email_not_verified", usuario.id)
            raise EmailNotVerifiedError

        await self._reforzar_hash_si_hace_falta(usuario, password)

        resultado = await self._abrir_sesion(usuario.id, user_agent)
        await self._uow.commit()
        return resultado

    def _registrar_fallo(self, motivo: str, user_id) -> None:
        """Deja rastro de un intento de inicio de sesion rechazado.

        NUNCA el email ni la contrasena: el email es un dato personal y
        acabaria replicado en cada sistema por el que pasen los logs. Con el
        user_id se investiga igual, y cuando no hay usuario el propio None dice
        que se probo una direccion que no existe.

        Sin este registro, un ataque de fuerza bruta no deja mas rastro que los
        429 del rate limiter, que no distinguen un ataque de un usuario torpe.
        """
        logger.warning("login_failed reason=%s user_id=%s", motivo, user_id)

    async def _reforzar_hash_si_hace_falta(self, usuario, password: str) -> None:
        """Rehash progresivo.

        Este es el UNICO momento en que existe la contrasena en claro, asi que
        es la unica oportunidad de recalcular un hash creado con parametros mas
        debiles. Sin esto, subir la configuracion de Argon2 dejaria los hashes
        viejos obsoletos para siempre: recalcularlos exigiria una contrasena
        que no se guarda.
        """
        if needs_rehash(usuario.password_hash):
            await self._users.update_password_hash(usuario.id, self._hasher(password))

    async def _abrir_sesion(self, user_id, user_agent: str | None) -> LoginResult:
        ahora = datetime.now(UTC)

        # La familia se decide aqui, antes de emitir nada: los dos tokens de
        # una sesion tienen que apuntar a la misma cadena.
        family_id = uuid4()

        access = create_access_token(
            str(user_id),
            secret=self._jwt_secret,
            algorithm=self._jwt_algorithm,
            expires_minutes=self._access_token_minutes,
            # Para que el cierre de sesion sepa que familia revocar sin tener
            # el refresh token a mano.
            family_id=str(family_id),
        )

        refresh = generate_opaque_token()
        await self._tokens.create(
            user_id=user_id,
            # Solo el hash: un volcado de la base no puede abrir sesiones.
            token_hash=hash_opaque_token(refresh),
            # Familia NUEVA por login. Un usuario con tres dispositivos tiene
            # tres cadenas independientes, y cerrar sesion en uno no toca los
            # otros. Es tambien lo que permite revocar solo la comprometida
            # cuando se detecta una reutilizacion.
            family_id=family_id,
            expires_at=ahora + timedelta(days=self._refresh_token_days),
            # Informativo: sirve para mostrar "sesiones abiertas" y cerrarlas
            # por dispositivo. NUNCA para autenticar: lo controla el cliente.
            user_agent=user_agent,
        )

        return LoginResult(access_token=access, refresh_token=refresh)
