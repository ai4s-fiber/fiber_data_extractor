"""add content hash for resumable bulk ingestion

Revision ID: 20260723_bulk_content_hash
Revises: 20260710_open_workspace
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa


revision = "20260723_bulk_content_hash"
down_revision = "20260710_open_workspace"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {
        column["name"] for column in inspector.get_columns(table_name)
    }


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return index_name in {
        index["name"] for index in inspector.get_indexes(table_name)
    }


def upgrade() -> None:
    if not _has_column("papers", "content_sha256"):
        op.add_column(
            "papers",
            sa.Column("content_sha256", sa.String(length=64), nullable=True),
        )
    if not _has_index("papers", "ix_papers_project_sha256"):
        op.create_index(
            "ix_papers_project_sha256",
            "papers",
            ["project_id", "content_sha256"],
            unique=False,
        )


def downgrade() -> None:
    if _has_index("papers", "ix_papers_project_sha256"):
        op.drop_index("ix_papers_project_sha256", table_name="papers")
    if _has_column("papers", "content_sha256"):
        op.drop_column("papers", "content_sha256")
