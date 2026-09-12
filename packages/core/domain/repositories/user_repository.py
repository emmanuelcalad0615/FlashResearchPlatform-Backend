"""Puerto de acceso a usuarios.

Solo el contrato: ni una linea de SQL. Los casos de uso dependen de esta
interfaz, no de la implementacion, y por eso se pueden probar enteros con un
doble en memoria, sin levantar Postgres.
"""

from abc import ABC, abstractmethod
from uuid import UUID

from packages.core.domain.entities import User


class UserRepository(ABC):
    @abstractmethod
    async def get_by_email(self, email: str) -> User | None:
        """El usuario con ese email, o None. La busqueda ignora mayusculas."""

    @abstractmethod
    async def get_by_id(self, user_id: UUID) -> User | None:
        """El usuario con ese id, o None."""

    @abstractmethod
    async def create(self, user_id: UUID, email: str, password_hash: str) -> User:
        """Crea el usuario con el id que le pasan.

        El id llega de fuera y no lo genera la base porque el signup necesita
        conocerlo ANTES de insertar: con el se declara `SET LOCAL
        app.current_user_id`, sin el cual la politica RLS de profiles rechaza
        la insercion del perfil.
        """

    @abstractmethod
    async def mark_email_verified(self, user_id: UUID) -> None:
        """Marca el correo como verificado."""

    @abstractmethod
    async def update_password_hash(self, user_id: UUID, password_hash: str) -> None:
        """Reemplaza el hash. Lo usa el rehash progresivo durante el login."""
