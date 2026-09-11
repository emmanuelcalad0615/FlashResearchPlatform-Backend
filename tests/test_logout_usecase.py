"""Tests de LogoutUseCase y LogoutAllUseCase.

Con dobles en memoria. Lo que se comprueba es el ALCANCE de cada cierre: cual
revoca una familia y cual las revoca todas. Confundirlos seria un fallo caro en
las dos direcciones —dejar viva una sesion robada, o echar al usuario de todos
sus dispositivos cada vez que cierra el portatil.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from packages.core.application.usecases.auth.logout import LogoutUseCase
from packages.core.application.usecases.auth.logout_all import LogoutAllUseCase
from packages.core.domain.policies.tokens import (
    generate_opaque_token,
    hash_opaque_token,
)
from tests.fakes import InMemoryRefreshTokenRepository, InMemoryUnitOfWork


class _Dobles:
    def __init__(self) -> None:
        self.tokens = InMemoryRefreshTokenRepository()
        self.uow = InMemoryUnitOfWork()
        self.user_id = uuid.uuid4()

    def logout(self) -> LogoutUseCase:
        return LogoutUseCase(tokens=self.tokens, uow=self.uow)

    def logout_all(self) -> LogoutAllUseCase:
        return LogoutAllUseCase(tokens=self.tokens, uow=self.uow)

    async def abrir_sesion(self, *, user_id=None) -> tuple[uuid.UUID, str]:
        """Simula un login: familia nueva y un refresh token vivo."""
        family_id = uuid.uuid4()
        token = generate_opaque_token()
        await self.tokens.create(
            user_id=user_id or self.user_id,
            token_hash=hash_opaque_token(token),
            family_id=family_id,
            expires_at=datetime.now(UTC) + timedelta(days=30),
            user_agent=None,
        )
        return family_id, token

    async def vivo(self, token: str) -> bool:
        fila = await self.tokens.get_by_hash(hash_opaque_token(token))
        return fila is not None and not fila.is_revoked


@pytest.fixture
def dobles() -> _Dobles:
    return _Dobles()


# ---------------------------------------------------------------------------
# LogoutUseCase — una sola sesion
# ---------------------------------------------------------------------------


async def test_revoca_la_familia_indicada(dobles) -> None:
    family_id, token = await dobles.abrir_sesion()

    await dobles.logout().execute(family_id)

    assert not await dobles.vivo(token)


async def test_no_toca_las_otras_sesiones_del_usuario(dobles) -> None:
    """Cerrar sesion en el portatil no cierra la del movil.

    Es la razon de que cada login abra su propia familia; sin este test, un
    logout que llamara a revoke_all_for_user pasaria el anterior.
    """
    family_portatil, token_portatil = await dobles.abrir_sesion()
    _, token_movil = await dobles.abrir_sesion()

    await dobles.logout().execute(family_portatil)

    assert not await dobles.vivo(token_portatil)
    assert await dobles.vivo(token_movil)


async def test_revoca_la_cadena_entera_de_esa_sesion(dobles) -> None:
    """Una familia con varios eslabones se cierra completa.

    Una sesion renovada tres veces tiene cuatro filas. Si el cierre solo
    revocara la ultima, las anteriores —ya usadas— seguirian sin revocar y el
    rastro quedaria a medias.
    """
    family_id, primero = await dobles.abrir_sesion()
    segundo = generate_opaque_token()
    await dobles.tokens.create(
        user_id=dobles.user_id,
        token_hash=hash_opaque_token(segundo),
        family_id=family_id,
        expires_at=datetime.now(UTC) + timedelta(days=30),
        user_agent=None,
    )

    await dobles.logout().execute(family_id)

    assert not await dobles.vivo(primero)
    assert not await dobles.vivo(segundo)


async def test_confirma_la_transaccion(dobles) -> None:
    family_id, _ = await dobles.abrir_sesion()

    await dobles.logout().execute(family_id)

    assert dobles.uow.commits == 1


async def test_sin_familia_no_falla_ni_escribe(dobles) -> None:
    """Un access token emitido antes de que existiera el claim `fid`.

    Esos tokens viven hasta quince minutos tras el despliegue. No hay familia
    que revocar, pero la operacion no puede reventar: la capa HTTP tiene que
    llegar a borrar las cookies igual.
    """
    _, token = await dobles.abrir_sesion()

    await dobles.logout().execute(None)

    assert dobles.uow.commits == 0
    assert await dobles.vivo(token)


async def test_una_familia_inexistente_no_revienta(dobles) -> None:
    """Cerrar dos veces, o cerrar una sesion ya cerrada, es inofensivo."""
    await dobles.logout().execute(uuid.uuid4())


# ---------------------------------------------------------------------------
# LogoutAllUseCase — todas las sesiones
# ---------------------------------------------------------------------------


async def test_revoca_todas_las_sesiones_del_usuario(dobles) -> None:
    _, movil = await dobles.abrir_sesion()
    _, portatil = await dobles.abrir_sesion()
    _, tablet = await dobles.abrir_sesion()

    await dobles.logout_all().execute(dobles.user_id)

    assert not await dobles.vivo(movil)
    assert not await dobles.vivo(portatil)
    assert not await dobles.vivo(tablet)


async def test_no_toca_las_sesiones_de_otros_usuarios(dobles) -> None:
    """El fallo que convertiria un cierre de sesion en una caida del servicio."""
    _, propia = await dobles.abrir_sesion()
    ajeno = uuid.uuid4()
    _, de_otro = await dobles.abrir_sesion(user_id=ajeno)

    await dobles.logout_all().execute(dobles.user_id)

    assert not await dobles.vivo(propia)
    assert await dobles.vivo(de_otro)


async def test_logout_all_confirma_la_transaccion(dobles) -> None:
    await dobles.abrir_sesion()

    await dobles.logout_all().execute(dobles.user_id)

    assert dobles.uow.commits == 1


async def test_un_usuario_sin_sesiones_no_revienta(dobles) -> None:
    await dobles.logout_all().execute(uuid.uuid4())
