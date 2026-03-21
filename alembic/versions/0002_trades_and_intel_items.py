"""trades + intel_items

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trades",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column(
            "order_kind",
            sa.String(length=16),
            server_default="unknown",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="filled",
            nullable=False,
        ),
        sa.Column("volume", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("price_krw", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("total_krw", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("fee_krw", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("exchange_order_id", sa.String(length=128), nullable=True),
        sa.Column("exchange_fill_id", sa.String(length=128), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trades_exchange_order_id", "trades", ["exchange_order_id"], unique=False)
    op.create_index("ix_trades_market_created", "trades", ["market", "created_at"], unique=False)

    op.create_table(
        "intel_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "inserted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("external_ref", sa.String(length=256), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("raw_content", sa.Text(), nullable=True),
        sa.Column("structured_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("curated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_intel_items_external_ref",
        "intel_items",
        ["external_ref"],
        unique=False,
    )
    op.create_index(
        "ix_intel_items_source_collected",
        "intel_items",
        ["source", "collected_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_intel_items_source_collected", table_name="intel_items")
    op.drop_index("ix_intel_items_external_ref", table_name="intel_items")
    op.drop_table("intel_items")
    op.drop_index("ix_trades_market_created", table_name="trades")
    op.drop_index("ix_trades_exchange_order_id", table_name="trades")
    op.drop_table("trades")
