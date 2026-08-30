from abc import ABC, abstractmethod
from datetime import date

from packages.core.domain.entities import InstrumentDTO, OHLCVBar


class MarketDataProvider(ABC):
    """Puerto: contrato que todo proveedor de datos de mercado debe cumplir.

    El sistema habla SIEMPRE con esta interfaz, nunca con un proveedor concreto.
    Cambiar de proveedor = escribir otro adapter que herede de aquí.
    """

    @abstractmethod
    def list_instruments(self, max_pages: int | None = None) -> list[InstrumentDTO]:
        """Devuelve el universo de instrumentos disponibles.

        max_pages: límite de páginas a traer (None = todas). Útil para acotar
        el consumo de cuota o procesar por tandas.
        """
        ...

    @abstractmethod
    def get_eod_bars(self, ticker: str, start: date, end: date) -> list[OHLCVBar]:
        """Devuelve las velas diarias (EOD) de un ticker en el rango [start, end]."""
        ...
