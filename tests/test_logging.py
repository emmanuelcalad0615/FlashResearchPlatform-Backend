import io
import json
import re

from fastapi.testclient import TestClient

from apps.api.core.config import settings
from apps.api.core.logging import configure_logging
from apps.api.core.middleware import REQUEST_ID_HEADER, resolve_request_id
from apps.api.main import app

client = TestClient(app)

UUID4_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def test_response_carries_a_generated_request_id():
    response = client.get("/health")
    assert UUID4_PATTERN.fullmatch(response.headers[REQUEST_ID_HEADER])


def test_safe_client_request_id_is_reused():
    # Permite correlacionar con el id que ya traiga el cliente o un proxy.
    response = client.get("/health", headers={REQUEST_ID_HEADER: "trace-abc-123"})
    assert response.headers[REQUEST_ID_HEADER] == "trace-abc-123"


def test_each_request_gets_a_different_id():
    first = client.get("/health").headers[REQUEST_ID_HEADER]
    second = client.get("/health").headers[REQUEST_ID_HEADER]
    assert first != second


def test_unsafe_client_request_ids_are_discarded():
    unsafe_values = [
        "x" * 65,                 # sin limite de tamano
        "abc\nfake-log-line",     # inyeccion de una linea falsa en el log
        "id with spaces",
        "<script>alert(1)</script>",
        "",
    ]
    for value in unsafe_values:
        assert UUID4_PATTERN.fullmatch(resolve_request_id(value)), value


def test_access_log_is_json_and_carries_the_request_id():
    buffer = io.StringIO()
    original = settings.log_json
    settings.log_json = True
    configure_logging(stream=buffer)
    try:
        client.get("/health", headers={REQUEST_ID_HEADER: "trace-xyz"})
    finally:
        settings.log_json = original
        configure_logging()

    events = [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]
    access = next(event for event in events if event["event"] == "http_request")

    assert access["request_id"] == "trace-xyz"
    assert access["method"] == "GET"
    assert access["path"] == "/health"
    assert access["status_code"] == 200
    assert access["level"] == "info"
    assert "timestamp" in access
    assert isinstance(access["duration_ms"], float)
