from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Clase declarativa base. Todos los modelos heredan de aquí.

    Base.metadata mantiene el registro de todas las tablas conocidas
    (se conectará a target_metadata en migrations/env.py para autogenerate).
    """

    pass
