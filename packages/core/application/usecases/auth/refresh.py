"""Caso de uso: canje de un refresh token por un par nuevo.

Es lo que permite que el access token dure quince minutos sin que el usuario
tenga que volver a escribir su contrasena.

Y es el unico flujo de autenticacion donde un fallo puede significar un robo en
curso, no un despiste. De ahi la deteccion de reutilizacion.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from packages.core.domain.entities import RefreshToken
from packages.core.domain.errors import InvalidTokenError
from packages.core.domain.policies.tokens import (
    create_access_token,
    generate_opaque_token,
    hash_opaque_token,
)
from packages.core.domain.repositories import RefreshTokenRepository, UnitOfWork

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RefreshResult:
    """El par nuevo. Igual que LoginResult: el dominio no sabe de cookies."""

    access_token: str
    refresh_token: str


class RefreshUseCase:
    def __init__(
        self,
        *,
        tokens: RefreshTokenRepository,
        uow: UnitOfWork,
        jwt_secret: str,
        jwt_algorithm: str,
        access_token_minutes: int,
        refresh_token_days: int,
    ) -> None:
        self._tokens = tokens
        self._uow = uow
        self._jwt_secret = jwt_secret
        self._jwt_algorithm = jwt_algorithm
        self._access_token_minutes = access_token_minutes
        self._refresh_token_days = refresh_token_days

    async def execute(
        self, refresh_token: str, *, user_agent: str | None = None
    ) -> RefreshResult:
        """Canjea el token por uno nuevo, o lo rechaza.

        Lanza InvalidTokenError en TODOS los casos de rechazo: desconocido,
        revocado, expirado y reutilizado. Un solo error a proposito —ver
        _detectar_reutilizacion—, asi que quien reciba la respuesta no puede
        deducir por que fallo.
        """
        actual = await self._tokens.get_by_hash(hash_opaque_token(refresh_token))

        if actual is None:
            # Ni siquiera existe. Cadena inventada, o token de una familia que
            # se borro. No hay nada que revocar.
            raise InvalidTokenError

        if actual.is_used:
            await self._detectar_reutilizacion(actual)

        if actual.is_revoked or actual.is_expired:
            raise InvalidTokenError

        # ROTACION: el token que se acaba de presentar queda gastado y en su
        # lugar se emite otro. Sin esto, un token robado serviria durante dias;
        # con esto, en cuanto el legitimo o el ladron lo usen, el otro se
        # encuentra con un token gastado y salta la deteccion de arriba.
        await self._tokens.mark_used(actual.id)

        resultado = await self._emitir_par(
            user_id=actual.user_id,
            # La MISMA familia, no una nueva. Es lo que mantiene la cadena
            # rastreable: si manana hay que cerrar esta sesion, una sola
            # revocacion se lleva todos los eslabones, incluido el que tuviera
            # un ladron.
            family_id=actual.family_id,
            user_agent=user_agent,
        )

        await self._uow.commit()
        return resultado

    async def _detectar_reutilizacion(self, token: RefreshToken) -> None:
        """Un refresh token ya gastado volvio a aparecer.

        Solo hay dos explicaciones, y desde el servidor son indistinguibles:

          - el cliente reintento con un token viejo (un reintento de red, dos
            pestanas abiertas),
          - alguien copio el token y lo esta usando.

        Ante la duda se asume lo segundo y se revoca la FAMILIA ENTERA, no solo
        este token. El efecto es el que justifica todo el diseno: el ladron usa
        el token robado, la victima usa el suyo, el segundo en llegar dispara
        esto, y los dos quedan fuera. La victima vuelve a entrar con su
        contrasena; el ladron no puede. El robo se detecta solo, sin que nadie
        lo denuncie.

        El coste es un cierre de sesion ocasional por un reintento legitimo.
        Barato comparado con una sesion robada que dura semanas.
        """
        await self._tokens.revoke_family(token.family_id)

        # COMMIT ANTES DE LANZAR, a proposito. El error sube hasta el manejo
        # centralizado, que responde 401 y termina la peticion; si la
        # revocacion viajara sin confirmar, el rollback de la sesion la
        # desharia y la familia comprometida seguiria viva. Es el unico sitio
        # del proyecto donde se confirma en el camino de error.
        await self._uow.commit()

        # El unico rastro de la deteccion. La respuesta NO lo menciona: si un
        # token reutilizado diera un error distinto, quien lo robo sabria que
        # lo detectaron. Aqui queda para quien opera el sistema.
        logger.warning(
            "refresh_token_reuse_detected user_id=%s family_id=%s",
            token.user_id,
            token.family_id,
        )

        raise InvalidTokenError

    async def _emitir_par(
        self, *, user_id: UUID, family_id: UUID, user_agent: str | None
    ) -> RefreshResult:
        ahora = datetime.now(UTC)

        access = create_access_token(
            str(user_id),
            secret=self._jwt_secret,
            algorithm=self._jwt_algorithm,
            expires_minutes=self._access_token_minutes,
            # La misma familia que hereda el refresh: los dos tokens del par
            # nuevo siguen apuntando a la cadena original.
            family_id=str(family_id),
        )

        refresh = generate_opaque_token()
        await self._tokens.create(
            user_id=user_id,
            token_hash=hash_opaque_token(refresh),
            family_id=family_id,
            # Ventana COMPLETA otra vez, contada desde ahora. Es lo que hace
            # que una sesion activa no caduque nunca: mientras el usuario siga
            # usandola, cada refresh la renueva. Una sesion abandonada muere
            # sola cuando pasan los dias sin canjearse.
            expires_at=ahora + timedelta(days=self._refresh_token_days),
            user_agent=user_agent,
        )

        return RefreshResult(access_token=access, refresh_token=refresh)
