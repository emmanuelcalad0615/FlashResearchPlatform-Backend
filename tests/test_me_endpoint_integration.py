"""GET /api/auth/me contra el grafo REAL y bajo politicas RLS de verdad.

Es el unico nivel que puede demostrar tres cosas:

  1. que get_current_user autentica contra la base, no contra un doble,
  2. que get_authenticated_session deja puesto app.current_user_id,
  3. que la RLS de profiles filtra, y por tanto que el punto 2 hace falta.

La peticion entera corre como `flash_test_app`, un rol NOSUPERUSER NOBYPASSRLS.
Con el rol `flash` de siempre estos tests pasarian en falso: los superusuarios
se saltan la RLS incondicionalmente.
"""

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.dependencies import get_email_sender, get_session
from apps.api.infrastructure.cookies import ACCESS_COOKIE
from apps.api.main import app
from tests.conftest import requiere_bd
from tests.fakes import InMemoryEmailSender

pytestmark = [pytest.mark.integration, requiere_bd]

SIGNUP = "/api/auth/signup"
VERIFY = "/api/auth/verify-email"
LOGIN = "/api/auth/login"
ME = "/api/auth/me"
PASSWORD = "una-frase-larga-y-seguraB"


class _CorreoConEnlace(InMemoryEmailSender):
    def build_verification_link(self, token: str) -> str:
        return f"https://app.test/verify?token={token}"


@pytest.fixture
def correo() -> _CorreoConEnlace:
    return _CorreoConEnlace()


@pytest_asyncio.fixture
async def client(rls_db_session, correo) -> AsyncClient:
    """Cliente HTTP cuya sesion de base corre bajo RLS.

    rls_db_session y no db_session: es lo que hace que las politicas se
    evaluen. Todo lo que escriba el test se revierte al terminar.
    """

    async def sesion_por_peticion():
        """Entrega la sesion con app.current_user_id SIN declarar.

        Corrige una diferencia entre esta fixture y produccion que hacia
        pasar los tests en falso.

        set_config(..., is_local=true) muere al cerrar la transaccion, y en
        produccion cada peticion abre la suya: toda peticion empieza sin la
        variable. Aqui NO, porque rls_db_session envuelve el test entero en
        una unica transaccion con savepoints, asi que el valor que dejo el
        registro sobrevivia a las peticiones siguientes. Resultado: los tests
        del SET pasaban aunque se borrara get_authenticated_session, porque
        leian el SET que hizo el repositorio de perfiles durante el signup.

        RESET y no set_config(..., ''): dice lo que significa. Deja la
        variable en cadena vacia, no en NULL —son estados distintos en
        Postgres—, y es justo el caso que la migracion 0005 enseno a las
        politicas a tratar como "nadie declarado".
        """
        await rls_db_session.execute(text("RESET app.current_user_id"))
        yield rls_db_session

    app.dependency_overrides[get_session] = sesion_por_peticion
    app.dependency_overrides[get_email_sender] = lambda: correo
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as cliente:
        yield cliente
    app.dependency_overrides.clear()


async def _sesion_iniciada(client, correo) -> str:
    """Registra, verifica e inicia sesion. Devuelve el email.

    Al terminar, el cliente lleva las cookies que dejo el login: a partir de
    aqui sus peticiones van autenticadas igual que las de un navegador.
    """
    email = f"me-int-{uuid.uuid4()}@ejemplo.com"
    await client.post(SIGNUP, json={"email": email, "password": PASSWORD})
    token = correo.verificaciones[-1][1].split("token=")[1]
    await client.post(VERIFY, json={"token": token})
    await client.post(LOGIN, json={"email": email, "password": PASSWORD})
    return email


# ---------------------------------------------------------------------------
# El recorrido completo
# ---------------------------------------------------------------------------


async def test_el_registro_completo_funciona_bajo_rls(client, correo) -> None:
    """Nada de esto se habia ejecutado nunca sujeto a las politicas.

    El registro inserta en profiles, que tiene FORCE ROW LEVEL SECURITY y una
    politica de INSERT que exige que la fila coincida con app.current_user_id.
    Como `flash` es superusuario, hasta hoy esa politica no se evaluaba jamas.
    Si al rol le faltara un permiso o al repositorio le faltara el SET, se ve
    aqui y no el dia del despliegue.
    """
    email = f"me-int-{uuid.uuid4()}@ejemplo.com"

    r = await client.post(SIGNUP, json={"email": email, "password": PASSWORD})

    assert r.status_code == 201


async def test_devuelve_al_usuario_de_la_cookie(client, correo) -> None:
    email = await _sesion_iniciada(client, correo)

    r = await client.get(ME)

    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["email"] == email
    assert cuerpo["email_verified"] is True
    # El registro crea el perfil sin nombre: el usuario aun no eligio ninguno.
    assert cuerpo["display_name"] is None


async def test_el_id_devuelto_es_el_del_token(client, correo) -> None:
    """Que el id sale del token y no de 'el primer usuario de la tabla'."""
    await _sesion_iniciada(client, correo)
    otro_email = f"me-int-{uuid.uuid4()}@ejemplo.com"
    await client.post(SIGNUP, json={"email": otro_email, "password": PASSWORD})

    cuerpo = (await client.get(ME)).json()

    assert cuerpo["email"] != otro_email


async def test_sin_haber_iniciado_sesion_responde_401(client) -> None:
    r = await client.get(ME)

    assert r.status_code == 401


async def test_usuario_borrado_con_token_vivo_responde_401(
    client, correo, rls_db_session
) -> None:
    """La razon de que get_current_user consulte la base en cada peticion.

    El access token sigue siendo autentico y sin caducar; lo que ya no existe
    es su dueno. Si la dependencia se fiara solo de la firma, una cuenta
    borrada conservaria acceso hasta que su token expirara.
    """
    email = await _sesion_iniciada(client, correo)
    assert (await client.get(ME)).status_code == 200

    await rls_db_session.execute(
        text("DELETE FROM users WHERE email = :email"), {"email": email}
    )
    await rls_db_session.flush()

    r = await client.get(ME)

    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Lo que solo se ve por dentro: la variable de sesion y la RLS
# ---------------------------------------------------------------------------


async def _declarar(sesion, user_id) -> None:
    await sesion.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": str(user_id)},
    )


async def test_cada_peticion_declara_a_su_usuario(
    client, correo, rls_db_session
) -> None:
    """El puente entre la cookie y las politicas de Postgres.

    Sin este SET, current_setting no coincide con nadie y las politicas de
    profiles no devuelven ninguna fila. No falla nada: simplemente no hay
    datos, que es la peor forma de fallar.
    """
    await _sesion_iniciada(client, correo)
    user_id = (await client.get(ME)).json()["id"]

    await client.get(ME)

    declarado = await rls_db_session.scalar(
        text("SELECT current_setting('app.current_user_id', true)")
    )

    assert declarado == user_id


async def test_la_rls_esconde_los_perfiles_ajenos(
    client, correo, rls_db_session
) -> None:
    """Que la politica de verdad filtra.

    Se consulta `SELECT count(*) FROM profiles` SIN ningun WHERE, dos veces
    sobre la misma conexion y con los mismos datos. Lo unico que cambia entre
    las dos es quien esta declarado, y el resultado cambia con el. Eso es la
    RLS trabajando.

    Bajo `flash` las dos devolverian 1: un superusuario ignora las politicas.
    Por eso este test necesita rls_db_session.
    """
    await _sesion_iniciada(client, correo)

    # La peticion anterior dejo declarado a su usuario; se limpia para medir
    # desde el mismo punto de partida que tiene una conexion recien abierta.
    await rls_db_session.execute(text("RESET app.current_user_id"))
    ajenos = await rls_db_session.scalar(text("SELECT count(*) FROM profiles"))

    # Una peticion autenticada vuelve a declarar al dueno.
    await client.get(ME)
    propios = await rls_db_session.scalar(text("SELECT count(*) FROM profiles"))

    assert ajenos == 0
    assert propios == 1


async def test_el_perfil_llega_al_cliente_gracias_a_ese_set(
    client, correo, rls_db_session
) -> None:
    """La cadena entera, de la cookie al JSON.

    Es el test que une los tres puntos: la cookie identifica al usuario, la
    dependencia lo declara ante Postgres, la politica deja ver su fila y el
    display_name sale en la respuesta. Si se cae cualquiera de los tres
    eslabones, aqui llega null.
    """
    await _sesion_iniciada(client, correo)
    user_id = uuid.UUID((await client.get(ME)).json()["id"])

    # Ponerle nombre. La politica de UPDATE tambien exige la variable.
    await _declarar(rls_db_session, user_id)
    await rls_db_session.execute(
        text("UPDATE profiles SET display_name = 'Ana' WHERE id = :id"),
        {"id": user_id},
    )
    await rls_db_session.flush()

    r = await client.get(ME)

    assert r.json()["display_name"] == "Ana"


async def test_la_cookie_de_acceso_es_la_que_autentica(client, correo) -> None:
    """Quitando solo esa cookie, la misma peticion pasa de 200 a 401.

    Deja escrito cual de las dos cookies sostiene la sesion: la de refresh
    sigue en el cliente y no sirve para esto.
    """
    await _sesion_iniciada(client, correo)
    assert (await client.get(ME)).status_code == 200

    del client.cookies[ACCESS_COOKIE]

    assert (await client.get(ME)).status_code == 401
