from datetime import date, datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from packages.core.models.base import Base


class Instrument(Base):
    """Catálogo de tickers. Los delistados se marcan is_active=False, nunca se borran."""

    __tablename__ = "instruments"

    ticker: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    exchange: Mapped[str] = mapped_column(Text, nullable=False)
    sector: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(
        nullable=False,
        server_default="true",
        comment="FALSE = delistado. NUNCA se borra: evita survivorship bias en backtests futuros.",
    )
    delisted_at: Mapped[date | None]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "exchange IN ('NYSE','NASDAQ','AMEX','OTHER')",
            name="instruments_exchange_check",
        ),
        Index(
            "idx_instruments_active",
            "is_active",
            postgresql_where=text("is_active"),
        ),
        Index("idx_instruments_exchange", "exchange"),
        Index("idx_instruments_sector", "sector"),
    )
