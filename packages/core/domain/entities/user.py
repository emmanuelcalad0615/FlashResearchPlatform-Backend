from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class User:
    """Un usuario del sistema, sin nada de persistencia dentro.

    Es un dataclass y no un modelo de SQLAlchemy a proposito: el dominio no
    puede depender de la infraestructura. Traducir entre esta entidad y la fila
    de la base es trabajo del repositorio, en la capa de fuera.

    frozen=True: quien quiera cambiar algo pasa por el repositorio. Asi no hay
    objetos a medio modificar circulando por los casos de uso.
    """

    id: UUID
    email: str
    password_hash: str
    email_verified: bool
    created_at: datetime
    updated_at: datetime
