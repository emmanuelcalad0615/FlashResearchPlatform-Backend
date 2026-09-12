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

    @abstractmethod
    async def get_latest_for_user(self, user_id: UUID) -> EmailVerification | None:
        """El ultimo token emitido a ese usuario, o None.

        Lo usa el reenvio para saber cuanto hace que se mando el anterior. Sin
        esta pregunta, el enfriamiento entre reenvios no se puede aplicar sin
        guardar estado en otro sitio.

        "Ultimo" significa el de `created_at` mayor. ANTE UN EMPATE, cual de
        los empatados se devuelve NO esta definido, y no hace falta que lo
        este: quien llama solo lee `created_at`, y los empatados tienen el
        mismo. El empate es real: dentro de una misma transaccion `now()` no
        avanza, asi que dos filas creadas seguidas comparten instante.
        """

    @abstractmethod
    async def invalidate_for_user(self, user_id: UUID) -> None:
        """Marca como usados los tokens vivos del usuario.

        El reenvio emite uno nuevo, y dejar vivos los anteriores multiplicaria
        los enlaces validos circulando por buzones y logs de correo. Solo el
        ultimo debe servir.
        """
