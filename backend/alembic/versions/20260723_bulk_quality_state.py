"""persist document type for bulk quality gates

Revision ID: 20260723_bulk_quality_state
Revises: 20260723_bulk_content_hash
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa


revision = "20260723_bulk_quality_state"
down_revision = "20260723_bulk_content_hash"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {
        column["name"] for column in inspector.get_columns(table_name)
    }


def upgrade() -> None:
    if not _has_column("papers", "document_type"):
        op.add_column(
            "papers",
            sa.Column("document_type", sa.String(length=30), nullable=True),
        )
    if not _has_column("papers", "extraction_skip_reason"):
        op.add_column(
            "papers",
            sa.Column(
                "extraction_skip_reason",
                sa.String(length=100),
                nullable=True,
            ),
        )


def downgrade() -> None:
    if _has_column("papers", "extraction_skip_reason"):
        op.drop_column("papers", "extraction_skip_reason")
    if _has_column("papers", "document_type"):
        op.drop_column("papers", "document_type")
