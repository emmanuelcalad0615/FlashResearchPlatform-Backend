from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID


@dataclass(frozen=True)
class EmailVerification:
    """Un enlace de verificacion de correo emitido."""

    id: UUID
    user_id: UUID
    expires_at: datetime
    used_at: datetime | None
    created_at: datetime

    @property
    def is_expired(self) -> bool:
        return datetime.now(UTC) >= self.expires_at

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    @property
    def is_usable(self) -> bool:
        return not (self.is_expired or self.is_used)
