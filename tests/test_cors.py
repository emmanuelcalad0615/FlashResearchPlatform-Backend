from fastapi.testclient import TestClient

from apps.api.core.config import settings
from apps.api.core.middleware import REQUEST_ID_HEADER
from apps.api.main import app

client = TestClient(app)

ALLOWED_ORIGIN = "http://localhost:5173"
FOREIGN_ORIGIN = "https://sitio-malicioso.example"


def test_allowed_origin_is_configured():
    # El default de desarrollo es el puerto de Vite.
    assert ALLOWED_ORIGIN in settings.cors_origins


def test_wildcard_origin_is_never_allowed():
    """Con allow_credentials=True, '*' es un agujero de seguridad."""
    assert "*" not in settings.cors_origins


def test_allowed_origin_gets_cors_headers():
    response = client.get("/health", headers={"Origin": ALLOWED_ORIGIN})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"


def test_foreign_origin_gets_no_cors_headers():
    # El navegador bloquea la lectura al no ver la cabecera.
    response = client.get("/health", headers={"Origin": FOREIGN_ORIGIN})
    assert "access-control-allow-origin" not in response.headers


def test_request_id_header_is_exposed_to_the_browser():
    """Sin expose_headers el JavaScript del frontend no puede leer el id."""
    response = client.get("/health", headers={"Origin": ALLOWED_ORIGIN})
    exposed = response.headers["access-control-expose-headers"]
    assert REQUEST_ID_HEADER.lower() in exposed.lower()


def test_preflight_is_answered_for_the_allowed_origin():
    response = client.options(
        "/health",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


def test_preflight_is_rejected_for_a_foreign_origin():
    response = client.options(
        "/health",
        headers={
            "Origin": FOREIGN_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in response.headers


def test_error_responses_also_carry_cors_headers():
    """CORS va por fuera del resto: un 404 tambien debe ser legible."""
    response = client.get("/no-existe", headers={"Origin": ALLOWED_ORIGIN})
    assert response.status_code == 404
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.json()["error"]["code"] == "not_found"
