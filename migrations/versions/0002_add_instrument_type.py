"""add type column to instruments

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-07
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "instruments",
        sa.Column(
            "type",
            sa.Text(),
            nullable=True,
            comment="Tipo crudo del proveedor: CS (acción común), ETF, ADRC, etc. Para filtrar el universo.",
        ),
    )


def downgrade() -> None:
    op.drop_column("instruments", "type")
