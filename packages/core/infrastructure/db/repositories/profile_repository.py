"""Implementacion del puerto de perfiles sobre SQLAlchemy."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.domain.entities import Profile
from packages.core.domain.repositories import ProfileRepository
from packages.core.infrastructure.db.models import Profile as ProfileORM


def _to_entity(fila: ProfileORM) -> Profile:
    return Profile(
        id=fila.id,
        display_name=fila.display_name,
        settings=fila.settings,
        created_at=fila.created_at,
        updated_at=fila.updated_at,
    )


class SqlAlchemyProfileRepository(ProfileRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, user_id: UUID, display_name: str | None = None) -> Profile:
        # La politica RLS de profiles solo deja insertar una fila cuyo id
        # coincida con el usuario declarado en app.current_user_id. Durante el
        # registro todavia no hay sesion, asi que se declara aqui.
        #
        # SET LOCAL y no SET: muere al cerrar la transaccion. Con SET a secas el
        # valor quedaria pegado a la conexion, y como el pool las reutiliza, la
        # siguiente peticion heredaria este usuario.
        #
        # Este detalle de Postgres vive en infraestructura a proposito: el caso
        # de uso llama a create() sin saber que existe RLS.
        await self._session.execute(
            text("SET LOCAL app.current_user_id = :uid"), {"uid": str(user_id)}
        )

        fila = ProfileORM(id=user_id, display_name=display_name)
        self._session.add(fila)
        await self._session.flush()
        await self._session.refresh(fila)
        return _to_entity(fila)

    async def get_by_id(self, user_id: UUID) -> Profile | None:
        fila = await self._session.get(ProfileORM, user_id)
        return _to_entity(fila) if fila else None
