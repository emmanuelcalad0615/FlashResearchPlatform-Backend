import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from packages.core.infrastructure.db.models.base import Base


class RefreshToken(Base):
    """Un refresh token vivo, guardado hasheado.

    Sus tres fechas opcionales forman la maquina de estados del token:

        used_at NULL, revoked_at NULL   -> activo, listo para usarse
        used_at con valor               -> ya rotado
        revoked_at con valor            -> muerto (logout o reutilizacion detectada)

    Interpretar esos estados es trabajo del caso de uso, no del modelo.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Nunca el token en claro: si roban un volcado de la base, los hashes no
    # sirven para entrar.
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    # Cadena de rotacion. Cada login abre una familia; cada refresh la hereda.
    # Si se reusa un token ya usado, se revoca la familia entera.
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Para poder mostrar las sesiones abiertas y cerrarlas por dispositivo.
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
