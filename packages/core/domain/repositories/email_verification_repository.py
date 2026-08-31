"""Puerto de acceso a los tokens de verificacion de correo."""

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from packages.core.domain.entities import EmailVerification


class EmailVerificationRepository(ABC):
    @abstractmethod
    async def create(
        self, *, user_id: UUID, token_hash: str, expires_at: datetime
    ) -> EmailVerification:
        """Guarda un token de verificacion nuevo, hasheado."""

    @abstractmethod
    async def get_by_hash(self, token_hash: str) -> EmailVerification | None:
        """El token con ese hash, o None. Devuelve la fila aunque este usada:
        distinguir 'no existe' de 'ya se uso' es decision del caso de uso."""

    @abstractmethod
    async def mark_used(self, token_id: UUID) -> None:
        """Marca el token como consumido. Es de un solo uso."""
