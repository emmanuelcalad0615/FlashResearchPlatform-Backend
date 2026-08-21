import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from packages.core.errors import ExternalServiceError
from packages.core.providers.polygon import PolygonAdapter

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def adapter() -> PolygonAdapter:
    return PolygonAdapter(api_key="test-key", base_url="https://api.polygon.io")


# ---- Capa pura: traducción (sin red) --------------------------------------


def test_parse_instruments_maps_exchange_and_active(adapter):
    payload = _load("polygon_tickers.json")
    result = adapter._parse_instruments(payload)

    by_ticker = {i.ticker: i for i in result}
    assert by_ticker["AAPL"].exchange == "NASDAQ"   # XNAS
    assert by_ticker["IBM"].exchange == "NYSE"       # XNYS
    assert by_ticker["CRYPTOX"].exchange == "OTHER"  # XLON desconocido → OTHER
    assert by_ticker["OLDCO"].is_active is False
    assert by_ticker["AAPL"].name == "Apple Inc."
    assert by_ticker["AAPL"].type == "CS"     # acción común
    assert by_ticker["AAA"].type == "ETF"      # fondo cotizado


def test_merge_bars_uses_raw_close_and_adjusted_adj_close(adapter):
    unadj = _load("polygon_aggs_unadjusted.json")
    adj = _load("polygon_aggs_adjusted.json")

    bars = adapter._merge_bars("AAPL", unadj, adj)

    assert len(bars) == 2
    first = bars[0]
    assert first.ticker == "AAPL"
    assert first.trade_date == date(2024, 1, 2)
    assert first.open == Decimal("150.0")
    assert first.close == Decimal("152.5")       # crudo (unadjusted)
    assert first.adj_close == Decimal("151.25")  # ajustado (adjusted)
    assert first.volume == 50000000


def test_merge_bars_matches_by_timestamp(adapter):
    # adj_close del segundo día debe ser el ajustado de ESE día, no el del primero
    unadj = _load("polygon_aggs_unadjusted.json")
    adj = _load("polygon_aggs_adjusted.json")

    bars = adapter._merge_bars("AAPL", unadj, adj)

    assert bars[1].trade_date == date(2024, 1, 3)
    assert bars[1].close == Decimal("151.0")
    assert bars[1].adj_close == Decimal("149.75")


def test_prices_are_decimal_not_float(adapter):
    bars = adapter._merge_bars(
        "AAPL", _load("polygon_aggs_unadjusted.json"), _load("polygon_aggs_adjusted.json")
    )
    assert isinstance(bars[0].close, Decimal)
    assert isinstance(bars[0].adj_close, Decimal)


# ---- Métodos públicos: HTTP interceptado con respx (sin red real) ----------


@respx.mock
def test_get_eod_bars_calls_both_adjusted_and_unadjusted(adapter):
    url = "https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day/2024-01-02/2024-01-03"
    respx.get(url, params={"adjusted": "false"}).mock(
        return_value=httpx.Response(200, json=_load("polygon_aggs_unadjusted.json"))
    )
    respx.get(url, params={"adjusted": "true"}).mock(
        return_value=httpx.Response(200, json=_load("polygon_aggs_adjusted.json"))
    )

    bars = adapter.get_eod_bars("AAPL", date(2024, 1, 2), date(2024, 1, 3))

    assert len(bars) == 2
    assert bars[0].close == Decimal("152.5")
    assert bars[0].adj_close == Decimal("151.25")


@respx.mock
def test_list_instruments_parses_and_stops_without_next_url(adapter):
    respx.get("https://api.polygon.io/v3/reference/tickers").mock(
        return_value=httpx.Response(200, json=_load("polygon_tickers.json"))
    )

    result = adapter.list_instruments()

    assert len(result) == 5
    assert {i.ticker for i in result} == {"AAPL", "IBM", "OLDCO", "CRYPTOX", "AAA"}


@respx.mock
def test_list_instruments_respects_max_pages(adapter):
    # Página 1 trae next_url; con max_pages=1 NO debe pedir la página 2.
    page1 = {**_load("polygon_tickers.json"), "next_url": "https://api.polygon.io/v3/reference/tickers?cursor=PAGE2"}
    route = respx.get("https://api.polygon.io/v3/reference/tickers").mock(
        return_value=httpx.Response(200, json=page1)
    )

    result = adapter.list_instruments(max_pages=1)

    assert len(result) == 5
    assert route.call_count == 1  # solo una petición: paró tras la página 1


@respx.mock
def test_http_error_becomes_a_domain_error(adapter):
    """El puerto no filtra httpx: quien llama solo ve errores de dominio."""
    respx.get("https://api.polygon.io/v3/reference/tickers").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )

    with pytest.raises(ExternalServiceError) as caught:
        adapter.list_instruments()

    error = caught.value
    assert error.code == "external_service_error"
    assert error.details == {"provider": "polygon", "status_code": 401}
    # La causa tecnica se conserva para el log, sin salir del adapter.
    assert isinstance(error.__cause__, httpx.HTTPStatusError)


@respx.mock
def test_upstream_5xx_carries_the_status_for_retry_decisions(adapter):
    respx.get("https://api.polygon.io/v3/reference/tickers").mock(
        return_value=httpx.Response(503, json={"error": "service unavailable"})
    )

    with pytest.raises(ExternalServiceError) as caught:
        adapter.list_instruments()

    # El worker decide reintentar leyendo esto, sin importar httpx.
    assert caught.value.details["status_code"] == 503


@respx.mock
def test_network_failure_also_becomes_a_domain_error(adapter):
    """Sin respuesta (timeout, DNS, conexion rechazada): la otra rama de httpx."""
    respx.get("https://api.polygon.io/v3/reference/tickers").mock(
        side_effect=httpx.ConnectTimeout("timed out")
    )

    with pytest.raises(ExternalServiceError) as caught:
        adapter.list_instruments()

    assert caught.value.details["reason"] == "ConnectTimeout"
    assert "status_code" not in caught.value.details


@respx.mock
def test_provider_error_never_leaks_the_api_key(adapter):
    """SEGURIDAD: la api_key viaja como query param; no puede acabar en el log."""
    respx.get("https://api.polygon.io/v3/reference/tickers").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )

    with pytest.raises(ExternalServiceError) as caught:
        adapter.list_instruments()

    rendered = f"{caught.value.message} {caught.value.details}"
    assert "test-key" not in rendered
    assert "apiKey" not in rendered


def test_adapter_requires_api_key():
    with pytest.raises(ValueError, match="api_key"):
        PolygonAdapter(api_key="")
