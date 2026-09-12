"""Tests de RefreshUseCase.

Con dobles en memoria: la rotacion y la deteccion de reutilizacion son reglas
de negocio y se prueban sin Postgres.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from packages.core.application.usecases.auth.refresh import RefreshUseCase
from packages.core.domain.errors import InvalidTokenError
from packages.core.domain.policies.tokens import (
    decode_access_token,
    generate_opaque_token,
    hash_opaque_token,
)
from tests.fakes import InMemoryRefreshTokenRepository, InMemoryUnitOfWork

SECRET = "un-secreto-de-prueba-de-al-menos-32-bytes-de-largo"
ALGORITMO = "HS256"
DIAS = 30


class _Dobles:
    def __init__(self) -> None:
        self.tokens = InMemoryRefreshTokenRepository()
        self.uow = InMemoryUnitOfWork()
        self.user_id = uuid.uuid4()
        self.family_id = uuid.uuid4()
        # El caso de uso se construye UNA vez, aqui. Antes se creaba en cada
        # llamada, y eso metia una construccion dentro de los bloques
        # `pytest.raises`: si reventara ahi, el test pasaria por la razon
        # equivocada sin que nadie lo notara.
        self.caso = RefreshUseCase(
            tokens=self.tokens,
            uow=self.uow,
            jwt_secret=SECRET,
            jwt_algorithm=ALGORITMO,
            access_token_minutes=15,
            refresh_token_days=DIAS,
        )

    async def emitir(self, *, expira_en_dias: int = DIAS) -> str:
        """Crea un refresh token vivo y devuelve la cadena en claro."""
        token = generate_opaque_token()
        await self.tokens.create(
            user_id=self.user_id,
            token_hash=hash_opaque_token(token),
            family_id=self.family_id,
            expires_at=datetime.now(UTC) + timedelta(days=expira_en_dias),
            user_agent=None,
        )
        return token


@pytest.fixture
def dobles() -> _Dobles:
    return _Dobles()


# ---------------------------------------------------------------------------
# Camino feliz
# ---------------------------------------------------------------------------


async def test_devuelve_un_par_nuevo(dobles) -> None:
    viejo = await dobles.emitir()

    resultado = await dobles.caso.execute(viejo)

    assert resultado.refresh_token != viejo
    claims = decode_access_token(
        resultado.access_token, secret=SECRET, algorithm=ALGORITMO
    )
    assert claims.user_id == str(dobles.user_id)


async def test_gasta_el_token_presentado(dobles) -> None:
    """La rotacion: un refresh token vale exactamente un uso."""
    viejo = await dobles.emitir()

    await dobles.caso.execute(viejo)

    usado = await dobles.tokens.get_by_hash(hash_opaque_token(viejo))
    assert usado.is_used


async def test_el_token_nuevo_hereda_la_familia(dobles) -> None:
    """Lo que mantiene la cadena rastreable.

    Si cada renovacion abriera familia nueva, revocar una sesion solo cerraria
    su ultimo eslabon y la deteccion de robo no tendria nada que revocar.
    """
    viejo = await dobles.emitir()

    resultado = await dobles.caso.execute(viejo)

    nuevo = await dobles.tokens.get_by_hash(hash_opaque_token(resultado.refresh_token))
    assert nuevo.family_id == dobles.family_id


async def test_renueva_la_ventana_completa(dobles) -> None:
    """Una sesion en uso no caduca; una abandonada muere sola.

    El token nuevo expira a los 30 dias contados desde AHORA, no desde que
    empezo la sesion.
    """
    viejo = await dobles.emitir(expira_en_dias=1)

    resultado = await dobles.caso.execute(viejo)

    nuevo = await dobles.tokens.get_by_hash(hash_opaque_token(resultado.refresh_token))
    assert nuevo.expires_at > datetime.now(UTC) + timedelta(days=DIAS - 1)


async def test_confirma_la_transaccion(dobles) -> None:
    viejo = await dobles.emitir()

    await dobles.caso.execute(viejo)

    assert dobles.uow.commits == 1


# ---------------------------------------------------------------------------
# Rechazos
# ---------------------------------------------------------------------------


async def test_token_desconocido(dobles) -> None:
    inventado = generate_opaque_token()

    with pytest.raises(InvalidTokenError):
        await dobles.caso.execute(inventado)


async def test_token_expirado(dobles) -> None:
    viejo = await dobles.emitir(expira_en_dias=-1)

    with pytest.raises(InvalidTokenError):
        await dobles.caso.execute(viejo)


async def test_token_revocado(dobles) -> None:
    viejo = await dobles.emitir()
    await dobles.tokens.revoke_family(dobles.family_id)

    with pytest.raises(InvalidTokenError):
        await dobles.caso.execute(viejo)


async def test_un_rechazo_no_emite_nada(dobles) -> None:
    """Que un intento fallido no deje un token vivo suelto en la base."""
    await dobles.emitir(expira_en_dias=-1)
    antes = len(dobles.tokens.por_id)

    inventado = generate_opaque_token()

    with pytest.raises(InvalidTokenError):
        await dobles.caso.execute(inventado)

    assert len(dobles.tokens.por_id) == antes


# ---------------------------------------------------------------------------
# Deteccion de reutilizacion — el corazon del paso
# ---------------------------------------------------------------------------


async def test_reutilizar_un_token_revoca_la_familia_entera(dobles) -> None:
    """El escenario del robo, completo.

    El ladron copia el refresh token y lo canjea: le funciona, porque desde el
    servidor es indistinguible del legitimo. Cuando la victima canjea el suyo
    —el mismo, ya gastado— salta esto y los DOS quedan fuera. La victima vuelve
    a entrar con su contrasena; el ladron no puede.
    """
    robado = await dobles.emitir()
    del_ladron = await dobles.caso.execute(robado)

    with pytest.raises(InvalidTokenError):
        await dobles.caso.execute(robado)

    # No basta con que el reintento falle: el token que el ladron obtuvo en su
    # canje tambien tiene que haber muerto. Sin esto, el test pasaria aunque la
    # revocacion no existiera.
    with pytest.raises(InvalidTokenError):
        await dobles.caso.execute(del_ladron.refresh_token)


async def test_la_revocacion_se_confirma_pese_al_error(dobles) -> None:
    """El commit del camino de error.

    Sin el, la excepcion sube, la sesion revierte, y la familia comprometida
    sigue viva: la revocacion se habria 'hecho' sin ningun efecto.
    """
    viejo = await dobles.emitir()
    await dobles.caso.execute(viejo)
    commits_antes = dobles.uow.commits

    with pytest.raises(InvalidTokenError):
        await dobles.caso.execute(viejo)

    assert dobles.uow.commits == commits_antes + 1


async def test_no_toca_las_otras_familias_del_usuario(dobles) -> None:
    """Un robo en un dispositivo no echa al usuario de los demas.

    Es la razon de que cada login abra su propia familia.
    """
    comprometido = await dobles.emitir()
    await dobles.caso.execute(comprometido)

    dobles.family_id = uuid.uuid4()          # otro dispositivo, otra sesion
    del_otro_movil = await dobles.emitir()

    with pytest.raises(InvalidTokenError):
        await dobles.caso.execute(comprometido)

    resultado = await dobles.caso.execute(del_otro_movil)
    assert resultado.access_token


async def test_deja_rastro_en_el_log(dobles, caplog) -> None:
    """El unico sitio donde se registra la deteccion.

    La respuesta HTTP no la menciona a proposito: un error distinto le diria a
    quien robo el token que fue descubierto.
    """
    viejo = await dobles.emitir()
    await dobles.caso.execute(viejo)

    with caplog.at_level(logging.WARNING), pytest.raises(InvalidTokenError):
        await dobles.caso.execute(viejo)

    assert "refresh_token_reuse_detected" in caplog.text
    assert str(dobles.family_id) in caplog.text


async def test_reutilizar_dentro_de_una_familia_ya_revocada_vuelve_a_avisar(
    dobles, caplog
) -> None:
    """Por que is_used se comprueba ANTES que is_revoked.

    Tras la primera deteccion la familia queda revocada, asi que sus tokens
    estan usados Y revocados. Si el orden fuera al reves, los reintentos
    saldrian por el rechazo normal y dejarian de registrarse: se perderia
    cuantas veces insiste quien tiene el token robado.
    """
    viejo = await dobles.emitir()
    await dobles.caso.execute(viejo)
    with pytest.raises(InvalidTokenError):
        await dobles.caso.execute(viejo)

    caplog.clear()
    with caplog.at_level(logging.WARNING), pytest.raises(InvalidTokenError):
        await dobles.caso.execute(viejo)

    assert "refresh_token_reuse_detected" in caplog.text
