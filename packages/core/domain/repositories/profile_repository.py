"""Puerto de acceso a perfiles."""

from abc import ABC, abstractmethod
from uuid import UUID

from packages.core.domain.entities import Profile


class ProfileRepository(ABC):
    @abstractmethod
    async def create(self, user_id: UUID, display_name: str | None = None) -> Profile:
        """Crea el perfil del usuario. Comparte id con el User.

        La implementacion se encarga de lo que la politica RLS de la tabla
        exija; el caso de uso no tiene por que saber que existe.
        """

    @abstractmethod
    async def get_by_id(self, user_id: UUID) -> Profile | None:
        """El perfil de ese usuario, o None."""
