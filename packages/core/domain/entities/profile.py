from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class Profile:
    """Preferencias de aplicacion del usuario. 1:1 con User.

    Separado de User a proposito: la identidad cambia por razones de seguridad,
    las preferencias por gusto del usuario. Comparten el id.
    """

    id: UUID
    display_name: str | None
    settings: dict
    created_at: datetime
    updated_at: datetime
