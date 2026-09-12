"""Implementacion del puerto de verificacion de correo sobre SQLAlchemy."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.domain.entities import EmailVerification
from packages.core.domain.repositories import EmailVerificationRepository
from packages.core.infrastructure.db.models import (
    EmailVerificationToken as EmailVerificationORM,
)


def _to_entity(fila: EmailVerificationORM) -> EmailVerification:
    return EmailVerification(
        id=fila.id,
        user_id=fila.user_id,
        expires_at=fila.expires_at,
        used_at=fila.used_at,
        created_at=fila.created_at,
    )


class SqlAlchemyEmailVerificationRepository(EmailVerificationRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, *, user_id: UUID, token_hash: str, expires_at: datetime
    ) -> EmailVerification:
        fila = EmailVerificationORM(
            user_id=user_id, token_hash=token_hash, expires_at=expires_at
        )
        self._session.add(fila)
        await self._session.flush()
        await self._session.refresh(fila)
        return _to_entity(fila)

    async def get_by_hash(self, token_hash: str) -> EmailVerification | None:
        fila = await self._session.scalar(
            select(EmailVerificationORM).where(
                EmailVerificationORM.token_hash == token_hash
            )
        )
        return _to_entity(fila) if fila else None

    async def mark_used(self, token_id: UUID) -> None:
        await self._session.execute(
            update(EmailVerificationORM)
            .where(EmailVerificationORM.id == token_id)
            .values(used_at=datetime.now(UTC))
        )

    async def get_latest_for_user(self, user_id: UUID) -> EmailVerification | None:
        fila = await self._session.scalar(
            select(EmailVerificationORM)
            .where(EmailVerificationORM.user_id == user_id)
            .order_by(EmailVerificationORM.created_at.desc())
            # Desempate por id SOLO para que el resultado sea estable entre
            # ejecuciones. No ordena por antiguedad: gen_random_uuid() no es
            # creciente. Ante un empate de created_at da igual cual salga, y el
            # puerto lo dice: quien llama solo lee created_at, que es el mismo
            # en los empatados.
            .order_by(EmailVerificationORM.id.desc())
            .limit(1)
        )
        return _to_entity(fila) if fila else None

    async def invalidate_for_user(self, user_id: UUID) -> None:
        await self._session.execute(
            update(EmailVerificationORM)
            .where(
                EmailVerificationORM.user_id == user_id,
                # Solo los vivos: pisar el used_at de uno ya consumido borraria
                # cuando se uso de verdad, que es lo que sirve para investigar.
                EmailVerificationORM.used_at.is_(None),
            )
            .values(used_at=datetime.now(UTC))
        )
