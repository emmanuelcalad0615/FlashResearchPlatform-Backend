"""Puerto de unidad de trabajo.

Marca donde empieza y termina un grupo de escrituras que deben ocurrir todas o
ninguna. Existe porque esa decision es de negocio, no de persistencia:

  - el repositorio sabe escribir UNA cosa, y hace flush
  - solo el caso de uso sabe que tres escrituras van juntas, y hace commit

Si cada repositorio confirmara por su cuenta, un fallo a mitad del signup
dejaria un usuario sin perfil, permanente en la base.

El caso de uso dice "confirma"; no sabe que por debajo es un COMMIT de Postgres.
"""

from abc import ABC, abstractmethod


class UnitOfWork(ABC):
    @abstractmethod
    async def commit(self) -> None:
        """Hace permanente todo lo escrito hasta aqui."""

    @abstractmethod
    async def rollback(self) -> None:
        """Descarta todo lo escrito desde el ultimo commit."""
