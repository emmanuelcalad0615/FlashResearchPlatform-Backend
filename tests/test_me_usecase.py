"""Tests del caso de uso GetMeUseCase.

Con repositorio en memoria: sin Postgres, sin app, sin red. El caso de uso solo
sabe pedirle el perfil al puerto, y eso es exactamente lo que se comprueba.
"""

import logging
import uuid

from packages.core.application.usecases.auth.me import GetMeUseCase
from tests.fakes import InMemoryProfileRepository


async def test_devuelve_el_perfil_del_usuario() -> None:
    perfiles = InMemoryProfileRepository()
    user_id = uuid.uuid4()
    await perfiles.create(user_id, display_name="Ana")

    perfil = await GetMeUseCase(profiles=perfiles).execute(user_id)

    assert perfil is not None
    assert perfil.id == user_id
    assert perfil.display_name == "Ana"


async def test_devuelve_none_si_no_hay_perfil() -> None:
    """Estado imposible en produccion, pero el caso de uso no revienta.

    Se responde None en vez de lanzar a proposito: /auth/me es la peticion que
    el frontend hace en cada carga de pagina, y un 500 por una anomalia del
    perfil sacaria de la aplicacion a un usuario con credenciales validas.
    """
    perfiles = InMemoryProfileRepository()

    perfil = await GetMeUseCase(profiles=perfiles).execute(uuid.uuid4())

    assert perfil is None


async def test_no_devuelve_el_perfil_de_otro_usuario() -> None:
    """Que el id que se pasa es el que se consulta, y no 'el primero que haya'.

    Sin este test, una implementacion que devolviera cualquier perfil pasaria
    los dos de arriba: con un solo perfil guardado, 'el correcto' y 'el unico'
    son indistinguibles.
    """
    perfiles = InMemoryProfileRepository()
    ana, beto = uuid.uuid4(), uuid.uuid4()
    await perfiles.create(ana, display_name="Ana")
    await perfiles.create(beto, display_name="Beto")

    perfil = await GetMeUseCase(profiles=perfiles).execute(beto)

    assert perfil is not None
    assert perfil.display_name == "Beto"


async def test_avisa_en_el_log_cuando_el_perfil_no_aparece(caplog) -> None:
    """Que la anomalia no pase en silencio.

    Responder 200 sin perfil es deliberado, pero si ademas no dejara rastro, el
    dia que la RLS quede mal configurada el unico sintoma seria que a todos los
    usuarios les desaparece el nombre, sin nada en los logs que lo explique.
    """
    perfiles = InMemoryProfileRepository()
    user_id = uuid.uuid4()

    with caplog.at_level(logging.WARNING):
        await GetMeUseCase(profiles=perfiles).execute(user_id)

    assert "profile_not_found_for_authenticated_user" in caplog.text
    assert str(user_id) in caplog.text


async def test_no_avisa_cuando_el_perfil_existe(caplog) -> None:
    """El aviso solo suena ante la anomalia.

    Sin este test, un logger que avisara siempre pasaria el de arriba y llenaria
    los logs de ruido en cada carga de pagina.
    """
    perfiles = InMemoryProfileRepository()
    user_id = uuid.uuid4()
    await perfiles.create(user_id, display_name="Ana")

    with caplog.at_level(logging.WARNING):
        await GetMeUseCase(profiles=perfiles).execute(user_id)

    assert caplog.text == ""
