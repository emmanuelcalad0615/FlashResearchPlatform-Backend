"""Implementacion del puerto de refresh tokens sobre SQLAlchemy."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.domain.entities import RefreshToken
from packages.core.domain.repositories import RefreshTokenRepository
from packages.core.infrastructure.db.models import RefreshToken as RefreshTokenORM


def _to_entity(fila: RefreshTokenORM) -> RefreshToken:
    # El token_hash NO viaja a la entidad: el caso de uso ya tiene el token en
    # claro y no necesita el hash. Cuanto menos circule, mejor.
    return RefreshToken(
        id=fila.id,
        user_id=fila.user_id,
        family_id=fila.family_id,
        expires_at=fila.expires_at,
        used_at=fila.used_at,
        revoked_at=fila.revoked_at,
        created_at=fila.created_at,
    )


class SqlAlchemyRefreshTokenRepository(RefreshTokenRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: UUID,
        token_hash: str,
        family_id: UUID,
        expires_at: datetime,
        user_agent: str | None,
    ) -> RefreshToken:
        fila = RefreshTokenORM(
            user_id=user_id,
            token_hash=token_hash,
            family_id=family_id,
            expires_at=expires_at,
            user_agent=user_agent,
        )
        self._session.add(fila)
        await self._session.flush()
        await self._session.refresh(fila)
        return _to_entity(fila)

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        # Sin filtrar por estado, a proposito: la deteccion de reutilizacion
        # necesita encontrar un token YA USADO para poder revocar la familia.
        # Si esta consulta lo escondiera, el robo pasaria desapercibido.
        fila = await self._session.scalar(
            select(RefreshTokenORM).where(RefreshTokenORM.token_hash == token_hash)
        )
        return _to_entity(fila) if fila else None

    async def mark_used(self, token_id: UUID) -> None:
        await self._session.execute(
            update(RefreshTokenORM)
            .where(RefreshTokenORM.id == token_id)
            .values(used_at=datetime.now(UTC))
        )

    async def revoke_family(self, family_id: UUID) -> None:
        """Cierra UNA sesion: toda su cadena de rotacion."""
        await self._session.execute(
            update(RefreshTokenORM)
            .where(
                RefreshTokenORM.family_id == family_id,
                # Sin volver a tocar los ya revocados: conservan la fecha
                # original, que es la que sirve para investigar despues.
                RefreshTokenORM.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )

    async def revoke_all_for_user(self, user_id: UUID) -> None:
        """Cierra TODAS las sesiones del usuario, en todos sus dispositivos."""
        await self._session.execute(
            update(RefreshTokenORM)
            .where(
                RefreshTokenORM.user_id == user_id,
                RefreshTokenORM.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
