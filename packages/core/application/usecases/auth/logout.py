"""Caso de uso: cierre de UNA sesion.

Cerrar sesion no es borrar la cookie. Si alguien copio el refresh token, que
desaparezca del navegador de la victima no le quita nada: su copia sigue
sirviendo. Lo que cierra la sesion de verdad es revocar la familia en la base.

Borrar las cookies tambien hace falta, pero eso es trabajo de la capa HTTP.
"""

from uuid import UUID

from packages.core.domain.repositories import RefreshTokenRepository, UnitOfWork


class LogoutUseCase:
    def __init__(
        self, *, tokens: RefreshTokenRepository, uow: UnitOfWork
    ) -> None:
        self._tokens = tokens
        self._uow = uow

    async def execute(self, family_id: UUID | None) -> None:
        """Revoca la cadena de rotacion de esta sesion.

        `family_id` llega del claim `fid` del access token, que esta FIRMADO:
        no es un dato que el cliente pueda elegir. Si viniera del cuerpo de la
        peticion, cualquiera podria cerrar la sesion de otro escribiendo un
        UUID ajeno.

        None no es un error. Un access token emitido antes de que existiera el
        claim no lo trae, y esos tokens siguen siendo validos hasta que
        caduquen. No hay familia que revocar, pero la peticion termina bien y
        la capa HTTP borra las cookies igual: el usuario pidio quedarse fuera y
        se queda fuera.
        """
        if family_id is None:
            return

        await self._tokens.revoke_family(family_id)
        await self._uow.commit()
