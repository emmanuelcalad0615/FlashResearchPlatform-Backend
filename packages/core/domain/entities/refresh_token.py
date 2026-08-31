from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID


@dataclass(frozen=True)
class RefreshToken:
    """Un refresh token emitido.

    Sus tres fechas opcionales forman una maquina de estados, y las reglas para
    leerla viven aqui, en el dominio: son ciertas independientemente de si el
    token se guarda en Postgres, en Redis o en un archivo.
    """

    id: UUID
    user_id: UUID
    family_id: UUID
    expires_at: datetime
    used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime

    @property
    def is_expired(self) -> bool:
        return datetime.now(UTC) >= self.expires_at

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_usable(self) -> bool:
        """True solo si el token se puede canjear por uno nuevo.

        Ojo: que esto sea False NO significa "rechazar y ya". Un token usado
        indica que alguien lo reutilizo, y eso obliga a revocar la familia
        entera. Distinguir los casos es trabajo del caso de uso.
        """
        return not (self.is_expired or self.is_used or self.is_revoked)
