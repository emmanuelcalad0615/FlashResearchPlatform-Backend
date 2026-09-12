"""El contrato publicado y el codigo no pueden separarse.

Estos tests son el gate real de sincronizacion del backend (HU-A14): corren
dentro del pytest que el CI ya ejecuta, sin red y sin base de datos. Si alguien
cambia la forma de la API sin regenerar contract/openapi.json, fallan aqui, no
en el frontend.
"""

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.infrastructure.middlewares.contract import CONTRACT_VERSION
from apps.api.infrastructure.middlewares.error_handlers import ErrorResponse
from apps.api.main import app
from scripts.export_openapi import CONTRACT_PATH, render_spec

client = TestClient(app)

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

# Los tres que ERROR_RESPONSES declara para toda ruta.
GLOBAL_ERROR_STATUSES = ("422", "429", "500")


def operations() -> list[tuple[str, str, dict]]:
    """(path, metodo, operacion) de todo el spec."""
    spec = app.openapi()
    return [
        (path, method, operation)
        for path, methods in spec["paths"].items()
        for method, operation in methods.items()
    ]


def test_export_is_deterministic():
    """Dos exportaciones seguidas dan el mismo texto.

    Sin esto el gate del CI daria falsos positivos: un reordenamiento de dict
    se veria como un cambio de contrato.
    """
    assert render_spec() == render_spec()


def test_committed_snapshot_matches_app():
    """El snapshot commiteado es el que produce el codigo de hoy.

    ESTE es el gate de drift. Si falla:
        uv run python scripts/export_openapi.py
    """
    assert CONTRACT_PATH.exists(), "Falta contract/openapi.json"
    assert CONTRACT_PATH.read_text(encoding="utf-8") == render_spec()


def test_contract_version_is_single_sourced():
    """Un solo numero en tres sitios: codigo, spec y manifiesto npm."""
    package_json = json.loads(
        (Path(CONTRACT_PATH).parent / "package.json").read_text(encoding="utf-8")
    )
    assert app.openapi()["info"]["version"] == CONTRACT_VERSION
    assert package_json["version"] == CONTRACT_VERSION


def test_contract_version_is_semver():
    assert SEMVER.match(CONTRACT_VERSION), f"CONTRACT_VERSION invalida: {CONTRACT_VERSION}"


def test_every_operation_has_stable_operation_id():
    """operationIds explicitos, unicos y sin el sufijo autogenerado de FastAPI.

    El default ("health_check_health_get") cambia al renombrar la funcion del
    router, y con el cambia el nombre del metodo en el cliente TypeScript: un
    breaking change sin cambio de API.
    """
    ids = []
    for path, method, operation in operations():
        operation_id = operation.get("operationId")
        assert operation_id, f"{method.upper()} {path} sin operationId"
        assert not operation_id.endswith(f"_{method}"), (
            f"{operation_id} parece autogenerado por FastAPI; declara operation_id explicito"
        )
        ids.append(operation_id)
    assert len(ids) == len(set(ids)), f"operationIds duplicados: {ids}"


def test_decimal_fields_are_strings():
    """Los Decimal salen como string en el contrato, nunca como number.

    Es la convencion del repo (precios siempre Decimal) llevada a la frontera:
    Pydantic serializa Decimal a string para no perder precision. Hoy ninguna
    ruta expone Decimal; el test es la red que atrapa al primer endpoint de
    mercado que llegue.
    """
    schemas = app.openapi().get("components", {}).get("schemas", {})
    for name, schema in schemas.items():
        for field, spec in schema.get("properties", {}).items():
            if "decimal" in str(spec.get("pattern", "")).lower() or spec.get("format") == "decimal":
                assert spec.get("type") == "string", f"{name}.{field} deberia ser string"


def test_every_operation_documents_its_success_shape():
    """Toda operacion declara el TIPO de su respuesta exitosa, no un objeto vacio.

    Una ruta sin response_model se documenta como {"schema": {}} y el frontend
    genera `unknown`: el contrato existe pero no dice nada util. Este test se
    escribio despues de encontrar exactamente eso en /health y / durante la
    implementacion, y evita que la proxima ruta nazca igual.

    El codigo exitoso no siempre es 200 (un POST que crea recurso responde
    201), asi que se busca el primer 2xx declarado en vez de asumirlo fijo.
    """
    for path, method, operation in operations():
        success_codes = [code for code in operation["responses"] if code.startswith("2")]
        assert success_codes, f"{method.upper()} {path} no declara una respuesta exitosa"
        schema = operation["responses"][success_codes[0]]["content"]["application/json"]["schema"]
        assert schema, f"{method.upper()} {path} no declara response_model"


# --- Envelope de error (Etapa 1.2-bis) -------------------------------------


def test_every_operation_documents_the_error_envelope():
    """Toda operacion declara 422, 429 y 500 apuntando a ErrorResponse.

    Una ruta nueva no puede escaparse: el `responses` global de la app se
    aplica a todas.
    """
    for path, method, operation in operations():
        for status in GLOBAL_ERROR_STATUSES:
            assert status in operation["responses"], f"{method.upper()} {path} sin {status}"
            schema = operation["responses"][status]["content"]["application/json"]["schema"]
            assert schema["$ref"] == "#/components/schemas/ErrorResponse", (
                f"{method.upper()} {path} documenta el {status} con otra forma: {schema}"
            )


def test_documented_error_shape_matches_runtime():
    """La forma documentada y la que la API devuelve de verdad son la misma.

    Cierra el circulo: si alguien cambia build_error_response() sin tocar
    ErrorResponse, falla aqui y no en el frontend.
    """
    response = client.get("/ruta-que-no-existe")

    assert response.status_code == 404
    envelope = ErrorResponse.model_validate(response.json())
    assert envelope.error.code == "not_found"
    assert envelope.error.details == {}


def test_http_validation_error_is_not_in_the_spec():
    """El 422 automatico de FastAPI no debe quedar en el contrato.

    FastAPI documenta {"detail": [...]} en toda ruta con parametros, pero
    validation_error_handler devuelve el envelope propio. Esta es la
    discrepancia que ERROR_RESPONSES corrige; el test evita que vuelva.
    """
    schemas = app.openapi().get("components", {}).get("schemas", {})
    assert "HTTPValidationError" not in schemas
