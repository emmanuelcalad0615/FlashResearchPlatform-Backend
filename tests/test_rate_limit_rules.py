"""Tests de los limites por ruta.

Con fakeredis: sin red, como exige CLAUDE.md. Lo que se comprueba aqui no es
que el contador cuente —eso ya lo cubre test_rate_limit.py— sino que cada ruta
reciba SU limite y que los contadores esten separados.
"""

import pytest
from fakeredis import FakeAsyncRedis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from apps.api.config import RateLimitRule, Settings
from apps.api.infrastructure.middlewares.error_handlers import register_error_handlers
from apps.api.infrastructure.middlewares.rate_limit import (
    LIMIT_HEADER,
    RETRY_AFTER_HEADER,
    RateLimitMiddleware,
)
from apps.api.infrastructure.middlewares.request_id import RequestIDMiddleware

LIMITE_GENERAL = 20
VENTANA_GENERAL = 60

LOGIN = RateLimitRule(prefix="/api/auth/login", limit=5, window_seconds=60)
SIGNUP = RateLimitRule(prefix="/api/auth/signup", limit=10, window_seconds=3600)


def build_app(redis_factory, reglas=(LOGIN, SIGNUP)) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        limit=LIMITE_GENERAL,
        window_seconds=VENTANA_GENERAL,
        rules=reglas,
        redis_factory=redis_factory,
    )
    app.add_middleware(RequestIDMiddleware)
    register_error_handlers(app)

    @app.post("/api/auth/login")
    async def _login() -> dict[str, str]:
        return {"ok": "login"}

    @app.post("/api/auth/signup")
    async def _signup() -> dict[str, str]:
        return {"ok": "signup"}

    @app.get("/api/auth/me")
    async def _me() -> dict[str, str]:
        return {"ok": "me"}

    @app.get("/api/movers")
    async def _movers() -> dict[str, str]:
        return {"ok": "movers"}

    return app


@pytest.fixture
def client() -> TestClient:
    fake = FakeAsyncRedis(decode_responses=True)
    return TestClient(build_app(lambda: fake))


# ---------------------------------------------------------------------------
# Cada ruta con su limite
# ---------------------------------------------------------------------------


def test_el_sexto_login_seguido_devuelve_429(client) -> None:
    """El criterio de aceptacion del paso, literal."""
    for intento in range(5):
        assert client.post("/api/auth/login").status_code == 200, f"fallo el {intento}"

    assert client.post("/api/auth/login").status_code == 429


def test_el_quinto_login_todavia_pasa(client) -> None:
    """Sin este, un limite de 1 pasaria el test de arriba.

    Los limites se equivocan igual de facil por abajo que por arriba, y un
    limite demasiado estrecho es un incidente de disponibilidad.
    """
    for _ in range(4):
        client.post("/api/auth/login")

    assert client.post("/api/auth/login").status_code == 200


def test_el_signup_tiene_su_propio_limite(client) -> None:
    """Diez, no cinco: cada regla manda en lo suyo."""
    for _ in range(10):
        assert client.post("/api/auth/signup").status_code == 200

    assert client.post("/api/auth/signup").status_code == 429


def test_una_ruta_sin_regla_usa_el_limite_general(client) -> None:
    for _ in range(LIMITE_GENERAL):
        assert client.get("/api/movers").status_code == 200

    assert client.get("/api/movers").status_code == 429


def test_la_cabecera_anuncia_el_limite_de_esa_ruta(client) -> None:
    login = client.post("/api/auth/login")
    movers = client.get("/api/movers")

    assert login.headers[LIMIT_HEADER] == "5"
    assert movers.headers[LIMIT_HEADER] == str(LIMITE_GENERAL)


def test_el_429_dice_cuanto_esperar(client) -> None:
    """Sin Retry-After el cliente reintenta a ciegas y empeora la congestion."""
    for _ in range(6):
        respuesta = client.post("/api/auth/login")

    assert respuesta.status_code == 429
    assert 0 < int(respuesta.headers[RETRY_AFTER_HEADER]) <= 60
    assert respuesta.json()["error"]["details"]["limit"] == 5


# ---------------------------------------------------------------------------
# Contadores separados — la razon de que el scope exista
# ---------------------------------------------------------------------------


def test_cada_regla_cuenta_con_SU_ventana(client) -> None:
    """La ventana es parte de la regla, no del limite general.

    El signup se limita por hora y el login por minuto. Si el contador del
    signup venciera a los 60 segundos, el limite real seria 10 por MINUTO
    —seiscientos por hora— y el numero de la configuracion no significaria
    nada. Solo el Retry-After delata cual ventana se aplico.
    """
    for _ in range(11):
        signup = client.post("/api/auth/signup")
    for _ in range(6):
        login = client.post("/api/auth/login")

    assert signup.status_code == login.status_code == 429
    assert int(signup.headers[RETRY_AFTER_HEADER]) > 60
    assert int(login.headers[RETRY_AFTER_HEADER]) <= 60


def test_agotar_el_login_no_bloquea_las_demas_rutas(client) -> None:
    """El fallo que convertiria el limite de login en una caida del servicio.

    Si compartieran contador, cinco intentos de contrasena dejarian al usuario
    sin poder ni cargar el dashboard.
    """
    for _ in range(6):
        client.post("/api/auth/login")

    assert client.get("/api/auth/me").status_code == 200
    assert client.get("/api/movers").status_code == 200


def test_navegar_no_gasta_el_cupo_de_login(client) -> None:
    """El mismo aislamiento, en la direccion contraria.

    Sin el, quien lleve un rato usando la aplicacion no podria iniciar sesion
    al caducarle la sesion.
    """
    for _ in range(LIMITE_GENERAL):
        client.get("/api/movers")

    assert client.post("/api/auth/login").status_code == 200


def test_login_y_signup_no_comparten_contador(client) -> None:
    for _ in range(6):
        client.post("/api/auth/login")

    assert client.post("/api/auth/signup").status_code == 200


# ---------------------------------------------------------------------------
# El orden de las reglas
# ---------------------------------------------------------------------------


def test_gana_la_primera_regla_que_casa_aunque_sea_la_generica() -> None:
    """Deja escrito el filo de la decision: manda el ORDEN, no la precision.

    Con el prefijo generico escrito primero, /api/auth/login recibe el limite
    de /api/auth y el suyo no se aplica nunca. Es el error de configuracion mas
    probable de este diseno, y como el mas especifico queda tapado en silencio,
    conviene que un test lo demuestre.
    """
    generica = RateLimitRule(prefix="/api/auth", limit=2, window_seconds=60)
    fake = FakeAsyncRedis(decode_responses=True)
    client = TestClient(build_app(lambda: fake, reglas=(generica, LOGIN)))

    for _ in range(2):
        assert client.post("/api/auth/login").status_code == 200

    assert client.post("/api/auth/login").status_code == 429


def test_con_el_orden_correcto_manda_la_especifica() -> None:
    fake = FakeAsyncRedis(decode_responses=True)
    client = TestClient(build_app(lambda: fake, reglas=(LOGIN, RateLimitRule(
        prefix="/api/auth", limit=2, window_seconds=60
    ))))

    for _ in range(5):
        assert client.post("/api/auth/login").status_code == 200

    assert client.post("/api/auth/login").status_code == 429


# ---------------------------------------------------------------------------
# Lectura de la configuracion
# ---------------------------------------------------------------------------


def test_lee_una_regla_bien_escrita() -> None:
    regla = RateLimitRule.parse("/api/auth/login:5:60")

    assert regla == LOGIN


def test_el_scope_no_cambia_al_ajustar_el_limite() -> None:
    """Ajustar un limite en produccion no puede reiniciar los contadores.

    Si el scope dependiera del numero, subir el limite liberaria al instante a
    todo el que estuviera bloqueado.
    """
    antes = RateLimitRule.parse("/api/auth/login:5:60").scope
    despues = RateLimitRule.parse("/api/auth/login:50:600").scope

    assert antes == despues


@pytest.mark.parametrize(
    "texto",
    [
        "/api/auth/login:5",            # falta un campo
        "/api/auth/login:5:60:extra",   # sobra uno
        "api/auth/login:5:60",          # prefijo sin barra inicial
        "/api/auth/login:muchas:60",    # no es un numero
        "/api/auth/login:0:60",         # bloquearia la ruta entera
        "/api/auth/login:5:0",          # ventana vacia
        "/api/auth/login:-5:60",        # negativo
        "",                             # vacia
    ],
)
def test_rechaza_reglas_mal_escritas(texto: str) -> None:
    with pytest.raises(ValueError):
        RateLimitRule.parse(texto)


def test_una_regla_mal_escrita_impide_arrancar(monkeypatch) -> None:
    """La guarda de arranque.

    Sin ella, la errata se descartaria en silencio y la ruta se quedaria con el
    limite general: el sintoma seria no tener sintoma.
    """
    monkeypatch.setenv("RATE_LIMIT_RULES", "/api/auth/login:cinco:60")

    with pytest.raises(ValidationError):
        Settings()


def test_el_orden_de_la_configuracion_se_conserva(monkeypatch) -> None:
    """Como manda el orden, la configuracion no puede reordenarse por dentro."""
    monkeypatch.setenv("RATE_LIMIT_RULES", "/api/z:1:60,/api/a:2:60")

    reglas = Settings().parsed_rate_limit_rules

    assert [r.prefix for r in reglas] == ["/api/z", "/api/a"]


# ---------------------------------------------------------------------------
# La otra mitad de la clave: quien pide
# ---------------------------------------------------------------------------


def test_dos_clientes_distintos_no_comparten_contador() -> None:
    """La clave tiene dos partes variables: scope e identidad.

    Todos los tests de arriba usan un solo cliente, asi que pasarian igual si
    la identidad no formara parte de la clave: el limite seria GLOBAL y el
    primer usuario que se equivocara de contrasena cinco veces dejaria sin
    login a todos los demas.
    """
    fake = FakeAsyncRedis(decode_responses=True)
    app = build_app(lambda: fake)
    ana = TestClient(app, client=("10.0.0.1", 5000))
    beto = TestClient(app, client=("10.0.0.2", 5000))

    for _ in range(6):
        ana.post("/api/auth/login")

    assert ana.post("/api/auth/login").status_code == 429
    assert beto.post("/api/auth/login").status_code == 200
