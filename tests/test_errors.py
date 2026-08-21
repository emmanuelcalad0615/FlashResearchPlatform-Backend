from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.core.error_handlers import STATUS_BY_ERROR, register_error_handlers
from apps.api.core.middleware import REQUEST_ID_HEADER, RequestIDMiddleware
from packages.core.errors import (
    AppError,
    ExternalServiceError,
    NotFoundError,
)

# Cadena que un bug podria filtrar: si aparece en una respuesta HTTP, es una fuga.
LEAKY_SECRET = "postgresql://flash:flash_dev_pw@localhost:5432/flash_research"


def build_test_app() -> FastAPI:
    """App de prueba con endpoints que revientan a proposito.

    Van aqui y no en la API real para no dejar rutas /boom vivas en produccion.
    """
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)
    register_error_handlers(app)

    @app.get("/boom/domain")
    async def _domain() -> None:
        raise NotFoundError("Instrument AAPL not found", details={"ticker": "AAPL"})

    @app.get("/boom/upstream")
    async def _upstream() -> None:
        raise ExternalServiceError(
            "Polygon returned 503", details={"provider": "polygon", "status_code": 503}
        )

    @app.get("/boom/bare")
    async def _bare() -> None:
        raise AppError()

    @app.get("/boom/unhandled")
    async def _unhandled() -> None:
        raise RuntimeError(f"connection to {LEAKY_SECRET} failed")

    @app.get("/items/{item_id}")
    async def _item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    @app.get("/ok")
    async def _ok() -> dict[str, str]:
        return {"status": "ok"}

    return app


# raise_server_exceptions=False: sin esto TestClient relanza la excepcion en vez
# de dejar que el handler del 500 la atienda, y no se podria probar.
client = TestClient(build_test_app(), raise_server_exceptions=False)


def test_domain_error_uses_its_mapped_status_and_code():
    response = client.get("/boom/domain")
    assert response.status_code == 404

    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["message"] == "Instrument AAPL not found"
    assert error["details"] == {"ticker": "AAPL"}


def test_upstream_failure_is_502_not_500():
    # Un proveedor caido no es un bug nuestro: 502, no 500.
    response = client.get("/boom/upstream")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "external_service_error"
    assert response.json()["error"]["details"]["status_code"] == 503


def test_unmapped_app_error_falls_back_to_500():
    response = client.get("/boom/bare")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"


def test_validation_error_reports_the_offending_field():
    response = client.get("/items/no-soy-un-entero")
    assert response.status_code == 422

    error = response.json()["error"]
    assert error["code"] == "validation_error"
    fields = error["details"]["fields"]
    assert any(field["field"] == "path.item_id" for field in fields)


def test_unknown_route_uses_the_shared_format():
    response = client.get("/no-existe")
    assert response.status_code == 404
    # El {"detail": "Not Found"} de Starlette quedo normalizado.
    assert "detail" not in response.json()
    assert response.json()["error"]["code"] == "not_found"


def test_method_not_allowed_uses_the_shared_format():
    response = client.post("/ok")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_unhandled_exception_does_not_leak_internals():
    """Test de SEGURIDAD: el mensaje real del bug no puede salir al cliente."""
    response = client.get("/boom/unhandled")
    assert response.status_code == 500

    body = response.text
    assert LEAKY_SECRET not in body
    assert "RuntimeError" not in body
    assert "Traceback" not in body

    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert error["message"] == "An unexpected error occurred"


def test_error_body_carries_the_request_id():
    response = client.get("/boom/domain", headers={REQUEST_ID_HEADER: "trace-err-1"})
    assert response.json()["error"]["request_id"] == "trace-err-1"
    assert response.headers[REQUEST_ID_HEADER] == "trace-err-1"


def test_unhandled_error_also_carries_the_request_id():
    # El handler del 500 corre por fuera del middleware: se lee de request.state.
    response = client.get("/boom/unhandled", headers={REQUEST_ID_HEADER: "trace-err-2"})
    assert response.json()["error"]["request_id"] == "trace-err-2"


def test_successful_response_has_no_error_key():
    response = client.get("/ok")
    assert response.status_code == 200
    assert "error" not in response.json()


def test_every_domain_error_has_a_status():
    """El precio de mapear fuera del dominio: nadie puede quedarse sin status.

    Si se agrega una excepcion a packages/core/errors.py y se olvida
    registrarla, este test lo detecta en vez de dejarla caer a un 500 mudo.
    """
    subclasses = AppError.__subclasses__()
    assert subclasses, "no se encontraron subclases de AppError"
    for klass in subclasses:
        assert klass in STATUS_BY_ERROR, f"{klass.__name__} no tiene status HTTP asignado"
