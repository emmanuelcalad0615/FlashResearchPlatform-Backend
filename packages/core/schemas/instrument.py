from pydantic import BaseModel, ConfigDict


class InstrumentDTO(BaseModel):
    """Instrumento canónico (formato interno del proveedor → sistema).

    Shape agnóstico de la base de datos: el job de catálogo (B02) lo traduce
    a filas de la tabla `instruments`.
    """

    model_config = ConfigDict(frozen=True)

    ticker: str
    name: str
    exchange: str  # normalizado a NYSE | NASDAQ | AMEX | OTHER
    type: str | None = None  # tipo crudo del proveedor: CS, ETF, ADRC, ... (para filtrar en B02)
    sector: str | None = None
    industry: str | None = None
    is_active: bool = True
