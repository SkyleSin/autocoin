"""rename intel_items -> research_items

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-21

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("intel_items", "research_items")
    op.execute(
        'ALTER INDEX "ix_intel_items_external_ref" RENAME TO "ix_research_items_external_ref"',
    )
    op.execute(
        'ALTER INDEX "ix_intel_items_source_collected" '
        'RENAME TO "ix_research_items_source_collected"',
    )


def downgrade() -> None:
    op.execute(
        'ALTER INDEX "ix_research_items_source_collected" '
        'RENAME TO "ix_intel_items_source_collected"',
    )
    op.execute(
        'ALTER INDEX "ix_research_items_external_ref" RENAME TO "ix_intel_items_external_ref"',
    )
    op.rename_table("research_items", "intel_items")
