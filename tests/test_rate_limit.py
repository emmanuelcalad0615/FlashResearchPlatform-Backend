import pytest
from fakeredis import FakeAsyncRedis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from apps.api.core.error_handlers import register_error_handlers
from apps.api.core.logging import REQUEST_ID_HEADER
from apps.api.core.middleware import RequestIDMiddleware
from apps.api.core.rate_limit import (
    LIMIT_HEADER,
    REMAINING_HEADER,
    RETRY_AFTER_HEADER,
    RateLimitMiddleware,
)

LIMIT = 3
WINDOW = 60


def build_app(redis_factory) -> FastAPI:
    """Replica el montaje de main.py: RateLimit por dentro de RequestID."""
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        limit=LIMIT,
        window_seconds=WINDOW,
        exempt_paths=frozenset({"/health"}),
        redis_factory=redis_factory,
    )
    app.add_middleware(RequestIDMiddleware)
    register_error_handlers(app)

    @app.get("/health")
    async def _health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/data")
    async def _data() -> dict[str, str]:
        return {"data": "ok"}

    return app


@pytest.fixture
def client() -> TestClient:
    """Redis falso en memoria: sin red, como exige CLAUDE.md."""
    fake = FakeAsyncRedis(decode_responses=True)
    return TestClient(build_app(lambda: fake))


@pytest.fixture
def broken_client() -> TestClient:
    """Simula Redis caido para probar el fail open."""

    class BrokenRedis:
        def pipeline(self):
            raise RedisConnectionError("Redis is down")

    return TestClient(build_app(BrokenRedis))


def test_requests_under_the_limit_pass(client):
    for _ in range(LIMIT):
        assert client.get("/data").status_code == 200


def test_request_over_the_limit_gets_429(client):
    for _ in range(LIMIT):
        client.get("/data")

    response = client.get("/data")
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"


def test_remaining_header_counts_down(client):
    first = client.get("/data")
    assert first.headers[LIMIT_HEADER] == str(LIMIT)
    assert first.headers[REMAINING_HEADER] == str(LIMIT - 1)

    second = client.get("/data")
    assert second.headers[REMAINING_HEADER] == str(LIMIT - 2)


def test_rejection_tells_the_client_when_to_retry(client):
    for _ in range(LIMIT + 1):
        response = client.get("/data")

    # Sin Retry-After el cliente reintenta a ciegas y empeora la congestion.
    retry_after = int(response.headers[RETRY_AFTER_HEADER])
    assert 1 <= retry_after <= WINDOW
    assert response.headers[REMAINING_HEADER] == "0"


def test_health_is_exempt(client):
    # El healthcheck de Docker lo llama cada 5s: si se autobloqueara, Docker
    # creeria que la API murio y la reiniciaria en bucle.
    for _ in range(LIMIT * 3):
        assert client.get("/health").status_code == 200


def test_exempt_path_does_not_consume_the_quota(client):
    for _ in range(LIMIT * 3):
        client.get("/health")

    assert client.get("/data").status_code == 200


def test_rejection_keeps_the_shared_error_shape(client):
    for _ in range(LIMIT + 1):
        response = client.get("/data", headers={REQUEST_ID_HEADER: "trace-429"})

    error = response.json()["error"]
    assert error["code"] == "rate_limited"
    assert error["details"] == {"limit": LIMIT, "window_seconds": WINDOW}
    # RateLimit va por dentro de RequestID, asi que el 429 lleva su id.
    assert error["request_id"] == "trace-429"


def test_fails_open_when_redis_is_down(broken_client):
    """Un limitador caido no debe tumbar la API entera."""
    for _ in range(LIMIT * 3):
        assert broken_client.get("/data").status_code == 200


async def test_window_expiry_is_set_only_once():
    """Sin nx=True cada peticion reiniciaria la ventana y no venceria nunca.

    Bajo carga constante eso dejaria al cliente bloqueado para siempre: el
    contador nunca llegaria a expirar.
    """
    fake = FakeAsyncRedis(decode_responses=True)
    limiter = RateLimitMiddleware(
        None, limit=LIMIT, window_seconds=WINDOW, redis_factory=lambda: fake
    )

    count, ttl = await limiter._register_hit("k")
    assert (count, ttl) == (1, WINDOW)

    # Simula que la ventana ya avanzo y quedan 5 segundos.
    await fake.expire("k", 5)

    count, ttl = await limiter._register_hit("k")
    assert count == 2
    assert ttl == 5, "el EXPIRE reinicio la ventana: falta nx=True"
