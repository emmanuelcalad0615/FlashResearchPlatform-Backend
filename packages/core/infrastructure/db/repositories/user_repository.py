"""Implementacion del puerto de usuarios sobre SQLAlchemy."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.domain.entities import User
from packages.core.domain.repositories import UserRepository
from packages.core.infrastructure.db.models import User as UserORM


def _to_entity(fila: UserORM) -> User:
    """Traduce la fila de la base a la entidad de dominio.

    El caso de uso recibe un dataclass desconectado de la sesion: no arrastra
    cargas perezosas ni puede disparar SQL por accidente al leer un atributo.
    """
    return User(
        id=fila.id,
        email=fila.email,
        password_hash=fila.password_hash,
        email_verified=fila.email_verified,
        created_at=fila.created_at,
        updated_at=fila.updated_at,
    )


class SqlAlchemyUserRepository(UserRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_email(self, email: str) -> User | None:
        # La columna es CITEXT, asi que la comparacion ignora mayusculas sin
        # necesidad de lower() por ninguna de las dos partes.
        fila = await self._session.scalar(select(UserORM).where(UserORM.email == email))
        return _to_entity(fila) if fila else None

    async def get_by_id(self, user_id: UUID) -> User | None:
        fila = await self._session.get(UserORM, user_id)
        return _to_entity(fila) if fila else None

    async def create(self, user_id: UUID, email: str, password_hash: str) -> User:
        fila = UserORM(id=user_id, email=email, password_hash=password_hash)
        self._session.add(fila)
        # flush y NO commit: manda el INSERT para que la FK de profiles pueda
        # apuntar a esta fila, pero deja la transaccion abierta. Quien decide
        # que operaciones son atomicas es el caso de uso.
        await self._session.flush()
        await self._session.refresh(fila)
        return _to_entity(fila)

    async def mark_email_verified(self, user_id: UUID) -> None:
        await self._session.execute(
            update(UserORM)
            .where(UserORM.id == user_id)
            .values(email_verified=True, updated_at=datetime.now(UTC))
        )

    async def update_password_hash(self, user_id: UUID, password_hash: str) -> None:
        await self._session.execute(
            update(UserORM)
            .where(UserORM.id == user_id)
            .values(password_hash=password_hash, updated_at=datetime.now(UTC))
        )
