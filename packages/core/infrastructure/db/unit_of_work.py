"""Implementacion del puerto UnitOfWork sobre una sesion de SQLAlchemy."""

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.domain.repositories import UnitOfWork


class SqlAlchemyUnitOfWork(UnitOfWork):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
