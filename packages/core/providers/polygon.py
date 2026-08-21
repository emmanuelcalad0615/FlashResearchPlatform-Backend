from datetime import UTC, date, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx

from packages.core.errors import ExternalServiceError
from packages.core.providers.base import MarketDataProvider
from packages.core.schemas import InstrumentDTO, OHLCVBar

# MIC (Market Identifier Code) de Polygon → enum interno de exchange.
_EXCHANGE_MAP = {
    "XNYS": "NYSE",
    "XNAS": "NASDAQ",
    "XASE": "AMEX",
}


def _map_exchange(mic: str | None) -> str:
    """Traduce el MIC de Polygon al enum interno. Desconocido → OTHER."""
    return _EXCHANGE_MAP.get(mic or "", "OTHER")


def _to_decimal(value: float | int | str) -> Decimal:
    """Convierte a Decimal vía str para no arrastrar imprecisión de float."""
    return Decimal(str(value))


def _epoch_ms_to_date(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date()


class PolygonAdapter(MarketDataProvider):
    """Adapter concreto para Polygon.io. Único punto del sistema que conoce Polygon."""

    def __init__(self, api_key: str, base_url: str = "https://api.polygon.io", timeout: float = 30.0):
        if not api_key:
            raise ValueError("PolygonAdapter requiere api_key")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        # Un solo cliente reusado en todas las llamadas: una conexión con keep-alive,
        # una consulta DNS. Evita reabrir DNS+TCP+TLS por petición (más rápido y robusto).
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        """Cierra la conexión HTTP subyacente. Llamar al terminar de usar el adapter."""
        self._client.close()

    def __enter__(self) -> "PolygonAdapter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- HTTP (capa que toca la red) --------------------------------------

    def _get(self, url: str, params: dict | None = None) -> dict:
        """Unico punto del adapter que toca la red.

        Envuelve los fallos de httpx en ExternalServiceError: el puerto promete
        que nadie fuera de aqui conoce la libreria HTTP, y las excepciones son
        parte del contrato tanto como la firma.
        """
        params = {**(params or {}), "apiKey": self._api_key}
        try:
            resp = self._client.get(url, params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Polygon respondio, pero con 4xx/5xx. El status viaja en details
            # para que el worker decida si reintentar (429/5xx) o no (401/404),
            # sin tener que importar httpx.
            raise ExternalServiceError(
                f"Polygon returned {exc.response.status_code}",
                details={
                    "provider": "polygon",
                    "status_code": exc.response.status_code,
                },
            ) from exc
        except httpx.RequestError as exc:
            # Nunca hubo respuesta: timeout, DNS, conexion rechazada, TLS.
            # Rama distinta de HTTPStatusError; atrapar solo la otra dejaria
            # abierto el caso mas comun en produccion.
            #
            # SEGURIDAD: nada de exc.request.url en details. La api_key viaja
            # como query param, y acabaria escrita en texto plano en el log.
            raise ExternalServiceError(
                "Polygon is unreachable",
                details={"provider": "polygon", "reason": type(exc).__name__},
            ) from exc
        return resp.json()

    def _get_aggs(self, ticker: str, start: date, end: date, *, adjusted: bool) -> dict:
        url = (
            f"{self._base_url}/v2/aggs/ticker/{ticker}/range/1/day/"
            f"{start.isoformat()}/{end.isoformat()}"
        )
        return self._get(url, {"adjusted": str(adjusted).lower(), "sort": "asc", "limit": 50000})

    # ---- Puerto: métodos públicos -----------------------------------------

    def list_instruments(self, max_pages: int | None = None) -> list[InstrumentDTO]:
        instruments: list[InstrumentDTO] = []
        url = f"{self._base_url}/v3/reference/tickers"
        base_params = {"market": "stocks", "active": "true", "limit": 1000}
        params = dict(base_params)

        pages = 0
        while True:
            payload = self._get(url, params)
            instruments.extend(self._parse_instruments(payload))
            pages += 1

            next_url = payload.get("next_url")
            if not next_url or (max_pages is not None and pages >= max_pages):
                break

            # El cursor de next_url NO preserva nuestro limit (cae al default 100).
            # Lo extraemos y re-aplicamos base_params para mantener páginas de 1000.
            cursor = parse_qs(urlparse(next_url).query).get("cursor", [None])[0]
            params = {**base_params, "cursor": cursor}

        return instruments

    def get_eod_bars(self, ticker: str, start: date, end: date) -> list[OHLCVBar]:
        raw_unadjusted = self._get_aggs(ticker, start, end, adjusted=False)
        raw_adjusted = self._get_aggs(ticker, start, end, adjusted=True)
        return self._merge_bars(ticker, raw_unadjusted, raw_adjusted)

    # ---- Traducción (capa pura, sin red — testeable con fixtures) ----------

    @staticmethod
    def _parse_instruments(payload: dict) -> list[InstrumentDTO]:
        out: list[InstrumentDTO] = []
        for r in payload.get("results", []):
            out.append(
                InstrumentDTO(
                    ticker=r["ticker"],
                    name=r.get("name") or r["ticker"],
                    exchange=_map_exchange(r.get("primary_exchange")),
                    type=r.get("type"),
                    is_active=r.get("active", True),
                )
            )
        return out

    @staticmethod
    def _merge_bars(ticker: str, unadjusted: dict, adjusted: dict) -> list[OHLCVBar]:
        # close crudo viene de unadjusted; adj_close viene de adjusted.
        # Se emparejan por timestamp de la vela (campo "t", epoch ms).
        adj_close_by_ts = {row["t"]: row["c"] for row in adjusted.get("results", [])}

        bars: list[OHLCVBar] = []
        for row in unadjusted.get("results", []):
            ts = row["t"]
            adj_c = adj_close_by_ts.get(ts, row["c"])  # fallback: crudo si falta ajustado
            bars.append(
                OHLCVBar(
                    ticker=ticker,
                    trade_date=_epoch_ms_to_date(ts),
                    open=_to_decimal(row["o"]),
                    high=_to_decimal(row["h"]),
                    low=_to_decimal(row["l"]),
                    close=_to_decimal(row["c"]),
                    adj_close=_to_decimal(adj_c),
                    volume=int(row["v"]),
                )
            )
        return bars
