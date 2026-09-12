"""Caso de uso: los datos del usuario de la sesion actual.

Quien llama ya tiene el User —lo cargo la dependencia que valido la cookie—, asi
que lo unico que falta es su perfil, que vive en otra tabla.
"""

import logging
from uuid import UUID

from packages.core.domain.entities import Profile
from packages.core.domain.repositories import ProfileRepository

logger = logging.getLogger(__name__)


class GetMeUseCase:
    def __init__(self, *, profiles: ProfileRepository) -> None:
        self._profiles = profiles

    async def execute(self, user_id: UUID) -> Profile | None:
        """El perfil del usuario, o None si no aparece.

        None es un estado que NO deberia darse: el registro crea usuario y
        perfil en la misma transaccion, asi que si hay uno hay el otro. Solo
        puede venir de datos corruptos o de que la sesion no haya declarado
        `app.current_user_id`, en cuyo caso la politica RLS de profiles no
        devuelve ninguna fila.

        Aun asi no se lanza un error, a proposito. `/auth/me` es la peticion que
        el frontend hace en cada carga de pagina para saber si hay sesion: si
        respondiera 500 ante una anomalia en el perfil, el usuario quedaria
        fuera de la aplicacion aunque sus credenciales esten perfectas. Se
        prefiere responder con display_name vacio y que la sesion siga en pie.

        Lo que NO se hace es callarlo. Sin este aviso, el dia que la RLS quede
        mal configurada la aplicacion seguiria respondiendo 200 y lo unico
        raro seria que a todo el mundo le desaparece el nombre. Con el, queda
        rastro en los logs de que algo esta roto.
        """
        perfil = await self._profiles.get_by_id(user_id)

        if perfil is None:
            # El id va en el mensaje y no en extra={}: el formateador de la API
            # no lleva ExtraAdder, asi que los campos sueltos no se renderizan.
            logger.warning(
                "profile_not_found_for_authenticated_user user_id=%s", user_id
            )

        return perfil
