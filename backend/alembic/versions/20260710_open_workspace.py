"""remove user system for open workspace

Revision ID: 20260710_open_workspace
Revises: 0001_baseline
Create Date: 2026-07-10
"""

from alembic import op
import sqlalchemy as sa


revision = "20260710_open_workspace"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return column_name in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    if _has_column(table_name, column_name):
        with op.batch_alter_table(table_name) as batch:
            batch.drop_column(column_name)


def _drop_table_if_exists(table_name: str) -> None:
    if _has_table(table_name):
        op.drop_table(table_name)


def upgrade() -> None:
    _drop_column_if_exists("projects", "created_by")
    _drop_column_if_exists("papers", "uploaded_by")
    _drop_column_if_exists("extraction_jobs", "created_by")
    _drop_column_if_exists("export_jobs", "created_by")
    _drop_column_if_exists("candidate_records", "assigned_to")
    _drop_column_if_exists("candidate_records", "reviewed_by")
    _drop_column_if_exists("review_logs", "user_id")
    _drop_table_if_exists("project_members")
    _drop_table_if_exists("users")


def downgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False, server_default="Local User"),
        sa.Column("password_hash", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("system_role", sa.String(length=20), nullable=False, server_default="member"),
        sa.Column("is_superadmin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "project_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="member"),
    )
