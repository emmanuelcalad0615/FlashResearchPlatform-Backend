from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, model_validator


class OHLCVBar(BaseModel):
    """Vela OHLCV canónica (formato interno, agnóstico del proveedor).

    Precios como Decimal, NUNCA float (regla del proyecto: precisión monetaria).
    """

    model_config = ConfigDict(frozen=True)

    ticker: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adj_close: Decimal
    volume: int

    @model_validator(mode="after")
    def _check_sane(self) -> "OHLCVBar":
        if self.high < self.low:
            raise ValueError(f"high ({self.high}) < low ({self.low}) para {self.ticker}")
        if self.volume < 0:
            raise ValueError(f"volume negativo ({self.volume}) para {self.ticker}")
        return self


class Quote(BaseModel):
    """Cotización puntual canónica."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    price: Decimal
    change_pct: Decimal | None
    volume: int
    timestamp: datetime
