"""Caso de uso: cierre de TODAS las sesiones del usuario.

Es la reaccion a un 'creo que alguien entro en mi cuenta': invalida todas las
cadenas de rotacion, en todos los dispositivos, a la vez.

Separado de LogoutUseCase a proposito. Se parecen al leerlos, pero responden a
preguntas distintas —'salgo de este equipo' y 'echa a todo el mundo'— y
mezclarlos en una funcion con un booleano haria que la llamada no dijera cual
de las dos cosas esta pasando.
"""

from uuid import UUID

from packages.core.domain.repositories import RefreshTokenRepository, UnitOfWork


class LogoutAllUseCase:
    def __init__(
        self, *, tokens: RefreshTokenRepository, uow: UnitOfWork
    ) -> None:
        self._tokens = tokens
        self._uow = uow

    async def execute(self, user_id: UUID) -> None:
        """Revoca todas las familias del usuario.

        No hace falta saber cuales son ni cuantas: el repositorio las busca por
        user_id. Incluye la sesion desde la que se pide, a proposito —si
        sospechas que hay un intruso, no sabes cual de las sesiones abiertas es
        la suya, asi que se cierran todas y vuelves a entrar con tu
        contrasena.

        El access token en curso sobrevive hasta que caduque: es sin estado y
        no hay forma de invalidarlo sin consultar la base en cada peticion.
        Por eso dura quince minutos. Esa es la ventana real de este cierre.
        """
        await self._tokens.revoke_all_for_user(user_id)
        await self._uow.commit()
