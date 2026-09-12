"""Puerto de acceso a refresh tokens."""

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from packages.core.domain.entities import RefreshToken


class RefreshTokenRepository(ABC):
    @abstractmethod
    async def create(
        self,
        *,
        user_id: UUID,
        token_hash: str,
        family_id: UUID,
        expires_at: datetime,
        user_agent: str | None,
    ) -> RefreshToken:
        """Guarda un refresh token nuevo. El hash, nunca el token en claro."""

    @abstractmethod
    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        """El token con ese hash, o None.

        Devuelve la fila AUNQUE este usada o revocada, a proposito: la
        deteccion de reutilizacion necesita encontrar un token ya usado para
        poder reaccionar. Si este metodo lo escondiera, el robo pasaria
        desapercibido.

        El repositorio dice que hay; el caso de uso decide que significa.
        """

    @abstractmethod
    async def mark_used(self, token_id: UUID) -> None:
        """Marca el token como consumido. Un refresh token vale un solo uso."""

    @abstractmethod
    async def revoke_family(self, family_id: UUID) -> None:
        """Revoca toda la cadena de rotacion. Cierra UNA sesion."""

    @abstractmethod
    async def revoke_all_for_user(self, user_id: UUID) -> None:
        """Revoca todas las familias del usuario. Cierra TODAS sus sesiones."""
