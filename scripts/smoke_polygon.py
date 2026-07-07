"""Smoke test manual del adapter de Polygon (llama a la API REAL).

Uso:
    uv run python scripts/smoke_polygon.py

Requiere POLYGON_API_KEY en .env. Free tier: 5 requests/min, datos EOD.
"""

from datetime import date

from dotenv import load_dotenv

from apps.api.core.config import settings
from packages.core.providers.polygon import PolygonAdapter

load_dotenv()


def main() -> None:
    if not settings.polygon_api_key:
        raise SystemExit("Falta POLYGON_API_KEY en .env")

    with PolygonAdapter(
        api_key=settings.polygon_api_key,
        base_url=settings.polygon_base_url,
    ) as adapter:
        print("== get_eod_bars(AAPL) ==")
        bars = adapter.get_eod_bars("AAPL", date(2025, 1, 2), date(2025, 1, 10))
        for b in bars:
            print(f"{b.trade_date}  close={b.close}  adj_close={b.adj_close}  vol={b.volume}")
        print(f"total velas: {len(bars)}\n")

        print("== list_instruments(max_pages=1) (primeros 5) ==")
        instruments = adapter.list_instruments(max_pages=1)
        for i in instruments[:5]:
            print(f"{i.ticker:8} {i.exchange:8} active={i.is_active}  {i.name}")
        print(f"total instrumentos: {len(instruments)}")


if __name__ == "__main__":
    main()
